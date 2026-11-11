"""Workflow engine: turns events and approval decisions into graph runs.

- One run at a time per workflow (Redis lock across processes, asyncio lock within one).
- Events are idempotent: the (tenant, event_id) unique key in workflow_events is the authority.
- A workflow paused for approval doesn't start a new run; department events are recorded as deferred and
  replayed as a re-check as soon as the approval run finishes.
"""
import asyncio
import contextlib
import uuid
from datetime import datetime, timezone

from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .agents.graph import Deps, build_graph, thread
from .config import Settings, get_settings
from .models import ApprovalDecision, ApprovalRequest, Patient, SimDepartmentRecord, Workflow, WorkflowEvent, utcnow
from .repo import audit, next_code, workflow_by_code
from .security import APPROVE_ANY, Principal
from .telemetry import tracer

DEPT_EVENTS = {
    "PHARMACY_TASK_UPDATED": "pharmacy",
    "BILLING_STATUS_UPDATED": "billing",
    "INSURANCE_AUTHORIZATION_UPDATED": "insurance",
    "LAB_REPORT_AVAILABLE": "laboratory",
    "LAB_ORDER_CREATED": "laboratory",
    "DOCUMENT_UPDATED": "documentation",
}
EVENT_TYPES = {"CLINICAL_DISCHARGE_AUTHORIZED", "ENCOUNTER_UPDATED", "DISCHARGE_TASK_COMPLETED", "PATIENT_DISCHARGED", "PATIENT_ADMITTED", *DEPT_EVENTS}
OPEN = ("in_progress", "blocked", "awaiting_approval", "cleared")


class DecisionError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Engine:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], checkpointer, settings: Settings | None = None, redis=None):
        self.sessionmaker = sessionmaker
        self.settings = settings or get_settings()
        self.graph = build_graph().compile(checkpointer=checkpointer)
        self.redis = redis
        self._locks: dict[str, asyncio.Lock] = {}

    @contextlib.asynccontextmanager
    async def lock(self, code: str):
        local = self._locks.setdefault(code, asyncio.Lock())
        async with local:
            if self.redis is None:
                yield
                return
            rlock = self.redis.lock(f"careflow:lock:{code}", timeout=300, blocking_timeout=120)
            async with rlock:
                yield

    def deps(self, tenant_id: uuid.UUID) -> Deps:
        return Deps(sessionmaker=self.sessionmaker, tenant_id=tenant_id, settings=self.settings)

    async def is_waiting(self, code: str) -> bool:
        st = await self.graph.aget_state(thread(code))
        return "approval" in (st.next or ())

    async def run(self, tenant_id: uuid.UUID, code: str, recheck: list[str] | None = None) -> dict:
        inp = {"workflow_code": code, "tenant_id": str(tenant_id), "recheck": recheck, "iteration": 0, "max_iterations": self.settings.max_iterations}
        with tracer.start_as_current_span("careflow.workflow.run", attributes={"workflow.code": code, "recheck": ",".join(recheck or ["all"])}):
            return await self.graph.ainvoke(inp, thread(code), context=self.deps(tenant_id))

    # --------------------------------------------------------------------------------------------
    async def handle_event(self, tenant_id: uuid.UUID, event: dict) -> str:
        """Returns processed | duplicate | ignored | deferred."""
        etype = event["type"]
        if etype not in EVENT_TYPES:
            raise ValueError(f"Unknown event type {etype}")
        occurred = event.get("occurred_at") or datetime.now(timezone.utc).isoformat()
        occurred_at = datetime.fromisoformat(occurred) if isinstance(occurred, str) else occurred

        async with self.sessionmaker() as s:
            row = WorkflowEvent(
                tenant_id=tenant_id, event_id=event["event_id"], type=etype, patient_ref=event["patient_ref"],
                correlation_id=event.get("correlation_id") or event["event_id"], payload=event.get("payload", {}), occurred_at=occurred_at,
            )
            s.add(row)
            try:
                await s.commit()
            except IntegrityError:
                await s.rollback()
                prior = await s.scalar(select(WorkflowEvent).where(WorkflowEvent.tenant_id == tenant_id, WorkflowEvent.event_id == event["event_id"]))
                # A retry of an event that failed mid-processing goes through; anything else is a duplicate.
                if prior is None or prior.status != "failed" or int(event.get("attempt", 1)) <= 1:
                    return "duplicate"
                row = prior
                row.status = "received"
                await s.commit()

            patient = await s.scalar(select(Patient).where(Patient.tenant_id == tenant_id, Patient.ref == event["patient_ref"]))
            if patient is None:
                row.status, row.error = "ignored", "unknown patient"
                await s.commit()
                return "ignored"
            wf = await s.scalar(select(Workflow).where(Workflow.patient_id == patient.id, Workflow.status.in_(OPEN), Workflow.deleted_at.is_(None)))

            if etype == "CLINICAL_DISCHARGE_AUTHORIZED":
                if wf is not None:
                    row.status, row.workflow_code = "duplicate", wf.code
                    await s.commit()
                    return "duplicate"
                if patient.status != "admitted":
                    row.status, row.error = "ignored", "patient already discharged"
                    await s.commit()
                    return "ignored"
                by = event.get("payload", {}).get("authorized_by", patient.attending)
                note = event.get("payload", {}).get("note")
                wf = Workflow(tenant_id=tenant_id, code=await next_code(s, "WF"), patient_id=patient.id, authorized_at=occurred_at, authorized_by=by)
                s.add(wf)
                await s.flush()
                # Discharge medicines are ordered at authorization; the pharmacy clock starts now if nobody set it.
                pharmacy = await s.scalar(select(SimDepartmentRecord).where(
                    SimDepartmentRecord.tenant_id == tenant_id, SimDepartmentRecord.patient_ref == patient.ref, SimDepartmentRecord.department == "pharmacy"))
                if pharmacy is not None and not pharmacy.data.get("ordered_at"):
                    pharmacy.data = {**pharmacy.data, "ordered_at": occurred_at.isoformat()}
                await audit(s, tenant_id, wf.code, "human", by, "Clinical discharge authorized",
                            note or "Recorded by the treating clinician; CareFlow starts administrative clearance")
                await audit(s, tenant_id, wf.code, "system", "Event consumer", f"{etype} received", f"Workflow {wf.code} created, idempotency key {event['event_id']}")
                row.workflow_code = wf.code
                await s.commit()
                code, recheck = wf.code, None
            elif wf is None:
                row.status, row.error = "ignored", "no open workflow"
                await s.commit()
                return "ignored"
            elif etype == "PATIENT_DISCHARGED":
                by = event.get("payload", {}).get("discharged_by", "Event consumer")
                wf.status, wf.discharged_at = "discharged", occurred_at
                patient.status = "discharged"
                row.status, row.workflow_code = "processed", wf.code
                await audit(s, tenant_id, wf.code, "human" if by != "Event consumer" else "system", by, "Patient discharged", "Workflow closed")
                await s.commit()
                return "processed"
            else:
                code = wf.code
                dept = DEPT_EVENTS.get(etype) or event.get("payload", {}).get("department")
                recheck = [dept] if dept else None
                row.workflow_code = code
                await s.commit()

        async with self.lock(code):
            if await self.is_waiting(code):
                async with self.sessionmaker() as s:
                    ev = await s.scalar(select(WorkflowEvent).where(WorkflowEvent.tenant_id == tenant_id, WorkflowEvent.event_id == event["event_id"]))
                    ev.status = "deferred"
                    await audit(s, tenant_id, code, "system", "Event consumer", f"{etype} deferred", "Workflow is waiting for approval; re-check runs right after the decision")
                    await s.commit()
                return "deferred"
            try:
                await self.run(tenant_id, code, recheck)
            except Exception as exc:
                await self._mark(tenant_id, event["event_id"], "failed", str(exc)[:500])
                raise
            await self._mark(tenant_id, event["event_id"], "processed")
        return "processed"

    async def _mark(self, tenant_id, event_id, status, error=None):
        async with self.sessionmaker() as s:
            ev = await s.scalar(select(WorkflowEvent).where(WorkflowEvent.tenant_id == tenant_id, WorkflowEvent.event_id == event_id))
            if ev:
                ev.status, ev.error = status, error
                ev.attempts += 1
                await s.commit()

    async def _replay_deferred(self, tenant_id: uuid.UUID, code: str, extra: list[str] | None = None):
        if await self.is_waiting(code):  # other approvals still open; replay once the last one is decided
            return
        async with self.sessionmaker() as s:
            rows = (await s.scalars(select(WorkflowEvent).where(WorkflowEvent.tenant_id == tenant_id, WorkflowEvent.workflow_code == code, WorkflowEvent.status == "deferred"))).all()
            depts = sorted({DEPT_EVENTS.get(r.type) or r.payload.get("department") for r in rows} - {None} | set(extra or []))
            for r in rows:
                r.status = "processed"
            await s.commit()
        if depts:
            await self.run(tenant_id, code, depts)

    # --------------------------------------------------------------------------------------------
    async def decide(self, principal: Principal, approval_code: str, decision: str, comment: str | None) -> ApprovalRequest:
        status_for = {"approve": "approved", "reject": "rejected", "request_information": "info_requested"}
        if decision not in status_for:
            raise DecisionError(422, f"Unknown decision {decision}")
        async with self.sessionmaker() as s:
            a = await s.scalar(
                select(ApprovalRequest).join(Workflow, Workflow.id == ApprovalRequest.workflow_id)
                .where(ApprovalRequest.code == approval_code, Workflow.tenant_id == principal.tenant_id)
            )
            if a is None:
                raise DecisionError(404, f"Approval {approval_code} doesn't exist.")
            wf = await s.get(Workflow, a.workflow_id)
            code = wf.code
        async with self.lock(code):
            async with self.sessionmaker() as s:
                a = await s.scalar(select(ApprovalRequest).where(ApprovalRequest.code == approval_code))
                if a.status != "pending":
                    raise DecisionError(409, f"Approval {approval_code} was already decided ({a.status}) by {a.decided_by or 'the system'}.")
                if principal.role != a.approver_role and principal.role not in APPROVE_ANY:
                    raise DecisionError(403, f"Only the {a.approver_role} role can decide this. You're signed in as {principal.role}.")
                if decision != "approve" and not (comment or "").strip():
                    raise DecisionError(422, "Add a comment explaining the rejection or what information is needed.")
                reviewer = f"{principal.name} ({principal.role})"
                a.status, a.decided_by, a.decided_at, a.comment = status_for[decision], reviewer, utcnow(), comment or None
                s.add(ApprovalDecision(approval_id=a.id, decision=decision, reviewer_id=principal.user_id, reviewer_name=principal.name, reviewer_role=principal.role, comment=comment))
                label = {"approve": "Approved", "reject": "Rejected", "request_information": "Asked for more information on"}[decision]
                await audit(s, principal.tenant_id, code, "human", reviewer, f"{label} {a.action}",
                            comment or ("Workflow resumes from its checkpoint" if decision == "approve" else "Workflow stays paused"), a.evidence_ids)
                await s.commit()
                dept = a.params.get("department")
            if await self.is_waiting(code):
                await self.graph.ainvoke(Command(resume={"code": approval_code, "decision": decision}), thread(code), context=self.deps(principal.tenant_id))
            await self._replay_deferred(principal.tenant_id, code, [dept] if decision == "request_information" and dept else None)
        async with self.sessionmaker() as s:
            return await s.scalar(select(ApprovalRequest).where(ApprovalRequest.code == approval_code))


async def start_for_patient(engine: Engine, tenant_id: uuid.UUID, patient_ref: str, authorized_by: str, occurred_at: str | None = None) -> str:
    """Convenience used by seed and evals: emit CLINICAL_DISCHARGE_AUTHORIZED."""
    return await engine.handle_event(
        tenant_id,
        {"event_id": f"auth-{patient_ref}-{uuid.uuid4().hex[:8]}", "type": "CLINICAL_DISCHARGE_AUTHORIZED", "patient_ref": patient_ref,
         "occurred_at": occurred_at, "payload": {"authorized_by": authorized_by}},
    )


async def find_workflow_code(session: AsyncSession, tenant_id: uuid.UUID, patient_ref: str) -> str | None:
    p = await session.scalar(select(Patient).where(Patient.tenant_id == tenant_id, Patient.ref == patient_ref))
    if not p:
        return None
    wf = await session.scalar(select(Workflow).where(Workflow.patient_id == p.id).order_by(Workflow.created_at.desc()).limit(1))
    return wf.code if wf else None


__all__ = ["Engine", "DecisionError", "start_for_patient", "workflow_by_code", "find_workflow_code"]
