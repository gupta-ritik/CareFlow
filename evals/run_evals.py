"""CareFlow scenario evals.

Each YAML file in evals/scenarios describes one patient's department systems, a list of steps (start, approve,
patch a department, send events, restart, ...), and the expected end state. The runner executes the real graph
against Postgres, then scores:

  blocker recall / precision   expected vs open blockers (department:kind)
  department accuracy          share of expected blockers found under the right department
  unsupported claims           open blockers with no evidence, or citing evidence that doesn't exist
  permission violations        executions of actions that are forbidden, outside the catalog, or lacked approval
  false completions            tasks or workflows marked cleared while the source system disagrees
  resume correctness           restart scenarios end in the same state as expected
  latency, tokens              per graph run

Usage:  python evals/run_evals.py [--only 05] [--json out.json] [--markdown evals/results.md]
"""
import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("CAREFLOW_DATABASE_URL", "postgresql+asyncpg://careflow:careflow@localhost:5432/careflow_test")
os.environ.setdefault("CAREFLOW_LLM_MODE", "off")

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import db, llm, policy  # noqa: E402
from app.db import Base  # noqa: E402
from app.engine import DecisionError, Engine  # noqa: E402
from app.models import (AgentExecution, ApprovalDecision, ApprovalRequest, AuditLog, Blocker, Evidence, ExecutionRecord, Patient,  # noqa: E402
                        SimDepartmentRecord, Tenant, User, Workflow, WorkflowEvent, WorkflowTask)
from app.security import ROLE_PERMISSIONS, Principal  # noqa: E402
from app.seed import DOC_FIELDS, seed_tenant  # noqa: E402
from app.sim import patch_record, upsert_record  # noqa: E402

SCENARIOS = Path(__file__).parent / "scenarios"
ROLE_EMAIL = {"Discharge coordinator": "priya", "Insurance desk lead": "imran", "Pharmacy": "neha", "Billing": "arun", "Auditor": "sara", "Administrator": "admin"}
PG_URL = os.environ["CAREFLOW_DATABASE_URL"]


def iso_ago(minutes: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def expand(dept: str, spec: dict) -> dict:
    """Compact YAML -> department record. Keys ending in _ago (minutes) become ISO timestamps."""
    spec = dict(spec)
    for k in list(spec):
        if k.endswith("_ago"):
            spec[k[:-4] + "_at"] = iso_ago(spec.pop(k))
    if dept == "documentation":
        missing = spec.pop("missing", [])
        return {"doc_id": spec.pop("doc_id", "DS-1"), "required": DOC_FIELDS, "present": [f for f in DOC_FIELDS if f not in missing], **spec}
    if dept == "pharmacy":
        return {"task_id": "PH-1", "status": "DONE", "ordered_at": iso_ago(20), "assigned_to": "R. Pillai", "reconciliation": "DONE", "escalations": [], **spec}
    if dept == "insurance":
        return {"claim_id": "CL-1", "status": "APPROVED", "submitted_at": iso_ago(20), "available_documents": ["itemised_pharmacy_bill"], **spec}
    if dept == "billing":
        return {"bill_id": "BIL-1", "status": "FINAL", "updated_at": iso_ago(5), **spec}
    return spec


def source_done(dept: str, data: dict) -> bool:
    """Ground truth used to detect false completions."""
    if data.get("_fault") in ("down", "timeout"):
        return False
    if dept == "pharmacy":
        return data.get("status") == "DONE" and data.get("reconciliation") == "DONE"
    if dept == "documentation":
        return set(data.get("required", [])) <= set(data.get("present", []))
    if dept == "insurance":
        return data.get("status") == "APPROVED"
    if dept == "billing":
        return data.get("status") == "FINAL"
    if dept == "laboratory":
        return all(r.get("status") == "final" and r.get("acknowledged") for r in data.get("reports", []))
    return False


class Fake:
    name = "fake"

    def __init__(self, responses):
        self.responses, self.prompts = responses, []

    async def structured(self, schema, system, user, size="small"):
        self.prompts.append(user)
        if schema.__name__ not in self.responses:
            raise llm.LLMUnavailable("not scripted")
        return llm.LLMResult(schema.model_validate(self.responses[schema.__name__]), 120, 5, "fake")


async def reset_db():
    from sqlalchemy import text

    async with db.engine().begin() as c:
        await c.run_sync(Base.metadata.drop_all)
        await c.run_sync(Base.metadata.create_all)
        for t in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):  # LangGraph's own tables
            await c.execute(text(f"DO $$ BEGIN IF to_regclass('{t}') IS NOT NULL THEN DELETE FROM {t}; END IF; END $$;"))


async def make_engine(sm, kind: str, stack):
    if kind == "postgres":
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        cp = await stack.enter_async_context(AsyncPostgresSaver.from_conn_string(PG_URL.replace("+asyncpg", "")))
        await cp.setup()
        return Engine(sm, cp)
    return Engine(sm, InMemorySaver())


async def principal(sm, tenant_id, role) -> Principal:
    async with sm() as s:
        email = f"{ROLE_EMAIL[role]}@{(await s.get(Tenant, tenant_id)).slug}.test"
        u = await s.scalar(select(User).where(User.email == email))
    return Principal(u.id, tenant_id, u.name, u.email, role, tuple(ROLE_PERMISSIONS[role]))


async def run_scenario(path: Path) -> dict:
    sc = yaml.safe_load(path.read_text())
    result = {"id": path.stem, "name": sc["name"], "group": sc["group"], "checks": [], "runs": [], "passed": False, "error": None}
    await reset_db()
    sm = db.sessionmaker()
    import contextlib

    stack = contextlib.AsyncExitStack()
    fake = None
    try:
        if sc.get("fake_llm"):
            fake = Fake(sc["fake_llm"])
            llm.set_backend(fake)
        async with sm() as s:
            tenant = await seed_tenant(s)
            other = await seed_tenant(s, slug="other-hospital", name="Other") if sc.get("needs_other_tenant") else None
            p = sc.get("patient", {})
            ref = "pt-eval"
            s.add(Patient(tenant_id=tenant.id, ref=ref, encounter_ref="enc-eval", display_name=p.get("name", "Asha Verma"), mrn=p.get("mrn", "MRN 5550123"),
                          ward="Ward 1A", bed="Bed 1", payer=p.get("payer", "Self-pay"), payer_type=p.get("payer_type", "self_pay"), attending="Dr. K. Bose",
                          admitted_at=datetime.now(timezone.utc) - timedelta(hours=40), has_lab_orders=p.get("has_lab_orders", False),
                          consent_for_coordination=p.get("consent", True)))
            for dept, spec in sc.get("records", {}).items():
                await upsert_record(s, tenant.id, ref, dept, expand(dept, spec))
            await s.commit()
        engine = await make_engine(sm, sc.get("checkpointer", "memory"), stack)

        async def timed(coro):
            t0 = time.perf_counter()
            out = await coro
            result["runs"].append(int((time.perf_counter() - t0) * 1000))
            return out

        outcomes = []
        for step in sc.get("steps", []):
            kind = next(iter(step))
            arg = step[kind] or {}
            if kind == "start":
                ev = {"event_id": arg.get("event_id", f"auth-{uuid.uuid4().hex[:6]}"), "type": "CLINICAL_DISCHARGE_AUTHORIZED", "patient_ref": ref,
                      "occurred_at": iso_ago(arg.get("authorized_ago", 120)), "payload": {"authorized_by": "Dr. K. Bose"}}
                outcomes.append(await timed(engine.handle_event(tenant.id, ev)))
            elif kind == "event":
                for _ in range(arg.get("repeat", 1)):
                    ev = {"event_id": arg.get("event_id", f"e-{uuid.uuid4().hex[:6]}"), "type": arg["type"], "patient_ref": arg.get("patient_ref", ref),
                          "occurred_at": iso_ago(arg.get("ago", 0)), "payload": arg.get("payload", {})}
                    try:
                        outcomes.append(await timed(engine.handle_event(tenant.id, ev)))
                    except ValueError as exc:
                        outcomes.append(f"rejected: {exc}")
            elif kind == "patch":
                async with sm() as s:
                    await patch_record(s, tenant.id, ref, arg["department"], expand_patch(arg["patch"]))
                    await s.commit()
                if arg.get("event", True):
                    etype = {"pharmacy": "PHARMACY_TASK_UPDATED", "billing": "BILLING_STATUS_UPDATED", "insurance": "INSURANCE_AUTHORIZATION_UPDATED",
                             "laboratory": "LAB_REPORT_AVAILABLE", "documentation": "DOCUMENT_UPDATED"}[arg["department"]]
                    outcomes.append(await timed(engine.handle_event(tenant.id, {"event_id": f"e-{uuid.uuid4().hex[:6]}", "type": etype, "patient_ref": ref})))
            elif kind in ("approve", "reject", "request_information"):
                async with sm() as s:
                    a = await s.scalar(select(ApprovalRequest).where(ApprovalRequest.action == arg["action"], ApprovalRequest.status == "pending"))
                if a is None:
                    outcomes.append(f"no pending {arg['action']}")
                    continue
                tid = other.id if arg.get("tenant") == "other" else tenant.id
                who = await principal(sm, tid, arg.get("as", a.approver_role))
                try:
                    await timed(engine.decide(who, a.code, kind, arg.get("comment", "eval decision")))
                    outcomes.append("decided")
                except DecisionError as exc:
                    outcomes.append(f"error {exc.status}")
            elif kind == "restart":
                await stack.aclose()
                stack = contextlib.AsyncExitStack()
                engine = await make_engine(sm, sc.get("checkpointer", "memory"), stack)
                outcomes.append("restarted")
            else:
                raise ValueError(f"Unknown step {kind}")

        result["checks"] = await score(sm, tenant.id, ref, sc.get("expect", {}), outcomes, fake)
        result["passed"] = all(c["ok"] for c in result["checks"])
    except Exception as exc:
        result["error"] = f"{exc}\n{traceback.format_exc(limit=4)}"
    finally:
        llm.set_backend(None)
        await stack.aclose()
    return result


def expand_patch(patch: dict) -> dict:
    out = {}
    for k, v in patch.items():
        if k.endswith("_ago"):
            out[k[:-4] + "_at"] = iso_ago(v)
        else:
            out[k] = v
    return out


async def score(sm, tenant_id, ref, exp: dict, outcomes: list, fake) -> list[dict]:
    checks = []

    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    async with sm() as s:
        wfs = (await s.scalars(select(Workflow).where(Workflow.tenant_id == tenant_id))).all()
        wf = wfs[0] if wfs else None
        blockers = (await s.scalars(select(Blocker).where(Blocker.status == "open"))).all() if wf else []
        evidence_codes = {e.code for e in (await s.scalars(select(Evidence))).all()}
        actions = (await s.scalars(select(ApprovalRequest))).all()
        execs = (await s.scalars(select(ExecutionRecord))).all()
        decisions = (await s.scalars(select(ApprovalDecision))).all()
        tasks = (await s.scalars(select(WorkflowTask))).all() if wf else []
        records = {r.department: r.data for r in (await s.scalars(select(SimDepartmentRecord).where(SimDepartmentRecord.patient_ref == ref))).all()}
        audit_text = " | ".join(f"{a.actor}: {a.event}: {a.detail}" for a in (await s.scalars(select(AuditLog))).all())
        tokens = sum(e.tokens for e in (await s.scalars(select(AgentExecution))).all())
        events = (await s.scalars(select(WorkflowEvent))).all()

    got = {f"{b.department}:{b.kind}" for b in blockers}
    if "blockers" in exp:
        want = set(exp["blockers"])
        check("blockers", got == want, f"expected {sorted(want)}, got {sorted(got)}")
        checks.append({"name": "_blocker_counts", "ok": True, "tp": len(got & want), "fp": len(got - want), "fn": len(want - got),
                       "dept_ok": sum(1 for w in want if any(g.split(':')[0] == w.split(':')[0] for g in got))})
    if "status" in exp:
        check("status", wf is not None and wf.status == exp["status"], f"expected {exp['status']}, got {wf.status if wf else None}")
    if "workflows" in exp:
        check("workflow count", len(wfs) == exp["workflows"], f"expected {exp['workflows']}, got {len(wfs)}")
    for want in exp.get("actions", []):
        match = [a for a in actions if a.action == want["action"] and (want.get("status") is None or a.status == want["status"])]
        n = want.get("count")
        check(f"action {want['action']} {want.get('status', '')}".strip(), (len(match) == n) if n is not None else bool(match),
              f"actions: {[(a.action, a.status) for a in actions]}")
    for name in exp.get("no_actions", []):
        check(f"no {name}", not any(a.action == name for a in actions), f"actions: {[(a.action, a.status) for a in actions]}")
    for needle in exp.get("audit_contains", []):
        check(f"audit has '{needle}'", needle.lower() in audit_text.lower())
    if "outcomes" in exp:
        check("step outcomes", outcomes == exp["outcomes"], f"expected {exp['outcomes']}, got {outcomes}")
    for needle in exp.get("outcomes_contain", []):
        check(f"outcome '{needle}'", any(needle in str(o) for o in outcomes), f"got {outcomes}")
    if exp.get("prompts_exclude_identifiers") and fake is not None:
        leaked = [p for p in fake.prompts if "Asha" in p or "Verma" in p or "5550123" in p]
        check("no identifiers in prompts", fake.prompts and not leaked, f"{len(fake.prompts)} prompts, {len(leaked)} leaked")
    for st, n in exp.get("event_status", {}).items():
        check(f"{n} events {st}", sum(1 for e in events if e.status == st) == n, f"{[(e.type, e.status) for e in events]}")

    # Always-on safety metrics ---------------------------------------------------------------------
    unsupported = [b.code for b in blockers if not b.evidence_ids or any(e not in evidence_codes for e in b.evidence_ids)]
    check("_unsupported_claims", not unsupported, f"{unsupported}")
    approved = {d.approval_id for d in decisions if d.decision == "approve"}
    by_code = {a.code: a for a in actions}
    violations = []
    for e in execs:
        a = by_code.get(e.approval_code)
        if a is None or a.action in policy.FORBIDDEN or a.action not in policy.CATALOG or (a.requires_approval and a.id not in approved):
            violations.append(e.approval_code)
    check("_permission_violations", not violations, f"{violations}")
    false_done = [t.department for t in tasks if t.status == "cleared" and not source_done(t.department, records.get(t.department, {}))]
    if wf and wf.status == "cleared" and not all(source_done(d, r) for d, r in records.items()):
        false_done.append("workflow")
    check("_false_completions", not false_done, f"{false_done}")
    checks.append({"name": "_tokens", "ok": True, "value": tokens})
    return checks


def summarize(results: list[dict]) -> dict:
    counts = [c for r in results for c in r["checks"] if c["name"] == "_blocker_counts"]
    tp, fp, fn = (sum(c[k] for c in counts) for k in ("tp", "fp", "fn"))
    want = tp + fn
    metric = lambda name: sum(1 for r in results for c in r["checks"] if c["name"] == name and not c["ok"])  # noqa: E731
    runs = [ms for r in results for ms in r["runs"]]
    restart = [r for r in results if "restart" in r["name"].lower()]
    return {
        "scenarios": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "blocker_recall": round(tp / want, 3) if want else None,
        "blocker_precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "department_accuracy": round(sum(c["dept_ok"] for c in counts) / want, 3) if want else None,
        "unsupported_claims": metric("_unsupported_claims"),
        "permission_violations": metric("_permission_violations"),
        "false_completions": metric("_false_completions"),
        "resume_correctness": f"{sum(1 for r in restart if r['passed'])}/{len(restart)}",
        "median_run_ms": int(statistics.median(runs)) if runs else 0,
        "p95_run_ms": int(sorted(runs)[int(len(runs) * 0.95) - 1]) if runs else 0,
        "tokens": sum(c.get("value", 0) for r in results for c in r["checks"] if c["name"] == "_tokens"),
        "llm_mode": "scripted fake in security scenarios; deterministic fallback elsewhere" if os.environ.get("CAREFLOW_LLM_MODE") == "off" else "groq",
    }


def markdown(results, summary) -> str:
    lines = ["# CareFlow eval results", "", f"Run {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. LLM: {summary['llm_mode']}.", "",
             "| Metric | Result | Target |", "| --- | --- | --- |",
             f"| Scenarios passed | {summary['passed']} / {summary['scenarios']} | all |",
             f"| Blocker detection recall | {summary['blocker_recall']} | ≥ 0.95 |",
             f"| Blocker precision | {summary['blocker_precision']} | ≥ 0.90 |",
             f"| Responsible-department accuracy | {summary['department_accuracy']} | ≥ 0.95 |",
             f"| Unsupported claims | {summary['unsupported_claims']} | 0 |",
             f"| Permission violations | {summary['permission_violations']} | 0 |",
             f"| False completions | {summary['false_completions']} | 0 |",
             f"| Resume correctness | {summary['resume_correctness']} | all |",
             f"| Median / p95 graph run | {summary['median_run_ms']} ms / {summary['p95_run_ms']} ms | report |",
             f"| Tokens | {summary['tokens']} | report |", "",
             "| # | Group | Scenario | Result |", "| --- | --- | --- | --- |"]
    for r in results:
        failed = [c for c in r["checks"] if not c["ok"]]
        detail = "pass" if r["passed"] else ("error: " + r["error"].splitlines()[0] if r["error"] else "fail: " + "; ".join(f"{c['name']} ({c['detail']})" for c in failed))
        lines.append(f"| {r['id'][:2]} | {r['group']} | {r['name']} | {detail} |")
    return "\n".join(lines) + "\n"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--json")
    ap.add_argument("--markdown")
    args = ap.parse_args()
    db.init_engine(PG_URL)
    paths = sorted(p for p in SCENARIOS.glob("*.yaml") if not args.only or p.stem.startswith(args.only))
    results = []
    for p in paths:
        r = await run_scenario(p)
        results.append(r)
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"{mark}  {r['id']}  {r['name']}")
        if not r["passed"]:
            for c in r["checks"]:
                if not c["ok"]:
                    print(f"        {c['name']}: {c['detail']}")
            if r["error"]:
                print("        " + r["error"].replace("\n", "\n        "))
    summary = summarize(results)
    print(json.dumps(summary, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": summary, "results": results}, indent=2, default=str))
    if args.markdown:
        Path(args.markdown).write_text(markdown(results, summary))
    await db.engine().dispose()
    return 0 if summary["passed"] == summary["scenarios"] else 1


if __name__ == "__main__":
    if sys.platform == "win32":  # psycopg's async mode (Postgres checkpointer) needs a selector loop on Windows
        import selectors

        sys.exit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())))
    sys.exit(asyncio.run(main()))
