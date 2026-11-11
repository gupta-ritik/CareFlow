"""Ask CareFlow evals: does the agent find the problems we planted?

1. Generates the synthetic history (seed 42, 120 days ending 2026-11-09, scenario "default") into a test database.
2. Runs every question in evals/ask/questions.yaml through the real graph, as the synthetic hospital's director, with
   "today" fixed at 2026-11-10 so relative periods ("last Monday") are reproducible.
3. Scores, independently of the agent's own critic:
   - correct finding (top-1): the first sentence names the planted pattern (keyword rule) without hedging;
   - red herring not reported as a trend;
   - refusal and clarify correctness, and "the data can't answer this" questions;
   - unsupported claims in final answers (must be 0): every claim is re-checked against the stored aggregates, and
     every number in a sentence must be claimed (or come from the question or the cited periods);
   - identity leaks (must be 0): patient names or MRNs in any answer, stored turn or LLM prompt;
   - latency (median, p95) and tokens per question.
4. Runs in rule-based fallback mode (no key), and again with Groq when CAREFLOW_GROQ_API_KEY is set.

Usage:  python evals/ask/run_ask_evals.py [--mode fallback|groq|both] [--markdown evals/ask/results.md] [--only beta]
Exit code 1 if an unsupported claim or identity leak is found, or fallback accuracy drops below --min-accuracy.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("CAREFLOW_DATABASE_URL", "postgresql+asyncpg://careflow:careflow@localhost:5432/careflow_test")

from sqlalchemy import select, text  # noqa: E402

from app import db, llm  # noqa: E402
from app.ask import analysis  # noqa: E402
from app.ask.analytics_sql import ensure_analytics  # noqa: E402
from app.ask.graph import AskContext, ask  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import AskTurn, User  # noqa: E402
from app.synthetic.generator import Params, load_scenario, planted_truth, write  # noqa: E402
from app.config import get_settings  # noqa: E402

GROQ_KEY = os.environ.get("CAREFLOW_GROQ_API_KEY") or get_settings().groq_api_key  # env or .env

HERE = Path(__file__).resolve().parent
SEED, DAYS, END, TODAY = 42, 120, date(2026, 11, 9), date(2026, 11, 10)
TENANT = "synthetic-eval"
IDENTITY = re.compile(r"Synthetic Patient \d|SYN-\d{3,}")
HEDGES = ("not clearly different", "not enough evidence", "no clear difference")


# ---- independent claim checking --------------------------------------------------------------------------------------
def _stat(result: dict, path: str):
    """Look up a claim field ("focus.median", "group[ward=3B].pct", "ratio", ...) in a public result."""
    if path == "min_cases":
        return analysis.MIN_CASES
    if path == "min_group":
        return 5
    if path in ("ratio", "diff", "diff_pct_points"):
        y = {"time": "median", "rate": "rate", "count": "n"}[result["kind"]]
        f, b = (result.get("focus") or {}).get(y), (result.get("baseline") or {}).get(y)
        if not isinstance(f, (int, float)) or not isinstance(b, (int, float)):
            return None
        return {"ratio": f / b if b else None, "diff": f - b, "diff_pct_points": (f - b) * 100}[path]
    m = re.fullmatch(r"(total|focus|baseline)\.(\w+)", path)
    if m:
        return (result.get(m[1]) or {}).get(m[2])
    m = re.fullmatch(r"(group|baseline_group)\[(.*)\]\.(\w+)", path)
    if m:
        key = dict(kv.split("=", 1) for kv in m[2].split(",")) if m[2] else {}
        for g in result.get("groups" if m[1] == "group" else "baseline_groups", []):
            if g["key"] == key:
                return g.get(m[3])
    return "unverifiable"


def unsupported(final: dict, question: str) -> list[str]:
    results = {r["id"]: r for r in final["results"]}
    allowed = analysis.context_numbers(question, results)
    bad = []
    for s in final["sentences"]:
        claimed = []
        for c in s["claims"]:
            r = results.get(c["result_id"])
            v = _stat(r, c["field"]) if r else None
            if v == "unverifiable":  # e.g. a p-value: present in the critic's facts but not in the public result
                claimed.append(float(c["value"]))
                continue
            if not isinstance(v, (int, float)) or not analysis.number_ok(float(c["value"]), [float(v)]):
                bad.append(f"{s['text']!r}: claim {c['result_id']}.{c['field']}={c['value']} but data says {v}")
            claimed.append(float(c["value"]))
        for raw in analysis._NUM.findall(s["text"]):
            x = float(raw.replace(",", ""))
            if not analysis.number_ok(x, claimed) and not any(analysis._close(x, a) for a in allowed):
                bad.append(f"{s['text']!r}: number {raw} has no claim")
    return bad


# ---- scoring ---------------------------------------------------------------------------------------------------------
def _has_all(text_: str, groups: list[list[str]]) -> bool:
    t = text_.lower()
    return all(any(alt.lower() in t for alt in group) for group in groups)


def score(q: dict, final: dict) -> tuple[bool, str]:
    exp = q["expect"]
    text_ = final["answer"] or final["message"]
    first = final["sentences"][0]["text"] if final["sentences"] else ""
    if exp == "finding":
        if final["status"] != "answered":
            return False, f"status {final['status']}"
        if _has_all(first, q["keywords"]) and not any(h in first.lower() for h in HEDGES):
            return True, "top-1"
        return False, "planted pattern not the first finding"
    if exp == "not_a_trend":
        ok = "not enough evidence" in text_.lower() and not re.search(r"\b(was|were) longer\b", text_.lower())
        return ok, "said not enough evidence" if ok else "reported it as a trend"
    if exp == "answered":
        ok = final["status"] == "answered" and any(s["claims"] for s in final["sentences"])
        if ok and q.get("keywords") and not _has_all(first, q["keywords"]):
            return False, "answered, but the first sentence misses the expected subject"
        return ok, "answered" if ok else f"status {final['status']}"
    if exp == "refuse":
        ok = final["status"] == "refused"
        return ok, "refused" if ok else f"status {final['status']}"
    if exp == "clarify":
        ok = final["status"] == "clarify" and final["message"].count("?") == 1 and not final["results"]
        return ok, "clarified" if ok else f"status {final['status']}"
    if exp == "cannot_answer":
        ok = final["status"] == "no_data" and "doesn't show" in text_.lower()
        return ok, "said the data can't show it" if ok else f"status {final['status']}"
    raise ValueError(exp)


class Recorder:
    """Wraps the real LLM backend and records every prompt, to check that no identity is ever sent."""

    def __init__(self, inner):
        self.inner, self.name, self.prompts = inner, getattr(inner, "name", "llm"), []

    async def structured(self, schema, system, user, size="small"):
        self.prompts.append(system + "\n" + user)
        return await self.inner.structured(schema, system, user, size)


# ---- run ------------------------------------------------------------------------------------------------------------
async def setup(url: str):
    db.init_engine(url)
    async with db.engine().begin() as c:
        await c.execute(text("DROP SCHEMA IF EXISTS analytics CASCADE"))
        await c.run_sync(Base.metadata.drop_all)
        await c.run_sync(Base.metadata.create_all)
        await ensure_analytics(c)
    sm = db.sessionmaker()
    params = Params(tenant=TENANT, days=DAYS, seed=SEED, end_date=END)
    scenario = load_scenario("default")
    ds, tenant = await write(sm, params, scenario)
    async with sm() as s:
        director = await s.scalar(select(User).where(User.tenant_id == tenant.id))
    return sm, tenant, director, planted_truth(params, scenario, ds)


async def run_mode(mode: str, sm, tenant, director, questions: list[dict]) -> dict:
    recorder = None
    if mode == "groq":
        from app.config import get_settings
        get_settings().llm_mode = "auto"
        llm.set_backend(None)
        recorder = Recorder(llm.backend())
        llm.set_backend(recorder)
    else:
        llm.set_backend(None)
        os.environ["CAREFLOW_LLM_MODE"] = "off"
        from app.config import get_settings
        get_settings().llm_mode = "off"
    rows = []
    for q in questions:
        ctx = AskContext(sm, tenant.id, director.id, director.name)
        started = time.perf_counter()
        _, final = await ask(ctx, q["question"], today=TODAY)
        latency = (time.perf_counter() - started) * 1000
        ok, why = score(q, final)
        bad = unsupported(final, q["question"])
        leak = bool(IDENTITY.search(json.dumps(final)))
        rows.append({"id": q["id"], "question": q["question"], "expect": q["expect"], "finding": q.get("finding"), "ok": ok, "why": why,
                     "status": final["status"], "unsupported": bad, "leak": leak, "latency_ms": round(latency), "tokens": final["tokens"],
                     "mode": final["mode"], "answer": final["answer"] or final["message"]})
        print(f"[{mode}] {'PASS' if ok else 'FAIL'} {q['id']}: {why}" + (f"  UNSUPPORTED: {bad}" if bad else "") + ("  LEAK" if leak else ""))
    async with sm() as s:  # stored turns must not contain identity either
        stored = (await s.scalars(select(AskTurn).where(AskTurn.tenant_id == tenant.id))).all()
        stored_leaks = sum(bool(IDENTITY.search(json.dumps([t.question, t.plan, t.steps, t.results, t.answer, t.claims], default=str)))
                           for t in stored)
    prompt_leaks = sum(bool(IDENTITY.search(p)) for p in recorder.prompts) if recorder else 0
    llm.set_backend(None)
    return {"mode": mode, "rows": rows, "stored_leaks": stored_leaks, "prompt_leaks": prompt_leaks,
            "llm_used": sum(r["mode"] == "llm" for r in rows)}


def summarize(run: dict) -> dict:
    rows = run["rows"]

    def rate(kind):
        sel = [r for r in rows if r["expect"] == kind]
        return sum(r["ok"] for r in sel), len(sel)

    lat = sorted(r["latency_ms"] for r in rows)
    return {
        "finding": rate("finding"), "not_a_trend": rate("not_a_trend"), "refuse": rate("refuse"), "clarify": rate("clarify"),
        "cannot_answer": rate("cannot_answer"), "answered": rate("answered"), "all": (sum(r["ok"] for r in rows), len(rows)),
        "unsupported": sum(len(r["unsupported"]) for r in rows),
        "leaks": sum(r["leak"] for r in rows) + run["stored_leaks"] + run["prompt_leaks"],
        "latency_median": round(statistics.median(lat)) if lat else 0, "latency_p95": lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else 0,
        "tokens": round(statistics.fmean(r["tokens"] for r in rows)) if rows else 0,
    }


def markdown(runs: list[dict], truth: dict) -> str:
    def frac(t):
        return f"{t[0]}/{t[1]}"

    out = ["# Ask CareFlow eval results", "",
           f"Generated by `python evals/ask/run_ask_evals.py`. Synthetic history: scenario `{truth['scenario']}`, seed {truth['seed']}, "
           f"{truth['days']} days ({truth['start_date']} to {truth['end_date']}); questions asked as if today were {TODAY.isoformat()}. "
           "All data is synthetic.", "",
           "| Mode | Planted finding found (top-1) | Red herring not called a trend | Refusals | Clarify | \"Data can't answer\" | "
           "Controls answered | Unsupported claims | Identity leaks | Latency median / p95 | Tokens per question |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for run in runs:
        s = summarize(run)
        label = {"fallback": "Rule-based fallback (no LLM)", "groq": f"Groq ({run['llm_used']} of {len(run['rows'])} answers used the LLM)"}[run["mode"]]
        out.append(f"| {label} | {frac(s['finding'])} | {frac(s['not_a_trend'])} | {frac(s['refuse'])} | {frac(s['clarify'])} | "
                   f"{frac(s['cannot_answer'])} | {frac(s['answered'])} | {s['unsupported']} | {s['leaks']} | "
                   f"{s['latency_median']} ms / {s['latency_p95']} ms | {s['tokens']} |")
    if not any(r["mode"] == "groq" for r in runs):
        out += ["", "The Groq mode was not run (no `CAREFLOW_GROQ_API_KEY`), so every number above comes from the rule-based fallback."]
    out += ["", "## Planted patterns", "", "| Pattern | Expected finding | Discharges affected |", "|---|---|---|"]
    for p in truth["patterns"]:
        out.append(f"| `{p['id']}` | {p['expected_finding']} | {p['affected_discharges']} |")
    for run in runs:
        out += ["", f"## Per question ({run['mode']})", "", "| | Question | Expected | Result | Answer |", "|---|---|---|---|---|"]
        for r in run["rows"]:
            ans = r["answer"].replace("|", "/").replace("\n", " ")
            exp = r["expect"] + (f" `{r['finding']}`" if r["finding"] else "")
            out.append(f"| {'PASS' if r['ok'] else 'FAIL'} | {r['question']} | {exp} | {r['why']} | {ans[:300]}{'…' if len(ans) > 300 else ''} |")
    return "\n".join(out) + "\n"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["fallback", "groq", "both"], default="both")
    ap.add_argument("--markdown", default=str(HERE / "results.md"))
    ap.add_argument("--json")
    ap.add_argument("--only", help="run only questions whose id contains this text")
    ap.add_argument("--min-accuracy", type=float, default=0.8, help="fail if fallback top-1 accuracy on planted findings is lower")
    args = ap.parse_args()
    questions = yaml.safe_load((HERE / "questions.yaml").read_text(encoding="utf-8"))["questions"]
    if args.only:
        questions = [q for q in questions if args.only in q["id"]]
    sm, tenant, director, truth = await setup(os.environ["CAREFLOW_DATABASE_URL"])
    modes = ["fallback", "groq"] if args.mode == "both" else [args.mode]
    if "groq" in modes and not GROQ_KEY:
        print("CAREFLOW_GROQ_API_KEY is not set: skipping the Groq run.")
        modes = [m for m in modes if m != "groq"]
    runs = []
    try:
        for m in modes:
            runs.append(await run_mode(m, sm, tenant, director, questions))
    finally:
        await db.engine().dispose()
    for run in runs:
        s = summarize(run)
        print(f"\n[{run['mode']}] planted findings {s['finding'][0]}/{s['finding'][1]}, red herring {s['not_a_trend'][0]}/{s['not_a_trend'][1]}, "
              f"refusals {s['refuse'][0]}/{s['refuse'][1]}, clarify {s['clarify'][0]}/{s['clarify'][1]}, can't answer "
              f"{s['cannot_answer'][0]}/{s['cannot_answer'][1]}, all {s['all'][0]}/{s['all'][1]}; unsupported claims {s['unsupported']}, "
              f"identity leaks {s['leaks']}; latency median {s['latency_median']} ms, p95 {s['latency_p95']} ms; tokens {s['tokens']}")
    if args.markdown and not args.only:
        Path(args.markdown).write_text(markdown(runs, truth), encoding="utf-8")
        print(f"Wrote {args.markdown}")
    if args.json:
        Path(args.json).write_text(json.dumps(runs, indent=2, default=str), encoding="utf-8")
    failed = False
    for run in runs:
        s = summarize(run)
        if s["unsupported"] or s["leaks"]:
            failed = True
        if run["mode"] == "fallback" and s["finding"][1] and s["finding"][0] / s["finding"][1] < args.min_accuracy:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
