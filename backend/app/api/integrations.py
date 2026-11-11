"""Integration endpoints (HL7 over HTTP, JSON connectors), integration keys, the message log, and bed management."""
import hashlib
import secrets
import uuid
from collections import Counter
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..integrations import outbound
from ..integrations.service import (BED_STATUSES, CONNECTOR_EVENTS, Bus, IntegrationError, handle_connector, handle_hl7, set_bed_status)
from ..models import Bed, IntegrationKey, IntegrationMessage, Patient, Tenant, utcnow
from ..security import AUDITOR, Principal, bearer, current_principal, require

router = APIRouter(prefix="/api")
SYSTEMS = ("his", "tpa", "pharmacy", "lis")
SYSTEM_LABEL = {"his": "Hospital information system (ADT)", "tpa": "TPA / payer gateway", "pharmacy": "Pharmacy system", "lis": "Laboratory (LIS)"}


class In(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _bus(request: Request) -> Bus:
    return Bus(getattr(request.app.state, "redis", None), request.app.state.engine)


async def _caller(request: Request, system: str, creds: HTTPAuthorizationCredentials | None, s: AsyncSession) -> tuple[Tenant, str]:
    """Hospital systems authenticate with an integration key (X-CareFlow-Key); administrators can also use their session."""
    key = request.headers.get("x-careflow-key")
    if key:
        row = await s.scalar(select(IntegrationKey).where(IntegrationKey.key_hash == _hash(key.strip()), IntegrationKey.is_active.is_(True)))
        if row is None:
            raise HTTPException(401, "Integration key is invalid or revoked.")
        if row.system not in (system, "any"):
            raise HTTPException(403, f"This key is for the {row.system} system, not {system}.")
        row.last_used_at = utcnow()
        await s.commit()
        return await s.get(Tenant, row.tenant_id), row.name
    p = await current_principal(creds, s)
    if not p.can("integration:write"):
        raise HTTPException(403, "Use an integration key (X-CareFlow-Key) or an administrator account.")
    return await s.get(Tenant, p.tenant_id), p.name


# ===== inbound =========================================================================================
@router.post("/integrations/hl7")
async def hl7_http(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer), s: AsyncSession = Depends(get_session)):
    """HL7 v2 over HTTP: POST the ER7 text (segments separated by CR or newlines). Returns the ACK as text."""
    from .routes import rate_limit

    tenant, _ = await _caller(request, "his", creds, s)
    await rate_limit(request, f"hl7:{tenant.id}", 600)
    raw = (await request.body()).decode("utf-8", "replace")
    if len(raw) > 1_000_000:
        raise HTTPException(413, "HL7 message is too large (1 MB maximum).")
    ack, out = await handle_hl7(request.app.state.sessionmaker, _bus(request), tenant, raw)
    code = "AA" if out.status in ("accepted", "warning", "duplicate") else ("AR" if out.status == "rejected" else "AE")
    return Response(ack.replace("\r", "\r\n"), media_type="text/plain", headers={"x-hl7-ack-code": code})


@router.post("/integrations/{system}/events")
async def connector_event(system: str, request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer),
                          s: AsyncSession = Depends(get_session)):
    """JSON connector: {"event": "...", "eventId": "...", "mrn": "...", "data": {...}} (see GET /api/integrations/catalog)."""
    from .routes import rate_limit

    if system not in SYSTEMS:
        raise HTTPException(404, f"Unknown system {system}. Use one of: {', '.join(SYSTEMS)}")
    tenant, name = await _caller(request, system, creds, s)
    await rate_limit(request, f"connector:{tenant.id}:{system}", 600)
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(422, "Body must be JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(422, "Body must be a JSON object")
    out = await handle_connector(request.app.state.sessionmaker, _bus(request), tenant, system, body, name)
    payload = {"status": out.status, "detail": out.summary, "patientRef": out.patient_ref}
    if out.status in ("error", "rejected"):
        raise HTTPException(422, out.summary or out.status)
    return payload


@router.get("/integrations/catalog")
async def catalog(_: Principal = Depends(require("integration:read"))):
    from ..integrations.service import SUPPORTED

    return {"hl7": sorted(SUPPORTED), "connectors": {k: [{"event": e, "description": d} for e, d in v.items()] for k, v in CONNECTOR_EVENTS.items()},
            "outboundEvents": sorted({e for _, e in outbound.ACTION_EVENTS.values()} | {e for _, e in outbound.STAFF_EVENTS.values()}
                                     | {"patient.discharged", "bed.status_changed"})}


# ===== status and log ==================================================================================
@router.get("/integrations/status")
async def integration_status(p: Principal = Depends(require("integration:read")), s: AsyncSession = Depends(get_session)):
    st = get_settings()
    since = utcnow() - timedelta(hours=24)
    rows = (await s.execute(
        select(IntegrationMessage.system, IntegrationMessage.direction, IntegrationMessage.status, func.count(), func.max(IntegrationMessage.created_at))
        .where(IntegrationMessage.tenant_id == p.tenant_id, IntegrationMessage.created_at >= since)
        .group_by(IntegrationMessage.system, IntegrationMessage.direction, IntegrationMessage.status)
    )).all()
    last = dict((await s.execute(select(IntegrationMessage.system, func.max(IntegrationMessage.created_at))
                                 .where(IntegrationMessage.tenant_id == p.tenant_id).group_by(IntegrationMessage.system))).all())
    keys = Counter((await s.scalars(select(IntegrationKey.system).where(IntegrationKey.tenant_id == p.tenant_id, IntegrationKey.is_active.is_(True)))).all())
    conf = outbound.configured()
    out = []
    for sys in SYSTEMS:
        mine = [r for r in rows if r[0] == sys]
        inbound = sum(r[3] for r in mine if r[1] == "inbound")
        errors = sum(r[3] for r in mine if r[2] in ("error", "rejected", "failed"))
        total = sum(r[3] for r in mine)
        out.append({
            "system": sys, "label": SYSTEM_LABEL[sys], "activeKeys": keys.get(sys, 0) + keys.get("any", 0),
            "outboundConfigured": conf[sys] or (sys == "his" and conf["hl7"]), "hl7OutboundConfigured": conf["hl7"] if sys == "his" else None,
            "inbound24h": inbound, "outbound24h": sum(r[3] for r in mine if r[1] == "outbound"), "errors24h": errors,
            "lastMessageAt": last.get(sys),
            "health": "idle" if not total else ("degraded" if errors / total >= 0.2 else "ok"),
        })
    return {"systems": out, "mllp": {"port": st.hl7_mllp_port, "allowedHosts": st.hl7_allowed_hosts, "tenant": st.hl7_tenant},
            "signing": bool(st.outbound_secret)}


@router.get("/integrations/messages")
async def integration_messages(system: str | None = None, direction: str | None = None, status: str | None = None,
                               limit: int = Query(100, le=500), include_raw: bool = Query(False, alias="includeRaw"),
                               p: Principal = Depends(require("integration:read")), s: AsyncSession = Depends(get_session)):
    q = select(IntegrationMessage).where(IntegrationMessage.tenant_id == p.tenant_id)
    if system:
        q = q.where(IntegrationMessage.system == system)
    if direction:
        q = q.where(IntegrationMessage.direction == direction)
    if status:
        q = q.where(IntegrationMessage.status == status)
    rows = (await s.scalars(q.order_by(IntegrationMessage.created_at.desc()).limit(limit))).all()
    return [{"id": str(r.id), "at": r.created_at, "direction": r.direction, "system": r.system, "protocol": r.protocol, "type": r.message_type,
             "controlId": r.control_id, "patientRef": r.patient_ref, "status": r.status, "detail": r.detail, "attempts": r.attempts,
             **({"raw": r.raw} if include_raw else {})} for r in rows]


# ===== keys ============================================================================================
class KeyIn(In):
    name: str = Field(min_length=2, max_length=200)
    system: str


@router.get("/integrations/keys")
async def list_keys(p: Principal = Depends(require("user:manage")), s: AsyncSession = Depends(get_session)):
    rows = (await s.scalars(select(IntegrationKey).where(IntegrationKey.tenant_id == p.tenant_id).order_by(IntegrationKey.created_at.desc()))).all()
    return [{"id": str(k.id), "name": k.name, "system": k.system, "prefix": k.key_prefix, "isActive": k.is_active, "createdBy": k.created_by,
             "createdAt": k.created_at, "lastUsedAt": k.last_used_at} for k in rows]


@router.post("/integrations/keys", status_code=201)
async def create_key(body: KeyIn, p: Principal = Depends(require("user:manage")), s: AsyncSession = Depends(get_session)):
    if body.system not in (*SYSTEMS, "any"):
        raise HTTPException(422, f"System must be one of {', '.join((*SYSTEMS, 'any'))}")
    key = f"cfk_{secrets.token_urlsafe(32)}"
    row = IntegrationKey(tenant_id=p.tenant_id, name=body.name.strip(), system=body.system, key_prefix=key[:12], key_hash=_hash(key),
                         created_by=f"{p.name} ({p.role})")
    s.add(row)
    await s.commit()
    return {"id": str(row.id), "name": row.name, "system": row.system, "prefix": row.key_prefix, "key": key,
            "note": "Copy this key now. CareFlow stores only a hash and can't show it again."}


@router.delete("/integrations/keys/{key_id}", status_code=204)
async def revoke_key(key_id: uuid.UUID, p: Principal = Depends(require("user:manage")), s: AsyncSession = Depends(get_session)):
    row = await s.scalar(select(IntegrationKey).where(IntegrationKey.id == key_id, IntegrationKey.tenant_id == p.tenant_id))
    if row is None:
        raise HTTPException(404, "Key not found.")
    row.is_active = False
    await s.commit()
    return Response(status_code=204)


# ===== beds ============================================================================================
class BedsIn(In):
    ward: str = Field(min_length=1, max_length=64)
    beds: list[str] = Field(min_length=1, max_length=200)


class BedPatch(In):
    status: str


def _bed_out(b: Bed, pt: Patient | None, principal: Principal) -> dict:
    name = None
    if pt is not None:
        name = " ".join(f"{x[0]}." for x in pt.display_name.split()) if principal.role == AUDITOR else pt.display_name
    return {"id": str(b.id), "ward": b.ward, "bed": b.bed, "status": b.status, "patientRef": b.patient_ref, "patientName": name,
            "patientStatus": pt.status if pt else None, "updatedAt": b.updated_at, "updatedBy": b.updated_by}


@router.get("/beds")
async def list_beds(p: Principal = Depends(require("workflow:read")), s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(Bed, Patient).outerjoin(Patient, (Patient.ref == Bed.patient_ref) & (Patient.tenant_id == Bed.tenant_id))
                            .where(Bed.tenant_id == p.tenant_id).order_by(Bed.ward, Bed.bed))).all()
    beds = [_bed_out(b, pt, p) for b, pt in rows]
    counts = Counter(b["status"] for b in beds)
    total = len(beds)
    return {"beds": beds, "summary": {"total": total, **{k: counts.get(k, 0) for k in BED_STATUSES},
                                      "occupancy": round(counts.get("occupied", 0) / total, 3) if total else None}}


@router.post("/beds", status_code=201)
async def add_beds(body: BedsIn, p: Principal = Depends(require("bed:manage")), s: AsyncSession = Depends(get_session)):
    added = 0
    for raw in body.beds:
        bed = raw.strip()[:32]
        if not bed or await s.scalar(select(Bed.id).where(Bed.tenant_id == p.tenant_id, Bed.ward == body.ward.strip(), Bed.bed == bed)):
            continue
        s.add(Bed(tenant_id=p.tenant_id, ward=body.ward.strip(), bed=bed, status="available", updated_by=f"{p.name} ({p.role})"))
        added += 1
    await s.commit()
    return {"added": added}


@router.patch("/beds/{bed_id}")
async def update_bed(bed_id: uuid.UUID, body: BedPatch, request: Request, p: Principal = Depends(require("bed:manage")),
                     s: AsyncSession = Depends(get_session)):
    b = await s.scalar(select(Bed).where(Bed.id == bed_id, Bed.tenant_id == p.tenant_id))
    if b is None:
        raise HTTPException(404, "Bed not found.")
    try:
        b = await set_bed_status(s, p.tenant_id, b.ward, b.bed, body.status, f"{p.name} ({p.role})")
    except IntegrationError as exc:
        raise HTTPException(409, str(exc)) from exc
    await s.commit()
    outbound.notify(request.app.state.sessionmaker, p.tenant_id, "his", "bed.status_changed", {"ward": b.ward, "bed": b.bed, "status": b.status})
    pt = await s.scalar(select(Patient).where(Patient.tenant_id == p.tenant_id, Patient.ref == b.patient_ref)) if b.patient_ref else None
    return _bed_out(b, pt, p)
