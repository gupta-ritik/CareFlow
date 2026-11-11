import statistics
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import rag
from ..config import get_settings
from ..db import get_session
from ..engine import DecisionError, Engine
from ..events import make_event, publish
from ..models import AgentExecution, ApprovalRequest, AuditLog, Blocker, Evidence, Patient, TaskDependency, Tenant, User, Workflow, WorkflowTask
from ..schemas import (ActionOut, AgentExecutionOut, AuditOut, BlockerOut, DecisionIn, EventIn, EvidenceOut, LoginIn, PatientContextOut,
                       SimPatchIn, TaskOut, TokenOut, WorkflowOut)
from ..security import AUDITOR, ROLE_PERMISSIONS, Principal, create_token, current_principal, require, verify_password
from ..sim import DEPARTMENTS, DepartmentError, patch_record

router = APIRouter(prefix="/api")

# ----- rate limiting ------------------------------------------------------------------------------
_local_hits: dict[str, list[float]] = defaultdict(list)


async def rate_limit(request: Request, key: str, limit: int, window: int = 60) -> None:
    redis = getattr(request.app.state, "redis", None)
    bucket = f"careflow:rl:{key}:{int(time.time() // window)}"
    if redis is not None:
        n = await redis.incr(bucket)
        if n == 1:
            await redis.expire(bucket, window)
    else:
        now = time.time()
        _local_hits[key] = [t for t in _local_hits[key] if now - t < window] + [now]
        n = len(_local_hits[key])
    if n > limit:
        raise HTTPException(429, f"Too many requests. Try again in under {window} seconds.")


def engine_of(request: Request) -> Engine:
    return request.app.state.engine


# ----- auth --------------------------------------------------------------------------------------
@router.post("/auth/login", response_model=TokenOut, response_model_by_alias=True)
async def login(body: LoginIn, request: Request, session: AsyncSession = Depends(get_session)):
    await rate_limit(request, f"login:{request.client.host if request.client else 'x'}", 10)
    settings = get_settings()
    user = await session.scalar(select(User).where(User.email == body.email.lower().strip()))
    now = datetime.now(timezone.utc)
    if user is not None and user.locked_until and user.locked_until > now:
        mins = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
        raise HTTPException(423, f"This account is locked after too many failed sign-ins. Try again in {mins} minute(s) or ask an administrator.")
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        if user is not None and user.is_active:
            user.failed_logins = (user.failed_logins or 0) + 1
            if user.failed_logins >= settings.login_max_failures:
                user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
                user.failed_logins = 0
            await session.commit()
        raise HTTPException(401, "Email or password is incorrect.")
    user.failed_logins, user.locked_until, user.last_login_at = 0, None, now
    await session.commit()
    user = await session.scalar(select(User).where(User.id == user.id))
    token, ttl = create_token(user)
    return TokenOut(access_token=token, expires_in=ttl, user={"name": user.name, "email": user.email, "role": user.role.name,
                                                             "permissions": ROLE_PERMISSIONS.get(user.role.name, [])})


@router.get("/auth/me")
async def me(p: Principal = Depends(current_principal)):
    return {"name": p.name, "email": p.email, "role": p.role, "permissions": list(p.permissions)}


@router.post("/auth/logout", status_code=204)
async def logout(_: Principal = Depends(current_principal)):
    # Stateless JWT: the client drops the token. With OIDC, the provider's end-session endpoint handles this.
    return Response(status_code=204)


# ----- serializers -------------------------------------------------------------------------------
def _mask(p: Patient, principal: Principal) -> PatientContextOut:
    name, mrn = p.display_name, p.mrn
    if principal.role == AUDITOR:  # auditors review decisions, not people
        name = " ".join(f"{part[0]}." for part in p.display_name.split())
        mrn = f"MRN ••••{p.mrn[-4:]}"
    return PatientContextOut(patient_id=p.ref, display_name=name, mrn=mrn, ward=p.ward, bed=p.bed, payer=p.payer, attending=p.attending, admitted_at=p.admitted_at)


async def workflows_out(s: AsyncSession, wfs: list[Workflow], principal: Principal) -> list[WorkflowOut]:
    """Serialize workflows with tasks, dependencies, open blockers and cited evidence: 5 queries for any number of
    workflows (not 5 per workflow, which made the discharge board slow against a remote database)."""
    if not wfs:
        return []
    ids = [wf.id for wf in wfs]
    tasks: dict = defaultdict(list)
    for t in (await s.scalars(select(WorkflowTask).where(WorkflowTask.workflow_id.in_(ids)).order_by(WorkflowTask.position))).all():
        tasks[t.workflow_id].append(t)
    deps: dict = defaultdict(lambda: defaultdict(list))
    for d in (await s.scalars(select(TaskDependency).where(TaskDependency.workflow_id.in_(ids)))).all():
        deps[d.workflow_id][d.department].append(d.depends_on)
    blockers: dict = defaultdict(list)
    for b in (await s.scalars(select(Blocker).where(Blocker.workflow_id.in_(ids), Blocker.status == "open").order_by(Blocker.since))).all():
        blockers[b.workflow_id].append(b)
    actions: dict = defaultdict(list)
    for a in (await s.scalars(select(ApprovalRequest).where(ApprovalRequest.workflow_id.in_(ids)))).all():
        actions[a.workflow_id].append(a)
    codes = {c for bs in blockers.values() for b in bs for c in b.evidence_ids} | {c for acts in actions.values() for a in acts for c in a.evidence_ids}
    evidence: dict = defaultdict(list)
    if codes:
        for e in (await s.scalars(select(Evidence).where(Evidence.workflow_id.in_(ids), Evidence.code.in_(codes)))).all():
            evidence[e.workflow_id].append(e)
    out = []
    for wf in wfs:
        wf_codes = {c for b in blockers[wf.id] for c in b.evidence_ids} | {c for a in actions[wf.id] for c in a.evidence_ids}
        out.append(WorkflowOut(
            id=wf.code, patient=_mask(wf.patient, principal), authorized_at=wf.authorized_at, authorized_by=wf.authorized_by, status=wf.status, summary=wf.summary,
            tasks=[TaskOut(id=f"{wf.code}-{t.department}", department=t.department, title=t.title, status=t.status, owner=t.owner, updated_at=t.source_updated_at,
                           depends_on=deps[wf.id].get(t.department, [])) for t in tasks[wf.id]],
            blockers=[BlockerOut(id=b.code, department=b.department, kind=b.kind, summary=b.summary, since=b.since, evidence_ids=b.evidence_ids, waiting_on=b.waiting_on)
                      for b in blockers[wf.id]],
            evidence=[EvidenceOut(id=e.code, source=e.source, query=e.query, observed=e.observed, observed_at=e.observed_at, untrusted=e.untrusted)
                      for e in evidence[wf.id] if e.code in wf_codes],
        ))
    return out


async def workflow_out(s: AsyncSession, wf: Workflow, principal: Principal) -> WorkflowOut:
    return (await workflows_out(s, [wf], principal))[0]


def action_out(a: ApprovalRequest, wf_code: str) -> ActionOut:
    return ActionOut(id=a.code, workflow_id=wf_code, blocker_id=a.blocker_code, action=a.action, description=a.description, expected_impact=a.expected_impact,
                     risk=a.risk, requires_approval=a.requires_approval, approver_role=a.approver_role, evidence_ids=a.evidence_ids, sop_refs=a.sop_refs,
                     status=a.status, created_at=a.created_at, decided_by=a.decided_by, decided_at=a.decided_at, comment=a.comment)


# ----- workflows ---------------------------------------------------------------------------------
@router.get("/workflows", response_model=list[WorkflowOut], response_model_by_alias=True)
async def list_workflows(response: Response, status: str | None = None, limit: int = Query(100, le=500), offset: int = 0,
                         p: Principal = Depends(require("workflow:read")), s: AsyncSession = Depends(get_session)):
    q = select(Workflow).where(Workflow.tenant_id == p.tenant_id, Workflow.deleted_at.is_(None))
    q = q.where(Workflow.status == status) if status else q.where(Workflow.status != "discharged")
    total = await s.scalar(select(func.count()).select_from(q.subquery()))
    rows = (await s.scalars(q.order_by(Workflow.authorized_at).limit(limit).offset(offset))).all()
    response.headers["X-Total-Count"] = str(total)
    return await workflows_out(s, list(rows), p)


async def _wf(s: AsyncSession, p: Principal, code: str) -> Workflow:
    wf = await s.scalar(select(Workflow).where(Workflow.tenant_id == p.tenant_id, Workflow.code == code, Workflow.deleted_at.is_(None)))
    if wf is None:  # same answer for "doesn't exist" and "other tenant's"
        raise HTTPException(404, f"Workflow {code} wasn't found.")
    return wf


@router.get("/workflows/{code}", response_model=WorkflowOut, response_model_by_alias=True)
async def get_workflow(code: str, p: Principal = Depends(require("workflow:read")), s: AsyncSession = Depends(get_session)):
    return await workflow_out(s, await _wf(s, p, code), p)


@router.post("/workflows/{code}/recheck", status_code=202)
async def recheck(code: str, request: Request, p: Principal = Depends(require("workflow:start")), s: AsyncSession = Depends(get_session)):
    """Re-read every department system now (also exposed as /resume for the spec's naming)."""
    await rate_limit(request, f"recheck:{p.user_id}", 30)
    wf = await _wf(s, p, code)
    eng = engine_of(request)
    if await eng.is_waiting(code):
        raise HTTPException(409, "This workflow is waiting for an approval decision. Decide it in Approvals first.")
    async with eng.lock(code):
        await eng.run(p.tenant_id, wf.code, None)
    return {"workflowId": code, "rechecked": True}


router.add_api_route("/workflows/{code}/resume", recheck, methods=["POST"], status_code=202)


# ----- approvals ---------------------------------------------------------------------------------
@router.get("/approvals", response_model=list[ActionOut], response_model_by_alias=True)
async def list_approvals(status: str | None = None, p: Principal = Depends(require("workflow:read")), s: AsyncSession = Depends(get_session)):
    q = (select(ApprovalRequest, Workflow.code).join(Workflow, Workflow.id == ApprovalRequest.workflow_id)
         .where(Workflow.tenant_id == p.tenant_id, Workflow.deleted_at.is_(None)).order_by(ApprovalRequest.created_at.desc()))
    if status:
        q = q.where(ApprovalRequest.status == status)
    return [action_out(a, code) for a, code in (await s.execute(q)).all()]


async def _decide(request: Request, code: str, decision: str, body: DecisionIn, p: Principal):
    await rate_limit(request, f"decide:{p.user_id}", 30)
    try:
        a = await engine_of(request).decide(p, code, decision, body.comment)
    except DecisionError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    async with request.app.state.sessionmaker() as s:
        wf = await s.get(Workflow, a.workflow_id)
        return action_out(a, wf.code).model_dump(by_alias=True)


@router.post("/approvals/{code}/approve")
async def approve(code: str, request: Request, body: DecisionIn = DecisionIn(), p: Principal = Depends(require("approval:decide"))):
    return await _decide(request, code, "approve", body, p)


@router.post("/approvals/{code}/reject")
async def reject(code: str, request: Request, body: DecisionIn, p: Principal = Depends(require("approval:decide"))):
    return await _decide(request, code, "reject", body, p)


@router.post("/approvals/{code}/request-information")
async def request_information(code: str, request: Request, body: DecisionIn, p: Principal = Depends(require("approval:decide"))):
    return await _decide(request, code, "request_information", body, p)


# ----- audit and agents --------------------------------------------------------------------------
@router.get("/audit", response_model=list[AuditOut], response_model_by_alias=True)
async def list_audit(workflow_id: str | None = Query(None, alias="workflowId"), limit: int = Query(300, le=2000),
                     p: Principal = Depends(require("workflow:read")), s: AsyncSession = Depends(get_session)):
    q = select(AuditLog).where(AuditLog.tenant_id == p.tenant_id)
    if workflow_id:
        q = q.where(AuditLog.workflow_code == workflow_id)
    rows = (await s.scalars(q.order_by(AuditLog.at.desc()).limit(limit))).all()
    return [AuditOut(id=r.code, workflow_id=r.workflow_code, at=r.at, actor_kind=r.actor_kind, actor=r.actor, event=r.event, detail=r.detail,
                     evidence_ids=r.evidence_ids, latency_ms=r.latency_ms, tokens=r.tokens) for r in rows]


@router.get("/agents/executions", response_model=list[AgentExecutionOut], response_model_by_alias=True)
async def list_executions(workflow_id: str | None = Query(None, alias="workflowId"), limit: int = Query(300, le=2000),
                          p: Principal = Depends(require("audit:read")), s: AsyncSession = Depends(get_session)):
    q = select(AgentExecution).where(AgentExecution.tenant_id == p.tenant_id)
    if workflow_id:
        q = q.where(AgentExecution.workflow_code == workflow_id)
    rows = (await s.scalars(q.order_by(AgentExecution.started_at.desc()).limit(limit))).all()
    return [AgentExecutionOut(id=str(r.id), workflow_id=r.workflow_code, agent=r.agent, mode=r.mode, status=r.status, input_summary=r.input_summary,
                              output_summary=r.output_summary, latency_ms=r.latency_ms, tokens=r.tokens, retries=r.retries, error=r.error, started_at=r.started_at) for r in rows]


@router.get("/agents")
async def list_agents(p: Principal = Depends(require("audit:read")), s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(
        select(AgentExecution.agent, AgentExecution.mode, func.count(), func.avg(AgentExecution.latency_ms), func.sum(AgentExecution.tokens),
               func.sum(AgentExecution.retries), func.count().filter(AgentExecution.status == "error"))
        .where(AgentExecution.tenant_id == p.tenant_id).group_by(AgentExecution.agent, AgentExecution.mode)
    )).all()
    out: dict[str, dict] = {}
    for agent, mode, n, lat, tok, retries, errors in rows:
        a = out.setdefault(agent, {"agent": agent, "calls": 0, "llmCalls": 0, "fallbackCalls": 0, "avgLatencyMs": 0.0, "tokens": 0, "retries": 0, "errors": 0, "_lat": 0.0})
        a["calls"] += n
        a["llmCalls"] += n if mode == "llm" else 0
        a["fallbackCalls"] += n if mode == "fallback" else 0
        a["_lat"] += float(lat or 0) * n
        a["tokens"] += int(tok or 0)
        a["retries"] += int(retries or 0)
        a["errors"] += int(errors or 0)
    for a in out.values():
        a["avgLatencyMs"] = round(a.pop("_lat") / a["calls"], 1) if a["calls"] else 0
    return sorted(out.values(), key=lambda a: -a["calls"])


# ----- analytics ---------------------------------------------------------------------------------
@router.get("/analytics/dashboard")
async def dashboard(p: Principal = Depends(require("analytics:read")), s: AsyncSession = Depends(get_session)):
    now = datetime.now(timezone.utc)
    wfs = (await s.scalars(select(Workflow).where(Workflow.tenant_id == p.tenant_id, Workflow.deleted_at.is_(None)))).all()
    ids = [w.id for w in wfs]
    blockers = (await s.scalars(select(Blocker).where(Blocker.workflow_id.in_(ids)))).all() if ids else []
    actions = (await s.scalars(select(ApprovalRequest).where(ApprovalRequest.workflow_id.in_(ids)))).all() if ids else []
    tasks = (await s.scalars(select(WorkflowTask).where(WorkflowTask.workflow_id.in_(ids)))).all() if ids else []

    open_wf = [w for w in wfs if w.status in ("in_progress", "blocked", "awaiting_approval")]
    waits = [(now - w.authorized_at).total_seconds() / 60 for w in open_wf]
    cleared = [w for w in wfs if w.cleared_at]
    clear_times = [(w.cleared_at - w.authorized_at).total_seconds() / 60 for w in cleared]
    by_status: dict[str, int] = defaultdict(int)
    for w in wfs:
        by_status[w.status] += 1

    open_b = [b for b in blockers if b.status == "open"]
    dept_open: dict[str, int] = defaultdict(int)
    dept_age: dict[str, list[float]] = defaultdict(list)
    kind_counts: dict[str, int] = defaultdict(int)
    for b in open_b:
        dept_open[b.department] += 1
        dept_age[b.department].append((now - b.since).total_seconds() / 60)
        kind_counts[b.kind] += 1
    pending_tasks: dict[str, int] = defaultdict(int)
    for t in tasks:
        if t.status != "cleared":
            pending_tasks[t.department] += 1

    needs_human = {a.workflow_id for a in actions if a.requires_approval}
    decided = [a for a in actions if a.requires_approval and a.decided_at]
    return {
        "activeWorkflows": len(open_wf),
        "awaitingApproval": by_status.get("awaiting_approval", 0),
        "blocked": by_status.get("blocked", 0),
        "readyToDischarge": by_status.get("cleared", 0),
        "discharged": by_status.get("discharged", 0),
        "medianWaitMinutes": round(statistics.median(waits)) if waits else 0,
        "avgClearanceMinutes": round(statistics.mean(clear_times)) if clear_times else None,
        "completionRate": round(len(cleared) / len(wfs), 3) if wfs else 0,
        "humanInterventionRate": round(len(needs_human) / len(wfs), 3) if wfs else 0,
        "recoveryRate": round(sum(1 for b in blockers if b.status == "resolved") / len(blockers), 3) if blockers else None,
        "avgDecisionMinutes": round(statistics.mean((a.decided_at - a.created_at).total_seconds() / 60 for a in decided), 1) if decided else None,
        "statusCounts": dict(by_status),
        "departments": [{"department": d, "openBlockers": dept_open.get(d, 0), "pendingTasks": pending_tasks.get(d, 0),
                         "avgBlockedMinutes": round(statistics.mean(dept_age[d])) if dept_age.get(d) else 0} for d in DEPARTMENTS],
        "blockerKinds": [{"kind": k, "count": v} for k, v in sorted(kind_counts.items(), key=lambda x: -x[1])],
        "actions": {"total": len(actions), "automatic": sum(1 for a in actions if not a.requires_approval),
                    "needingApproval": sum(1 for a in actions if a.requires_approval), "verified": sum(1 for a in actions if a.status == "verified"),
                    "verificationFailed": sum(1 for a in actions if a.status == "verification_failed")},
        "clearanceTimes": [{"workflowId": w.code, "minutes": round(m)} for w, m in zip(cleared, clear_times)],
    }


# ----- events and simulated department systems ---------------------------------------------------
@router.post("/events", status_code=202)
async def post_event(body: EventIn, request: Request, p: Principal = Depends(require("integration:write")), s: AsyncSession = Depends(get_session)):
    """Integration endpoint for hospital systems (HIS, pharmacy, TPA). Queued on Redis Streams when available, else processed inline.

    Update a department's record first (payload.record merges into it), then CareFlow re-checks that department."""
    await rate_limit(request, f"events:{p.user_id}", 120)
    tenant = await s.get(Tenant, p.tenant_id)
    record = (body.payload or {}).get("record")
    dept = (body.payload or {}).get("department")
    if isinstance(record, dict) and dept:
        try:
            await patch_record(s, p.tenant_id, body.patient_ref, dept, record)
        except DepartmentError as exc:
            raise HTTPException(404, str(exc)) from exc
        await s.commit()
    event = make_event(body.type, tenant.slug, body.patient_ref, body.payload, body.event_id, body.correlation_id)
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        msg = await publish(redis, event)
        return {"queued": True, "eventId": event["event_id"], "streamId": msg}
    outcome = await engine_of(request).handle_event(p.tenant_id, event)
    return {"queued": False, "eventId": event["event_id"], "outcome": outcome}


# ----- knowledge ---------------------------------------------------------------------------------
@router.get("/knowledge/search")
async def knowledge_search(q: str, department: str | None = None, _: Principal = Depends(require("workflow:read"))):
    return [{"citation": c.citation, "title": c.title, "department": c.department, "text": c.text} for c in rag.index().search(q, department, k=5)]


@router.post("/knowledge", status_code=201)
async def knowledge_ingest(request: Request, p: Principal = Depends(require("user:manage"))):
    raw = (await request.body()).decode()
    if len(raw) > 200_000:
        raise HTTPException(413, "SOP is too large (200 KB maximum).")
    try:
        chunks = rag.ingest(raw)
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, f"SOP couldn't be read: {exc}") from exc
    return {"docId": chunks[0].doc_id if chunks else None, "sections": len(chunks)}


@router.get("/health")
async def health(request: Request):
    checks = {"database": "ok", "redis": "ok" if getattr(request.app.state, "redis", None) else "not configured"}
    try:
        async with request.app.state.sessionmaker() as s:
            await s.execute(select(1))
    except Exception as exc:  # pragma: no cover
        checks["database"] = f"error: {exc}"
    from .. import llm

    checks["llm"] = "groq" if llm.backend() else "deterministic fallback (no CAREFLOW_GROQ_API_KEY)"
    return checks


__all__ = ["router", "uuid"]
