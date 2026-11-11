"""Outbound notifications to hospital systems.

- JSON webhooks to the HIS, TPA/payer gateway, pharmacy and LIS (one URL each, all optional). Each request carries
  `X-CareFlow-Signature: sha256=<hex HMAC of the body>` when CAREFLOW_OUTBOUND_SECRET is set, so the receiver can
  verify it came from CareFlow.
- HL7 v2 ADT^A03 over MLLP to the HIS when CareFlow closes a discharge.

Delivery is best-effort with retries and never blocks the user's request; every attempt is logged in
`integration_messages` and shown on the Integrations page.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone

import httpx

from ..config import get_settings
from ..models import IntegrationMessage
from . import hl7

log = logging.getLogger("careflow.outbound")
SYSTEMS = ("his", "tpa", "pharmacy", "lis")
_tasks: set[asyncio.Task] = set()

# CareFlow coordination actions -> (system, event) worth telling the department system about.
ACTION_EVENTS = {
    "escalate_pharmacy_task": ("pharmacy", "task.escalated"),
    "attach_document_to_claim": ("tpa", "claim.document_attached"),
    "retry_portal_submission": ("tpa", "claim.resubmitted"),
    "request_lab_acknowledgement": ("lis", "report.acknowledgement_requested"),
}
# Staff actions in CareFlow's queues -> outbound event.
STAFF_EVENTS = {
    ("insurance", "submit"): ("tpa", "preauth.final_submitted"),
    ("insurance", "add_document"): ("tpa", "claim.document_available"),
    ("insurance", "convert_self_pay"): ("tpa", "claim.withdrawn_self_pay"),
    ("pharmacy", "mark_ready"): ("pharmacy", "discharge_medicines.ready"),
    ("pharmacy", "complete_reconciliation"): ("pharmacy", "reconciliation.completed"),
    ("laboratory", "acknowledge"): ("lis", "report.acknowledged"),
    ("billing", "finalize"): ("his", "final_bill.ready"),
}


def url_for(system: str) -> str | None:
    return getattr(get_settings(), f"{system}_webhook_url", None)


def configured() -> dict[str, bool]:
    s = get_settings()
    out = {sys: bool(url_for(sys)) for sys in SYSTEMS}
    out["hl7"] = bool(s.hl7_outbound_host)
    return out


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def schedule(coro) -> None:
    """Run in the background; keep a reference so the task isn't garbage-collected."""
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:  # no loop (CLI); just drop it
        coro.close()
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _log(sm, tenant_id, **kw) -> None:
    try:
        async with sm() as s:
            s.add(IntegrationMessage(tenant_id=tenant_id, direction="outbound", **kw))
            await s.commit()
    except Exception:  # logging must never break delivery
        log.exception("couldn't record outbound message")


async def deliver_webhook(sm, tenant_id, system: str, event: str, data: dict, patient_ref: str | None = None,
                          attempts: int = 3, backoff: float = 1.0) -> bool:
    url = url_for(system)
    if not url:
        return False
    s = get_settings()
    msg_id = f"cfo-{uuid.uuid4().hex[:16]}"
    body = json.dumps({"id": msg_id, "event": event, "occurredAt": datetime.now(timezone.utc).isoformat(), "data": data},
                      default=str, separators=(",", ":")).encode()
    headers = {"content-type": "application/json", "user-agent": "CareFlow-Integrations/1.0", "x-careflow-event": event, "x-careflow-id": msg_id}
    if s.outbound_secret:
        headers["x-careflow-signature"] = sign(body, s.outbound_secret)
    detail, ok, tries = "", False, 0
    async with httpx.AsyncClient(timeout=s.outbound_timeout_seconds) as client:
        for tries in range(1, attempts + 1):
            try:
                r = await client.post(url, content=body, headers=headers)
                ok = 200 <= r.status_code < 300
                detail = f"HTTP {r.status_code}"
                if ok or 400 <= r.status_code < 500 and r.status_code not in (408, 429):
                    break  # success, or a client error retrying won't fix
            except httpx.HTTPError as exc:
                detail = f"{type(exc).__name__}: {exc}"[:300]
            await asyncio.sleep(backoff * tries)
    await _log(sm, tenant_id, system=system, protocol="webhook", message_type=event, control_id=msg_id, patient_ref=patient_ref,
               status="sent" if ok else "failed", detail=detail, raw=body.decode()[:20000], attempts=tries)
    return ok


async def mllp_send(host: str, port: int, text: str, timeout: float = 10.0) -> str:
    """Send one framed HL7 message and return the ACK text."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    try:
        writer.write(hl7.frame(text))
        await writer.drain()
        data = await asyncio.wait_for(reader.readuntil(hl7.EB + hl7.CR), timeout)
        return data.strip(hl7.SB + hl7.EB + hl7.CR).decode("utf-8", "replace")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def deliver_hl7(sm, tenant_id, text: str, patient_ref: str | None = None, attempts: int = 3, backoff: float = 1.0) -> bool:
    s = get_settings()
    if not s.hl7_outbound_host:
        return False
    msg = hl7.parse(text)
    detail, ok, tries = "", False, 0
    for tries in range(1, attempts + 1):
        try:
            reply = hl7.parse(await mllp_send(s.hl7_outbound_host, s.hl7_outbound_port, text, s.outbound_timeout_seconds))
            code = reply.value("MSA", 1)
            ok, detail = code in ("AA", "CA"), f"ACK {code} {reply.value('MSA', 3)}".strip()
            if ok or code in ("AR", "CR"):
                break
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, hl7.Hl7Error) as exc:
            detail = f"{type(exc).__name__}: {exc}"[:300]
        await asyncio.sleep(backoff * tries)
    await _log(sm, tenant_id, system="his", protocol="hl7v2", message_type=msg.message_type, control_id=msg.control_id, patient_ref=patient_ref,
               status="sent" if ok else "failed", detail=detail, raw=text[:20000], attempts=tries)
    return ok


def patient_payload(pt) -> dict:
    return {"patientRef": pt.ref, "mrn": pt.mrn, "name": pt.display_name, "ward": pt.ward, "bed": pt.bed, "encounter": pt.encounter_ref,
            "payer": pt.payer, "payerType": pt.payer_type}


def notify(sm, tenant_id, system: str, event: str, data: dict, patient_ref: str | None = None) -> None:
    if url_for(system):
        schedule(deliver_webhook(sm, tenant_id, system, event, data, patient_ref))


def notify_discharge(sm, tenant_id, facility: str, pt, wf) -> None:
    """Patient has left: ADT^A03 to the HIS (MLLP) and a webhook to the HIS."""
    data = {**patient_payload(pt), "workflowId": wf.code, "dischargedAt": wf.discharged_at or datetime.now(timezone.utc)}
    notify(sm, tenant_id, "his", "patient.discharged", data, pt.ref)
    if get_settings().hl7_outbound_host:
        text = hl7.adt_a03(facility=facility, mrn=pt.mrn, name=pt.display_name, ward=pt.ward, bed=pt.bed, attending=pt.attending,
                           visit_number=pt.encounter_ref, admitted_at=pt.admitted_at, discharged_at=wf.discharged_at or datetime.now(timezone.utc))
        schedule(deliver_hl7(sm, tenant_id, text, pt.ref))
