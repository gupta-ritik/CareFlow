"""Minimum-necessary data for LLM calls and traces.

Agents never receive patient names, MRNs, phone numbers or emails. `redact` scrubs free text, and
`known_identifiers` lets callers scrub the exact identity values of the patient in context.
"""
import re

_PATTERNS = [
    (re.compile(r"\bMRN[\s:#-]*\d{4,}\b", re.I), "[MRN]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "[EMAIL]"),
    (re.compile(r"(?<!\w)(?:\+91[\s-]?)?[6-9]\d{9}\b"), "[PHONE]"),
    (re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"), "[ID-NUMBER]"),  # Aadhaar-like 12-digit numbers
    (re.compile(r"\b\d{2}-\d{4}-\d{4}-\d{4}\b"), "[ABHA]"),  # ABHA number format
]


def redact(text: str, identifiers: list[str] | None = None) -> str:
    out = text
    for value in sorted(identifiers or [], key=len, reverse=True):
        if value and len(value) >= 3:
            out = re.sub(re.escape(value), "[PATIENT]", out, flags=re.I)
    for pattern, token in _PATTERNS:
        out = pattern.sub(token, out)
    return out


def redact_obj(obj, identifiers: list[str] | None = None):
    if isinstance(obj, str):
        return redact(obj, identifiers)
    if isinstance(obj, list):
        return [redact_obj(v, identifiers) for v in obj]
    if isinstance(obj, dict):
        return {k: redact_obj(v, identifiers) for k, v in obj.items()}
    return obj


def known_identifiers(display_name: str, mrn: str) -> list[str]:
    parts = [p for p in display_name.split() if len(p) >= 3]
    digits = re.sub(r"\D", "", mrn)
    return [display_name, mrn, digits, *parts]


# Phrases that indicate text is trying to instruct the agent. Retrieved and document text is data:
# a hit marks the evidence untrusted and is logged, and the policy layer still decides every action.
_INJECTION = re.compile(
    r"(ignore (all |any )?(previous|prior) instructions|you are now|system prompt|"
    r"disregard (the )?(rules|policy)|approve (the|this) (claim|payment|discharge)|"
    r"mark (the )?patient (as )?(fit|discharged)|authori[sz]e discharge)",
    re.I,
)


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION.search(text))
