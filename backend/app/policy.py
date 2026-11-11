"""Action catalog and policy check.

LLM agents propose; this module decides. An action must be in the catalog, target the department of the blocker
it addresses, carry valid parameters, and cite evidence. Anything clinical or financial (authorizing discharge,
prescribing, approving claims, recording payments) is not in the catalog, so it can never be executed.
"""
from dataclasses import dataclass

from .security import COORDINATOR, INSURANCE_LEAD


@dataclass(frozen=True)
class CatalogAction:
    name: str
    departments: tuple[str, ...]
    risk: str  # low | medium | high
    approver_role: str | None  # None = runs without a reviewer
    required_params: tuple[str, ...]
    description: str


CATALOG: dict[str, CatalogAction] = {
    a.name: a
    for a in [
        CatalogAction("notify_department", ("documentation", "pharmacy", "laboratory", "insurance", "billing"), "low", None, ("message",), "Send a reminder to the department queue"),
        CatalogAction("create_followup_task", ("documentation", "pharmacy", "laboratory", "insurance", "billing"), "low", None, ("title",), "Create a follow-up task for the department"),
        CatalogAction("create_document_task", ("documentation",), "low", None, ("doc_id", "missing"), "Ask the responsible person to complete a document"),
        CatalogAction("request_lab_acknowledgement", ("laboratory",), "low", None, ("report_ids",), "Ask the lab desk to acknowledge final reports"),
        CatalogAction("escalate_pharmacy_task", ("pharmacy",), "medium", COORDINATOR, ("task_id",), "Escalate a pharmacy task to the duty pharmacist"),
        CatalogAction("retry_portal_submission", ("insurance",), "medium", INSURANCE_LEAD, (), "Retry submission to the payer portal"),
        CatalogAction("attach_document_to_claim", ("insurance",), "high", INSURANCE_LEAD, ("claim_id", "document"), "Attach an existing document to an insurance claim"),
    ]
}

# Never executable, whatever an agent or a document says. Kept as a list so tests can assert it.
FORBIDDEN = (
    "authorize_discharge",
    "mark_medically_fit",
    "prescribe_medication",
    "modify_medication",
    "approve_claim",
    "record_payment",
    "modify_invoice",
)


@dataclass
class PolicyResult:
    allowed: bool
    reason: str
    risk: str = "high"
    requires_approval: bool = True
    approver_role: str = COORDINATOR


def check(action: str, department: str, params: dict, evidence_ids: list[str], known_evidence: set[str]) -> PolicyResult:
    if action in FORBIDDEN:
        return PolicyResult(False, f"{action} is a clinical or financial decision and is never automated")
    spec = CATALOG.get(action)
    if spec is None:
        return PolicyResult(False, f"{action} is not in the action catalog")
    if department not in spec.departments:
        return PolicyResult(False, f"{action} can't act on {department}")
    missing = [p for p in spec.required_params if not params.get(p)]
    if missing:
        return PolicyResult(False, f"{action} is missing parameters: {', '.join(missing)}")
    cited = [e for e in evidence_ids if e in known_evidence]
    if not cited:
        return PolicyResult(False, f"{action} cites no evidence that was actually retrieved")
    return PolicyResult(
        True,
        "allowed",
        risk=spec.risk,
        requires_approval=spec.approver_role is not None,
        approver_role=spec.approver_role or "None (low risk)",
    )


def candidates_for(department: str) -> list[str]:
    return [a.name for a in CATALOG.values() if department in a.departments]
