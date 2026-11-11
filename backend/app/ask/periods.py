"""Turn the time words in a question into exact local dates (deterministic; the LLM never computes dates).

All dates are local to the hospital (CAREFLOW_TIMEZONE). A period is inclusive: start and end are both included.
"day N" / "week N" count from the first day of the hospital's data (day 0), which is how the synthetic scenarios and
the planted-truth file describe their patterns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
_MON = {m[:3]: i + 1 for i, m in enumerate(MONTHS)}
DEFAULT_DAYS = 90


@dataclass
class Resolved:
    start: date
    end: date
    label: str
    baseline: tuple[date, date, str] | None = None  # implied comparison, e.g. "after day 90" -> before day 90
    default: bool = False


def fmt(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1][:3].capitalize()} {d.year}"


def span_label(a: date, b: date) -> str:
    if a == b:
        return f"{WEEKDAYS[a.weekday()][:3].capitalize()} {fmt(a)}"
    return f"{fmt(a)} to {fmt(b)}"


def _month_range(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return start, nxt - timedelta(days=1)


def _parse_date(text: str, today: date) -> date | None:
    if m := re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        return date(int(m[1]), int(m[2]), int(m[3]))
    mon = "|".join(m for m in _MON) + "|" + "|".join(MONTHS)
    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({mon})[a-z]*\.?(?:,?\s+(\d{{4}}))?\b", text)
    if m:
        day, month, year = int(m[1]), _MON[m[2][:3]], int(m[3]) if m[3] else None
    elif m := re.search(rf"\b({mon})[a-z]*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", text):
        day, month, year = int(m[2]), _MON[m[1][:3]], int(m[3]) if m[3] else None
    else:
        return None
    try:
        d = date(year or today.year, month, day)
    except ValueError:
        return None
    if year is None and d > today:
        d = date(today.year - 1, month, day)
    return d


def resolve(question: str, today: date, data_start: date | None = None, data_end: date | None = None) -> Resolved | None:
    """The period a question asks about, or None if it names none (the caller then uses a default)."""
    q = question.lower()
    last = min(today, data_end) if data_end else today
    origin = data_start

    # Relative to the start of the data: day N, week N.
    if origin is not None:
        def day(n: int) -> date:
            return origin + timedelta(days=n)

        if m := re.search(r"\b(after|since|from)\s+day\s+(\d+)", q):
            n = int(m[2])
            return Resolved(day(n), last, f"day {n} onwards ({span_label(day(n), last)})",
                            baseline=(origin, day(n) - timedelta(days=1), f"before day {n} ({span_label(origin, day(n) - timedelta(days=1))})"))
        if m := re.search(r"\bbefore\s+day\s+(\d+)", q):
            n = int(m[1])
            return Resolved(origin, day(n) - timedelta(days=1), f"before day {n}")
        if m := re.search(r"\bdays?\s+(\d+)\s*(?:to|-|–|and|through)\s*(\d+)", q):
            a, b = int(m[1]), int(m[2])
            return Resolved(day(a), day(b), f"days {a} to {b} ({span_label(day(a), day(b))})")
        if m := re.search(r"\bday\s+(\d+)\b", q):
            n = int(m[1])
            return Resolved(day(n), day(n), f"day {n} ({span_label(day(n), day(n))})")
        if m := re.search(r"\bweeks?\s+(\d+)\s*(?:to|-|–|and|through)\s*(\d+)", q):
            a, b = int(m[1]), int(m[2])
            s, e = day(7 * (a - 1)), day(7 * b - 1)
            return Resolved(s, e, f"weeks {a} to {b} ({span_label(s, e)})")
        if m := re.search(r"\bweek\s+(\d+)\b", q):
            a = int(m[1])
            s, e = day(7 * (a - 1)), day(7 * a - 1)
            return Resolved(s, e, f"week {a} ({span_label(s, e)})")

    if re.search(r"\btoday\b", q):
        return Resolved(today, today, f"today ({span_label(today, today)})")
    if re.search(r"\byesterday\b", q):
        y = today - timedelta(days=1)
        return Resolved(y, y, f"yesterday ({span_label(y, y)})")
    if m := re.search(r"\b(?:last|past|previous)\s+(\d+)\s+(day|week|month)s?\b", q):
        n, unit = int(m[1]), m[2]
        days = n * {"day": 1, "week": 7, "month": 30}[unit]
        s = today - timedelta(days=days)
        return Resolved(s, today - timedelta(days=1), f"the last {n} {unit}{'s' if n > 1 else ''} ({span_label(s, today - timedelta(days=1))})")
    if re.search(r"\bthis\s+week\b", q):
        s = today - timedelta(days=today.weekday())
        return Resolved(s, today, f"this week ({span_label(s, today)})")
    if re.search(r"\blast\s+week\b", q):
        s = today - timedelta(days=today.weekday() + 7)
        return Resolved(s, s + timedelta(days=6), f"last week ({span_label(s, s + timedelta(days=6))})")
    if re.search(r"\bthis\s+month\b", q):
        s = today.replace(day=1)
        return Resolved(s, today, f"this month ({span_label(s, today)})")
    if re.search(r"\blast\s+month\b", q):
        s, e = _month_range(today.year - (today.month == 1), (today.month - 2) % 12 + 1)
        return Resolved(s, e, f"last month ({span_label(s, e)})")
    # "last Monday", "on Monday" (a single day), but not "on Mondays" (a pattern, handled as a filter).
    if m := re.search(r"\b(last|this|previous|past|on)\s+(" + "|".join(WEEKDAYS) + r")\b(?!s)", q):
        wd = WEEKDAYS.index(m[2])
        back = (today.weekday() - wd) % 7 or (7 if m[1] in ("last", "previous", "past", "on") else 0)
        d = today - timedelta(days=back)
        return Resolved(d, d, f"{m[1]} {m[2].capitalize()} ({span_label(d, d)})")
    if d := _parse_date(q, today):
        return Resolved(d, d, span_label(d, d))
    if m := re.search(r"\b(?:since|from)\s+(" + "|".join(MONTHS) + r")\b", q):
        month = MONTHS.index(m[1]) + 1
        year = today.year if month <= today.month else today.year - 1
        s = date(year, month, 1)
        return Resolved(s, today, f"since {m[1].capitalize()} ({span_label(s, today)})")
    if m := re.search(r"\b(?:in|during|for)\s+(" + "|".join(MONTHS) + r")(?:\s+(\d{4}))?\b", q):
        month = MONTHS.index(m[1]) + 1
        year = int(m[2]) if m[2] else (today.year if month <= today.month else today.year - 1)
        s, e = _month_range(year, month)
        return Resolved(s, min(e, today), f"{m[1].capitalize()} {year}")
    return None


def default_period(today: date) -> Resolved:
    s = today - timedelta(days=DEFAULT_DAYS)
    e = today - timedelta(days=1)
    return Resolved(s, e, f"the last {DEFAULT_DAYS} days ({span_label(s, e)})", default=True)


def baseline_before(start: date, end: date) -> tuple[date, date, str]:
    """The comparison for a period: the 4 weeks before it for a short period, else the same length just before."""
    length = (end - start).days + 1
    span = max(28, length)
    b_end = start - timedelta(days=1)
    b_start = start - timedelta(days=span)
    label = f"the 4 weeks before ({span_label(b_start, b_end)})" if span == 28 else f"the {span} days before ({span_label(b_start, b_end)})"
    return b_start, b_end, label


def mentions_period(question: str) -> bool:
    return resolve(question, date(2000, 6, 15), date(2000, 1, 1)) is not None
