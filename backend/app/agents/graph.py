"""CareFlow discharge-clearance graph.

START -> load_context -> plan -> [check_department x N, in parallel] -> analyze
      -> resolve -> policy (inside resolve) -> approval (interrupt, one action at a time) -> execute -> verify
      -> plan (re-check what changed)  ... -> report -> END

One LangGraph thread per workflow (thread_id = workflow code). A run ends at `report` when nothing more can be
done until a department system changes; the next event starts a new run on the same thread, re-checking only the
departments that event concerns and reusing saved results for the rest. Database handles travel in the runtime
context, so checkpoints hold only workflow state, never patient identity.
"""
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send, interrupt
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .. import policy, rag
from ..config import Settings, get_settings
from ..fhir import FhirClient, fetch_patient_context
from ..models import ApprovalRequest, AuditLog, Blocker, Evidence, ExecutionRecord, TaskDependency, VerificationRecord, WorkflowTask, utcnow
from ..redaction import known_identifiers
from ..repo import add_evidence, audit, next_code, record_agent, workflow_by_code
from ..sim import DepartmentError, DepartmentGateway, DepartmentTimeout, DepartmentUnavailable
from ..integrations import outbound
from . import llm_agents
from .checks import CHECKS, TITLES, duration

ACTIVE_APPROVAL = ("pending", "approved", "auto_approved", "executed", "verified", "rejected", "verification_failed", "execution_failed", "superseded")
NO_AUTOMATION = ("waiting_on_dependency", "action_not_applied", "no_record", "payer_denied")
DEPT_ACTOR = {d: f"{d.capitalize()} check" for d in CHECKS}


def merge(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


class CareFlowState(TypedDict, total=False):
    workflow_code: str
    tenant_id: str
    patient_ref: str
    payer_type: str
    has_lab_orders: bool
    departments: list[str]
    dependencies: dict[str, list[str]]
    recheck: list[str] | None  # None = all departments
    to_check: list[str]
    dept_results: Annotated[dict[str, dict], merge]
    blockers: list[dict]
    iteration: int
    pending_approvals: list[str]
    to_execute: list[str]
    just_executed: list[str]
    status: str
    stop_reason: str | None
    max_iterations: int


@dataclass
class Deps:
    sessionmaker: async_sessionmaker[AsyncSession]
    tenant_id: uuid.UUID
    settings: Settings = field(default_factory=get_settings)
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    retry_backoff: float = 0.2


Ctx = Runtime[Deps]


async def _identifiers(s: AsyncSession, deps: Deps, code: str) -> list[str]:
    wf = await workflow_by_code(s, deps.tenant_id, code)
    return known_identifiers(wf.patient.display_name, wf.patient.mrn) if wf else []


# ------------------------------------------------------------------------------------------------
async def load_context(state: CareFlowState, runtime: Ctx) -> dict:
    deps = runtime.context
    async with deps.sessionmaker() as s:
        wf = await workflow_by_code(s, deps.tenant_id, state["workflow_code"])
        p = wf.patient
        has_lab, payer_type = p.has_lab_orders, p.payer_type
        if deps.settings.fhir_base_url:
            client = FhirClient(deps.settings.fhir_base_url, deps.settings.fhir_token)
            try:
                ctx = await fetch_patient_context(client, p.ref, p.encounter_ref)
                has_lab = ctx["has_lab_orders"]
                p.ward, p.bed = ctx["ward"], ctx["bed"] or p.bed
            except Exception as exc:  # FHIR down: keep the last known context, say so
                await audit(s, deps.tenant_id, wf.code, "system", "FHIR client", "FHIR read failed; using stored context", str(exc)[:200])
            finally:
                await client.aclose()
        if not p.consent_for_coordination:
            wf.status = "blocked"
            await audit(s, deps.tenant_id, wf.code, "system", "Consent check", "Stopped: no consent recorded for coordination", "A person must record consent before CareFlow reads department systems")
            await s.commit()
            return {"stop_reason": "no_consent", "status": "blocked"}
        await s.commit()
    return {"patient_ref": p.ref, "payer_type": payer_type, "has_lab_orders": has_lab, "stop_reason": None}


async def plan(state: CareFlowState, runtime: Ctx) -> dict:
    deps = runtime.context
    iteration = state.get("iteration", 0) + 1
    if state.get("departments"):
        recheck = state.get("recheck")
        if recheck is None:
            to_check = list(state["departments"])
        else:
            dependents = [d for d, ons in state["dependencies"].items() if any(r in ons for r in recheck)]
            to_check = [d for d in state["departments"] if d in set(recheck) | set(dependents)]
        return {"to_check": to_check, "iteration": iteration, "recheck": None}

    async with deps.sessionmaker() as s:
        wf = await workflow_by_code(s, deps.tenant_id, state["workflow_code"])
        forbidden = known_identifiers(wf.patient.display_name, wf.patient.mrn)
        run = await llm_agents.plan(state["payer_type"], state["has_lab_orders"], forbidden)
        out: llm_agents.PlanOut = run.value  # type: ignore[assignment]
        await s.execute(delete(WorkflowTask).where(WorkflowTask.workflow_id == wf.id))
        await s.execute(delete(TaskDependency).where(TaskDependency.workflow_id == wf.id))
        for i, d in enumerate(out.departments):
            s.add(WorkflowTask(workflow_id=wf.id, department=d, title=llm_agents_title(d), status="not_started", position=i))
        for dep in out.dependencies:
            s.add(TaskDependency(workflow_id=wf.id, department=dep.department, depends_on=dep.depends_on))
        deps_map: dict[str, list[str]] = {}
        for dep in out.dependencies:
            deps_map.setdefault(dep.department, []).append(dep.depends_on)
        chain = "; ".join(f"{d.department} depends on {d.depends_on}" for d in out.dependencies) or "no dependencies"
        await audit(s, deps.tenant_id, wf.code, "agent", "Workflow Planner", f"Planned {len(out.departments)} department tasks",
                    f"{chain}; others run in parallel", latency_ms=run.latency_ms, tokens=run.tokens or None)
        await record_agent(s, deps.tenant_id, wf.code, "Workflow Planner", run.mode, "ok", f"payer={state['payer_type']} lab={state['has_lab_orders']}",
                           f"{out.departments} | {run.note}", run.latency_ms, run.tokens)
        await s.commit()
    return {"departments": list(out.departments), "dependencies": deps_map, "to_check": list(out.departments), "iteration": iteration, "recheck": None}


def llm_agents_title(d: str) -> str:
    return TITLES[d]


def route_checks(state: CareFlowState):
    if state.get("stop_reason"):
        return "report"
    targets = state.get("to_check") or []
    if not targets:
        return "analyze"
    return [Send("check_department", {"department": d, "workflow_code": state["workflow_code"], "patient_ref": state["patient_ref"]}) for d in targets]


async def check_department(payload: dict, runtime: Ctx) -> dict:
    deps = runtime.context
    dept, ref, code = payload["department"], payload["patient_ref"], payload["workflow_code"]
    started = time.perf_counter()
    retries, data, failure = 0, None, None
    # One session (one pooled connection) for the read and the writes that record it: each department check used to
    # take two, and with a remote database every extra checkout costs round trips.
    async with deps.sessionmaker() as s:
        for attempt in range(3):
            try:
                data = await DepartmentGateway(s, deps.tenant_id).read(ref, dept)
                failure = None
                break
            except (DepartmentUnavailable, DepartmentTimeout) as exc:
                failure, retries = str(exc), attempt
                await s.rollback()  # start the next attempt (and the writes below) on a clean transaction
                await asyncio.sleep(deps.retry_backoff * (2**attempt))
        latency = int((time.perf_counter() - started) * 1000)

        wf = await workflow_by_code(s, deps.tenant_id, code)
        now = deps.now()
        if failure or data is None:
            kind = "system_unavailable" if failure else "no_record"
            msg = f"{dept.capitalize()} system unreachable after 3 attempts" if failure else f"No {dept} record found for this encounter"
            ev = await add_evidence(s, wf.id, dept, f"{dept.capitalize()} system", f"read({dept}, patient={ref})", failure or "Record not found", now, False)
            result = {"status": "blocked", "owner": "", "title": llm_agents_title(dept), "source_updated_at": now.isoformat(), "raw_status": "ERROR",
                      "issues": [{"kind": kind, "summary": msg, "evidence_ids": [ev.code], "waiting_on": None, "params": {}}], "flags": [], "error": failure}
            await audit(s, deps.tenant_id, code, "system", DEPT_ACTOR[dept], "Department system unreachable" if failure else "Record missing",
                        msg, [ev.code], latency_ms=latency)
        else:
            r = CHECKS[dept](ref, data, deps.settings, now)
            codes = []
            for obs in r.evidence:
                ev = await add_evidence(s, wf.id, dept, obs.source, obs.query, obs.observed, now, obs.untrusted)
                codes.append(ev.code)
            issues = [{"kind": i.kind, "summary": i.summary, "evidence_ids": [codes[k] for k in i.evidence], "waiting_on": i.waiting_on, "params": i.params} for i in r.issues]
            result = {"status": r.status, "owner": r.owner, "title": r.title, "source_updated_at": r.source_updated_at or now.isoformat(),
                      "raw_status": r.raw_status, "issues": issues, "flags": r.flags, "error": None}
            await audit(s, deps.tenant_id, code, "system", DEPT_ACTOR[dept], "Status read", r.evidence[0].observed, codes[:1], latency_ms=latency)
            if "document_text_contains_instructions" in r.flags and not await s.scalar(
                    select(AuditLog.id).where(AuditLog.workflow_code == code, AuditLog.actor == "Injection guard").limit(1)):
                untrusted = [c for c, o in zip(codes, r.evidence) if o.untrusted]
                await audit(s, deps.tenant_id, code, "system", "Injection guard", "Document text contained instructions; treated as data",
                            "The text was not followed and cannot trigger any action", untrusted)
        await record_agent(s, deps.tenant_id, code, DEPT_ACTOR[dept], "deterministic", "error" if failure else "ok", f"{dept} for {ref}",
                           result["raw_status"], latency, retries=retries, error=failure)
        await s.commit()
    return {"dept_results": {dept: result}}


async def analyze(state: CareFlowState, runtime: Ctx) -> dict:
    deps = runtime.context
    results = {d: dict(state["dept_results"][d]) for d in state["departments"] if d in state.get("dept_results", {})}
    findings: list[dict] = []
    for d, r in results.items():
        for issue in r["issues"]:
            findings.append({"department": d, **{k: issue[k] for k in ("kind", "summary", "evidence_ids", "waiting_on")}, "params": issue.get("params", {})})
    # Dependencies: a department that can't start because another hasn't cleared.
    for d, ons in state.get("dependencies", {}).items():
        r = results.get(d)
        if not r or r["status"] == "cleared":
            continue
        for on in ons:
            other = results.get(on)
            if other and other["status"] != "cleared" and r["raw_status"] in ("NOT_STARTED", ""):
                r["status"] = "not_started"
                ev = other["issues"][0]["evidence_ids"] if other["issues"] else []
                if not ev:
                    async with deps.sessionmaker() as s:
                        wf = await workflow_by_code(s, deps.tenant_id, state["workflow_code"])
                        row = await s.scalar(select(Evidence).where(Evidence.workflow_id == wf.id, Evidence.department == on).order_by(Evidence.observed_at.desc()).limit(1))
                        ev = [row.code] if row else []
                findings.append({"department": d, "kind": "waiting_on_dependency", "summary": f"{d.capitalize()} cannot start until {on} clears",
                                 "evidence_ids": ev, "waiting_on": on, "params": {}})

    async with deps.sessionmaker() as s:
        wf = await workflow_by_code(s, deps.tenant_id, state["workflow_code"])
        all_codes = sorted({c for f in findings for c in f["evidence_ids"]})
        ev_rows = (await s.scalars(select(Evidence).where(Evidence.workflow_id == wf.id, Evidence.code.in_(all_codes)))).all() if all_codes else []
        evidence = {e.code: e.observed for e in ev_rows}
        run = await llm_agents.analyze([{k: f[k] for k in ("department", "kind", "summary", "evidence_ids", "waiting_on")} for f in findings], evidence,
                                       known_identifiers(wf.patient.display_name, wf.patient.mrn))
        out: llm_agents.AnalysisOut = run.value  # type: ignore[assignment]
        by_key = {(f["department"], f["kind"]): f for f in findings}

        tasks = {t.department: t for t in (await s.scalars(select(WorkflowTask).where(WorkflowTask.workflow_id == wf.id))).all()}
        for d, r in results.items():
            if d in tasks:
                if r["status"] == "cleared" and tasks[d].cleared_at is None:
                    tasks[d].cleared_at = deps.now()
                elif r["status"] != "cleared":
                    tasks[d].cleared_at = None
                tasks[d].status = r["status"]
                tasks[d].owner = r["owner"] or tasks[d].owner
                try:
                    tasks[d].source_updated_at = datetime.fromisoformat(r["source_updated_at"])
                except (TypeError, ValueError):
                    pass

        open_rows = {(b.department, b.kind): b for b in (await s.scalars(select(Blocker).where(Blocker.workflow_id == wf.id, Blocker.status == "open"))).all()}
        previous_keys = set(open_rows)
        kept: list[dict] = []
        for b in sorted(out.blockers, key=lambda x: x.priority):
            f = by_key[(b.department, b.kind)]
            row = open_rows.pop((b.department, b.kind), None)
            if row is None:
                row = Blocker(workflow_id=wf.id, code=await next_code(s, "BL"), department=b.department, kind=b.kind, summary=b.summary,
                              since=deps.now(), evidence_ids=b.evidence_ids, waiting_on=f["waiting_on"])
                s.add(row)
            else:
                row.summary, row.evidence_ids, row.waiting_on = b.summary, b.evidence_ids, f["waiting_on"]
            await s.flush()
            kept.append({"code": row.code, "department": b.department, "kind": b.kind, "summary": b.summary, "evidence_ids": b.evidence_ids,
                         "waiting_on": f["waiting_on"], "params": f["params"]})
        # Action-not-applied blockers come from verify, not from checks; keep them open until a person resolves them.
        for key, row in list(open_rows.items()):
            if row.kind == "action_not_applied":
                kept.append({"code": row.code, "department": row.department, "kind": row.kind, "summary": row.summary, "evidence_ids": row.evidence_ids,
                             "waiting_on": None, "params": {}})
                open_rows.pop(key)
        for row in open_rows.values():
            row.status, row.resolved_at = "resolved", deps.now()
            await audit(s, deps.tenant_id, wf.code, "system", "Blocker Analyzer", f"Blocker {row.code} resolved", f"{row.department}: no longer observed in the source system")

        if {(b["department"], b["kind"]) for b in kept} != previous_keys and kept:  # log only when the picture changes
            await audit(s, deps.tenant_id, wf.code, "agent", "Blocker Analyzer", f"Found {len(kept)} blocker{'s' if len(kept) != 1 else ''}",
                        "; ".join(b["summary"] for b in kept) or "none", sorted({c for b in kept for c in b["evidence_ids"]}),
                        latency_ms=run.latency_ms, tokens=run.tokens or None)
        await record_agent(s, deps.tenant_id, wf.code, "Blocker Analyzer", run.mode, "ok", f"{len(findings)} findings", f"{len(kept)} blockers | {run.note}",
                           run.latency_ms, run.tokens)
        wf.status = "blocked" if kept else "in_progress"
        await s.commit()
    return {"blockers": kept, "dept_results": results, "status": "blocked" if kept else "in_progress"}


def route_after_analyze(state: CareFlowState) -> str:
    return "resolve" if state.get("blockers") else "report"


async def resolve(state: CareFlowState, runtime: Ctx) -> dict:
    deps = runtime.context
    pending, auto = [], []
    async with deps.sessionmaker() as s:
        wf = await workflow_by_code(s, deps.tenant_id, state["workflow_code"])
        forbidden = known_identifiers(wf.patient.display_name, wf.patient.mrn)
        existing = (await s.scalars(select(ApprovalRequest).where(ApprovalRequest.workflow_id == wf.id))).all()
        known_ev = {e for b in state["blockers"] for e in b["evidence_ids"]}
        for b in state["blockers"]:
            if b["kind"] in NO_AUTOMATION:
                continue
            prior = [a for a in existing if a.blocker_code == b["code"]]
            if any(a.status in ACTIVE_APPROVAL for a in prior):
                pending += [a.code for a in prior if a.status == "pending"]
                continue
            sop = [{"citation": c.citation, "text": c.text} for c in rag.index().search(b["summary"], b["department"])]
            run = await llm_agents.resolve(b, policy.candidates_for(b["department"]), sop, forbidden)
            out: llm_agents.ResolutionOut = run.value  # type: ignore[assignment]
            await record_agent(s, deps.tenant_id, wf.code, "Resolution Planner", run.mode, "ok", f"{b['code']} {b['kind']}", f"{out.action} | {run.note}",
                               run.latency_ms, run.tokens)
            if out.action == "none":
                await audit(s, deps.tenant_id, wf.code, "agent", "Resolution Planner", f"No automated action for {b['code']}", "Left for staff to handle", b["evidence_ids"])
                continue
            params = llm_agents.action_params(out.action, b)
            verdict = policy.check(out.action, b["department"], params, out.evidence_ids, known_ev)
            if not verdict.allowed:
                await audit(s, deps.tenant_id, wf.code, "system", "Policy check", f"Refused {out.action}", verdict.reason, out.evidence_ids, latency_ms=1)
                continue
            if any(a.status == "info_requested" for a in prior):
                out.description += " (re-proposed with SOP context after a request for more information)"
            row = ApprovalRequest(
                workflow_id=wf.id, code=await next_code(s, "AC"), blocker_code=b["code"], action=out.action, params=params,
                description=out.description, expected_impact=out.expected_impact, risk=verdict.risk, requires_approval=verdict.requires_approval,
                approver_role=verdict.approver_role, evidence_ids=out.evidence_ids, sop_refs=[x["citation"] for x in sop],
                status="pending" if verdict.requires_approval else "auto_approved",
            )
            s.add(row)
            await s.flush()
            await audit(s, deps.tenant_id, wf.code, "agent", "Resolution Planner", f"Proposed {out.action}",
                        f"{verdict.risk.capitalize()} risk; " + (f"needs {verdict.approver_role.lower()} approval" if verdict.requires_approval else "runs without a reviewer"),
                        out.evidence_ids, latency_ms=run.latency_ms, tokens=run.tokens or None)
            if verdict.requires_approval:
                pending.append(row.code)
                await audit(s, deps.tenant_id, wf.code, "system", "Policy check", "Approval required", f"Action {row.code} paused with interrupt()", latency_ms=1)
            else:
                auto.append(row.code)
                await audit(s, deps.tenant_id, wf.code, "system", "Policy check", "Auto-approved low-risk action", f"{row.code} {out.action}", latency_ms=1)
        if pending:
            wf.status = "awaiting_approval"
        await s.commit()
    return {"pending_approvals": sorted(set(pending)), "to_execute": auto}


def route_after_resolve(state: CareFlowState) -> str:
    # Low-risk actions run first; verify then loops back, and resolve routes to approval for what's still pending.
    if state.get("to_execute"):
        return "execute"
    if state.get("pending_approvals"):
        return "approval"
    return "report"


async def approval(state: CareFlowState, runtime: Ctx) -> dict:
    """Pause until every pending action has a decision.

    Decisions are written to the database by the API before it resumes the graph, so reviewers can decide in any
    order. Each resume replays this node; each interrupt() call consumes one resume value, and the loop pauses
    again while anything is still undecided.
    """
    deps = runtime.context
    pending = list(state.get("pending_approvals") or [])
    if not pending:
        return {}

    async def statuses() -> dict[str, str]:
        async with deps.sessionmaker() as s:
            rows = (await s.execute(select(ApprovalRequest.code, ApprovalRequest.status).where(ApprovalRequest.code.in_(pending)))).all()
        return {c: st for c, st in rows}

    current = await statuses()
    while any(st == "pending" for st in current.values()):
        interrupt({"waiting_on": sorted(c for c, st in current.items() if st == "pending")})
        current = await statuses()
    approved = [c for c in pending if current.get(c) == "approved"]
    return {"pending_approvals": [], "to_execute": list(state.get("to_execute") or []) + approved}


def route_after_approval(state: CareFlowState) -> str:
    return "execute" if state.get("to_execute") else "report"


def _mutation(action: str, params: dict, ref_code: str, now_iso: str):
    entry = {"ref": ref_code, "at": now_iso}

    def m(data: dict):
        if action == "escalate_pharmacy_task":
            data.setdefault("escalations", []).append({**entry, "task_id": params["task_id"], "to": "duty pharmacist", "follow_up_minutes": 30})
        elif action == "notify_department":
            data.setdefault("notifications", []).append({**entry, "message": params["message"]})
        elif action == "create_followup_task":
            data.setdefault("followups", []).append({**entry, "title": params["title"]})
        elif action == "create_document_task":
            data.setdefault("tasks", []).append({**entry, "doc_id": params["doc_id"], "missing": params["missing"], "assignee": "attending"})
        elif action == "request_lab_acknowledgement":
            data.setdefault("ack_requests", []).append({**entry, "report_ids": params["report_ids"]})
        elif action == "attach_document_to_claim":
            if params["document"] not in data.get("available_documents", []):
                raise DepartmentError("insurance", f"{params['document']} does not exist; nothing was attached")
            data.setdefault("attachments", []).append({**entry, "document": params["document"]})
            data.update(status="PENDING_PAYER", submitted_at=now_iso, query=None)
        elif action == "retry_portal_submission":
            data.setdefault("submissions", []).append(entry)
            data.update(status="PENDING_PAYER", submitted_at=now_iso)
        return {"applied": action}

    return m


def _effect_present(action: str, data: dict, ref_code: str) -> bool:
    lists = {
        "escalate_pharmacy_task": "escalations", "notify_department": "notifications", "create_followup_task": "followups",
        "create_document_task": "tasks", "request_lab_acknowledgement": "ack_requests", "attach_document_to_claim": "attachments",
        "retry_portal_submission": "submissions",
    }
    return any(e.get("ref") == ref_code for e in data.get(lists[action], []))


async def execute(state: CareFlowState, runtime: Ctx) -> dict:
    deps = runtime.context
    done, skipped, notices = [], set(), []
    async with deps.sessionmaker() as s:
        wf = await workflow_by_code(s, deps.tenant_id, state["workflow_code"])
        gw = DepartmentGateway(s, deps.tenant_id)
        for code in state.get("to_execute") or []:
            a = await s.scalar(select(ApprovalRequest).where(ApprovalRequest.code == code, ApprovalRequest.workflow_id == wf.id))
            key = f"{code}:execute"
            if a is None or await s.scalar(select(ExecutionRecord).where(ExecutionRecord.idempotency_key == key)):
                continue  # already executed (e.g. resumed after a crash)
            dept = a.params["department"]
            started = time.perf_counter()
            # Precondition: the blocker this action addresses must still be there.
            blocker = await s.scalar(select(Blocker).where(Blocker.workflow_id == wf.id, Blocker.code == a.blocker_code))
            try:
                current = await gw.read(wf.patient.ref, dept)
                still = current is not None and blocker is not None and blocker.kind in {i.kind for i in CHECKS[dept](wf.patient.ref, current, deps.settings, deps.now()).issues}
                if not still:
                    a.status = "superseded"
                    skipped.add(dept)
                    s.add(ExecutionRecord(workflow_id=wf.id, approval_code=code, action=a.action, idempotency_key=key, success=False, result={"skipped": "blocker no longer present"}))
                    await audit(s, deps.tenant_id, wf.code, "system", "Executor", f"Skipped {a.action}",
                                f"{code}: the {dept} system no longer shows this blocker, so nothing was done", a.evidence_ids)
                    continue
            except DepartmentError:
                pass  # can't confirm; attempt the write and let it fail loudly if the system is down
            try:
                result = await gw.write(wf.patient.ref, dept, _mutation(a.action, a.params, code, deps.now().isoformat()))
                ok, err = True, None
            except DepartmentError as exc:
                result, ok, err = {"ok": False, "error": str(exc)}, False, str(exc)
            latency = int((time.perf_counter() - started) * 1000)
            s.add(ExecutionRecord(workflow_id=wf.id, approval_code=code, action=a.action, idempotency_key=key, success=ok, result=result))
            a.status = "executed" if ok else "execution_failed"
            await audit(s, deps.tenant_id, wf.code, "system", "Executor", f"Executed {a.action}" if ok else f"{a.action} failed",
                        f"{code} on {dept}" if ok else err or "", a.evidence_ids, latency_ms=latency)
            if ok:
                done.append(code)
                if (target := outbound.ACTION_EVENTS.get(a.action)):
                    notices.append((target, {**outbound.patient_payload(wf.patient), "workflowId": wf.code, "actionId": code,
                                             "action": a.action, "description": a.description, "params": a.params}))
        await s.commit()
    for (system, event), data in notices:
        outbound.notify(deps.sessionmaker, deps.tenant_id, system, event, data, data["patientRef"])
    return {"to_execute": [], "just_executed": done, "recheck": sorted(skipped)}


async def verify(state: CareFlowState, runtime: Ctx) -> dict:
    """Never trust the write's response: re-read the department system and look for the change."""
    deps = runtime.context
    touched = set(state.get("recheck") or [])  # departments whose actions were skipped as superseded
    async with deps.sessionmaker() as s:
        wf = await workflow_by_code(s, deps.tenant_id, state["workflow_code"])
        gw = DepartmentGateway(s, deps.tenant_id)
        for code in state.get("just_executed") or []:
            a = await s.scalar(select(ApprovalRequest).where(ApprovalRequest.code == code, ApprovalRequest.workflow_id == wf.id))
            dept = a.params["department"]
            touched.add(dept)
            started = time.perf_counter()
            try:
                data = await gw.read(wf.patient.ref, dept) or {}
                present = _effect_present(a.action, data, code)
            except DepartmentError as exc:
                data, present = {"error": str(exc)}, False
            latency = int((time.perf_counter() - started) * 1000)
            s.add(VerificationRecord(workflow_id=wf.id, approval_code=code, verified=present, observed={"department": dept, "found_ref": present}))
            ev = await add_evidence(s, wf.id, dept, f"{dept.capitalize()} system", f"verify({a.action}, ref={code})",
                                    "Change found in the department system" if present else "Write reported success but no change is recorded", deps.now(), False)
            if present:
                a.status = "verified"
                await audit(s, deps.tenant_id, wf.code, "system", "Verify", f"Verified {a.action}", f"Re-read {dept}; change recorded for {code}", [ev.code], latency_ms=latency)
            else:
                a.status = "verification_failed"
                b = Blocker(workflow_id=wf.id, code=await next_code(s, "BL"), department=dept, kind="action_not_applied",
                            summary=f"{a.action} reported success but the {dept} system shows no change", since=deps.now(), evidence_ids=[ev.code])
                s.add(b)
                await audit(s, deps.tenant_id, wf.code, "system", "Verify", "Verification failed", f"{code}: write returned success, re-read found nothing; needs a person", [ev.code], latency_ms=latency)
        await s.commit()
    return {"just_executed": [], "recheck": sorted(touched)}


def route_after_verify(state: CareFlowState) -> str:
    return "plan" if state.get("iteration", 0) < state.get("max_iterations", 4) else "report"


async def report(state: CareFlowState, runtime: Ctx) -> dict:
    deps = runtime.context
    async with deps.sessionmaker() as s:
        wf = await workflow_by_code(s, deps.tenant_id, state["workflow_code"])
        if state.get("stop_reason"):
            return {"status": wf.status}
        tasks = (await s.scalars(select(WorkflowTask).where(WorkflowTask.workflow_id == wf.id).order_by(WorkflowTask.position))).all()
        blockers = (await s.scalars(select(Blocker).where(Blocker.workflow_id == wf.id, Blocker.status == "open"))).all()
        pending = await s.scalar(select(ApprovalRequest.code).where(ApprovalRequest.workflow_id == wf.id, ApprovalRequest.status == "pending").limit(1))
        all_clear = tasks and all(t.status == "cleared" for t in tasks) and not blockers
        status = "cleared" if all_clear else "awaiting_approval" if pending else "blocked" if blockers else "in_progress"
        previous = wf.status
        wf.status = status
        waited = duration(int((deps.now() - wf.authorized_at).total_seconds() // 60))
        if status != previous or status == "cleared" or not wf.summary:
            run = await llm_agents.report(status, [{"department": t.department, "status": t.status} for t in tasks],
                                          [{"department": b.department, "summary": b.summary} for b in blockers], waited,
                                          known_identifiers(wf.patient.display_name, wf.patient.mrn))
            wf.summary = run.value.summary  # type: ignore[attr-defined]
            await record_agent(s, deps.tenant_id, wf.code, "Reporter", run.mode, "ok", status, wf.summary, run.latency_ms, run.tokens)
            if status == "cleared" and wf.cleared_at is None:
                wf.cleared_at = deps.now()
                await audit(s, deps.tenant_id, wf.code, "system", "Verify", "All departments cleared", f"Re-read {len(tasks)} source systems; workflow complete")
                await audit(s, deps.tenant_id, wf.code, "agent", "Reporter", "Clearance summary written", wf.summary, latency_ms=run.latency_ms, tokens=run.tokens or None)
        await s.commit()
    return {"status": status}


def build_graph():
    g = StateGraph(CareFlowState, context_schema=Deps)
    g.add_node("load_context", load_context)
    g.add_node("plan", plan)
    g.add_node("check_department", check_department)
    g.add_node("analyze", analyze)
    g.add_node("resolve", resolve)
    g.add_node("approval", approval)
    g.add_node("execute", execute)
    g.add_node("verify", verify)
    g.add_node("report", report)

    g.add_edge(START, "load_context")
    g.add_conditional_edges("load_context", lambda st: "report" if st.get("stop_reason") else "plan", ["plan", "report"])
    g.add_conditional_edges("plan", route_checks, ["check_department", "analyze", "report"])
    g.add_edge("check_department", "analyze")
    g.add_conditional_edges("analyze", route_after_analyze, ["resolve", "report"])
    g.add_conditional_edges("resolve", route_after_resolve, ["approval", "execute", "report"])
    g.add_conditional_edges("approval", route_after_approval, ["execute", "report"])
    g.add_edge("execute", "verify")
    g.add_conditional_edges("verify", route_after_verify, ["plan", "report"])
    g.add_edge("report", END)
    return g


def thread(code: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": code}}
