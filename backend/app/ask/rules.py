"""Deterministic rules: the guard, question understanding, and rule-based plans.

These run first (the guard) or as the fallback when no LLM is configured or the LLM's output fails validation. They
cover the common question types: "why slow on <date>", "slowest ward/department/TPA", "TPA turnaround", "trend of
discharge time", "did X get faster after day N", "is X slower on Mondays / weekend afternoons", payer queries and
incomplete documents.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ..redaction import looks_like_injection
from . import periods
from .measures import DEPARTMENTS
from .schemas import Compare, Period, Step

CAN_ANSWER = ("I can answer questions about discharge operations: discharge times, department clearance times, blockers, "
              "payer turnaround and queries, approval times, bed occupancy and agent runs, always as totals for the hospital.")

SUGGESTIONS = [
    "Why were discharges slow last Monday?",
    "Which department is slowest this month?",
    "Which TPA is slowest in the last 30 days?",
    "What is the trend of discharge time over the last 90 days?",
    "Which ward has the most incomplete documents?",
    "Did billing get faster after day 90?",
]


# ---- guard ---------------------------------------------------------------------------------------------------------
@dataclass
class GuardResult:
    allowed: bool
    category: str = "ok"
    message: str = ""
    injection: bool = False
    unsure: bool = False


_IDENTITY = re.compile(
    r"\b(which|what|who|list|show|give|tell me|name|find)\b(?:\s+(?:me|us|all|the|of|those|these|individual|specific|\w+est)){0,3}\s+patients?\b"
    r"|\bpatient'?s?\s+(names?|details|identity|phone|address|mrn|contact)"
    r"|\b(names?|mrns?|phone numbers?|mobile numbers?|addresses|emails?|aadhaa?r|abha|contact details)\b"
    r"|\bsynthetic patient \d+|\bSYN-\d+|\bwho (was|were|is) (the )?(slowest|patient)", re.I)
_CLINICAL = re.compile(
    r"\b(diagnos\w*|prognos\w*|symptoms?|dosage|dose of|prescri\w*|treatment for|treat\w* plan|lab results?|test results?|"
    r"blood (?:test|report|pressure|sugar)|side effects?|what (medicine|medication|drug)s?|which (medicine|medication|drug)s?|"
    r"clinical (advice|condition|decision)|is it safe to discharge|should .* be discharged|medically fit)\b", re.I)
_OTHER_HOSPITAL = re.compile(
    r"\b(other|another|different|neighbou?ring|competitor'?s?|all|every)\s+hospitals?\b|\bcompare\s+(us|our hospital)\s+(with|to|against)\b"
    r"|\b(other|another)\s+tenants?\b|\btenant_id\b", re.I)
_WRITE = re.compile(
    r"^\s*(please\s+|can you\s+|could you\s+|kindly\s+)?(delete|remove|update|change|set|approve|reject|close|discharge|cancel|mark|"
    r"assign|add|create|insert|drop|edit|reset|modify|escalate|send|notify)\b"
    r"|\b(insert into|update \w+ set|delete from|drop table)\b", re.I)
_PERSONISH = re.compile(r"\b(patient|doctor|nurse|clinician|pharmacist|person|people|who)\b", re.I)


def guard(question: str) -> GuardResult:
    q = question.strip()
    if looks_like_injection(q):
        return GuardResult(False, "injection", "This question contains instructions aimed at the assistant, so I won't run it. " + CAN_ANSWER, injection=True)
    if _WRITE.search(q):
        return GuardResult(False, "write", "Ask CareFlow only reads totals; it can't change any data. Make changes on the workflow board. " + CAN_ANSWER)
    if _IDENTITY.search(q):
        return GuardResult(False, "identity", "I can't identify patients or show names, MRNs or contact details. I can show totals instead, "
                                               "for example how many discharges were slow and in which department.")
    if _CLINICAL.search(q):
        return GuardResult(False, "clinical", "I don't answer clinical questions (diagnoses, medicines, results or fitness for discharge). " + CAN_ANSWER)
    if _OTHER_HOSPITAL.search(q):
        return GuardResult(False, "other_hospital", "I can only see your hospital's data, not other hospitals'. " + CAN_ANSWER)
    return GuardResult(True, unsure=bool(_PERSONISH.search(q)))


# ---- understanding ------------------------------------------------------------------------------------------------
TPA_NAMES = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta"]
DOCS = {"itemi[sz]ed (pharmacy )?bill": "itemised_pharmacy_bill", "discharge summar": "discharge_summary",
        "investigation report": "investigation_reports", "id proof|identity proof": "id_proof"}
UNANSWERABLE = re.compile(r"\b(staffing|staff shortage|short[- ]staffed|understaffed|roster|rota|shifts?|holiday|festival|weather|rain|"
                          r"strike|on leave|absent|sick leave|morale|salary|salaries|lazy|traffic)\b", re.I)


CHANGE_WORDS = re.compile(r"\b(get|got|become|became|getting)\s+(faster|slower|better|worse|quicker)|\bimprov|\bchanged?\b|before and after", re.I)
_DAYNAME = {w[:3]: w.capitalize() for w in periods.WEEKDAYS}


@dataclass
class Understanding:
    measure: str | None = None
    intent: str = "describe"         # why_slow | ranking | change | focus_vs_rest | describe | trend
    group_by: list[str] = field(default_factory=list)
    filters: dict[str, list[str]] = field(default_factory=dict)
    focus: dict[str, list[str]] = field(default_factory=dict)
    focus_label: str = ""
    period: periods.Resolved | None = None
    baseline: tuple[date, date, str] | None = None
    rank: str = "highest"
    clarify: str | None = None
    unanswerable: str | None = None

    def as_dict(self) -> dict:
        p = self.period
        return {"measure": self.measure, "intent": self.intent, "group_by": self.group_by, "filters": self.filters, "focus": self.focus,
                "focus_label": self.focus_label, "rank": self.rank, "clarify": self.clarify, "unanswerable": self.unanswerable,
                "period": {"start": p.start.isoformat(), "end": p.end.isoformat(), "label": p.label, "default": p.default} if p else None,
                "baseline": {"start": self.baseline[0].isoformat(), "end": self.baseline[1].isoformat(), "label": self.baseline[2]} if self.baseline else None}


def _has(q: str, pattern: str) -> bool:
    return re.search(pattern, q, re.I) is not None


def _tpa(q: str) -> list[str]:
    has_tpa = re.search(r"\btpas?\b", q, re.I)
    return [f"TPA {n}" for n in TPA_NAMES if re.search(rf"\btpa\s+{n}\b", q, re.I) or (has_tpa and re.search(rf"\b{n}\b", q, re.I))]


def _wards(q: str) -> list[str]:
    return [m.upper() for m in re.findall(r"\bward\s+(\d[a-z])\b", q, re.I)] or [m.upper() for m in re.findall(r"\b([1-9][ab])\b", q, re.I)]


def _depts(q: str) -> list[str]:
    found = []
    for d, pat in (("documentation", r"documentation|discharge summar|paperwork"), ("pharmacy", r"pharmacy|medicines?"),
                   ("laboratory", r"\blab(oratory)?\b"), ("insurance", r"\binsurance\b"), ("billing", r"billing|\bbills?\b(?! itemi)")):
        if re.search(pat, q, re.I):
            found.append(d)
    return found


def _weekdays(q: str) -> list[str]:
    if _has(q, r"\bweekends?\b"):
        return ["sat", "sun"]
    if _has(q, r"\bweekdays\b"):
        return ["mon", "tue", "wed", "thu", "fri"]
    return [w[:3] for w in periods.WEEKDAYS if re.search(rf"\b{w}s\b", q, re.I)]  # plural = a pattern ("on Mondays")


def _hours(q: str) -> tuple[list[str], str]:
    if m := re.search(r"\b(\d{1,2})(?::00)?\s*(?:-|–|to|and)\s*(\d{1,2})(?::00)?\b", q):
        a, b = int(m[1]), int(m[2])
        if 0 <= a < b <= 24 and (":" in m[0] or "between" in q.lower()):
            return [str(h) for h in range(a, b)], f"{a:02d}:00-{b:02d}:00"
    for word, (a, b) in {"mornings?": (7, 12), "afternoons?": (12, 18), "evenings?": (18, 22), "nights?": (22, 24)}.items():
        if _has(q, rf"\b{word}\b"):
            return [str(h) for h in range(a, b)], f"{a:02d}:00-{b:02d}:00"
    return [], ""


def _measure(q: str) -> str | None:
    if _has(q, r"\bstaff updates?\b|\bupdates by staff\b"):
        return "staff_updates"
    if _has(q, r"\b(llm|fallback|ai runs?|agent runs?|rule-based)\b"):
        return "llm_runs"
    if _has(q, r"\b(beds?|occupancy)\b"):
        return "bed_occupancy"
    if _has(q, r"which documents|what documents|documents? (do|did) (payers|tpas?) (ask|request)"):
        return "requested_documents"
    if _has(q, r"\bquer(y|ies)\b|\basks? for\b|\brequests? for\b|itemi[sz]ed"):
        return "payer_query_rate"
    if _has(q, r"incomplete doc|missing (attending )?signature|unsigned|missing field|documentation (backlog|problems?)|incomplete"):
        return "blocker_rate"
    if _has(q, r"\bblockers?\b"):
        return "blocker_duration" if _has(q, r"\b(how long|duration|last|take)\b") else "blocker_count"
    payer_words = r"\btpas?\b|\bpayers?\b|\binsurers?\b|turnaround|final (authori[sz]ation|approval)|pre-?auth"
    if not _has(q, payer_words) and _has(q, r"\bapprovals?\b.*\b(take|time|long|slow|fast|decide)|\bdecision time\b"):
        return "approval_decision_time"
    if _has(q, payer_words):
        return "tpa_turnaround" if not _depts(q) or _tpa(q) or _has(q, r"\btpas?\b|payer") else "department_clearance_time"
    if _depts(q) or _has(q, r"\bdepartments?\b"):
        return "department_clearance_time"
    if _has(q, r"discharg|slow|fast|long|delay|quick|time|trend|wait"):
        return "discharge_time"
    return None


BLOCKER_WORDS = {r"incomplete doc|missing (attending )?signature|unsigned|missing field|documentation": "document_incomplete",
                 r"pharmacy delay": "pharmacy_delay", r"payer quer": "payer_query", r"payer delay": "payer_delay",
                 r"billing delay": "billing_delay", r"lab(oratory)? (report|acknowledg)": "lab_unacknowledged"}


def understand(question: str, today: date, data_start: date | None, data_end: date | None) -> Understanding:
    q = question.lower()
    u = Understanding()
    if UNANSWERABLE.search(q):
        u.unanswerable = ("CareFlow's data doesn't show why: it has no staff rosters, shifts, leave, holidays or weather. It can show "
                          "when and where time was lost, for example which department or payer was slower.")
        return u
    u.measure = _measure(q)
    u.period = periods.resolve(question, today, data_start, data_end)
    if u.period and u.period.baseline:
        u.baseline = u.period.baseline

    # Filters.
    tpas, wards, depts, wds = _tpa(question), _wards(question), _depts(q), _weekdays(q)
    hours, hours_label = _hours(q)
    if tpas and u.measure in ("tpa_turnaround", "payer_query_rate", "requested_documents", "discharge_time", "department_clearance_time"):
        u.filters["tpa"] = tpas
    if wards:
        u.filters["ward"] = wards
    if u.measure == "department_clearance_time" and depts:
        u.filters["department"] = depts[:1] if len(depts) > 1 and "department" not in q else depts
    for pat, doc in DOCS.items():
        if re.search(pat, q) and u.measure in ("payer_query_rate", "requested_documents"):
            u.filters["document"] = [doc]
    if u.measure in ("blocker_rate", "blocker_count", "blocker_duration"):
        for pat, kind in BLOCKER_WORDS.items():
            if re.search(pat, q):
                u.filters["blocker_kind"] = [kind]
                break
    for pt, pat in (("cashless", r"\bcashless\b"), ("government", r"\bgovernment|scheme\b"), ("self_pay", r"\bself[- ]pay")):
        if re.search(pat, q):
            u.filters.setdefault("payer_type", []).append(pt)

    # Intent.
    rank_dim = None
    for dim, pat in (("ward", r"\bwards?\b"), ("department", r"\bdepartments?\b"), ("tpa", r"\btpas?\b|\bpayers?\b|\binsurers?\b"),
                     ("weekday", r"day of (the )?week|\bweekdays?\b"), ("hour", r"time of day|\bhours?\b"),
                     ("approver_role", r"\bapprovers?\b|\broles?\b"), ("document", r"\bdocuments?\b"), ("blocker_kind", r"kind of blocker|blocker kinds?")):
        if re.search(pat, q):
            rank_dim = rank_dim or dim
    # "Which weekday is slowest for TPA Gamma": the word after which/what is what to rank, even if a TPA is named too.
    if m := re.search(r"\b(?:which|what)\s+(wards?|departments?|tpas?|payers?|insurers?|weekdays?|days? of the week|hours?|time of day|"
                      r"approvers?|roles?|documents?)\b", q):
        w = m[1]
        rank_dim = ("ward" if w.startswith("ward") else "department" if w.startswith("department") else
                    "tpa" if w.startswith(("tpa", "payer", "insurer")) else "weekday" if w.startswith(("weekday", "day")) else
                    "hour" if w.startswith(("hour", "time")) else "approver_role" if w.startswith(("approver", "role")) else "document")
    ranking = re.search(r"\b(which|what|slowest|fastest|most|least|highest|lowest|worst|best|top|longest|shortest|rank)\b", q)
    if re.search(r"\b(fastest|least|lowest|best|shortest|quickest)\b", q):
        u.rank = "lowest"

    if u.measure is None:
        u.clarify = "Which do you want to know about: discharge time, a department's clearance time, payer turnaround, or blockers?"
        return u

    if re.search(r"\bwhy\b", q) and re.search(r"slow|long|delay|late|bad|high", q):
        u.intent = "why_slow"
        if u.period is None:
            u.clarify = "Which day or period do you mean? For example, \"last Monday\" or \"29 August\"."
            return u
    elif re.search(r"\btrend\b|over time|\bdaily\b|day by day|each day", q):
        u.intent = "trend"
        u.group_by = ["day"]
    elif (wds or hours) and rank_dim not in ("weekday", "hour") and not CHANGE_WORDS.search(q):
        u.intent = "focus_vs_rest"
        u.baseline = None  # "since day 60" only sets the period; the comparison is these days against the others
        if wds:
            u.focus["weekday"] = wds
        if hours:
            u.focus["hour"] = hours
        u.focus_label = " ".join(x for x in (("weekends" if wds == ["sat", "sun"] else ", ".join(f"{_DAYNAME[w]}s" for w in wds)) if wds else "",
                                              hours_label) if x)
    elif ranking and rank_dim and rank_dim in _dims_of(u.measure) and not CHANGE_WORDS.search(q):
        u.intent = "ranking"
        u.group_by = [rank_dim]
        u.baseline = None  # "since day 60" only sets the period
    elif u.baseline or CHANGE_WORDS.search(q):
        u.intent = "change"
    elif (tpas or wards) and re.search(r"\b(more|less|slower|faster|often|longer|higher|lower)\b", q):
        u.intent = "focus_vs_rest"
        dim, vals = ("tpa", u.filters.pop("tpa")) if tpas and "tpa" in u.filters else ("ward", u.filters.pop("ward"))
        u.focus[dim] = vals
        u.focus_label = ", ".join(vals) if dim == "tpa" else ", ".join(f"Ward {w}" for w in vals)
    elif ranking and u.measure == "department_clearance_time" and "department" not in u.filters:
        u.intent = "ranking"
        u.group_by = ["department"]
    if u.measure == "department_clearance_time" and u.intent in ("describe", "trend", "change", "focus_vs_rest") and "department" not in u.filters:
        u.group_by = u.group_by or ["department"] if u.intent == "describe" else u.group_by
    if u.measure == "bed_occupancy":
        u.intent, u.group_by = "ranking" if ranking else "describe", ["ward"] if ranking or "ward" in q else []
    if u.intent == "change" and u.baseline is None and u.period is not None and not u.period.default:
        u.baseline = periods.baseline_before(u.period.start, u.period.end)
    if u.period is None:
        u.period = periods.default_period(today) if data_end is None else periods.default_period(min(today, data_end + _one_day()))
    return u


def _one_day():
    from datetime import timedelta
    return timedelta(days=1)


def _dims_of(measure: str) -> tuple[str, ...]:
    from .measures import REGISTRY
    return REGISTRY[measure].dimensions


# ---- rule-based plans ---------------------------------------------------------------------------------------------
def _p(start: date, end: date, label: str) -> Period:
    return Period(start=start, end=end, label=label)


def rule_plan(u: Understanding) -> list[Step]:
    assert u.measure and u.period
    period = _p(u.period.start, u.period.end, u.period.label)
    filters = {k: v for k, v in u.filters.items()}
    if u.intent == "why_slow":
        b = u.baseline or periods.baseline_before(u.period.start, u.period.end)
        cmp = Compare(kind="period", period=_p(b[0], b[1], b[2]), focus_label=u.period.label, baseline_label=b[2])
        steps = [Step(measure="discharge_time", filters={k: v for k, v in filters.items() if k in ("ward", "payer_type", "tpa")},
                      period=period, compare=cmp, why="Checking discharge times")]
        steps.append(Step(measure="department_clearance_time", group_by=["department"],
                          filters={k: v for k, v in filters.items() if k in ("ward", "payer_type", "tpa", "department")},
                          period=period, compare=cmp, why="Splitting by department"))
        return steps
    if u.intent == "ranking":
        return [Step(measure=u.measure, group_by=u.group_by, filters=filters, period=period, rank=u.rank,
                     why=f"Comparing {u.group_by[0].replace('_', ' ')}s")]
    if u.intent == "change":
        b = u.baseline or periods.baseline_before(u.period.start, u.period.end)
        return [Step(measure=u.measure, filters=filters, period=period, group_by=[g for g in u.group_by if g != "day"],
                     compare=Compare(kind="period", period=_p(b[0], b[1], b[2]), focus_label=u.period.label, baseline_label=b[2]),
                     why="Comparing before and after")]
    if u.intent == "focus_vs_rest":
        rest = "other days" if set(u.focus) == {"weekday"} else "other times" if set(u.focus) & {"weekday", "hour"} else "the others"
        return [Step(measure=u.measure, filters=filters, period=period,
                     compare=Compare(kind="rest", focus=u.focus, focus_label=u.focus_label, baseline_label=rest),
                     why=f"Comparing {u.focus_label} with {rest}")]
    if u.intent == "trend":
        b = periods.baseline_before(u.period.start, u.period.end)
        half = u.period.start + (u.period.end - u.period.start) // 2
        return [Step(measure=u.measure, filters=filters, period=period, group_by=["day"], why="Checking each day"),
                Step(measure=u.measure, filters=filters, period=_p(half + _one_day(), u.period.end, f"second half ({periods.span_label(half + _one_day(), u.period.end)})"),
                     compare=Compare(kind="period", period=_p(u.period.start, half, f"first half ({periods.span_label(u.period.start, half)})"),
                                     focus_label="the second half", baseline_label="the first half"),
                     why="Comparing the first and second half")] if (u.period.end - u.period.start).days >= 13 else \
               [Step(measure=u.measure, filters=filters, period=period, group_by=["day"], why="Checking each day"),
                Step(measure=u.measure, filters=filters, period=period, compare=Compare(kind="period", period=_p(b[0], b[1], b[2]),
                     focus_label=u.period.label, baseline_label=b[2]), why="Comparing with the period before")]
    # describe
    if u.measure == "bed_occupancy":
        return [Step(measure=u.measure, group_by=u.group_by, filters=filters, period=period, why="Checking beds")]
    b = periods.baseline_before(u.period.start, u.period.end)
    return [Step(measure=u.measure, group_by=u.group_by, filters=filters, period=period,
                 compare=Compare(kind="period", period=_p(b[0], b[1], b[2]), focus_label=u.period.label, baseline_label=b[2]),
                 why=f"Checking {u.measure.replace('_', ' ')}")]


DEEPEN_BY = {"insurance": "tpa", "billing": "payer_type", "documentation": "ward", "pharmacy": "hour", "laboratory": "ward"}


def deepen(step: Step, department: str) -> Step | None:
    """The analyst's rule-based next step: split the slow department by the dimension most likely to explain it."""
    dim = DEEPEN_BY.get(department)
    if dim is None:
        return None
    return Step(measure="department_clearance_time", group_by=[dim], filters={**{k: v for k, v in step.filters.items() if k != "department"},
                "department": [department]}, period=step.period, compare=step.compare, why=f"Splitting {department} by {dim.replace('_', ' ')}")
