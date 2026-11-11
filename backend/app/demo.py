"""Public demo mode: anyone can explore CareFlow as any role, without an account (CAREFLOW_DEMO_MODE=true).

Everything happens inside one hospital of its own, DEMO_SLUG. No other hospital is read or changed.

- Demo accounts: one per role in DEMO_ROLES. Their passwords are random and never shown, so the only way in is the
  "Try the demo" buttons (POST /api/demo/login). There is no Administrator: visitors can't add users or integration keys.
- Demo data: DEMO_HISTORY_DAYS of discharged history from the synthetic generator (for the Director dashboard and Ask
  CareFlow) plus the 8 scripted live patients from app.seed, whose workflows run through the real engine.
- Reset: when the data is older than CAREFLOW_DEMO_RESET_HOURS, the next start-up or demo sign-in rebuilds it in the
  background. Render's free plan has no cron, so the API checks for itself.

Demo data is entirely fictional. Never enable demo mode on a server that holds real patient data.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from .config import Settings
from .models import (AgentExecution, ApprovalDecision, ApprovalRequest, AskSession, AskTurn, AuditLog, Bed, Blocker, Evidence,
                     ExecutionRecord, IntegrationKey, IntegrationMessage, Patient, SimDepartmentRecord, TaskDependency, Tenant, User,
                     VerificationRecord, Workflow, WorkflowEvent, WorkflowTask, utcnow)
from .security import (AUDITOR, BILLING, CLINICIAN, COORDINATOR, DIRECTOR, INSURANCE_LEAD, LABORATORY, PHARMACY, WARD_CLERK,
                       ROLE_DESCRIPTIONS, hash_password)

log = logging.getLogger("careflow.demo")

DEMO_SLUG = "careflow-demo"
DEMO_NAME = "CareFlow Demo Hospital (synthetic)"
DEMO_DOMAIN = f"{DEMO_SLUG}.test"

# key (used in the API and the URL-safe role switcher), role, page to open first
DEMO_ROLES: list[tuple[str, str, str]] = [
    ("director", DIRECTOR, "/director"),
    ("coordinator", COORDINATOR, "/"),
    ("clinician", CLINICIAN, "/patients"),
    ("ward-clerk", WARD_CLERK, "/patients"),
    ("pharmacy", PHARMACY, "/queue"),
    ("laboratory", LABORATORY, "/queue"),
    ("insurance", INSURANCE_LEAD, "/approvals"),
    ("billing", BILLING, "/queue"),
    ("auditor", AUDITOR, "/audit"),
]
ROLE_BY_KEY = {key: (role, home) for key, role, home in DEMO_ROLES}

# A live seed patient whose creation time marks when the demo data was last built.
_MARKER_REF = "pt-8812"

_task: asyncio.Task | None = None
_lock = asyncio.Lock()


def demo_email(key: str) -> str:
    return f"{key}@{DEMO_DOMAIN}"


def roles_payload() -> list[dict]:
    return [{"key": key, "role": role, "home": home, "description": ROLE_DESCRIPTIONS.get(role, "")} for key, role, home in DEMO_ROLES]


def preparing() -> bool:
    return _task is not None and not _task.done()


def _insert(s):
    if s.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


async def ensure_accounts(s) -> Tenant:
    """The demo hospital and one account per demo role (fast; safe to call on every demo sign-in).

    Start-up's background rebuild and the first visitor's sign-in can run this at the same moment, so the inserts use
    ON CONFLICT DO NOTHING instead of check-then-insert."""
    from .api.hospital import _ensure_roles  # avoid an import cycle with the API package
    from .repo import ensure_counters

    insert = _insert(s)
    await s.execute(insert(Tenant).values(id=uuid.uuid4(), slug=DEMO_SLUG, name=DEMO_NAME, created_at=utcnow(), updated_at=utcnow())
                    .on_conflict_do_nothing(index_elements=["slug"]))
    tenant = await s.scalar(select(Tenant).where(Tenant.slug == DEMO_SLUG))
    roles = await _ensure_roles(s)
    await ensure_counters(s)
    existing = set((await s.scalars(select(User.email).where(User.tenant_id == tenant.id))).all())
    for key, role, _ in DEMO_ROLES:
        if demo_email(key) not in existing:
            await s.execute(insert(User).values(
                id=uuid.uuid4(), tenant_id=tenant.id, email=demo_email(key), name=f"Demo {role.lower()}", role_id=roles[role].id,
                password_hash=hash_password(secrets.token_urlsafe(32)),  # nobody knows it: password sign-in is impossible
                created_at=utcnow(), updated_at=utcnow()).on_conflict_do_nothing(index_elements=["email"]))
    await s.flush()
    return tenant


async def _is_stale(sm, settings: Settings) -> bool:
    async with sm() as s:
        built = await s.scalar(select(Patient.created_at).join(Tenant, Tenant.id == Patient.tenant_id)
                               .where(Tenant.slug == DEMO_SLUG, Patient.ref == _MARKER_REF))
    if built is None:
        return True
    if built.tzinfo is None:
        built = built.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - built > timedelta(hours=settings.demo_reset_hours)


async def _clear(s, tid) -> None:
    """Delete every row of the demo hospital except the hospital itself and its demo accounts."""
    wf_ids = select(Workflow.id).where(Workflow.tenant_id == tid)
    ap_ids = select(ApprovalRequest.id).where(ApprovalRequest.workflow_id.in_(wf_ids))
    await s.execute(delete(ApprovalDecision).where(ApprovalDecision.approval_id.in_(ap_ids)))
    for model in (ApprovalRequest, Blocker, Evidence, WorkflowTask, TaskDependency, ExecutionRecord, VerificationRecord):
        await s.execute(delete(model).where(model.workflow_id.in_(wf_ids)))
    await s.execute(delete(AskTurn).where(AskTurn.tenant_id == tid))
    for model in (AskSession, Workflow, AuditLog, AgentExecution, WorkflowEvent, SimDepartmentRecord, Bed, Patient,
                  IntegrationMessage, IntegrationKey):
        await s.execute(delete(model).where(model.tenant_id == tid))


async def rebuild(engine, sm, settings: Settings) -> None:
    """Replace the demo hospital's data: synthetic history, then the scripted live patients and their workflows."""
    from .engine import start_for_patient
    from .integrations.service import occupy_bed
    from .seed import seed_patients
    from .synthetic.generator import Params, load_scenario, write

    started = datetime.now(timezone.utc)
    async with sm() as s:
        tenant = await ensure_accounts(s)
        await _clear(s, tenant.id)
        await s.commit()
    if settings.demo_history_days > 0:
        params = Params.from_settings(settings, tenant=DEMO_SLUG, days=settings.demo_history_days, seed=started.toordinal())
        await write(sm, params, load_scenario("default"), DEMO_NAME)
    async with sm() as s:
        tenant = await s.scalar(select(Tenant).where(Tenant.slug == DEMO_SLUG))
        live = await seed_patients(s, tenant)
        for pt in (await s.scalars(select(Patient).where(Patient.tenant_id == tenant.id, Patient.status == "admitted"))).all():
            if pt.ward and pt.bed:
                await occupy_bed(s, tenant.id, pt.ward, pt.bed, pt.ref, "demo")
        await s.commit()
    for ref, attending, minutes in live:
        try:
            await start_for_patient(engine, tenant.id, ref, attending, (started - timedelta(minutes=minutes)).isoformat())
        except Exception:  # noqa: BLE001 - one patient's workflow failing shouldn't leave the rest of the demo empty
            log.exception("Demo workflow for %s failed to start", ref)
    log.info("Demo data rebuilt in %.0f s", (datetime.now(timezone.utc) - started).total_seconds())


async def refresh_if_stale(engine, sm, settings: Settings) -> None:
    """Start a background rebuild when the demo data is missing or too old. Returns immediately."""
    global _task
    async with _lock:
        if preparing() or not await _is_stale(sm, settings):
            return

        async def run():
            try:
                await rebuild(engine, sm, settings)
            except Exception:  # noqa: BLE001 - logged; the next sign-in tries again
                log.exception("Rebuilding the demo data failed")

        _task = asyncio.create_task(run(), name="careflow-demo-rebuild")


async def stop() -> None:
    if preparing():
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
