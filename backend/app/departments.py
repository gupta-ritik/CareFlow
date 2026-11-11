"""Department work: what each department records, and the actions its staff take.

When a patient is admitted, CareFlow creates one working record per department that will be involved. Staff
update those records from their work queue; every update is recorded in the record's history and sends the
department's update event, so the agents re-check that department. The same records can instead be fed by a
hospital system through /api/events (integration).

CareFlow's own coordination actions (reminders, escalations, document requests) are appended to the same record
and shown to staff in their queue as "Requests from CareFlow".
"""
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import SimDepartmentRecord

DEPARTMENTS = ("documentation", "pharmacy", "laboratory", "insurance", "billing")

DOC_FIELDS = [
    "diagnosis_summary", "procedures", "discharge_medicines_list", "follow_up_plan", "diet_advice", "warning_signs",
    "contact_numbers", "investigations_summary", "condition_at_discharge", "attending_name", "registration_number",
    "attending_signature",
]

DEPT_EVENT = {
    "pharmacy": "PHARMACY_TASK_UPDATED",
    "billing": "BILLING_STATUS_UPDATED",
    "insurance": "INSURANCE_AUTHORIZATION_UPDATED",
    "laboratory": "LAB_REPORT_AVAILABLE",
    "documentation": "DOCUMENT_UPDATED",
}

# Lists CareFlow's executor appends to (see agents/graph.py `_mutation`), shown to staff as requests.
REQUEST_LISTS = {
    "notifications": "Reminder",
    "escalations": "Escalated to duty pharmacist",
    "tasks": "Document request",
    "ack_requests": "Acknowledgement request",
    "followups": "Follow-up",
    "attachments": "Document attached to claim",
    "submissions": "Resubmitted to payer",
}


class ActionError(ValueError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short() -> str:
    return uuid.uuid4().hex[:6].upper()


def initial_records(payer_type: str, has_lab_orders: bool) -> dict[str, dict]:
    recs: dict[str, dict] = {
        "documentation": {"doc_id": f"DS-{_short()}", "required": list(DOC_FIELDS), "present": [], "in_review": False},
        "pharmacy": {"task_id": f"PH-{_short()}", "status": "NOT_STARTED", "ordered_at": None, "assigned_to": None,
                     "reconciliation": "DRAFT", "escalations": []},
        "billing": {"bill_id": f"BIL-{_short()}", "status": "NOT_STARTED"},
    }
    if payer_type != "self_pay":
        recs["insurance"] = {"claim_id": f"CL-{_short()}", "status": "NOT_SUBMITTED", "submitted_at": None,
                             "available_documents": [], "requested_document": None}
    if has_lab_orders:
        recs["laboratory"] = {"reports": []}
    now = now_iso()
    for r in recs.values():
        r["updated_at"] = now
        r["history"] = []
    return recs


# ----- staff actions ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class StaffAction:
    name: str
    label: str
    params: tuple[str, ...]
    apply: Callable[[dict, dict], str]  # mutates the record, returns a one-line description


def _need(params: dict, key: str) -> str:
    value = str(params.get(key) or "").strip()
    if not value:
        raise ActionError(f"'{key}' is required")
    return value[:200]


def _doc_fields(d: dict, p: dict) -> str:
    fields = p.get("present")
    if not isinstance(fields, list) or any(f not in d["required"] for f in fields):
        raise ActionError("present must be a list of required document fields")
    d["present"] = sorted(set(fields), key=d["required"].index)
    d["in_review"] = False
    return f"{len(d['present'])} of {len(d['required'])} required fields complete"


def _doc_review(d: dict, p: dict) -> str:
    d["in_review"] = bool(p.get("in_review", True))
    return "Marked in review" if d["in_review"] else "Review finished"


def _ph_assign(d: dict, p: dict) -> str:
    d["assigned_to"] = _need(p, "pharmacist")
    if d["status"] == "NOT_STARTED":
        d["status"] = "IN_PROGRESS"
    return f"Assigned to {d['assigned_to']}"


def _ph_ready(d: dict, p: dict) -> str:
    d["status"] = "DONE"
    return "Discharge medicines prepared"


def _ph_recon(d: dict, p: dict) -> str:
    d["reconciliation"] = "DONE"
    return "Medication reconciliation completed"


def _lab_add(d: dict, p: dict) -> str:
    rid = _need(p, "report_id")
    if any(r["id"] == rid for r in d["reports"]):
        raise ActionError(f"Report {rid} is already listed")
    d["reports"].append({"id": rid, "status": "final" if p.get("final") else "pending", "acknowledged": False})
    return f"Report {rid} added ({'final' if p.get('final') else 'pending'})"


def _lab_report(d: dict, rid: str) -> dict:
    for r in d["reports"]:
        if r["id"] == rid:
            return r
    raise ActionError(f"No report {rid}")


def _lab_final(d: dict, p: dict) -> str:
    r = _lab_report(d, _need(p, "report_id"))
    r["status"] = "final"
    return f"Report {r['id']} final"


def _lab_ack(d: dict, p: dict) -> str:
    r = _lab_report(d, _need(p, "report_id"))
    if r["status"] != "final":
        raise ActionError(f"Report {r['id']} isn't final yet")
    r["acknowledged"] = True
    return f"Report {r['id']} acknowledged by the ward"


def _ins_submit(d: dict, p: dict) -> str:
    d.update(status="PENDING_PAYER", submitted_at=now_iso(), requested_document=None)
    return "Final authorization request submitted to payer"


def _ins_query(d: dict, p: dict) -> str:
    doc = _need(p, "document").lower().replace(" ", "_")
    d.update(status="QUERY_RAISED", requested_document=doc)
    return f"Payer query recorded: {doc.replace('_', ' ')} requested"


def _ins_add_doc(d: dict, p: dict) -> str:
    doc = _need(p, "document").lower().replace(" ", "_")
    if doc not in d["available_documents"]:
        d["available_documents"].append(doc)
    return f"{doc.replace('_', ' ').capitalize()} available for the claim"


def _ins_approve(d: dict, p: dict) -> str:
    d.update(status="APPROVED")
    return "Payer approved final authorization"


def _ins_deny(d: dict, p: dict) -> str:
    d.update(status="DENIED", denial_reason=_need(p, "reason"))
    return f"Payer denied: {d['denial_reason']}"


def _ins_self_pay(d: dict, p: dict) -> str:
    d.update(status="APPROVED", converted_to_self_pay=True)
    return "Converted to self-pay; insurance no longer blocks billing"


def _bill_start(d: dict, p: dict) -> str:
    d["status"] = "PREPARING"
    return "Final bill preparation started"


def _bill_final(d: dict, p: dict) -> str:
    d["status"] = "FINAL"
    return "Final bill ready"


ACTIONS: dict[str, dict[str, StaffAction]] = {
    dept: {a.name: a for a in acts}
    for dept, acts in {
        "documentation": [
            StaffAction("update_fields", "Update completed fields", ("present",), _doc_fields),
            StaffAction("set_review", "Mark in review", ("in_review",), _doc_review),
        ],
        "pharmacy": [
            StaffAction("assign", "Assign pharmacist", ("pharmacist",), _ph_assign),
            StaffAction("mark_ready", "Medicines ready", (), _ph_ready),
            StaffAction("complete_reconciliation", "Reconciliation complete", (), _ph_recon),
        ],
        "laboratory": [
            StaffAction("add_report", "Add report", ("report_id", "final"), _lab_add),
            StaffAction("mark_final", "Mark final", ("report_id",), _lab_final),
            StaffAction("acknowledge", "Ward acknowledged", ("report_id",), _lab_ack),
        ],
        "insurance": [
            StaffAction("submit", "Submit to payer", (), _ins_submit),
            StaffAction("record_query", "Record payer query", ("document",), _ins_query),
            StaffAction("add_document", "Document available", ("document",), _ins_add_doc),
            StaffAction("record_approval", "Payer approved", (), _ins_approve),
            StaffAction("record_denial", "Payer denied", ("reason",), _ins_deny),
            StaffAction("convert_self_pay", "Convert to self-pay", (), _ins_self_pay),
        ],
        "billing": [
            StaffAction("start", "Start final bill", (), _bill_start),
            StaffAction("finalize", "Final bill ready", (), _bill_final),
        ],
    }.items()
}


async def get_record(s: AsyncSession, tenant_id: uuid.UUID, patient_ref: str, dept: str) -> SimDepartmentRecord | None:
    return await s.scalar(
        select(SimDepartmentRecord).where(
            SimDepartmentRecord.tenant_id == tenant_id, SimDepartmentRecord.patient_ref == patient_ref, SimDepartmentRecord.department == dept
        )
    )


async def apply_action(s: AsyncSession, tenant_id: uuid.UUID, patient_ref: str, dept: str, action: str, params: dict, actor: str) -> tuple[dict, str]:
    spec = ACTIONS.get(dept, {}).get(action)
    if spec is None:
        raise ActionError(f"'{action}' isn't an action for {dept}")
    row = await get_record(s, tenant_id, patient_ref, dept)
    if row is None:
        raise ActionError(f"This patient has no {dept} record")
    data = deepcopy(row.data)
    data.pop("_fault", None)
    summary = spec.apply(data, params or {})
    data["updated_at"] = now_iso()
    data.setdefault("history", []).append({"at": data["updated_at"], "by": actor, "action": action, "summary": summary})
    data["history"] = data["history"][-50:]
    row.data = data
    await s.flush()
    return data, summary


def requests_from_careflow(data: dict) -> list[dict]:
    out = []
    for key, label in REQUEST_LISTS.items():
        for e in data.get(key, []):
            text = e.get("message") or e.get("title") or ", ".join(e.get("missing", []) or e.get("report_ids", [])) or e.get("document") or e.get("to") or ""
            out.append({"ref": e.get("ref"), "at": e.get("at"), "kind": label, "text": str(text).replace("_", " ")})
    return sorted(out, key=lambda x: x["at"] or "", reverse=True)


def public_record(data: dict) -> dict:
    """What staff see: the record without internal keys or CareFlow's request lists."""
    return {k: v for k, v in data.items() if not k.startswith("_") and k not in REQUEST_LISTS}
