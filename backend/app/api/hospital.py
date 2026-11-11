"""Hospital operations API: first-run setup, users, patients, discharge authorization, department work, reports."""
import csv
import io
import re
import statistics
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import departments as D
from ..cache import Cache, executive_scope
from ..config import get_settings
from ..db import get_session
from ..fhir import FhirClient, FhirError, fetch_patient_context
from ..models import (ApprovalRequest, AuditLog, Blocker, Patient, Role, SimDepartmentRecord, Tenant, User, Workflow, WorkflowTask, utcnow)
from ..repo import audit, ensure_counters
from ..security import (ADMIN, AUDITOR, DEPT_PERMISSIONS, ROLE_DESCRIPTIONS, ROLE_PERMISSIONS, Principal, create_token, current_principal,
                        hash_password, password_problem, require, verify_password)
from ..integrations import outbound
from ..integrations.service import occupy_bed, release_bed
from ..sim import upsert_record

router = APIRouter(prefix="/api")
PAYER_TYPES = ("self_pay", "cashless", "government")


class In(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


def engine_of(request: Request):
    return request.app.state.engine


async def emit(request: Request, tenant_id: uuid.UUID, etype: str, patient_ref: str, payload: dict | None = None) -> str:
    """User actions are processed inline so the person sees the result; integrations use the Redis stream."""
    event = {"event_id": f"ui-{uuid.uuid4().hex[:12]}", "type": etype, "patient_ref": patient_ref,
             "occurred_at": datetime.now(timezone.utc).isoformat(), "payload": payload or {}}
    return await engine_of(request).handle_event(tenant_id, event)


def _masked(p: Patient, principal: Principal) -> tuple[str, str]:
    if principal.role == AUDITOR:
        return " ".join(f"{x[0]}." for x in p.display_name.split()), f"MRN ••••{p.mrn[-4:]}"
    return p.display_name, p.mrn


# ===== first-run setup ===============================================================================
class SetupIn(In):
    hospital_name: str = Field(min_length=2, max_length=200)
    admin_name: str = Field(min_length=2, max_length=200)
    admin_email: EmailStr
    password: str = Field(min_length=12, max_length=200)


async def _needs_setup(s: AsyncSession) -> bool:
    """True until the first real account exists. Demo accounts (app.demo) live in their own hospital and don't count."""
    from ..demo import DEMO_SLUG

    real_users = select(func.count()).select_from(User).join(Tenant, Tenant.id == User.tenant_id).where(Tenant.slug != DEMO_SLUG)
    return (await s.scalar(real_users)) == 0


@router.get("/setup/status")
async def setup_status(s: AsyncSession = Depends(get_session)):
    return {"needsSetup": await _needs_setup(s), "fhirConfigured": bool(get_settings().fhir_base_url)}


@router.post("/setup", status_code=201)
async def setup(body: SetupIn, s: AsyncSession = Depends(get_session)):
    if not await _needs_setup(s):
        raise HTTPException(409, "CareFlow is already set up. Sign in instead.")
    if (problem := password_problem(body.password, body.admin_email)):
        raise HTTPException(422, problem)
    slug = re.sub(r"[^a-z0-9]+", "-", body.hospital_name.lower()).strip("-")[:40] or "hospital"
    if await s.scalar(select(Tenant).where(Tenant.slug == slug)):
        slug = f"{slug}-{uuid.uuid4().hex[:4]}"
    tenant = Tenant(slug=slug, name=body.hospital_name.strip())
    s.add(tenant)
    await s.flush()
    roles = await _ensure_roles(s)
    await ensure_counters(s)
    user = User(tenant_id=tenant.id, email=body.admin_email.lower(), name=body.admin_name.strip(), role_id=roles[ADMIN].id,
                password_hash=hash_password(body.password))
    s.add(user)
    await s.commit()
    user = await s.scalar(select(User).where(User.id == user.id))
    token, ttl = create_token(user)
    return {"accessToken": token, "expiresIn": ttl, "user": _session_user(user)}


async def _ensure_roles(s: AsyncSession) -> dict[str, Role]:
    roles = {r.name: r for r in (await s.scalars(select(Role))).all()}
    for name, perms in ROLE_PERMISSIONS.items():
        if name not in roles:
            roles[name] = Role(name=name, permissions=perms, description=ROLE_DESCRIPTIONS.get(name, ""))
            s.add(roles[name])
        else:
            roles[name].permissions = perms
    await s.flush()
    return roles


def _session_user(u: User) -> dict:
    return {"name": u.name, "email": u.email, "role": u.role.name, "permissions": ROLE_PERMISSIONS.get(u.role.name, [])}


# ===== users =========================================================================================
class UserIn(In):
    name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    role: str
    password: str = Field(min_length=12, max_length=200)


class UserPatch(In):
    role: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=200)


class PasswordIn(In):
    current_password: str
    new_password: str = Field(min_length=12, max_length=200)


def _user_out(u: User) -> dict:
    locked = bool(u.locked_until and u.locked_until > utcnow())
    return {"id": str(u.id), "name": u.name, "email": u.email, "role": u.role.name, "isActive": u.is_active, "createdAt": u.created_at,
            "lastLoginAt": u.last_login_at, "locked": locked}


@router.get("/roles")
async def roles(_: Principal = Depends(current_principal)):
    return [{"name": n, "description": ROLE_DESCRIPTIONS.get(n, ""), "permissions": p} for n, p in ROLE_PERMISSIONS.items()]


@router.get("/users")
async def list_users(p: Principal = Depends(require("user:manage")), s: AsyncSession = Depends(get_session)):
    rows = (await s.scalars(select(User).where(User.tenant_id == p.tenant_id).order_by(User.name))).all()
    return [_user_out(u) for u in rows]


@router.post("/users", status_code=201)
async def create_user(body: UserIn, p: Principal = Depends(require("user:manage")), s: AsyncSession = Depends(get_session)):
    if body.role not in ROLE_PERMISSIONS:
        raise HTTPException(422, f"Unknown role {body.role}")
    if (problem := password_problem(body.password, body.email)):
        raise HTTPException(422, problem)
    if await s.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(409, f"An account for {body.email} already exists.")
    roles = await _ensure_roles(s)
    u = User(tenant_id=p.tenant_id, email=body.email.lower(), name=body.name.strip(), role_id=roles[body.role].id, password_hash=hash_password(body.password))
    s.add(u)
    await s.commit()
    return _user_out(await s.scalar(select(User).where(User.id == u.id)))


@router.patch("/users/{user_id}")
async def update_user(user_id: uuid.UUID, body: UserPatch, p: Principal = Depends(require("user:manage")), s: AsyncSession = Depends(get_session)):
    u = await s.scalar(select(User).where(User.id == user_id, User.tenant_id == p.tenant_id))
    if u is None:
        raise HTTPException(404, "User not found.")
    if u.id == p.user_id and (body.is_active is False or (body.role and body.role != ADMIN)):
        raise HTTPException(409, "You can't deactivate yourself or remove your own administrator role.")
    if body.role:
        if body.role not in ROLE_PERMISSIONS:
            raise HTTPException(422, f"Unknown role {body.role}")
        u.role_id = (await _ensure_roles(s))[body.role].id
    if body.is_active is not None:
        u.is_active = body.is_active
    if body.password:
        if (problem := password_problem(body.password, u.email)):
            raise HTTPException(422, problem)
        u.password_hash = hash_password(body.password)
        u.failed_logins, u.locked_until = 0, None
    if body.is_active:
        u.failed_logins, u.locked_until = 0, None  # re-activating also unlocks
    uid = u.id
    await s.commit()
    s.expire_all()
    return _user_out(await s.scalar(select(User).where(User.id == uid)))


@router.post("/auth/change-password", status_code=204)
async def change_password(body: PasswordIn, p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    u = await s.get(User, p.user_id)
    if not verify_password(body.current_password, u.password_hash):
        raise HTTPException(400, "Current password is incorrect.")
    if (problem := password_problem(body.new_password, u.email)):
        raise HTTPException(422, problem)
    u.password_hash = hash_password(body.new_password)
    await s.commit()
    return Response(status_code=204)


# ===== patients ======================================================================================
class PatientIn(In):
    display_name: str = Field(min_length=2, max_length=200)
    mrn: str = Field(min_length=2, max_length=64)
    ward: str = Field(min_length=1, max_length=64)
    bed: str = Field(default="", max_length=32)
    payer: str = Field(min_length=2, max_length=200)
    payer_type: str
    attending: str = Field(min_length=2, max_length=200)
    admitted_at: datetime | None = None
    has_lab_orders: bool = False
    consent_for_coordination: bool = True


class PatientPatch(In):
    ward: str | None = None
    bed: str | None = None
    attending: str | None = None
    payer: str | None = None
    has_lab_orders: bool | None = None
    consent_for_coordination: bool | None = None


class FhirImportIn(In):
    patient_id: str
    encounter_id: str
    payer: str = Field(min_length=2, max_length=200)
    payer_type: str
    attending: str = Field(min_length=2, max_length=200)
    consent_for_coordination: bool = True


async def _patients_out(s: AsyncSession, pts: list[Patient], principal: Principal) -> list[dict]:
    """Serialize patients with their latest workflow and departments: 3 queries for any number of patients (not 1 + 2N)."""
    if not pts:
        return []
    ids, refs = [pt.id for pt in pts], [pt.ref for pt in pts]
    latest: dict[uuid.UUID, Workflow] = {}
    for wf in (await s.scalars(select(Workflow).where(Workflow.patient_id.in_(ids)).order_by(Workflow.created_at))).all():
        latest[wf.patient_id] = wf  # ascending order: the last one seen is the newest
    depts: dict[str, list[str]] = defaultdict(list)
    for ref, dept in (await s.execute(select(SimDepartmentRecord.patient_ref, SimDepartmentRecord.department)
                                      .where(SimDepartmentRecord.tenant_id == principal.tenant_id, SimDepartmentRecord.patient_ref.in_(refs)))).all():
        depts[ref].append(dept)
    out = []
    for pt in pts:
        name, mrn = _masked(pt, principal)
        wf = latest.get(pt.id)
        out.append({
            "ref": pt.ref, "displayName": name, "mrn": mrn, "ward": pt.ward, "bed": pt.bed, "payer": pt.payer, "payerType": pt.payer_type,
            "attending": pt.attending, "admittedAt": pt.admitted_at, "hasLabOrders": pt.has_lab_orders, "consentForCoordination": pt.consent_for_coordination,
            "status": pt.status, "source": pt.source, "departments": sorted(depts.get(pt.ref, []), key=D.DEPARTMENTS.index),
            "workflow": {"id": wf.code, "status": wf.status} if wf else None,
        })
    return out


async def _patient_out(s: AsyncSession, pt: Patient, principal: Principal) -> dict:
    return (await _patients_out(s, [pt], principal))[0]


@router.get("/patients")
async def list_patients(status: str | None = Query("admitted"), q: str | None = None,
                        p: Principal = Depends(require("workflow:read")), s: AsyncSession = Depends(get_session)):
    query = select(Patient).where(Patient.tenant_id == p.tenant_id)
    if status and status != "all":
        query = query.where(Patient.status == status)
    if q:
        like = f"%{q.strip()}%"
        query = query.where((Patient.display_name.ilike(like)) | (Patient.mrn.ilike(like)) | (Patient.ward.ilike(like)))
    rows = (await s.scalars(query.order_by(Patient.admitted_at.desc()).limit(500))).all()
    return await _patients_out(s, list(rows), p)


async def _create_patient(s: AsyncSession, tenant_id: uuid.UUID, body, ref: str, encounter_ref: str, source: str, admitted_at: datetime,
                          display_name: str, mrn: str, ward: str, bed: str, has_lab: bool) -> Patient:
    if body.payer_type not in PAYER_TYPES:
        raise HTTPException(422, f"Payer type must be one of {', '.join(PAYER_TYPES)}")
    if await s.scalar(select(Patient).where(Patient.tenant_id == tenant_id, Patient.mrn == mrn)):
        raise HTTPException(409, f"A patient with {mrn} already exists.")
    pt = Patient(tenant_id=tenant_id, ref=ref, encounter_ref=encounter_ref, display_name=display_name, mrn=mrn, ward=ward, bed=bed,
                 payer=body.payer.strip(), payer_type=body.payer_type, attending=body.attending.strip(), admitted_at=admitted_at,
                 has_lab_orders=has_lab, consent_for_coordination=body.consent_for_coordination, source=source)
    s.add(pt)
    for dept, data in D.initial_records(body.payer_type, has_lab).items():
        await upsert_record(s, tenant_id, ref, dept, data)
    await s.flush()
    return pt


@router.post("/patients", status_code=201)
async def admit_patient(body: PatientIn, p: Principal = Depends(require("patient:write")), s: AsyncSession = Depends(get_session)):
    mrn = body.mrn.strip() if body.mrn.strip().upper().startswith("MRN") else f"MRN {body.mrn.strip()}"
    pt = await _create_patient(s, p.tenant_id, body, f"pt-{uuid.uuid4().hex[:10]}", f"enc-{uuid.uuid4().hex[:10]}", "manual",
                               body.admitted_at or utcnow(), body.display_name.strip(), mrn, body.ward.strip(), body.bed.strip(), body.has_lab_orders)
    await occupy_bed(s, p.tenant_id, pt.ward, pt.bed, pt.ref, f"{p.name} ({p.role})")
    await s.commit()
    return await _patient_out(s, pt, p)


@router.post("/patients/import-fhir", status_code=201)
async def import_fhir(body: FhirImportIn, p: Principal = Depends(require("patient:write")), s: AsyncSession = Depends(get_session)):
    settings = get_settings()
    if not settings.fhir_base_url:
        raise HTTPException(400, "No FHIR server is configured. Set CAREFLOW_FHIR_BASE_URL, or add the patient manually.")
    if await s.scalar(select(Patient).where(Patient.tenant_id == p.tenant_id, Patient.ref == body.patient_id)):
        raise HTTPException(409, f"FHIR patient {body.patient_id} is already in CareFlow.")
    client = FhirClient(settings.fhir_base_url, settings.fhir_token)
    try:
        ctx = await fetch_patient_context(client, body.patient_id, body.encounter_id)
    except FhirError as exc:
        raise HTTPException(502, f"FHIR server: {exc}") from exc
    except Exception as exc:
        raise HTTPException(502, f"Couldn't read from the FHIR server: {exc}") from exc
    finally:
        await client.aclose()
    pt = await _create_patient(s, p.tenant_id, body, ctx["patient_ref"], ctx["encounter_ref"], "fhir", ctx["admitted_at"] or utcnow(),
                               ctx["display_name"], ctx["mrn"], ctx["ward"], ctx["bed"], ctx["has_lab_orders"])
    await occupy_bed(s, p.tenant_id, pt.ward, pt.bed, pt.ref, f"{p.name} ({p.role})")
    await s.commit()
    return await _patient_out(s, pt, p)


async def _patient(s: AsyncSession, p: Principal, ref: str) -> Patient:
    pt = await s.scalar(select(Patient).where(Patient.tenant_id == p.tenant_id, Patient.ref == ref))
    if pt is None:
        raise HTTPException(404, "Patient not found.")
    return pt


@router.patch("/patients/{ref}")
async def update_patient(ref: str, body: PatientPatch, p: Principal = Depends(require("patient:write")), s: AsyncSession = Depends(get_session)):
    pt = await _patient(s, p, ref)
    for field in ("ward", "bed", "attending", "payer", "consent_for_coordination"):
        value = getattr(body, field)
        if value is not None:
            setattr(pt, field, value.strip() if isinstance(value, str) else value)
    if body.has_lab_orders and not pt.has_lab_orders:
        pt.has_lab_orders = True
        await upsert_record(s, p.tenant_id, pt.ref, "laboratory", D.initial_records(pt.payer_type, True)["laboratory"])
    if (body.ward is not None or body.bed is not None) and pt.status == "admitted":
        await occupy_bed(s, p.tenant_id, pt.ward, pt.bed, pt.ref, f"{p.name} ({p.role})")
    await s.commit()
    return await _patient_out(s, pt, p)


class AuthorizeIn(In):
    note: str | None = Field(default=None, max_length=500)


@router.post("/patients/{ref}/authorize-discharge")
async def authorize_discharge(ref: str, request: Request, body: AuthorizeIn = AuthorizeIn(),
                              p: Principal = Depends(require("discharge:authorize")), s: AsyncSession = Depends(get_session)):
    """The treating clinician records their decision. CareFlow never makes this decision itself."""
    pt = await _patient(s, p, ref)
    if pt.status != "admitted":
        raise HTTPException(409, "This patient has already been discharged.")
    existing = await s.scalar(select(Workflow).where(Workflow.patient_id == pt.id, Workflow.status != "discharged"))
    if existing:
        raise HTTPException(409, f"Discharge is already authorized (workflow {existing.code}).")
    patient_id = pt.id
    await emit(request, p.tenant_id, "CLINICAL_DISCHARGE_AUTHORIZED", ref, {"authorized_by": p.name, "note": body.note})
    s.expire_all()
    wf = await s.scalar(select(Workflow).where(Workflow.patient_id == patient_id).order_by(Workflow.created_at.desc()).limit(1))
    return {"workflowId": wf.code if wf else None, "status": wf.status if wf else None}


@router.post("/workflows/{code}/discharge")
async def close_discharge(code: str, request: Request, p: Principal = Depends(require("discharge:close")), s: AsyncSession = Depends(get_session)):
    wf = await s.scalar(select(Workflow).where(Workflow.tenant_id == p.tenant_id, Workflow.code == code))
    if wf is None:
        raise HTTPException(404, f"Workflow {code} wasn't found.")
    if wf.status != "cleared":
        raise HTTPException(409, "Every department must be cleared before the patient leaves.")
    pt_ref, by = wf.patient.ref, f"{p.name} ({p.role})"
    await emit(request, p.tenant_id, "PATIENT_DISCHARGED", pt_ref, {"discharged_by": by})
    s.expire_all()
    wf = await s.scalar(select(Workflow).where(Workflow.tenant_id == p.tenant_id, Workflow.code == code))
    released = await release_bed(s, p.tenant_id, pt_ref, by)
    await s.commit()
    tenant = await s.get(Tenant, p.tenant_id)
    sm = request.app.state.sessionmaker
    outbound.notify_discharge(sm, p.tenant_id, tenant.name, wf.patient, wf)
    await Cache(getattr(request.app.state, "redis", None)).bump(executive_scope(p.tenant_id))  # census changed: refresh the dashboard now
    for b in released:
        outbound.notify(sm, p.tenant_id, "his", "bed.status_changed", {"ward": b.ward, "bed": b.bed, "status": b.status})
    return {"workflowId": code, "status": "discharged", "bedsReleased": [f"{b.ward}/{b.bed}" for b in released]}


class ArchiveIn(In):
    reason: str = Field("Test or demo patient", max_length=500)


@router.post("/patients/{ref}/archive")
async def archive_patient(ref: str, request: Request, body: ArchiveIn = ArchiveIn(), p: Principal = Depends(require("user:manage")),
                          s: AsyncSession = Depends(get_session)):
    """Administrator only: take a patient (typically a test patient) out of every list, queue, approval and dashboard.

    Nothing is deleted: the patient row, workflows and audit trail stay in the database (status "archived", workflows
    soft-deleted), and the archive itself is recorded in the audit trail."""
    pt = await s.scalar(select(Patient).where(Patient.tenant_id == p.tenant_id, Patient.ref == ref))
    if pt is None:
        raise HTTPException(404, "Patient not found.")
    if pt.status == "archived":
        raise HTTPException(409, "This patient is already archived.")
    now, by = utcnow(), f"{p.name} ({p.role})"
    wfs = (await s.scalars(select(Workflow).where(Workflow.patient_id == pt.id, Workflow.deleted_at.is_(None)))).all()
    for wf in wfs:
        wf.deleted_at = now
    pt.status = "archived"
    released = await release_bed(s, p.tenant_id, pt.ref, by)
    code = wfs[-1].code if wfs else "-"
    await audit(s, p.tenant_id, code, "human", by, "Archived patient", f"{pt.mrn}: {body.reason.strip() or 'no reason given'}")
    await s.commit()
    await Cache(getattr(request.app.state, "redis", None)).bump(executive_scope(p.tenant_id))  # census changed
    return {"ref": pt.ref, "status": "archived", "workflowsArchived": len(wfs), "bedsReleased": [f"{b.ward}/{b.bed}" for b in released]}


# ===== department work queues =========================================================================
class ActionIn(In):
    action: str
    params: dict = Field(default_factory=dict)


def _dept_guard(p: Principal, dept: str, read_only: bool = False) -> None:
    if dept not in D.DEPARTMENTS:
        raise HTTPException(404, f"Unknown department {dept}")
    if p.can(DEPT_PERMISSIONS[dept]) or (read_only and p.can("dept:read")):
        return
    raise HTTPException(403, f"Your role ({p.role}) can't update {dept}.")


@router.get("/departments")
async def my_departments(p: Principal = Depends(current_principal)):
    return [{"department": d, "canAct": p.can(DEPT_PERMISSIONS[d]),
             "actions": [{"name": a.name, "label": a.label, "params": list(a.params)} for a in D.ACTIONS[d].values()]}
            for d in D.DEPARTMENTS if p.can(DEPT_PERMISSIONS[d]) or p.can("dept:read")]


@router.get("/departments/{dept}/queue")
async def department_queue(dept: str, p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    _dept_guard(p, dept, read_only=True)
    rows = (await s.execute(
        select(SimDepartmentRecord, Patient).join(Patient, (Patient.ref == SimDepartmentRecord.patient_ref) & (Patient.tenant_id == SimDepartmentRecord.tenant_id))
        .where(SimDepartmentRecord.tenant_id == p.tenant_id, SimDepartmentRecord.department == dept, Patient.status == "admitted")
    )).all()
    # Open workflows, this department's tasks and open blockers for every row at once: 4 queries, not 1 + 3N.
    pids = [pt.id for _, pt in rows]
    wf_by_patient = {wf.patient_id: wf for wf in (await s.scalars(
        select(Workflow).where(Workflow.patient_id.in_(pids), Workflow.status != "discharged"))).all()} if pids else {}
    wf_ids = [wf.id for wf in wf_by_patient.values()]
    task_by_wf = {t.workflow_id: t for t in (await s.scalars(
        select(WorkflowTask).where(WorkflowTask.workflow_id.in_(wf_ids), WorkflowTask.department == dept))).all()} if wf_ids else {}
    blockers_by_wf: dict[uuid.UUID, list[Blocker]] = defaultdict(list)
    if wf_ids:
        for b in (await s.scalars(select(Blocker).where(Blocker.workflow_id.in_(wf_ids), Blocker.department == dept, Blocker.status == "open"))).all():
            blockers_by_wf[b.workflow_id].append(b)
    out = []
    for rec, pt in rows:
        wf = wf_by_patient.get(pt.id)
        task = task_by_wf.get(wf.id) if wf else None
        blockers = blockers_by_wf.get(wf.id, []) if wf else []
        name, mrn = _masked(pt, p)
        out.append({
            "patientRef": pt.ref, "displayName": name, "mrn": mrn, "ward": pt.ward, "bed": pt.bed, "payer": pt.payer,
            "workflow": {"id": wf.code, "status": wf.status, "authorizedAt": wf.authorized_at} if wf else None,
            "taskStatus": task.status if task else None,
            "record": D.public_record(rec.data),
            "requests": D.requests_from_careflow(rec.data),
            "blockers": [{"id": b.code, "kind": b.kind, "summary": b.summary, "since": b.since} for b in blockers],
        })
    rank = {"blocked": 0, "in_progress": 1, "not_started": 2, None: 3, "cleared": 4}
    out.sort(key=lambda r: (rank.get(r["taskStatus"], 3), -(r["workflow"]["authorizedAt"].timestamp() if r["workflow"] else 0)))
    return out


@router.post("/departments/{dept}/{patient_ref}/actions")
async def department_action(dept: str, patient_ref: str, body: ActionIn, request: Request,
                            p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    _dept_guard(p, dept)
    pt = await _patient(s, p, patient_ref)
    if pt.status != "admitted":
        raise HTTPException(409, "This patient has been discharged.")
    try:
        data, summary = await D.apply_action(s, p.tenant_id, patient_ref, dept, body.action, body.params, f"{p.name} ({p.role})")
    except D.ActionError as exc:
        raise HTTPException(422, str(exc)) from exc
    await s.commit()
    outcome = await emit(request, p.tenant_id, D.DEPT_EVENT[dept], patient_ref, {"department": dept, "by": p.name, "summary": summary})
    if (target := outbound.STAFF_EVENTS.get((dept, body.action))):
        outbound.notify(request.app.state.sessionmaker, p.tenant_id, target[0], target[1],
                        {**outbound.patient_payload(pt), "department": dept, "summary": summary, "by": f"{p.name} ({p.role})",
                         "record": {k: v for k, v in D.public_record(data).items() if k != "history"}}, patient_ref)
    return {"record": D.public_record(data), "summary": summary, "event": outcome}


# ===== reports =======================================================================================
def _minutes(a: datetime | None, b: datetime | None) -> int | None:
    return int((b - a).total_seconds() // 60) if a and b else None


@router.get("/workflows/{code}/report")
async def clearance_report(code: str, p: Principal = Depends(require("workflow:read")), s: AsyncSession = Depends(get_session)):
    wf = await s.scalar(select(Workflow).where(Workflow.tenant_id == p.tenant_id, Workflow.code == code))
    if wf is None:
        raise HTTPException(404, f"Workflow {code} wasn't found.")
    tenant = await s.get(Tenant, p.tenant_id)
    pt = wf.patient
    name, mrn = _masked(pt, p)
    tasks = (await s.scalars(select(WorkflowTask).where(WorkflowTask.workflow_id == wf.id).order_by(WorkflowTask.position))).all()
    blockers = (await s.scalars(select(Blocker).where(Blocker.workflow_id == wf.id).order_by(Blocker.since))).all()
    actions = (await s.scalars(select(ApprovalRequest).where(ApprovalRequest.workflow_id == wf.id).order_by(ApprovalRequest.created_at))).all()
    events = (await s.scalars(select(AuditLog).where(AuditLog.tenant_id == p.tenant_id, AuditLog.workflow_code == code,
                                                     AuditLog.actor_kind.in_(("human", "agent"))).order_by(AuditLog.at))).all()
    now = utcnow()
    return {
        "hospital": tenant.name,
        "workflowId": wf.code,
        "generatedAt": now,
        "generatedBy": f"{p.name} ({p.role})",
        "patient": {"displayName": name, "mrn": mrn, "ward": pt.ward, "bed": pt.bed, "payer": pt.payer, "payerType": pt.payer_type,
                    "attending": pt.attending, "admittedAt": pt.admitted_at},
        "status": wf.status,
        "authorizedAt": wf.authorized_at,
        "authorizedBy": wf.authorized_by,
        "clearedAt": wf.cleared_at,
        "dischargedAt": wf.discharged_at,
        "clearanceMinutes": _minutes(wf.authorized_at, wf.cleared_at),
        "elapsedMinutes": _minutes(wf.authorized_at, wf.discharged_at or wf.cleared_at or now),
        "summary": wf.summary,
        "departments": [{"department": t.department, "title": t.title, "status": t.status, "owner": t.owner, "clearedAt": t.cleared_at,
                         "minutesToClear": _minutes(wf.authorized_at, t.cleared_at),
                         "blockers": sum(1 for b in blockers if b.department == t.department)} for t in tasks],
        "blockers": [{"id": b.code, "department": b.department, "kind": b.kind, "summary": b.summary, "since": b.since, "resolvedAt": b.resolved_at,
                      "status": b.status, "minutesOpen": _minutes(b.since, b.resolved_at or now)} for b in blockers],
        "actions": [{"id": a.code, "action": a.action, "description": a.description, "risk": a.risk, "requiresApproval": a.requires_approval,
                     "approverRole": a.approver_role, "status": a.status, "createdAt": a.created_at, "decidedBy": a.decided_by,
                     "decidedAt": a.decided_at, "comment": a.comment} for a in actions],
        "timeline": [{"at": e.at, "actorKind": e.actor_kind, "actor": e.actor, "event": e.event, "detail": e.detail} for e in events],
    }


@router.get("/reports/workflows.csv")
async def workflows_csv(start: date | None = Query(None, alias="from"), end: date | None = Query(None, alias="to"),
                        p: Principal = Depends(require("analytics:read")), s: AsyncSession = Depends(get_session)):
    q = select(Workflow).where(Workflow.tenant_id == p.tenant_id, Workflow.deleted_at.is_(None))
    if start:
        q = q.where(Workflow.authorized_at >= datetime.combine(start, time.min, timezone.utc))
    if end:
        q = q.where(Workflow.authorized_at < datetime.combine(end + timedelta(days=1), time.min, timezone.utc))
    wfs = (await s.scalars(q.order_by(Workflow.authorized_at))).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["workflow", "patient", "mrn", "ward", "payer_type", "authorized_at", "authorized_by", "status", "cleared_at", "discharged_at",
                "clearance_minutes", "blockers_found", "actions", "actions_needing_approval", "slowest_department", "slowest_department_minutes"])
    ids = [wf.id for wf in wfs]
    tasks_by: dict[uuid.UUID, list[WorkflowTask]] = defaultdict(list)
    acts_by: dict[uuid.UUID, list[ApprovalRequest]] = defaultdict(list)
    blockers_n: dict[uuid.UUID, int] = {}
    if ids:  # 3 queries for the whole report instead of 3 per workflow
        for t in (await s.scalars(select(WorkflowTask).where(WorkflowTask.workflow_id.in_(ids)))).all():
            tasks_by[t.workflow_id].append(t)
        for a in (await s.scalars(select(ApprovalRequest).where(ApprovalRequest.workflow_id.in_(ids)))).all():
            acts_by[a.workflow_id].append(a)
        blockers_n = dict((await s.execute(select(Blocker.workflow_id, func.count()).where(Blocker.workflow_id.in_(ids)).group_by(Blocker.workflow_id))).all())
    for wf in wfs:
        tasks, acts, n_blockers = tasks_by.get(wf.id, []), acts_by.get(wf.id, []), blockers_n.get(wf.id, 0)
        timed = [(t.department, _minutes(wf.authorized_at, t.cleared_at)) for t in tasks if t.cleared_at]
        slow = max(timed, key=lambda x: x[1]) if timed else ("", "")
        name, mrn = _masked(wf.patient, p)
        w.writerow([wf.code, name, mrn, wf.patient.ward, wf.patient.payer_type, wf.authorized_at.isoformat(), wf.authorized_by, wf.status,
                    wf.cleared_at.isoformat() if wf.cleared_at else "", wf.discharged_at.isoformat() if wf.discharged_at else "",
                    _minutes(wf.authorized_at, wf.cleared_at) if wf.cleared_at else "", n_blockers, len(acts),
                    sum(1 for a in acts if a.requires_approval), slow[0], slow[1]])
    filename = f"careflow-discharges-{(start or date.today()).isoformat()}.csv"
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


__all__ = ["router", "statistics"]
