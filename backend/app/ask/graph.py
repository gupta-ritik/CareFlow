"""Ask CareFlow: a LangGraph StateGraph that answers a director's operations question with checked numbers.

    guard -> understand -> planner -> data_agent -> analyst -(dig deeper, max 3 data rounds)-> data_agent
                                                          \\-> writer -> critic -(fix, max 2 retries)-> writer
                                                                              \\-> save

- guard: deterministic rules first (identity, clinical, other hospitals, writes, prompt injection); the LLM only when
  the rules can't decide.
- understand: periods are resolved deterministically in the hospital's time zone (app/ask/periods.py); the LLM only
  fills in the measure when the rules can't, or asks ONE clarifying question.
- planner: 1-4 steps over the measure registry, validated by Pydantic; an invalid plan is re-planned once, then the
  rule-based plan is used.
- data_agent: deterministic, registry queries only (app/ask/measures.py).
- analyst: deterministic statistics (Mann-Whitney U / Fisher, at least 10 cases each side, p < 0.05); the LLM may ask
  for one more step; without an LLM a rule picks the next split.
- writer (LLM, small model, or rule-based) -> critic: every number must match a stored result; unsupported sentences go
  back to the writer (at most 2 retries), then are removed; names, medical advice and causes the data can't show are
  rejected ("CareFlow's data doesn't show why").
- save: ask_sessions / ask_turns and an audit entry.

Only aggregates and the redacted question ever reach the LLM. The whole run is limited to CAREFLOW_ASK_TIMEOUT seconds;
on timeout the verified part is returned, marked partial.
"""
from __future__ import annotations

import asyncio
import json
import logging
import operator
import time
import uuid
from datetime import date, datetime
from typing import Annotated, Any, AsyncIterator, TypedDict
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, select, text

from .. import llm
from ..config import get_settings
from ..models import AskSession, AskTurn
from ..redaction import redact
from ..repo import audit
from . import analysis, rules
from .analytics_sql import ROLE
from .measures import REGISTRY, DataContext, public, registry_for_prompt, run_step
from .periods import Resolved
from .schemas import AnalystOut, GuardOut, PlanOut, Step, UnderstandOut, WriterOut

log = logging.getLogger("careflow.ask")
MAX_ROUNDS = 3
MAX_CRITIC_RETRIES = 2

PROGRESS = {
    "guard": "Checking the question",
    "understand": "Working out the period and the measure",
    "planner": "Planning the analysis",
    "analyst": "Comparing with the baseline",
    "writer": "Writing the answer",
    "critic": "Checking every number against the data",
    "save": "Saving the answer",
}


class AskState(TypedDict, total=False):
    question: str
    redacted: str
    today: str
    status: str
    message: str
    injection: bool
    data_start: str | None
    data_end: str | None
    understanding: dict
    plan: list[dict]
    plan_mode: str
    pending: list[dict]
    rounds: int
    results: list[dict]
    findings: list[dict]
    draft: list[dict]
    draft_mode: str
    critic_notes: list[str]
    critic_retries: int
    removed: list[str]
    answer: dict
    turn_id: str
    session_id: str
    llm_calls: Annotated[list[dict], operator.add]
    progress: Annotated[list[str], operator.add]


class AskContext:
    """What every node needs: database access for this hospital and who is asking."""

    def __init__(self, sessionmaker, tenant_id, user_id, user_name: str, session_id: str | None = None, tz: str | None = None,
                 use_role: bool | None = None):
        s = get_settings()
        self.sm, self.tenant_id, self.user_id, self.user_name = sessionmaker, tenant_id, user_id, user_name
        self.session_id = session_id
        self.tz = tz or s.timezone
        self.use_role = use_role
        self.data: DataContext | None = None
        self.deadline: float | None = None  # perf_counter time by which the answer must be ready (set by run_ask)

    async def data_ctx(self) -> DataContext:
        if self.data is None:
            use = self.use_role
            if use is None:
                async with self.sm() as s:
                    use = bool(await s.scalar(text("SELECT pg_has_role(current_user, oid, 'MEMBER') FROM pg_roles WHERE rolname = :r"), {"r": ROLE}))
            self.data = DataContext(self.sm, self.tenant_id, self.tz, use_role=use)
        return self.data


def _ctx(config) -> AskContext:
    return config["configurable"]["ctx"]


LLM_RESERVE_SECONDS = 4.0   # always left for the deterministic data, statistics and critic
LLM_CALL_MAX_SECONDS = 10.0  # one LLM call never takes more than this; the node then uses its rules


async def _llm(config, node: str, schema, system: str, user: str, size: str = "small"):
    """(value, call record). value is None when there is no LLM, its output failed validation, or the time budget for
    this question is too short; the node then falls back to its rules, so an answer always gets verified data."""
    deadline = getattr(_ctx(config), "deadline", None) if config else None
    budget = LLM_CALL_MAX_SECONDS
    if deadline is not None:
        budget = min(budget, deadline - time.perf_counter() - LLM_RESERVE_SECONDS)
        if budget < 2.0:
            return None, {"node": node, "mode": "fallback", "tokens": 0, "error": "skipped: not enough time left"}
    try:
        res = await asyncio.wait_for(llm.structured(schema, system, redact(user), size=size), timeout=budget)
        return res.value, {"node": node, "mode": "llm", "tokens": res.tokens, "model": res.model}
    except (asyncio.TimeoutError, TimeoutError):
        return None, {"node": node, "mode": "fallback", "tokens": 0, "error": f"LLM call took longer than {budget:.0f} s"}
    except llm.LLMUnavailable as exc:
        return None, {"node": node, "mode": "fallback", "tokens": 0, "error": str(exc)[:200]}
    except Exception as exc:  # noqa: BLE001 - schema validation errors from the model's output
        return None, {"node": node, "mode": "fallback", "tokens": 0, "error": f"invalid output: {str(exc)[:200]}"}


def _resolved(d: dict | None) -> Resolved | None:
    if not d:
        return None
    return Resolved(date.fromisoformat(d["start"]), date.fromisoformat(d["end"]), d["label"], default=d.get("default", False))


def _understanding_from(d: dict) -> rules.Understanding:
    u = rules.Understanding(measure=d["measure"], intent=d["intent"], group_by=d["group_by"], filters=d["filters"], focus=d["focus"],
                            focus_label=d["focus_label"], rank=d["rank"], clarify=d["clarify"], unanswerable=d["unanswerable"])
    u.period = _resolved(d.get("period"))
    b = d.get("baseline")
    u.baseline = (date.fromisoformat(b["start"]), date.fromisoformat(b["end"]), b["label"]) if b else None
    return u


# ---- nodes --------------------------------------------------------------------------------------------------------
async def guard_node(state: AskState, config) -> dict:
    q = state["question"]
    g = rules.guard(q)
    calls = []
    if g.allowed and g.unsure:
        out, call = await _llm(config, "guard", GuardOut,
                               "You screen questions for a hospital operations assistant. Refuse requests to identify patients, "
                               "clinical questions, other hospitals' data, or changing data. Operational totals are fine.", q)
        calls.append(call)
        if out is not None and not out.allowed:
            g = rules.GuardResult(False, out.category, rules.CAN_ANSWER)
    upd: dict = {"redacted": redact(q), "injection": g.injection, "llm_calls": calls, "progress": [PROGRESS["guard"]]}
    if not g.allowed:
        upd.update(status="refused", message=g.message)
    return upd


async def understand_node(state: AskState, config) -> dict:
    ctx = _ctx(config)
    dc = await ctx.data_ctx()
    async with ctx.sm() as s:
        if dc.use_role:
            await s.execute(text(f"SET LOCAL ROLE {ROLE}"))
        row = (await s.execute(text("SELECT min(authorized_at), max(authorized_at) FROM analytics.discharges WHERE tenant_id = :t"),
                               {"t": ctx.tenant_id})).one()
        await s.rollback()
    z = ZoneInfo(dc.tz)
    start = row[0].astimezone(z).date() if row[0] else None
    end = row[1].astimezone(z).date() if row[1] else None
    today = date.fromisoformat(state["today"])
    u = rules.understand(state["question"], today, start, end)
    calls = []
    if u.measure is None and u.unanswerable is None and u.clarify:
        out, call = await _llm(config, "understand", UnderstandOut,
                               "Map a hospital director's operations question to ONE measure from this list, or ask ONE short "
                               "clarifying question if something essential is missing.\n" + registry_for_prompt(), state["redacted"])
        calls.append(call)
        if out is not None and out.measure:
            retry = rules.understand(state["question"] + f" ({REGISTRY[out.measure].label})", today, start, end)
            if retry.measure == out.measure and not retry.clarify:
                u = retry
            else:
                u.measure, u.clarify = out.measure, None
                u.intent = out.intent or "describe"
                if out.group_by and out.group_by in REGISTRY[out.measure].dimensions:
                    u.group_by = [out.group_by]
                    u.intent = "ranking" if u.intent == "describe" else u.intent
                from .periods import default_period
                u.period = u.period or default_period(today)
        elif out is not None and out.clarify:
            u.clarify = out.clarify
    upd: dict = {"understanding": u.as_dict(), "data_start": start.isoformat() if start else None,
                 "data_end": end.isoformat() if end else None, "llm_calls": calls, "progress": [PROGRESS["understand"]]}
    if u.unanswerable:
        upd.update(status="no_data", message=u.unanswerable)
    elif u.clarify:
        upd.update(status="clarify", message=u.clarify)
    elif start is None:
        upd.update(status="no_data", message="There is no discharge history for this hospital yet, so there is nothing to analyse.")
    return upd


def _check_plan(steps: list[Step], u: rules.Understanding) -> list[Step]:
    """Dates always come from the deterministic resolver, never from the LLM."""
    out = []
    for st in steps:
        st = st.model_copy(deep=True)
        if u.period and (st.period.start, st.period.end) != (u.period.start, u.period.end) and not (st.compare and st.compare.kind == "period"):
            st.period.start, st.period.end, st.period.label = u.period.start, u.period.end, u.period.label
        if not st.why:
            st.why = f"Checking {REGISTRY[st.measure].label}"
        out.append(st)
    return out


async def planner_node(state: AskState, config) -> dict:
    u = _understanding_from(state["understanding"])
    fallback = rules.rule_plan(u)
    calls, steps, mode = [], None, "fallback"
    if llm.backend() is not None:
        prompt = (f"Question: {state['redacted']}\nResolved period: {u.period.start} to {u.period.end} ({u.period.label})\n"
                  f"Understanding: {json.dumps(state['understanding'])}\nMeasures:\n{registry_for_prompt()}\n"
                  f"A rule-based draft plan you may improve: {json.dumps([s.model_dump(mode='json') for s in fallback])}")
        system = ("Plan 1-4 data steps. Each step uses ONE measure from the list, with allowed dimensions and filters only. "
                  "Use the resolved period exactly. Return JSON matching the schema.")
        for attempt in range(2):  # the first plan, then one re-plan with the validation error
            out, call = await _llm(config, "planner", PlanOut, system, prompt)
            calls.append(call)
            if out is not None and u.measure and out.steps[0].measure != u.measure:
                # The question names a measure (found by the rules); a plan that answers something else is rejected.
                call["error"] = f"the first step must use the measure {u.measure}"
                call["mode"] = "fallback"
                out = None
            if out is not None:
                steps, mode = _check_plan(out.steps, u), "llm"
                break
            prompt += f"\nYour previous plan was rejected: {call.get('error', '')}. Use only the listed measures, dimensions and filters."
    steps = steps or fallback
    plan = [s.model_dump(mode="json") for s in steps]
    return {"plan": plan, "pending": plan, "plan_mode": mode, "rounds": 0, "results": [], "llm_calls": calls,
            "progress": [PROGRESS["planner"]]}


async def data_agent_node(state: AskState, config) -> dict:
    ctx = _ctx(config)
    dc = await ctx.data_ctx()
    results = list(state.get("results", []))
    msgs = []
    for raw in state.get("pending", []):
        step = Step.model_validate(raw)
        rid = f"R{len(results) + 1}"
        results.append(await run_step(dc, step, rid))
        msgs.append(f"{step.why or 'Checking ' + REGISTRY[step.measure].label}…")
    return {"results": results, "pending": [], "rounds": state.get("rounds", 0) + 1, "progress": msgs}


def _findings(results: list[dict]) -> list[dict]:
    out = []
    for r in results:
        out += analysis.analyse(r)
    return out


async def analyst_node(state: AskState, config) -> dict:
    results = state["results"]
    findings = _findings(results)
    upd: dict = {"findings": findings, "progress": [PROGRESS["analyst"]], "llm_calls": []}
    if all(isinstance(r["total"].get("n"), int) and r["total"]["n"] == 0 for r in results):
        p = results[0]["params"]["period"]
        upd.update(status="no_data", message=f"There is no data for {p['label'] or p['start'] + ' to ' + p['end']}. Try a longer period.")
        return upd
    if state.get("rounds", 0) >= MAX_ROUNDS:
        return upd
    extra: Step | None = None
    if llm.backend() is not None:
        out, call = await _llm(config, "analyst", AnalystOut,
                               "You are a hospital operations analyst. Given aggregate results and statistical findings, decide if ONE "
                               "more data step would explain the main finding (for example split the slow department by TPA or ward). "
                               "Return extra_step null if the answer is already clear.\nMeasures:\n" + registry_for_prompt(),
                               json.dumps({"question": state["redacted"], "results": [public(r) for r in results], "findings": findings},
                                          default=str)[:12000], size="large")
        upd["llm_calls"].append(call)
        if out is not None and out.extra_step is not None:
            extra = out.extra_step
    else:
        u = state["understanding"]
        already = {(r["measure"], tuple(r["params"]["group_by"]), tuple(sorted((r["params"].get("filters") or {}).get("department", [])))) for r in results}
        for fi in findings:
            if fi["type"] == "group_change" and "department" in fi["group"] and u["intent"] == "why_slow":
                r = next(x for x in results if x["id"] == fi["result_id"])
                cand = rules.deepen(Step.model_validate(r["params"]), fi["group"]["department"])
                if cand and (cand.measure, tuple(cand.group_by), tuple(cand.filters.get("department", []))) not in already:
                    extra = cand
                break
    if extra is not None:
        upd["pending"] = [extra.model_dump(mode="json")]
    return upd


def _facts(state: AskState) -> dict[str, dict]:
    by_result: dict[str, list[dict]] = {}
    for f in state.get("findings", []):
        by_result.setdefault(f["result_id"], []).append(f)
    return {r["id"]: analysis.facts(r, by_result.get(r["id"], [])) for r in state["results"]}


async def writer_node(state: AskState, config) -> dict:
    results = {r["id"]: r for r in state["results"]}
    calls = []
    if llm.backend() is not None:
        prompt = json.dumps({"question": state["redacted"], "findings": state["findings"], "facts": _facts(state),
                             "results": [public(r) for r in state["results"]],
                             "problems_with_previous_draft": state.get("critic_notes", [])}, default=str)[:14000]
        out, call = await _llm(config, "writer", WriterOut,
                               "Write 2-5 short plain-English sentences answering the question. Every number you write must be one of "
                               "the facts, cited as a claim {result_id, field, value}. Minutes stay in minutes. Say 'not enough "
                               "evidence' where a finding's label says so. Never name patients, give medical advice, or claim causes "
                               "the data can't show (staffing, rosters, weather): say \"CareFlow's data doesn't show why.\" instead. "
                               "Suggest up to 3 follow-up questions.", prompt)
        calls.append(call)
        if out is not None:
            return {"draft": [s.model_dump() for s in out.sentences], "draft_mode": "llm",
                    "answer": {"follow_ups": out.follow_ups[:3]}, "llm_calls": calls, "progress": [PROGRESS["writer"]]}
    return {"draft": analysis.write_rules(state["findings"], results), "draft_mode": "rules",
            "answer": {"follow_ups": analysis.follow_ups(state["understanding"], state["findings"], results)},
            "llm_calls": calls, "progress": [PROGRESS["writer"]]}


async def critic_node(state: AskState, config) -> dict:
    results = {r["id"]: r for r in state["results"]}
    tables = _facts(state)
    # Results whose comparison has too few cases: no sentence may report a difference for them.
    thin = {f["result_id"] for f in state.get("findings", []) if f["type"] == "comparison" and f["label"] == "not enough evidence"}
    kept, problems, _ = analysis.critic(state["draft"], state["question"], results, tables, thin)
    retries = state.get("critic_retries", 0)
    hard = [p for p in problems if p.rsplit('": ', 1)[-1].startswith(("the number", "cites", "R", "reports a difference"))]
    if hard and state.get("draft_mode") == "llm" and retries < MAX_CRITIC_RETRIES:
        return {"critic_notes": problems, "critic_retries": retries + 1, "progress": ["Fixing a sentence the data doesn't support"]}
    if not [s for s in kept if s["text"] != analysis.NO_CAUSE]:  # nothing usable left: the rule-based writer, checked again
        kept, more, _ = analysis.critic(analysis.write_rules(state["findings"], results), state["question"], results, tables)
        problems += more
    answer = _answer(state, kept, state.get("answer", {}).get("follow_ups") or
                     analysis.follow_ups(state["understanding"], state["findings"], results))
    return {"answer": answer, "removed": problems, "critic_notes": [], "status": "answered", "progress": [PROGRESS["critic"]]}


def _answer(state: AskState, sentences: list[dict], follow: list[str], partial: bool = False) -> dict:
    results = {r["id"]: r for r in state.get("results", [])}
    return {"sentences": sentences, "text": " ".join(s["text"] for s in sentences), "based_on": analysis.based_on(results),
            "follow_ups": follow[:3], "chart": analysis.chart_spec(results), "partial": partial}


async def save_node(state: AskState, config) -> dict:
    ids = await save_turn(_ctx(config), state)
    return {**ids, "progress": [PROGRESS["save"]]}


# ---- saving -------------------------------------------------------------------------------------------------------
async def save_turn(ctx: AskContext, state: dict, latency_ms: int = 0) -> dict:
    status = state.get("status") or "error"
    answer = state.get("answer") or {}
    if status != "answered":
        answer = {"sentences": [], "text": state.get("message", ""), "based_on": "", "follow_ups": rules.SUGGESTIONS[:3]
                  if status in ("refused", "no_data", "error") else [], "chart": None, "partial": False, "message": state.get("message", "")}
    calls = state.get("llm_calls", [])
    mode = "llm" if any(c["mode"] == "llm" for c in calls) else "fallback"
    model = next((c.get("model") for c in calls if c.get("model")), None)
    results = [public(r) for r in state.get("results", [])]
    claims = [c for s in answer.get("sentences", []) for c in s.get("claims", [])]
    question = state.get("redacted") or redact(state.get("question", ""))
    async with ctx.sm() as s:
        sess = None
        if ctx.session_id:
            try:
                sess = await s.scalar(select(AskSession).where(AskSession.id == uuid.UUID(str(ctx.session_id)),
                                                              AskSession.tenant_id == ctx.tenant_id, AskSession.user_id == ctx.user_id))
            except ValueError:
                sess = None
        if sess is None:
            sess = AskSession(tenant_id=ctx.tenant_id, user_id=ctx.user_id, title=question[:200])
            s.add(sess)
            await s.flush()
        sess.updated_at = datetime.now().astimezone()
        turn = AskTurn(session_id=sess.id, tenant_id=ctx.tenant_id, user_id=ctx.user_id, question=question, status=status,
                       partial=bool(answer.get("partial")),
                       plan={"understanding": state.get("understanding"), "plan_mode": state.get("plan_mode"), "rounds": state.get("rounds", 0),
                             "removed_sentences": state.get("removed", []), "injection": state.get("injection", False)},
                       steps=[{"result_id": r["id"], "measure": r["measure"], "params": r["params"]} for r in results],
                       results=results, answer=answer, claims=claims, latency_ms=latency_ms,
                       tokens=sum(c.get("tokens", 0) for c in calls), model=model, mode=mode)
        s.add(turn)
        await s.flush()
        await audit(s, ctx.tenant_id, "ASK", "human", ctx.user_name, "Asked Ask CareFlow a question",
                    f"Turn {turn.id}: status {status}{', partial' if turn.partial else ''}; mode {mode}"
                    + ("; prompt-injection text flagged" if state.get("injection") else ""))
        await s.commit()
        return {"turn_id": str(turn.id), "session_id": str(sess.id)}


# ---- the graph ----------------------------------------------------------------------------------------------------
def _after_guard(state: AskState) -> str:
    return "save" if state.get("status") else "understand"


def _after_understand(state: AskState) -> str:
    return "save" if state.get("status") else "planner"


def _after_analyst(state: AskState) -> str:
    if state.get("status"):
        return "save"
    return "data_agent" if state.get("pending") and state.get("rounds", 0) < MAX_ROUNDS else "writer"


def _after_critic(state: AskState) -> str:
    return "writer" if state.get("critic_notes") else "save"


def build_graph():
    g = StateGraph(AskState)
    for name, fn in (("guard", guard_node), ("understand", understand_node), ("planner", planner_node), ("data_agent", data_agent_node),
                     ("analyst", analyst_node), ("writer", writer_node), ("critic", critic_node), ("save", save_node)):
        g.add_node(name, fn)
    g.add_edge(START, "guard")
    g.add_conditional_edges("guard", _after_guard, ["understand", "save"])
    g.add_conditional_edges("understand", _after_understand, ["planner", "save"])
    g.add_edge("planner", "data_agent")
    g.add_edge("data_agent", "analyst")
    g.add_conditional_edges("analyst", _after_analyst, ["data_agent", "writer", "save"])
    g.add_edge("writer", "critic")
    g.add_conditional_edges("critic", _after_critic, ["writer", "save"])
    g.add_edge("save", END)
    return g.compile()


_graph = None


def graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def final_event(state: dict) -> dict:
    a = state.get("answer") or {}
    return {"type": "answer", "status": state.get("status", "error"), "turnId": state.get("turn_id"), "sessionId": state.get("session_id"),
            "question": state.get("redacted", ""), "message": state.get("message", "") if state.get("status") != "answered" else "",
            "answer": a.get("text", "") if state.get("status") == "answered" else state.get("message", ""),
            "sentences": a.get("sentences", []), "basedOn": a.get("based_on", ""), "followUps": a.get("follow_ups", []),
            "chart": a.get("chart"), "partial": bool(a.get("partial")), "results": [public(r) for r in state.get("results", [])],
            "mode": "llm" if any(c["mode"] == "llm" for c in state.get("llm_calls", [])) else "fallback",
            "tokens": sum(c.get("tokens", 0) for c in state.get("llm_calls", []))}


async def _partial(ctx: AskContext, state: dict, latency_ms: int) -> dict:
    """The run timed out: keep what's verified. Deterministic findings and sentences from the results so far."""
    state = dict(state)
    if state.get("status") in ("refused", "clarify", "no_data"):
        pass
    elif state.get("results"):
        state["findings"] = _findings(state["results"])
        results = {r["id"]: r for r in state["results"]}
        kept, problems, _ = analysis.critic(analysis.write_rules(state["findings"], results), state["question"], results, _facts(state))
        note = {"text": "This answer is partial: the time limit was reached before every check finished.", "claims": []}
        state["answer"] = _answer(state, kept + [note], analysis.follow_ups(state.get("understanding", {}), state["findings"], results), partial=True)
        state["status"] = "answered"
    else:
        state["status"] = "error"
        state["message"] = "The question took too long to answer and nothing could be verified in time. Try a shorter period."
    if not state.get("turn_id"):
        state.update(await save_turn(ctx, state, latency_ms))
    return state


async def run_ask(ctx: AskContext, question: str, today: date | None = None, timeout: float | None = None) -> AsyncIterator[dict]:
    """Stream progress events, then one final "answer" event."""
    s = get_settings()
    timeout = s.ask_timeout if timeout is None else timeout
    today = today or datetime.now(ZoneInfo(ctx.tz)).date()
    started = time.perf_counter()
    state: dict[str, Any] = {"question": question, "today": today.isoformat(), "llm_calls": [], "progress": []}
    queue: asyncio.Queue = asyncio.Queue()

    async def runner():
        try:
            async for chunk in graph().astream({"question": question, "today": today.isoformat()},
                                               {"configurable": {"ctx": ctx}, "recursion_limit": 40}, stream_mode="updates"):
                for node, upd in chunk.items():
                    await queue.put(("update", node, upd or {}))
            await queue.put(("done", None, None))
        except Exception as exc:  # noqa: BLE001
            log.exception("Ask CareFlow run failed")
            await queue.put(("error", None, exc))

    deadline = time.perf_counter() + timeout
    ctx.deadline = deadline  # LLM calls budget themselves against this, leaving time for the deterministic steps
    task = asyncio.create_task(runner())
    timed_out = False
    try:
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                timed_out = True
                break
            try:
                kind, node, upd = await asyncio.wait_for(queue.get(), timeout=remaining)
            except (asyncio.TimeoutError, TimeoutError):
                timed_out = True
                break
            if kind == "done":
                break
            if kind == "error":
                state["status"], state["message"] = "error", "Something went wrong while answering. It has been logged; please try again."
                break
            for k, v in upd.items():
                if k in ("llm_calls", "progress"):
                    state[k] = state.get(k, []) + v
                else:
                    state[k] = v
            for msg in upd.get("progress", []):
                yield {"type": "progress", "node": node, "message": msg if msg.endswith("…") else msg + "…"}
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
    latency = int((time.perf_counter() - started) * 1000)
    if timed_out or state.get("status") == "error" or not state.get("turn_id"):
        if timed_out:
            yield {"type": "progress", "node": "timeout", "message": "Time limit reached; returning what was verified…"}
        state = await _partial(ctx, state, latency)
    else:
        async with ctx.sm() as s:  # record the real latency on the saved turn
            turn = await s.get(AskTurn, uuid.UUID(state["turn_id"]))
            if turn is not None:
                turn.latency_ms = latency
                await s.commit()
    yield {**final_event(state), "latencyMs": latency}


async def ask(ctx: AskContext, question: str, today: date | None = None, timeout: float | None = None) -> tuple[list[dict], dict]:
    """Run to completion (tests and evals): (progress events, final event)."""
    events = [e async for e in run_ask(ctx, question, today, timeout)]
    return events[:-1], events[-1]


__all__ = ["AskContext", "run_ask", "ask", "graph", "func"]
