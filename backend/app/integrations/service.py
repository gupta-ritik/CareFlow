"""Inbound integration processing, shared by the MLLP listener, the HL7-over-HTTP endpoint and the JSON connectors.

Every inbound message:
  1. is de-duplicated on its control ID (HL7 MSH-10, or the connector's eventId),
  2. updates CareFlow's patient / department / bed records exactly like a staff click would (with history),
  3. emits the department's update event, so the agents re-check that department (through Redis Streams when
     available, otherwise inline), and
  4. is logged in `integration_messages` with its outcome.

Rules worth knowing:
  - Pharmacy orders only start the discharge-medicine clock when they arrive after discharge authorization, or when
    they're marked as discharge orders (HL7 ORC-29 = "O", or the connector's `order_placed` event). Inpatient orders
    during the stay don't count.
  - Lab orders during the stay do count: a report must be final and acknowledged before discharge.
  - Lab results: only report status is read (OBR-25). Result values are never read.
"""
from __future__ import annotations

import logging
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import departments as D
from ..events import make_event, publish
from ..models import Bed, IntegrationMessage, Patient, SimDepartmentRecord, Tenant, Workflow, utcnow
from ..sim import upsert_record
from . import hl7

log = logging.getLogger("careflow.integrations")
OPEN = ("in_progress", "blocked", "awaiting_approval", "cleared")
BED_STATUSES = ("available", "occupied", "cleaning", "blocked")


class IntegrationError(ValueError):
    pass


@dataclass
class Bus:
    """Where department-update events go: Redis Streams (workers) if available, else the engine inline."""

    redis: Any = None
    engine: Any = None

    async def emit(self, tenant: Tenant, etype: str, patient_ref: str, payload: dict, event_id: str | None = None) -> str:
        event = make_event(etype, tenant.slug, patient_ref, payload, event_id)
        if self.redis is not None:
            await publish(self.redis, event)
            return "queued"
        if self.engine is not None:
            return await self.engine.handle_event(tenant.id, event)
        return "not_dispatched"


@dataclass
class Outcome:
    status: str = "accepted"  # accepted | duplicate | warning | error | rejected
    detail: str = ""
    patient_ref: str | None = None
    events: list[tuple[str, dict]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        self.notes.append(text)

    @property
    def summary(self) -> str:
        return "; ".join([*([self.detail] if self.detail else []), *self.notes])[:2000]


# ---- lookups -------------------------------------------------------------------------------------------
def _mrn_variants(mrn: str) -> list[str]:
    m = (mrn or "").strip()
    bare = m[4:].strip() if m.upper().startswith("MRN ") else m
    return list(dict.fromkeys([m, bare, f"MRN {bare}"]))


async def find_patient(s: AsyncSession, tenant_id: uuid.UUID, mrn: str | None = None, ref: str | None = None) -> Patient | None:
    if ref:
        pt = await s.scalar(select(Patient).where(Patient.tenant_id == tenant_id, Patient.ref == ref))
        if pt:
            return pt
    if mrn:
        return await s.scalar(select(Patient).where(Patient.tenant_id == tenant_id, Patient.mrn.in_(_mrn_variants(mrn))))
    return None


async def open_workflow(s: AsyncSession, pt: Patient) -> Workflow | None:
    return await s.scalar(select(Workflow).where(Workflow.patient_id == pt.id, Workflow.status.in_(OPEN), Workflow.deleted_at.is_(None)))


async def resolve_tenant(s: AsyncSession, slug: str | None) -> Tenant:
    if slug:
        t = await s.scalar(select(Tenant).where(Tenant.slug == slug))
        if t is None:
            raise IntegrationError(f"Unknown hospital {slug}")
        return t
    t = await s.scalar(select(Tenant).order_by(Tenant.created_at).limit(1))
    if t is None:
        raise IntegrationError("CareFlow isn't set up yet")
    return t


# ---- beds ------------------------------------------------------------------------------------------------
async def occupy_bed(s: AsyncSession, tenant_id: uuid.UUID, ward: str, bed: str, patient_ref: str, by: str) -> None:
    ward, bed = (ward or "").strip(), (bed or "").strip()
    held = (await s.scalars(select(Bed).where(Bed.tenant_id == tenant_id, Bed.patient_ref == patient_ref))).all()
    for b in held:
        if (b.ward, b.bed) != (ward, bed):
            b.status, b.patient_ref, b.updated_by = "cleaning", None, by
    if not ward or not bed:
        await s.flush()
        return
    row = await s.scalar(select(Bed).where(Bed.tenant_id == tenant_id, Bed.ward == ward, Bed.bed == bed))
    if row is None:
        row = Bed(tenant_id=tenant_id, ward=ward, bed=bed)
        s.add(row)
    row.status, row.patient_ref, row.updated_by, row.updated_at = "occupied", patient_ref, by, utcnow()
    await s.flush()


async def release_bed(s: AsyncSession, tenant_id: uuid.UUID, patient_ref: str, by: str) -> list[Bed]:
    """Patient has left: the bed goes to housekeeping (cleaning) until someone marks it available."""
    rows = (await s.scalars(select(Bed).where(Bed.tenant_id == tenant_id, Bed.patient_ref == patient_ref))).all()
    for b in rows:
        b.status, b.patient_ref, b.updated_by, b.updated_at = "cleaning", None, by, utcnow()
    await s.flush()
    return list(rows)


# ---- patients -------------------------------------------------------------------------------------------
async def admit_or_update(s: AsyncSession, tenant_id: uuid.UUID, f: hl7.PatientFields, source: str, by: str, out: Outcome) -> Patient:
    pt = await find_patient(s, tenant_id, f.mrn)
    if pt is None:
        mrn = f.mrn if f.mrn.upper().startswith("MRN") else f"MRN {f.mrn}"
        pt = Patient(tenant_id=tenant_id, ref=f"pt-{uuid.uuid4().hex[:10]}", encounter_ref=f.visit_number or f"enc-{uuid.uuid4().hex[:10]}",
                     display_name=f.name, mrn=mrn, ward=f.ward or "Unassigned", bed=f.bed, payer=f.payer or "Self-pay", payer_type=f.payer_type,
                     attending=f.attending or "Attending not recorded", admitted_at=f.admitted_at or utcnow(), has_lab_orders=False,
                     consent_for_coordination=True, source=source)
        s.add(pt)
        await s.flush()
        for dept, data in D.initial_records(f.payer_type, False).items():
            await upsert_record(s, tenant_id, pt.ref, dept, data)
        out.note(f"admitted {pt.display_name} ({pt.mrn})")
    elif pt.status == "discharged":
        # Readmission: same MRN, new encounter. Department records start fresh.
        pt.status, pt.admitted_at = "admitted", f.admitted_at or utcnow()
        pt.encounter_ref = f.visit_number or f"enc-{uuid.uuid4().hex[:10]}"
        _apply_demographics(pt, f)
        pt.has_lab_orders = False
        for dept, data in D.initial_records(pt.payer_type, False).items():
            await upsert_record(s, tenant_id, pt.ref, dept, data)
        out.note(f"readmitted {pt.mrn}")
    else:
        changed = _apply_demographics(pt, f)
        out.note(f"updated {pt.mrn}" + (f" ({', '.join(changed)})" if changed else " (no changes)"))
    await occupy_bed(s, tenant_id, pt.ward, pt.bed, pt.ref, by)
    out.patient_ref = pt.ref
    return pt


def _apply_demographics(pt: Patient, f: hl7.PatientFields) -> list[str]:
    changed = []
    for attr, value in (("display_name", f.name), ("ward", f.ward), ("bed", f.bed), ("attending", f.attending)):
        if value and getattr(pt, attr) != value:
            setattr(pt, attr, value)
            changed.append(attr.replace("display_name", "name"))
    if f.payer and pt.payer != f.payer:
        pt.payer = f.payer
        changed.append("payer")
    return changed


# ---- department records ---------------------------------------------------------------------------------
async def _record(s: AsyncSession, tenant_id: uuid.UUID, ref: str, dept: str) -> SimDepartmentRecord | None:
    return await s.scalar(select(SimDepartmentRecord).where(
        SimDepartmentRecord.tenant_id == tenant_id, SimDepartmentRecord.patient_ref == ref, SimDepartmentRecord.department == dept))


async def _mutate(s: AsyncSession, tenant_id: uuid.UUID, ref: str, dept: str, actor: str, action: str, fn) -> str | None:
    row = await _record(s, tenant_id, ref, dept)
    if row is None:
        raise IntegrationError(f"This patient has no {dept} record")
    data = deepcopy(row.data)
    summary = fn(data)
    if not summary:
        return None
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    data.setdefault("history", []).append({"at": data["updated_at"], "by": actor, "action": action, "summary": summary})
    data["history"] = data["history"][-50:]
    row.data = data
    await s.flush()
    return summary


async def staff_action(s: AsyncSession, tenant_id: uuid.UUID, pt: Patient, dept: str, action: str, params: dict, actor: str, out: Outcome) -> None:
    """Same code path as a staff click in the work queue."""
    if await _record(s, tenant_id, pt.ref, dept) is None:
        raise IntegrationError(f"{pt.mrn} has no {dept} record (check payment type / lab orders)")
    try:
        _, summary = await D.apply_action(s, tenant_id, pt.ref, dept, action, params, actor)
    except D.ActionError as exc:
        raise IntegrationError(str(exc)) from exc
    out.note(f"{dept}: {summary}")
    out.events.append((D.DEPT_EVENT[dept], {"department": dept, "by": actor, "summary": summary}))


async def pharmacy_order(s: AsyncSession, tenant_id: uuid.UUID, pt: Patient, at: datetime | None, actor: str, out: Outcome,
                         discharge_order: bool) -> None:
    if not discharge_order and await open_workflow(s, pt) is None:
        out.note("inpatient pharmacy order before discharge authorization: not tracked")
        return
    when = (at or datetime.now(timezone.utc)).isoformat()

    def fn(d):
        d["ordered_at"] = when
        if d.get("status") in (None, "NOT_STARTED", "DONE"):
            d["status"] = "IN_PROGRESS"
        return f"Discharge medicines ordered ({when[:16].replace('T', ' ')} UTC)"

    summary = await _mutate(s, tenant_id, pt.ref, "pharmacy", actor, "order_placed", fn)
    if summary:
        out.note(f"pharmacy: {summary}")
        out.events.append((D.DEPT_EVENT["pharmacy"], {"department": "pharmacy", "by": actor, "summary": summary}))


async def ensure_lab(s: AsyncSession, tenant_id: uuid.UUID, pt: Patient) -> None:
    if await _record(s, tenant_id, pt.ref, "laboratory") is None:
        pt.has_lab_orders = True
        await upsert_record(s, tenant_id, pt.ref, "laboratory", D.initial_records(pt.payer_type, True)["laboratory"])


async def lab_report(s: AsyncSession, tenant_id: uuid.UUID, pt: Patient, report_id: str, actor: str, out: Outcome,
                     final: bool | None = None, cancel: bool = False, acknowledge: bool = False) -> None:
    await ensure_lab(s, tenant_id, pt)

    def fn(d):
        reports = d.setdefault("reports", [])
        r = next((x for x in reports if x["id"] == report_id), None)
        if cancel:
            if r and r.get("status") != "final":
                reports.remove(r)
                return f"Lab order {report_id} cancelled"
            return None
        if r is None:
            r = {"id": report_id, "status": "final" if final else "pending", "acknowledged": False}
            reports.append(r)
            if not acknowledge:
                return f"Report {report_id} added ({r['status']})"
        if acknowledge:
            if r["status"] != "final":
                raise IntegrationError(f"Report {report_id} isn't final yet")
            if r.get("acknowledged"):
                return None
            r["acknowledged"] = True
            return f"Report {report_id} acknowledged by the ward"
        if final is not None:
            new = "final" if final else "pending"
            if r["status"] == new:
                return None
            r["status"] = new
            if new == "final":
                r["acknowledged"] = False  # a corrected result needs a fresh acknowledgement
            return f"Report {report_id} {new}"
        return None

    summary = await _mutate(s, tenant_id, pt.ref, "laboratory", actor, "lab_update", fn)
    if summary:
        out.note(f"laboratory: {summary}")
        out.events.append((D.DEPT_EVENT["laboratory"], {"department": "laboratory", "by": actor, "summary": summary}))


async def his_discharge(s: AsyncSession, tenant_id: uuid.UUID, pt: Patient, actor: str, out: Outcome) -> None:
    """The HIS says the patient has left. Release the bed; close the CareFlow workflow if it's cleared."""
    await release_bed(s, tenant_id, pt.ref, actor)
    wf = await open_workflow(s, pt)
    if wf is None:
        if pt.status == "admitted":
            out.status = "warning"
            out.note("discharged in the HIS without a CareFlow discharge authorization; marked discharged")
            pt.status = "discharged"
        return
    if wf.status == "cleared":
        out.events.append(("PATIENT_DISCHARGED", {"discharged_by": actor}))
        out.note(f"closed {wf.code}")
    else:
        out.status = "warning"
        out.note(f"HIS discharged the patient while {wf.code} is still {wf.status.replace('_', ' ')}; left open for staff to review")


# ---- logging --------------------------------------------------------------------------------------------
async def is_duplicate(s: AsyncSession, tenant_id: uuid.UUID, system: str, control_id: str) -> bool:
    if not control_id:
        return False
    return bool(await s.scalar(select(IntegrationMessage.id).where(
        IntegrationMessage.tenant_id == tenant_id, IntegrationMessage.direction == "inbound", IntegrationMessage.system == system,
        IntegrationMessage.control_id == control_id, IntegrationMessage.status.in_(("accepted", "warning"))).limit(1)))


async def log_inbound(sm, tenant_id, system: str, protocol: str, message_type: str, control_id: str, out: Outcome, raw: str) -> None:
    async with sm() as s:
        s.add(IntegrationMessage(tenant_id=tenant_id, direction="inbound", system=system, protocol=protocol, message_type=message_type[:64],
                                 control_id=(control_id or "")[:128], patient_ref=out.patient_ref, status=out.status, detail=out.summary, raw=raw[:20000]))
        await s.commit()


async def _dispatch(bus: Bus, tenant: Tenant, out: Outcome, control_id: str) -> None:
    if not out.patient_ref:
        return
    for i, (etype, payload) in enumerate(out.events):
        try:
            res = await bus.emit(tenant, etype, out.patient_ref, payload, f"int-{control_id}-{i}"[:64] if control_id else None)
            if res not in ("queued", "processed", "deferred", "ignored", "duplicate"):
                out.note(f"{etype}: {res}")
        except Exception as exc:  # the record update is saved; the re-check can be retried with Recheck
            log.exception("dispatch failed")
            out.status = "warning"
            out.note(f"{etype} not dispatched: {exc}")


# ---- HL7 v2 ---------------------------------------------------------------------------------------------
SUPPORTED = {"ADT^A01", "ADT^A04", "ADT^A02", "ADT^A08", "ADT^A03", "ORM^O01", "OML^O21", "RDE^O11", "RDS^O13", "ORU^R01"}


async def handle_hl7(sm, bus: Bus, tenant: Tenant, text: str, actor_system: str = "HIS") -> tuple[str, Outcome]:
    """Process one HL7 v2 message; returns (ACK text, outcome)."""
    try:
        msg = hl7.parse(text)
    except hl7.Hl7Error as exc:
        out = Outcome("rejected", f"Unparseable message: {exc}")
        await log_inbound(sm, tenant.id, "his", "hl7v2", "unknown", "", out, text or "")
        return hl7.ack(None, "AR", str(exc), tenant.name), out

    mtype, ctrl = msg.message_type, msg.control_id
    base_type = "^".join(mtype.split("^")[:2])
    system = {"ORU": "lis", "OML": "lis", "RDE": "pharmacy", "RDS": "pharmacy"}.get(base_type.split("^")[0], "his")
    actor = f"{msg.sending_app or actor_system} (HL7 {base_type})"
    out = Outcome()

    async with sm() as s:
        if await is_duplicate(s, tenant.id, system, ctrl):
            out = Outcome("duplicate", f"Message {ctrl} was already processed")
            await log_inbound(sm, tenant.id, system, "hl7v2", base_type, ctrl, out, msg.raw)
            return hl7.ack(msg, "AA", "Duplicate; already processed", tenant.name), out
        if base_type not in SUPPORTED:
            out = Outcome("rejected", f"{base_type} isn't handled by CareFlow")
            await log_inbound(sm, tenant.id, system, "hl7v2", base_type, ctrl, out, msg.raw)
            return hl7.ack(msg, "AR", out.detail, tenant.name), out
        try:
            f = hl7.patient_fields(msg)
            if base_type in ("ADT^A01", "ADT^A04"):
                await admit_or_update(s, tenant.id, f, "hl7", actor, out)
            else:
                pt = await find_patient(s, tenant.id, f.mrn)
                if pt is None:
                    if base_type == "ADT^A08":
                        await admit_or_update(s, tenant.id, f, "hl7", actor, out)
                        pt = None
                    else:
                        raise IntegrationError(f"Unknown patient MRN {f.mrn}; send ADT^A01 first")
                if pt is not None:
                    out.patient_ref = pt.ref
                    if base_type in ("ADT^A02", "ADT^A08"):
                        changed = _apply_demographics(pt, f)
                        await occupy_bed(s, tenant.id, pt.ward, pt.bed, pt.ref, actor)
                        out.note(f"{'transferred' if base_type == 'ADT^A02' else 'updated'} {pt.mrn}: {', '.join(changed) or 'no changes'}")
                    elif base_type == "ADT^A03":
                        await his_discharge(s, tenant.id, pt, actor, out)
                    else:
                        await _orders(s, tenant.id, msg, pt, actor, out)
            await s.commit()
        except (IntegrationError, hl7.Hl7Error) as exc:
            await s.rollback()
            out = Outcome("error", str(exc), out.patient_ref)
            await log_inbound(sm, tenant.id, system, "hl7v2", base_type, ctrl, out, msg.raw)
            return hl7.ack(msg, "AE", str(exc), tenant.name), out

    await _dispatch(bus, tenant, out, ctrl)
    await log_inbound(sm, tenant.id, system, "hl7v2", base_type, ctrl, out, msg.raw)
    return hl7.ack(msg, "AA", out.summary[:200], tenant.name), out


async def _orders(s: AsyncSession, tenant_id: uuid.UUID, msg: hl7.Message, pt: Patient, actor: str, out: Outcome) -> None:
    items = hl7.orders(msg)
    if not items:
        out.note("no pharmacy or lab orders in the message")
        return
    orc = msg.first("ORC")
    discharge_flag = (msg.value(orc, 29) or "").upper() == "O"
    for o in items:
        if o.kind == "pharmacy":
            if o.control in ("CA", "DC", "OC"):
                out.note(f"pharmacy order {o.order_id} cancelled (no change to the discharge task)")
            elif o.status == "CM":
                await staff_action(s, tenant_id, pt, "pharmacy", "mark_ready", {}, actor, out)
            else:
                await pharmacy_order(s, tenant_id, pt, o.at, actor, out, discharge_order=discharge_flag)
        else:
            if o.control in ("CA", "DC", "OC"):
                await lab_report(s, tenant_id, pt, o.order_id, actor, out, cancel=True)
            else:
                await lab_report(s, tenant_id, pt, o.order_id, actor, out, final=o.status.upper() in hl7.FINAL_RESULT)


# ---- JSON connectors ------------------------------------------------------------------------------------
CONNECTOR_EVENTS: dict[str, dict[str, str]] = {
    "tpa": {
        "preauth_submitted": "Final authorization request submitted to the payer",
        "query_raised": "Payer asked for a document (data.document)",
        "document_available": "A requested document is available for the claim (data.document)",
        "approved": "Payer approved final authorization",
        "denied": "Payer denied (data.reason)",
    },
    "pharmacy": {
        "order_placed": "Discharge medicines ordered (data.orderedAt, optional)",
        "pharmacist_assigned": "Pharmacist assigned (data.pharmacist)",
        "dispensed": "Discharge medicines prepared",
        "reconciled": "Medication reconciliation completed",
    },
    "lis": {
        "report_registered": "Lab order/report registered, not final (data.reportId)",
        "report_final": "Report final (data.reportId)",
        "report_acknowledged": "Ward acknowledged the final report (data.reportId)",
        "order_cancelled": "Lab order cancelled (data.reportId)",
    },
    "his": {
        "admitted": "Admit or update a patient (data: name, ward, bed, attending, payer, payerType, admittedAt)",
        "transferred": "Patient moved (data.ward, data.bed)",
        "discharged": "Patient has left the hospital",
        "bed_status": "Set a bed's status (data.ward, data.bed, data.status)",
    },
}

TPA_ACTIONS = {"preauth_submitted": ("submit", ()), "query_raised": ("record_query", ("document",)),
               "document_available": ("add_document", ("document",)), "approved": ("record_approval", ()), "denied": ("record_denial", ("reason",))}
PHARMACY_ACTIONS = {"pharmacist_assigned": ("assign", ("pharmacist",)), "dispensed": ("mark_ready", ()), "reconciled": ("complete_reconciliation", ())}


def _need(data: dict, key: str) -> str:
    v = str(data.get(key) or "").strip()
    if not v:
        raise IntegrationError(f"data.{key} is required")
    return v[:200]


async def handle_connector(sm, bus: Bus, tenant: Tenant, system: str, body: dict, actor_name: str) -> Outcome:
    event = str(body.get("event") or "").strip()
    data = body.get("data") or {}
    event_id = str(body.get("eventId") or "")[:100]
    mrn, ref = body.get("mrn"), body.get("patientRef")
    out = Outcome()
    actor = f"{actor_name} ({system.upper()} connector)"
    raw = str(body)[:20000]
    if event not in CONNECTOR_EVENTS.get(system, {}):
        out = Outcome("rejected", f"Unknown {system} event {event!r}. Supported: {', '.join(CONNECTOR_EVENTS.get(system, {}))}")
        await log_inbound(sm, tenant.id, system, "webhook", event or "unknown", event_id, out, raw)
        return out
    async with sm() as s:
        if await is_duplicate(s, tenant.id, system, event_id):
            out = Outcome("duplicate", f"Event {event_id} was already processed")
            await log_inbound(sm, tenant.id, system, "webhook", event, event_id, out, raw)
            return out
        try:
            if system == "his" and event == "bed_status":
                await set_bed_status(s, tenant.id, _need(data, "ward"), _need(data, "bed"), _need(data, "status"), actor)
                out.note(f"bed {data['ward']}/{data['bed']} -> {data['status']}")
            elif system == "his" and event == "admitted":
                payer_type = str(data.get("payerType") or "self_pay")
                if payer_type not in ("self_pay", "cashless", "government"):
                    raise IntegrationError("data.payerType must be self_pay, cashless or government")
                f = hl7.PatientFields(mrn=str(mrn or "").strip() or _need(data, "mrn"), name=_need(data, "name"), ward=str(data.get("ward") or ""),
                                      bed=str(data.get("bed") or ""), attending=str(data.get("attending") or ""), payer=str(data.get("payer") or ""),
                                      payer_type=payer_type, admitted_at=_iso(data.get("admittedAt")), visit_number=str(data.get("encounter") or ""))
                await admit_or_update(s, tenant.id, f, "hl7", actor, out)
            else:
                pt = await find_patient(s, tenant.id, mrn, ref)
                if pt is None:
                    raise IntegrationError("Patient not found; send mrn or patientRef of an admitted patient")
                out.patient_ref = pt.ref
                if pt.status != "admitted" and event != "discharged":
                    raise IntegrationError("This patient has been discharged")
                if system == "tpa":
                    action, keys = TPA_ACTIONS[event]
                    await staff_action(s, tenant.id, pt, "insurance", action, {k: _need(data, k) for k in keys}, actor, out)
                elif system == "pharmacy":
                    if event == "order_placed":
                        await pharmacy_order(s, tenant.id, pt, _iso(data.get("orderedAt")), actor, out, discharge_order=True)
                    else:
                        action, keys = PHARMACY_ACTIONS[event]
                        await staff_action(s, tenant.id, pt, "pharmacy", action, {k: _need(data, k) for k in keys}, actor, out)
                elif system == "lis":
                    rid = _need(data, "reportId")
                    if event == "report_registered":
                        await lab_report(s, tenant.id, pt, rid, actor, out, final=False)
                    elif event == "report_final":
                        await lab_report(s, tenant.id, pt, rid, actor, out, final=True)
                    elif event == "report_acknowledged":
                        await lab_report(s, tenant.id, pt, rid, actor, out, acknowledge=True)
                    else:
                        await lab_report(s, tenant.id, pt, rid, actor, out, cancel=True)
                elif event == "transferred":
                    pt.ward, pt.bed = _need(data, "ward"), str(data.get("bed") or "")
                    await occupy_bed(s, tenant.id, pt.ward, pt.bed, pt.ref, actor)
                    out.note(f"moved to {pt.ward} {pt.bed}".strip())
                elif event == "discharged":
                    await his_discharge(s, tenant.id, pt, actor, out)
            if not out.notes:
                out.note("no change")
            await s.commit()
        except IntegrationError as exc:
            await s.rollback()
            out = Outcome("error", str(exc), out.patient_ref)
            await log_inbound(sm, tenant.id, system, "webhook", event, event_id, out, raw)
            return out
    await _dispatch(bus, tenant, out, event_id or uuid.uuid4().hex[:12])
    await log_inbound(sm, tenant.id, system, "webhook", event, event_id, out, raw)
    return out


def _iso(v) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError as exc:
        raise IntegrationError(f"Invalid date {v!r}; use ISO 8601") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def set_bed_status(s: AsyncSession, tenant_id: uuid.UUID, ward: str, bed: str, status: str, by: str) -> Bed:
    if status not in BED_STATUSES:
        raise IntegrationError(f"Bed status must be one of {', '.join(BED_STATUSES)}")
    row = await s.scalar(select(Bed).where(Bed.tenant_id == tenant_id, Bed.ward == ward, Bed.bed == bed))
    if row is None:
        row = Bed(tenant_id=tenant_id, ward=ward, bed=bed, status="available")
        s.add(row)
    if status == "occupied" and not row.patient_ref:
        raise IntegrationError("A bed becomes occupied by admitting or transferring a patient into it")
    if status != "occupied" and row.patient_ref:
        pt = await find_patient(s, tenant_id, ref=row.patient_ref)
        if pt is not None and pt.status == "admitted":
            raise IntegrationError(f"Bed {ward}/{bed} is occupied by an admitted patient; transfer or discharge them first")
        row.patient_ref = None
    row.status, row.updated_by, row.updated_at = status, by, utcnow()
    await s.flush()
    return row
