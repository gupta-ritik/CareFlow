"""API response shapes. camelCase on the wire to match web/src/lib/types.ts."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class Out(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class PatientContextOut(Out):
    patient_id: str
    display_name: str
    mrn: str
    ward: str
    bed: str
    payer: str
    attending: str
    admitted_at: datetime


class TaskOut(Out):
    id: str
    department: str
    title: str
    status: str
    owner: str
    updated_at: datetime
    depends_on: list[str]


class EvidenceOut(Out):
    id: str
    source: str
    query: str
    observed: str
    observed_at: datetime
    untrusted: bool = False


class BlockerOut(Out):
    id: str
    department: str
    kind: str
    summary: str
    since: datetime
    evidence_ids: list[str]
    waiting_on: str | None = None


class WorkflowOut(Out):
    id: str
    patient: PatientContextOut
    authorized_at: datetime
    authorized_by: str
    status: str
    summary: str | None = None
    tasks: list[TaskOut]
    blockers: list[BlockerOut]
    evidence: list[EvidenceOut]


class ActionOut(Out):
    id: str
    workflow_id: str
    blocker_id: str
    action: str
    description: str
    expected_impact: str
    risk: str
    requires_approval: bool
    approver_role: str
    evidence_ids: list[str]
    sop_refs: list[str]
    status: str
    created_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    comment: str | None = None


class AuditOut(Out):
    id: str
    workflow_id: str
    at: datetime
    actor_kind: str
    actor: str
    event: str
    detail: str
    evidence_ids: list[str] = []
    latency_ms: int | None = None
    tokens: int | None = None


class AgentExecutionOut(Out):
    id: str
    workflow_id: str
    agent: str
    mode: str
    status: str
    input_summary: str
    output_summary: str
    latency_ms: int
    tokens: int
    retries: int
    error: str | None
    started_at: datetime


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(Out):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict


class DecisionIn(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


class EventIn(BaseModel):
    type: str
    patient_ref: str = Field(alias="patientRef")
    event_id: str | None = Field(default=None, alias="eventId")
    correlation_id: str | None = Field(default=None, alias="correlationId")
    payload: dict = Field(default_factory=dict)
    model_config = ConfigDict(populate_by_name=True)


class SimPatchIn(BaseModel):
    patch: dict = Field(default_factory=dict)
    emit_event: bool = Field(default=True, alias="emitEvent")
    model_config = ConfigDict(populate_by_name=True)


class Page(Out):
    items: list
    total: int
    limit: int
    offset: int
