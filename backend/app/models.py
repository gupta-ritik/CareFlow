"""Database models.

Patient identity lives only in `patients`. Workflow, agent and audit tables reference patients by id/ref,
and nothing in those tables is sent to an LLM without passing through `redaction`.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, JSONType


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = pk()
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))


class Role(Base, TimestampMixin):
    __tablename__ = "roles"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(String(300), default="")
    permissions: Mapped[list[str]] = mapped_column(JSONType, default=list)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"))
    password_hash: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    role: Mapped[Role] = relationship(lazy="joined")


class Counter(Base):
    """Human-readable codes (WF-2041, EV-1042) shown in the UI alongside UUID keys."""

    __tablename__ = "code_counters"
    name: Mapped[str] = mapped_column(String(16), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


class Patient(Base, TimestampMixin):
    """Sensitive store. Synthetic data only in development."""

    __tablename__ = "patients"
    __table_args__ = (UniqueConstraint("tenant_id", "ref"), UniqueConstraint("tenant_id", "mrn"))
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    ref: Mapped[str] = mapped_column(String(64))  # FHIR Patient id
    encounter_ref: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(200))
    mrn: Mapped[str] = mapped_column(String(64))
    ward: Mapped[str] = mapped_column(String(64))
    bed: Mapped[str] = mapped_column(String(32))
    payer: Mapped[str] = mapped_column(String(200))
    payer_type: Mapped[str] = mapped_column(String(32))  # cashless | self_pay | government
    attending: Mapped[str] = mapped_column(String(200))
    admitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    has_lab_orders: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_for_coordination: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="admitted", index=True)  # admitted | discharged
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual | fhir


class Workflow(Base, TimestampMixin):
    __tablename__ = "workflows"
    __table_args__ = (UniqueConstraint("tenant_id", "code"), Index("ix_workflows_tenant_status", "tenant_id", "status"))
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    code: Mapped[str] = mapped_column(String(32))
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"))
    status: Mapped[str] = mapped_column(String(32), default="in_progress")
    authorized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    authorized_by: Mapped[str] = mapped_column(String(200))
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discharged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    patient: Mapped[Patient] = relationship(lazy="joined")


class WorkflowTask(Base, TimestampMixin):
    __tablename__ = "workflow_tasks"
    __table_args__ = (UniqueConstraint("workflow_id", "department"),)
    id: Mapped[uuid.UUID] = pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    department: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(32), default="not_started")
    owner: Mapped[str] = mapped_column(String(200), default="")
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)


class TaskDependency(Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (UniqueConstraint("workflow_id", "department", "depends_on"),)
    id: Mapped[uuid.UUID] = pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    department: Mapped[str] = mapped_column(String(32))
    depends_on: Mapped[str] = mapped_column(String(32))


class Blocker(Base, TimestampMixin):
    """investigation_cases in the spec."""

    __tablename__ = "blockers"
    id: Mapped[uuid.UUID] = pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(32))
    department: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evidence_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    waiting_on: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[uuid.UUID] = pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(32), index=True)
    department: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(100))
    query: Mapped[str] = mapped_column(String(300))
    observed: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    untrusted: Mapped[bool] = mapped_column(Boolean, default=False)


class ApprovalRequest(Base, TimestampMixin):
    """A proposed action. Low-risk actions are recorded here too, with requires_approval = False."""

    __tablename__ = "approval_requests"
    id: Mapped[uuid.UUID] = pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    blocker_code: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict] = mapped_column(JSONType, default=dict)
    description: Mapped[str] = mapped_column(Text)
    expected_impact: Mapped[str] = mapped_column(Text)
    risk: Mapped[str] = mapped_column(String(16))
    requires_approval: Mapped[bool] = mapped_column(Boolean)
    approver_role: Mapped[str] = mapped_column(String(64))
    evidence_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    sop_refs: Mapped[list[str]] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)


class ApprovalDecision(Base):
    __tablename__ = "approval_decisions"
    id: Mapped[uuid.UUID] = pk()
    approval_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("approval_requests.id", ondelete="CASCADE"), index=True)
    decision: Mapped[str] = mapped_column(String(32))
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewer_name: Mapped[str] = mapped_column(String(200))
    reviewer_role: Mapped[str] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExecutionRecord(Base):
    __tablename__ = "execution_records"
    id: Mapped[uuid.UUID] = pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    approval_code: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    success: Mapped[bool] = mapped_column(Boolean)
    result: Mapped[dict] = mapped_column(JSONType, default=dict)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VerificationRecord(Base):
    __tablename__ = "verification_records"
    id: Mapped[uuid.UUID] = pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    approval_code: Mapped[str] = mapped_column(String(32))
    verified: Mapped[bool] = mapped_column(Boolean)
    observed: Mapped[dict] = mapped_column(JSONType, default=dict)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentExecution(Base):
    __tablename__ = "agent_executions"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    workflow_code: Mapped[str] = mapped_column(String(32), index=True)
    agent: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(16))  # llm | fallback | deterministic
    status: Mapped[str] = mapped_column(String(16))  # ok | error
    input_summary: Mapped[str] = mapped_column(Text, default="")
    output_summary: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowEvent(Base):
    __tablename__ = "workflow_events"
    __table_args__ = (UniqueConstraint("tenant_id", "event_id"),)
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    event_id: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(64))
    patient_ref: Mapped[str] = mapped_column(String(64))
    workflow_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="received")  # received | processed | duplicate | dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_tenant_at", "tenant_id", "at"),)
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    code: Mapped[str] = mapped_column(String(32))
    workflow_code: Mapped[str] = mapped_column(String(32), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor_kind: Mapped[str] = mapped_column(String(16))  # agent | human | system
    actor: Mapped[str] = mapped_column(String(200))
    event: Mapped[str] = mapped_column(String(300))
    detail: Mapped[str] = mapped_column(Text, default="")
    evidence_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SimDepartmentRecord(Base):
    """Each department's working record for a patient (pharmacy task, claim, bill, documents, lab reports).

    Department staff update it from their work queue in CareFlow; an integration can update it through /api/events.
    (Table name kept from the first version for migration compatibility.)
    """

    __tablename__ = "sim_department_records"
    __table_args__ = (UniqueConstraint("tenant_id", "patient_ref", "department"),)
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    patient_ref: Mapped[str] = mapped_column(String(64))
    department: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ----- integrations and beds -----------------------------------------------------------------------
class IntegrationKey(Base):
    """API key a hospital system uses to call CareFlow's integration endpoints. Only a SHA-256 hash is stored."""

    __tablename__ = "integration_keys"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    system: Mapped[str] = mapped_column(String(32))  # his | tpa | pharmacy | lis | any
    key_prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IntegrationMessage(Base):
    """Every message exchanged with a hospital system, inbound or outbound, for troubleshooting and audit."""

    __tablename__ = "integration_messages"
    __table_args__ = (Index("ix_intmsg_tenant_at", "tenant_id", "created_at"),)
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    direction: Mapped[str] = mapped_column(String(8))  # inbound | outbound
    system: Mapped[str] = mapped_column(String(32))  # his | tpa | pharmacy | lis
    protocol: Mapped[str] = mapped_column(String(16))  # hl7v2 | webhook
    message_type: Mapped[str] = mapped_column(String(64))
    control_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    patient_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16))  # accepted | duplicate | warning | error | sent | failed | skipped
    detail: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Bed(Base):
    __tablename__ = "beds"
    __table_args__ = (UniqueConstraint("tenant_id", "ward", "bed"),)
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    ward: Mapped[str] = mapped_column(String(64))
    bed: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="available")  # available | occupied | cleaning | blocked
    patient_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_by: Mapped[str] = mapped_column(String(200), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ----- Ask CareFlow -------------------------------------------------------------------------------
class AskSession(Base):
    """A conversation with Ask CareFlow. Belongs to one user in one hospital."""

    __tablename__ = "ask_sessions"
    __table_args__ = (Index("ix_ask_sessions_user", "tenant_id", "user_id", "updated_at"),)
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AskTurn(Base):
    """One question and its answer. Holds aggregates only: never row-level data or patient identity."""

    __tablename__ = "ask_turns"
    id: Mapped[uuid.UUID] = pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ask_sessions.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    question: Mapped[str] = mapped_column(Text)  # redacted
    status: Mapped[str] = mapped_column(String(16))  # answered | clarify | refused | no_data | error
    partial: Mapped[bool] = mapped_column(Boolean, default=False)
    plan: Mapped[dict] = mapped_column(JSONType, default=dict)       # understanding + plan mode
    steps: Mapped[list] = mapped_column(JSONType, default=list)      # each step: measure and parameters
    results: Mapped[list] = mapped_column(JSONType, default=list)    # result summaries (aggregates only)
    answer: Mapped[dict] = mapped_column(JSONType, default=dict)     # sentences, based on, follow-ups, chart
    claims: Mapped[list] = mapped_column(JSONType, default=list)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="fallback")  # llm | fallback
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)   # thumbs: 1 or -1
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NotificationChannel(Base):
    """Where a user wants alerts: a browser/phone Web Push subscription or a Telegram chat (app/notify.py)."""

    __tablename__ = "notification_channels"
    __table_args__ = (UniqueConstraint("kind", "address", name="uq_notification_channel"),)
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # webpush | telegram
    address: Mapped[str] = mapped_column(Text)  # push endpoint URL, or Telegram chat id
    keys: Mapped[dict] = mapped_column(JSONType, default=dict)  # Web Push p256dh/auth keys
    label: Mapped[str] = mapped_column(String(200), default="")  # e.g. "Chrome on Android", "Telegram @asha"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
