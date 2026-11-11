"""Department checks: deterministic reads of each department system.

Each check returns a `DeptResult`: a status, the evidence it observed (exact query and value), and any issues,
each pointing at evidence by index. No LLM is involved. Clinical content is never interpreted; the lab check only
looks at report status and acknowledgement.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..config import Settings
from ..redaction import looks_like_injection


@dataclass
class Observation:
    source: str
    query: str
    observed: str
    untrusted: bool = False


@dataclass
class Issue:
    kind: str
    summary: str
    evidence: list[int]  # indexes into DeptResult.evidence
    waiting_on: str | None = None
    params: dict = field(default_factory=dict)


@dataclass
class DeptResult:
    department: str
    status: str  # cleared | in_progress | blocked | not_started
    owner: str
    title: str
    source_updated_at: str
    evidence: list[Observation] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    raw_status: str = ""
    error: str | None = None
    flags: list[str] = field(default_factory=list)


TITLES = {
    "documentation": "Discharge summary and forms complete",
    "pharmacy": "Discharge medicines prepared and reconciled",
    "laboratory": "Pending reports acknowledged",
    "insurance": "Final authorization from payer",
    "billing": "Final bill prepared",
}
OWNERS = {
    "documentation": "Ward clerk desk",
    "pharmacy": "Inpatient pharmacy",
    "laboratory": "Lab front office",
    "insurance": "TPA desk",
    "billing": "Billing counter",
}


def _minutes_since(iso: str | None, now: datetime) -> int:
    if not iso:
        return 0
    parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0, int((now - parsed).total_seconds() // 60))


def duration(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m" if m else f"{h}h"


def _base(dept: str, data: dict) -> DeptResult:
    return DeptResult(dept, "in_progress", data.get("owner", OWNERS[dept]), TITLES[dept], data.get("updated_at", ""))


def check_pharmacy(ref: str, data: dict, s: Settings, now: datetime) -> DeptResult:
    r = _base("pharmacy", data)
    tid, status = data.get("task_id"), data.get("status", "NOT_STARTED")
    r.raw_status = status
    assignee = data.get("assigned_to") or "no pharmacist assigned"
    r.evidence.append(Observation("Pharmacy system", f"get_pharmacy_tasks(patient={ref})", f"Task {tid} status {status}, {assignee}"))
    age = _minutes_since(data.get("ordered_at"), now)
    r.evidence.append(Observation("Pharmacy system", f"get_medication_workflow_status(task={tid})",
                                  f"Ordered {duration(age)} ago; reconciliation form {data.get('reconciliation', 'DRAFT')}; escalations {len(data.get('escalations', []))}"))
    if status == "DONE" and data.get("reconciliation") == "DONE":
        r.status = "cleared"
        return r
    if data.get("reconciliation") != "DONE" and status == "DONE":
        r.issues.append(Issue("reconciliation_incomplete", "Medication reconciliation form not completed", [1], params={"task_id": tid}))
    if status != "DONE" and age > s.sla_pharmacy:
        r.issues.append(Issue("pharmacy_delay", f"Discharge medicines not prepared {duration(age)} after the order was placed", [0, 1], params={"task_id": tid}))
    r.status = "blocked" if r.issues else "in_progress"
    return r


def check_documentation(ref: str, data: dict, s: Settings, now: datetime) -> DeptResult:
    r = _base("documentation", data)
    doc = data.get("doc_id")
    required, present = data.get("required", []), set(data.get("present", []))
    missing = [f for f in required if f not in present]
    r.raw_status = "COMPLETE" if not missing else "INCOMPLETE"
    r.evidence.append(Observation(
        "Document system", f"validate_document(doc={doc})",
        f"{len(required) - len(missing)} of {len(required)} required fields present" + (f"; missing {', '.join(missing)}" if missing else ""),
    ))
    note = data.get("note")
    if note:
        injected = looks_like_injection(note)
        r.evidence.append(Observation("Document system", f"get_documents(doc={doc}, part=note)", note[:300], untrusted=injected))
        if injected:
            r.flags.append("document_text_contains_instructions")
    if not missing:
        r.status = "cleared"
        return r
    if data.get("in_review"):
        r.status = "in_progress"
        return r
    label = ", ".join(m.replace("_", " ") for m in missing)
    r.issues.append(Issue("document_incomplete", f"Discharge summary is missing: {label}", [0], params={"doc_id": doc, "missing": missing}))
    r.status = "blocked"
    return r


def check_insurance(ref: str, data: dict, s: Settings, now: datetime) -> DeptResult:
    r = _base("insurance", data)
    claim, status = data.get("claim_id"), data.get("status", "NOT_SUBMITTED")
    r.raw_status = status
    age = _minutes_since(data.get("submitted_at"), now)
    r.evidence.append(Observation("Insurance system", f"get_authorization_status(claim={claim})",
                                  f"Final authorization {status}" + (f", submitted {duration(age)} ago" if data.get("submitted_at") else "")))
    if status == "APPROVED":
        r.status = "cleared"
        return r
    if status == "DENIED":
        reason = data.get("denial_reason") or "no reason recorded"
        r.issues.append(Issue("payer_denied", f"Payer denied final authorization ({reason}); the payment route must change", [0], params={"claim_id": claim}))
        r.status = "blocked"
        return r
    if status == "QUERY_RAISED":
        doc = data.get("requested_document", "document")
        available = doc in data.get("available_documents", [])
        r.evidence.append(Observation("Insurance system", f"get_insurance_status(claim={claim})", f"Query raised by payer: {doc.replace('_', ' ')} required"))
        r.evidence.append(Observation("Document system", f"get_documents(type={doc})",
                                      f"{doc.replace('_', ' ').capitalize()} {'exists, not attached to the claim' if available else 'does not exist yet'}"))
        summary = f"Payer asked for the {doc.replace('_', ' ')}; " + ("it exists but has not been sent" if available else "it has not been prepared")
        r.issues.append(Issue("payer_query", summary, [0, 1, 2], params={"claim_id": claim, "document": doc if available else "", "available": available}))
    elif status == "PENDING_PAYER" and age > s.sla_insurance:
        r.issues.append(Issue("payer_delay", f"Payer has not sent final authorization {duration(age)} after submission", [0], params={"claim_id": claim}))
    elif status == "NOT_SUBMITTED":
        r.issues.append(Issue("not_submitted", "Final authorization request has not been submitted to the payer", [0], params={"claim_id": claim}))
    r.status = "blocked" if r.issues else "in_progress"
    return r


def check_billing(ref: str, data: dict, s: Settings, now: datetime) -> DeptResult:
    r = _base("billing", data)
    status = data.get("status", "NOT_STARTED")
    r.raw_status = status
    age = _minutes_since(data.get("updated_at"), now)
    r.evidence.append(Observation("Billing system", f"get_billing_status(bill={data.get('bill_id')})", f"Final bill {status}, last update {duration(age)} ago"))
    if status == "FINAL":
        r.status = "cleared"
    elif status == "PREPARING" and age > s.sla_billing:
        r.issues.append(Issue("billing_delay", f"Final bill still in preparation {duration(age)} after the last update", [0]))
        r.status = "blocked"
    else:
        r.status = "not_started" if status == "NOT_STARTED" else "in_progress"
    return r


def check_laboratory(ref: str, data: dict, s: Settings, now: datetime) -> DeptResult:
    r = _base("laboratory", data)
    reports = data.get("reports", [])
    final_unack = [x["id"] for x in reports if x.get("status") == "final" and not x.get("acknowledged")]
    pending = [x["id"] for x in reports if x.get("status") != "final"]
    r.raw_status = "PENDING" if pending else ("UNACKNOWLEDGED" if final_unack else "ACKNOWLEDGED")
    r.evidence.append(Observation("Lab system", f"get_lab_report_status(patient={ref})",
                                  f"{len(reports)} reports: {len(pending)} not final, {len(final_unack)} final and unacknowledged"))
    if not pending and not final_unack:
        r.status = "cleared"
    elif final_unack:
        r.issues.append(Issue("lab_unacknowledged", f"{len(final_unack)} final lab report(s) not acknowledged by the ward", [0], params={"report_ids": final_unack}))
        r.status = "blocked"
    return r


CHECKS = {
    "documentation": check_documentation,
    "pharmacy": check_pharmacy,
    "laboratory": check_laboratory,
    "insurance": check_insurance,
    "billing": check_billing,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
