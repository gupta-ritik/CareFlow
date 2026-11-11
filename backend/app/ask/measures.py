"""The approved measure registry and the deterministic data agent. NO free-form SQL.

Every measure is a parameterized SQLAlchemy query over one view in the "analytics" schema (no names or MRNs). The
runner always:
- filters by the asking user's hospital (tenant_id), whatever the plan says;
- switches to the read-only role careflow_analytics, with SET LOCAL statement_timeout = 3000 (ms);
- returns at most 5000 rows;
- suppresses any group with fewer than 5 patients ("fewer than 5"), which is then never used for a conclusion.

Time measures return the raw minute values per group as `_samples`, used only by the deterministic statistics in the
analyst; samples are stripped before anything is sent to an LLM, stored or returned by the API.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, and_, case, cast, column, distinct, exists, extract, func, literal, select, table, text

from .analytics_sql import ROLE

ROW_LIMIT = 5000
MIN_GROUP = 5          # privacy: smaller groups are shown as "fewer than 5"
STATEMENT_TIMEOUT_MS = 3000
WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DEPARTMENTS = ["documentation", "pharmacy", "laboratory", "insurance", "billing"]
BLOCKER_KINDS = ["document_incomplete", "pharmacy_delay", "reconciliation_incomplete", "lab_unacknowledged", "lab_pending",
                 "not_submitted", "payer_query", "payer_delay", "waiting_on_dependency", "billing_delay", "source_unavailable"]


@dataclass(frozen=True)
class Dimension:
    name: str
    label: str
    column: str | None = None          # view column; None = derived from the measure's time column
    values: tuple[str, ...] | None = None  # allowed values (None = any short string)


DIMENSIONS: dict[str, Dimension] = {d.name: d for d in [
    Dimension("day", "day"),
    Dimension("weekday", "weekday", values=tuple(WEEKDAY_KEYS)),
    Dimension("hour", "hour of day", values=tuple(str(h) for h in range(24))),
    Dimension("ward", "ward", "ward"),
    Dimension("department", "department", "department", tuple(DEPARTMENTS)),
    Dimension("payer_type", "payer type", "payer_type", ("cashless", "government", "self_pay")),
    Dimension("tpa", "TPA", "tpa"),
    Dimension("blocker_kind", "blocker kind", "kind", tuple(BLOCKER_KINDS)),
    Dimension("approver_role", "approver role", "approver_role"),
    Dimension("document", "requested document", "query_document"),
    Dimension("mode", "agent mode", "mode", ("llm", "fallback", "deterministic")),
    Dimension("agent", "agent", "agent"),
    Dimension("risk", "risk", "risk", ("low", "medium", "high")),
]}


@dataclass(frozen=True)
class Measure:
    name: str
    label: str
    description: str
    unit: str             # minutes | share | count
    kind: str             # time | rate | count
    view: str
    time_col: str | None
    columns: tuple[str, ...]
    dimensions: tuple[str, ...]
    filters: tuple[str, ...]
    value: str | None = None       # time measures: the minutes column
    noun: str = "discharges"
    where: tuple[tuple[str, Any], ...] = ()   # fixed conditions (column, value)
    numerator: str | None = None   # rate measures: how the "hit" is defined (see _numerator)
    special_filters: tuple[str, ...] = ()     # filters applied inside the numerator, not to rows
    note: str = ""

    @property
    def patient_level(self) -> bool:
        return "workflow_id" in self.columns


_D = ("workflow_id", "tenant_id", "authorized_at", "ward", "payer_type", "tpa")
REGISTRY: dict[str, Measure] = {m.name: m for m in [
    Measure("discharge_time", "discharge time", "Minutes from clinical discharge authorization to the patient leaving.",
            "minutes", "time", "discharges", "authorized_at", (*_D, "discharge_minutes"),
            ("day", "weekday", "hour", "ward", "payer_type", "tpa"), ("day", "weekday", "hour", "ward", "payer_type", "tpa"),
            value="discharge_minutes"),
    Measure("department_clearance_time", "department clearance time",
            "Minutes a department took to clear, from when it could start (authorization, or when the department it waits on "
            "cleared, e.g. billing after insurance).", "minutes", "time", "department_tasks", "authorized_at",
            (*_D, "department", "minutes"), ("day", "weekday", "hour", "ward", "department", "payer_type", "tpa"),
            ("day", "weekday", "hour", "ward", "department", "payer_type", "tpa"), value="minutes", noun="department tasks"),
    Measure("blocker_count", "blockers", "Number of blockers found, by kind or department.", "count", "count", "blockers", "since",
            (*_D, "department", "kind"), ("day", "weekday", "hour", "ward", "department", "payer_type", "tpa", "blocker_kind"),
            ("day", "weekday", "hour", "ward", "department", "payer_type", "tpa", "blocker_kind"), noun="blockers"),
    Measure("blocker_rate", "share of discharges with a blocker",
            "Share of discharges that had at least one blocker (of the filtered kind, if given).", "share", "rate", "discharges",
            "authorized_at", _D, ("day", "weekday", "hour", "ward", "payer_type", "tpa"),
            ("day", "weekday", "hour", "ward", "payer_type", "tpa", "blocker_kind"), numerator="blocker", special_filters=("blocker_kind",)),
    Measure("blocker_duration", "blocker duration", "Minutes from a blocker being found to it being resolved.", "minutes", "time",
            "blockers", "since", (*_D, "department", "kind", "minutes"),
            ("day", "weekday", "hour", "ward", "department", "payer_type", "tpa", "blocker_kind"),
            ("day", "weekday", "hour", "ward", "department", "payer_type", "tpa", "blocker_kind"), value="minutes", noun="blockers"),
    Measure("tpa_turnaround", "payer turnaround (final authorization)",
            "Minutes from submitting the final authorization request to the payer approving it. Pre-authorization isn't recorded "
            "by CareFlow.", "minutes", "time", "claims", "submitted_at",
            ("workflow_id", "tenant_id", "submitted_at", "ward", "payer_type", "tpa", "minutes"),
            ("day", "weekday", "hour", "ward", "payer_type", "tpa"), ("day", "weekday", "hour", "ward", "payer_type", "tpa"),
            value="minutes", noun="claims"),
    Measure("payer_query_rate", "payer query rate",
            "Share of claims where the payer asked for a document (only the filtered document, if given).", "share", "rate", "claims",
            "submitted_at", ("workflow_id", "tenant_id", "submitted_at", "ward", "payer_type", "tpa", "queried", "query_document"),
            ("day", "weekday", "hour", "ward", "payer_type", "tpa"), ("day", "weekday", "hour", "ward", "payer_type", "tpa", "document"),
            numerator="query", special_filters=("document",), noun="claims"),
    Measure("requested_documents", "documents requested by payers", "Number of payer queries, by requested document.", "count", "count",
            "claims", "submitted_at", ("workflow_id", "tenant_id", "submitted_at", "ward", "payer_type", "tpa", "queried", "query_document"),
            ("day", "weekday", "ward", "payer_type", "tpa", "document"), ("day", "weekday", "ward", "payer_type", "tpa", "document"),
            where=(("queried", True),), noun="payer queries"),
    Measure("approval_decision_time", "approval decision time", "Minutes from an action being proposed to a person deciding it.",
            "minutes", "time", "approvals", "created_at",
            ("workflow_id", "tenant_id", "created_at", "approver_role", "risk", "minutes", "requires_approval"),
            ("day", "weekday", "hour", "approver_role", "risk"), ("day", "weekday", "hour", "approver_role", "risk"),
            value="minutes", where=(("requires_approval", True),), noun="approvals"),
    Measure("staff_updates", "staff updates", "Number of updates made by people (no names).", "count", "count", "staff_updates", "at",
            ("tenant_id", "at"), ("day", "weekday", "hour"), ("day", "weekday", "hour"), noun="updates"),
    Measure("llm_runs", "agent runs", "Agent runs by mode: LLM or rule-based fallback.", "count", "count", "agent_runs", "started_at",
            ("tenant_id", "started_at", "agent", "mode"), ("day", "weekday", "mode", "agent"), ("day", "weekday", "mode", "agent"), noun="runs"),
    Measure("bed_occupancy", "bed occupancy", "Share of beds occupied right now (a snapshot; the period is ignored). Bed turnaround "
            "times aren't recorded.", "share", "rate", "beds", None, ("tenant_id", "ward", "status"), ("ward",), ("ward",),
            numerator="occupied", noun="beds"),
]}


# ---- helpers ------------------------------------------------------------------------------------------------------
def _view(m: Measure):
    return table(m.view, *[column(c) for c in m.columns], schema="analytics")


def _dim_expr(m: Measure, t, dim: str, tz: str):
    d = DIMENSIONS[dim]
    if d.column:
        return t.c[d.column]
    local = func.timezone(tz, t.c[m.time_col])
    if dim == "day":
        return func.to_char(local, "YYYY-MM-DD")
    if dim == "weekday":
        return cast(extract("isodow", local), Integer)
    return cast(extract("hour", local), Integer)  # hour


def _filter_values(dim: str, values: list[str]) -> list:
    if dim == "weekday":
        return [WEEKDAY_KEYS.index(v) + 1 for v in values]
    if dim == "hour":
        return [int(v) for v in values]
    return list(values)


def _key_value(dim: str, v) -> str:
    if v is None:
        return "none"
    if dim == "weekday":
        return WEEKDAY_KEYS[int(v) - 1]
    return str(v)


def _bounds(period, tz: str) -> tuple[datetime, datetime]:
    z = ZoneInfo(tz)
    start = datetime.combine(period.start, datetime.min.time(), z).astimezone(timezone.utc)
    end = datetime.combine(period.end + timedelta(days=1), datetime.min.time(), z).astimezone(timezone.utc)
    return start, end


def _numerator(m: Measure, t, specials: dict[str, list[str]]):
    if m.numerator == "query":
        docs = specials.get("document")
        return and_(t.c.queried, t.c.query_document.in_(docs)) if docs else t.c.queried
    if m.numerator == "occupied":
        return t.c.status == "occupied"
    if m.numerator == "blocker":
        b = table("blockers", column("workflow_id"), column("kind"), schema="analytics")
        cond = [b.c.workflow_id == t.c.workflow_id]
        if specials.get("blocker_kind"):
            cond.append(b.c.kind.in_(specials["blocker_kind"]))
        return exists().where(*cond)
    raise ValueError(m.numerator)


def stats_of(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "median": None, "p90": None, "mean": None}
    v = sorted(values)
    return {"n": len(v), "median": round(statistics.median(v)), "p90": round(v[min(len(v) - 1, int(len(v) * 0.9))]),
            "mean": round(statistics.fmean(v))}


def _summarize(m: Measure, cells: list[dict]) -> dict:
    """Combine raw cells (one per DB group) into one summary with its samples."""
    patients = sum(c["patients"] for c in cells) if m.patient_level else None
    if m.kind == "time":
        samples = [x for c in cells for x in c["values"]]
        out = stats_of(samples)
        out["patients"] = patients
        return {**out, "_samples": samples}
    n = sum(c["n"] for c in cells)
    if m.kind == "rate":
        hits = sum(c["hits"] for c in cells)
        return {"n": n, "hits": hits, "rate": round(hits / n, 3) if n else None, "pct": round(100 * hits / n, 1) if n else None,
                "patients": patients}
    return {"n": n, "patients": patients}


def _suppress(m: Measure, s: dict) -> dict:
    if m.patient_level and s.get("patients") is not None and 0 < s["patients"] < MIN_GROUP:
        keep = {k: v for k, v in s.items() if k == "_samples"}
        return {**keep, "n": "fewer than 5", "suppressed": True}
    return {**s, "suppressed": False}


# ---- the runner --------------------------------------------------------------------------------------------------
@dataclass
class DataContext:
    sessionmaker: Any
    tenant_id: Any
    tz: str = "Asia/Kolkata"
    use_role: bool = True
    queries: list[str] = field(default_factory=list)  # compiled SQL, for tests


async def _query(ctx: DataContext, m: Measure, step, period, group_dims: list[str]) -> list[dict]:
    t = _view(m)
    specials = {k: v for k, v in step.filters.items() if k in m.special_filters}
    dims = [_dim_expr(m, t, d, ctx.tz) for d in group_dims]
    cols = [e.label(f"g{i}") for i, e in enumerate(dims)]
    aggs = [func.count().label("n")]
    if m.patient_level:
        aggs.append(func.count(distinct(t.c.workflow_id)).label("patients"))
    if m.kind == "time":
        aggs.append(func.array_agg(t.c[m.value]).label("values"))
    if m.kind == "rate":
        aggs.append(func.sum(case((_numerator(m, t, specials), 1), else_=0)).label("hits"))
    q = select(*cols, *aggs).select_from(t).where(t.c.tenant_id == ctx.tenant_id)  # the tenant filter is never optional
    if m.time_col:
        start, end = _bounds(period, ctx.tz)
        q = q.where(t.c[m.time_col] >= start, t.c[m.time_col] < end)
    if m.kind == "time":
        q = q.where(t.c[m.value].isnot(None))
    for col, val in m.where:
        q = q.where(t.c[col] == val)
    for dim, values in step.filters.items():
        if dim in m.special_filters:
            continue
        q = q.where(_dim_expr(m, t, dim, ctx.tz).in_(_filter_values(dim, values)))
    for dim in group_dims:  # "which TPA": patients without a TPA (self-pay, government) are not a TPA group
        if DIMENSIONS[dim].column:
            q = q.where(t.c[DIMENSIONS[dim].column].isnot(None))
    if dims:
        q = q.group_by(*dims)
    q = q.limit(ROW_LIMIT)
    ctx.queries.append(str(q.compile(compile_kwargs={"literal_binds": False})))

    async with ctx.sessionmaker() as s:
        conn = await s.connection()
        if ctx.use_role:
            await conn.execute(text(f"SET LOCAL ROLE {ROLE}"))
        await conn.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
        rows = (await conn.execute(q)).mappings().all()
        await s.rollback()
    out = []
    for r in rows:
        key = {d: _key_value(d, r[f"g{i}"]) for i, d in enumerate(group_dims)}
        out.append({"key": key, "n": r["n"], "patients": r.get("patients") or 0,
                    "values": [float(x) for x in (r.get("values") or []) if x is not None], "hits": int(r.get("hits") or 0)})
    return out


def _matches(cell_key: dict, focus: dict[str, list[str]]) -> bool:
    return all(cell_key.get(d) in vals for d, vals in focus.items())


async def run_step(ctx: DataContext, step, result_id: str) -> dict:
    """Run one validated plan step. Returns a result with aggregates (and `_samples` for the statistics)."""
    m = REGISTRY[step.measure]
    focus = step.compare.focus if step.compare and step.compare.kind == "rest" else {}
    group_dims = list(dict.fromkeys([*step.group_by, *focus]))
    cells = await _query(ctx, m, step, step.period, group_dims)
    row_count = len(cells)

    def groups_for(cs: list[dict]) -> list[dict]:
        by: dict[tuple, list[dict]] = {}
        for c in cs:
            by.setdefault(tuple(c["key"].get(d) for d in step.group_by), []).append(c)
        out = []
        for k, members in sorted(by.items()):
            out.append({"key": dict(zip(step.group_by, k)), **_suppress(m, _summarize(m, members))})
        return out

    label = m.label
    if m.name == "department_clearance_time" and len(step.filters.get("department", [])) == 1:
        label = f"{step.filters['department'][0]} clearance time"
    if m.name == "blocker_rate" and step.filters.get("blocker_kind"):
        label = f"share of discharges with a {' or '.join(k.replace('_', ' ') for k in step.filters['blocker_kind'])} blocker"
    if m.name == "payer_query_rate" and step.filters.get("document"):
        label = f"rate of payer queries for the {' or '.join(d.replace('_', ' ') for d in step.filters['document'])}"
    result: dict[str, Any] = {
        "id": result_id, "measure": m.name, "label": label, "unit": m.unit, "kind": m.kind, "noun": m.noun,
        "params": step.model_dump(mode="json"), "row_count": row_count,
        "total": _suppress(m, _summarize(m, cells)),
        "groups": groups_for(cells) if step.group_by else [],
    }
    if step.compare and step.compare.kind == "rest":
        result["focus"] = _suppress(m, _summarize(m, [c for c in cells if _matches(c["key"], focus)]))
        result["baseline"] = _suppress(m, _summarize(m, [c for c in cells if not _matches(c["key"], focus)]))
    elif step.compare and step.compare.kind == "period":
        base = await _query(ctx, m, step, step.compare.period, group_dims)
        result["row_count"] += len(base)
        result["focus"] = result["total"]
        result["baseline"] = _suppress(m, _summarize(m, base))
        if step.group_by:
            result["baseline_groups"] = groups_for(base)
    return result


def public(result: dict) -> dict:
    """A result without raw samples: what the LLM, the database and the API ever see."""
    def clean(v):
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items() if k != "_samples"}
        if isinstance(v, list):
            return [clean(x) for x in v]
        return v
    return clean(result)


def registry_for_prompt() -> str:
    lines = []
    for m in REGISTRY.values():
        lines.append(f"- {m.name} ({m.unit}): {m.description} Split by: {', '.join(m.dimensions)}. Filters: {', '.join(m.filters)}.")
    return "\n".join(lines)


__all__ = ["REGISTRY", "DIMENSIONS", "DataContext", "run_step", "public", "stats_of", "literal"]
