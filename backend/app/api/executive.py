"""Executive overview for the dean / hospital director: census, flow, bottlenecks, approvals, beds, integrations, AI."""
import statistics
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..cache import Cache, executive_scope
from ..config import get_settings
from ..db import get_session
from ..departments import DEPARTMENTS
from ..models import AgentExecution, ApprovalRequest, AuditLog, Bed, Blocker, IntegrationMessage, Patient, Workflow, WorkflowTask
from ..security import Principal, require

router = APIRouter(prefix="/api")
OPEN = ("in_progress", "blocked", "awaiting_approval")
DELAY_KINDS = ("pharmacy_delay", "payer_delay", "billing_delay")


def _mins(a: datetime | None, b: datetime | None) -> float | None:
    return (b - a).total_seconds() / 60 if a and b else None


def _stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "avg": None, "median": None, "p90": None}
    v = sorted(values)
    return {"count": len(v), "avg": round(statistics.mean(v)), "median": round(statistics.median(v)), "p90": round(v[min(len(v) - 1, int(len(v) * 0.9))])}


@router.get("/executive/overview")
async def overview(request: Request, days: int = Query(30, ge=1, le=365), p: Principal = Depends(require("executive:read")),
                   s: AsyncSession = Depends(get_session)):
    # Several people keep this page open and it polls every 15 s; a short per-hospital cache spares the database.
    # Only these aggregates are cached, never workflow state, approvals or the audit trail.
    cache = Cache(getattr(request.app.state, "redis", None))
    return await cache.get_or_load(executive_scope(p.tenant_id), f"overview:{days}d", get_settings().executive_cache_seconds,
                                   lambda: build_overview(s, p.tenant_id, days))


async def build_overview(s: AsyncSession, tid, days: int) -> dict:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    today = datetime.combine(now.date(), time.min, timezone.utc)

    patients = (await s.scalars(select(Patient).where(Patient.tenant_id == tid, Patient.status == "admitted"))).all()
    wfs = (await s.scalars(select(Workflow).where(Workflow.tenant_id == tid, Workflow.deleted_at.is_(None),
                                                  (Workflow.status != "discharged") | (Workflow.discharged_at >= since - timedelta(days=14))))).all()
    open_wf = [w for w in wfs if w.status in OPEN]
    ready = [w for w in wfs if w.status == "cleared"]
    with_wf = {w.patient_id for w in wfs if w.status != "discharged"}
    ids = [w.id for w in wfs]
    blockers = (await s.scalars(select(Blocker).where(Blocker.workflow_id.in_(ids)))).all() if ids else []
    tasks = (await s.scalars(select(WorkflowTask).where(WorkflowTask.workflow_id.in_(ids)))).all() if ids else []
    actions = (await s.scalars(select(ApprovalRequest).where(ApprovalRequest.workflow_id.in_(ids)))).all() if ids else []
    wf_by_id = {w.id: w for w in wfs}

    # ---- flow ------------------------------------------------------------------------------------
    in_window = [w for w in wfs if w.cleared_at and w.cleared_at >= since]
    left = [w for w in wfs if w.discharged_at and w.discharged_at >= since]
    clearance = _stats([_mins(w.authorized_at, w.cleared_at) for w in in_window])
    auth_to_exit = _stats([_mins(w.authorized_at, w.discharged_at) for w in left])
    ready_to_exit = _stats([_mins(w.cleared_at, w.discharged_at) for w in left if w.cleared_at])

    trend = []
    for i in range(13, -1, -1):
        d0 = today - timedelta(days=i)
        d1 = d0 + timedelta(days=1)
        cl = [_mins(w.authorized_at, w.cleared_at) for w in wfs if w.cleared_at and d0 <= w.cleared_at < d1]
        trend.append({"date": d0.date().isoformat(),
                      "authorized": sum(1 for w in wfs if d0 <= w.authorized_at < d1),
                      "discharged": sum(1 for w in wfs if w.discharged_at and d0 <= w.discharged_at < d1),
                      "medianClearanceMinutes": round(statistics.median(cl)) if cl else None})

    # ---- departments -----------------------------------------------------------------------------
    open_b = [b for b in blockers if b.status == "open" and wf_by_id[b.workflow_id].status in (*OPEN, "cleared")]
    depts = []
    for d in DEPARTMENTS:
        cleared = [_mins(wf_by_id[t.workflow_id].authorized_at, t.cleared_at) for t in tasks
                   if t.department == d and t.cleared_at and t.cleared_at >= since]
        mine_open = [b for b in open_b if b.department == d]
        depts.append({
            "department": d,
            "openBlockers": len(mine_open),
            "pendingTasks": sum(1 for t in tasks if t.department == d and t.status != "cleared" and wf_by_id[t.workflow_id].status in OPEN),
            "avgMinutesToClear": round(statistics.mean(cleared)) if cleared else None,
            "slaBreaches": sum(1 for b in blockers if b.department == d and b.kind in DELAY_KINDS and b.since >= since),
            "oldestBlockerMinutes": round(max(_mins(b.since, now) for b in mine_open)) if mine_open else None,
        })
    bottleneck = max(depts, key=lambda x: (x["openBlockers"], x["oldestBlockerMinutes"] or 0)) if any(x["openBlockers"] for x in depts) else None

    # ---- approvals --------------------------------------------------------------------------------
    pending = [a for a in actions if a.status == "pending"]
    decided = [a for a in actions if a.requires_approval and a.decided_at and a.decided_at >= since]
    approvals = {
        "pending": len(pending),
        "byRole": dict(Counter(a.approver_role for a in pending)),
        "byRisk": dict(Counter(a.risk for a in pending)),
        "oldestPendingMinutes": round(max(_mins(a.created_at, now) for a in pending)) if pending else None,
        "avgDecisionMinutes": round(statistics.mean(_mins(a.created_at, a.decided_at) for a in decided), 1) if decided else None,
        "decided": len(decided),
        "rejected": sum(1 for a in decided if a.status == "rejected"),
        "automaticActions": sum(1 for a in actions if not a.requires_approval and a.created_at >= since),
    }

    # ---- longest waits ----------------------------------------------------------------------------
    waits = []
    for w in sorted(open_wf + ready, key=lambda w: w.authorized_at)[:10]:
        blocking = sorted({b.department for b in open_b if b.workflow_id == w.id})
        waits.append({"workflowId": w.code, "patient": w.patient.display_name, "mrn": w.patient.mrn, "ward": w.patient.ward, "bed": w.patient.bed,
                      "status": w.status, "waitMinutes": round(_mins(w.authorized_at, now)), "blockingDepartments": blocking,
                      "payerType": w.patient.payer_type})

    # ---- beds --------------------------------------------------------------------------------------
    bed_counts = Counter((await s.scalars(select(Bed.status).where(Bed.tenant_id == tid))).all())
    beds_total = sum(bed_counts.values())
    by_ward = defaultdict(lambda: Counter())
    for ward, st in (await s.execute(select(Bed.ward, Bed.status).where(Bed.tenant_id == tid))).all():
        by_ward[ward][st] += 1

    # ---- integrations ------------------------------------------------------------------------------
    day_ago = now - timedelta(hours=24)
    rows = (await s.execute(select(IntegrationMessage.system, IntegrationMessage.status, func.count(), func.max(IntegrationMessage.created_at))
                            .where(IntegrationMessage.tenant_id == tid, IntegrationMessage.created_at >= day_ago)
                            .group_by(IntegrationMessage.system, IntegrationMessage.status))).all()
    integ = {}
    for sys in ("his", "tpa", "pharmacy", "lis"):
        mine = [r for r in rows if r[0] == sys]
        integ[sys] = {"messages24h": sum(r[2] for r in mine), "errors24h": sum(r[2] for r in mine if r[1] in ("error", "rejected", "failed")),
                      "lastMessageAt": max((r[3] for r in mine), default=None)}

    # ---- AI agents ---------------------------------------------------------------------------------
    ai = (await s.execute(select(AgentExecution.mode, func.count(), func.sum(AgentExecution.tokens), func.avg(AgentExecution.latency_ms),
                                 func.count().filter(AgentExecution.status == "error"))
                          .where(AgentExecution.tenant_id == tid, AgentExecution.started_at >= since).group_by(AgentExecution.mode))).all()
    ai_out = {"runs": 0, "llm": 0, "fallback": 0, "deterministic": 0, "errors": 0, "tokens": 0}
    for mode, n, tok, _lat, err in ai:
        ai_out["runs"] += n
        ai_out[mode if mode in ("llm", "fallback", "deterministic") else "deterministic"] += n
        ai_out["tokens"] += int(tok or 0)
        ai_out["errors"] += int(err or 0)

    # ---- staff activity ----------------------------------------------------------------------------
    staff = (await s.execute(select(AuditLog.actor, func.count()).where(AuditLog.tenant_id == tid, AuditLog.actor_kind == "human", AuditLog.at >= since)
                             .group_by(AuditLog.actor).order_by(func.count().desc()).limit(8))).all()

    return {
        "generatedAt": now, "windowDays": days,
        "census": {
            "inHospital": len(patients),
            "awaitingAuthorization": sum(1 for pt in patients if pt.id not in with_wf),
            "inClearance": len(open_wf),
            "blocked": sum(1 for w in open_wf if w.status == "blocked"),
            "awaitingApproval": sum(1 for w in open_wf if w.status == "awaiting_approval"),
            "readyToDischarge": len(ready),
            "dischargedToday": sum(1 for w in wfs if w.discharged_at and w.discharged_at >= today),
            "dischargedInWindow": len(left),
        },
        "flow": {"clearanceMinutes": clearance, "authorizationToExitMinutes": auth_to_exit, "readyToExitMinutes": ready_to_exit,
                 "slaBreaches": sum(1 for b in blockers if b.kind in DELAY_KINDS and b.since >= since),
                 "blockersFound": sum(1 for b in blockers if b.since >= since),
                 "blockersResolved": sum(1 for b in blockers if b.status == "resolved" and b.resolved_at and b.resolved_at >= since)},
        "trend": trend,
        "departments": depts,
        "bottleneck": bottleneck["department"] if bottleneck else None,
        "approvals": approvals,
        "longestWaits": waits,
        "payerMix": dict(Counter(pt.payer_type for pt in patients)),
        "beds": {"total": beds_total, **{k: bed_counts.get(k, 0) for k in ("available", "occupied", "cleaning", "blocked")},
                 "occupancy": round(bed_counts.get("occupied", 0) / beds_total, 3) if beds_total else None,
                 "byWard": [{"ward": w, **dict(c), "total": sum(c.values())} for w, c in sorted(by_ward.items())]},
        "integrations": integ,
        "ai": ai_out,
        "staffActivity": [{"actor": a, "actions": n} for a, n in staff],
    }


@router.get("/executive/cache-stats")
async def cache_stats(request: Request, p: Principal = Depends(require("executive:read"))):
    """Dashboard cache hit/miss counters since Redis started (for benchmarking; contains no patient data)."""
    return {"ttlSeconds": get_settings().executive_cache_seconds, **await Cache(getattr(request.app.state, "redis", None)).stats()}
