"""Synthetic history generator: determinism, safety, planted patterns, dependencies, and no real-looking identities."""
import re
import statistics
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.models import AuditLog, Patient, Tenant, Workflow
from app.synthetic import generator as gen
from app.synthetic.generator import GeneratorRefused, Params, check_allowed, generate, load_scenario, write

END = date(2026, 11, 9)  # fixed so the same seed always gives the same calendar
TZ = ZoneInfo("Asia/Kolkata")


@pytest.fixture(scope="module")
def full():
    """120 days, seed 42: the dataset the planted-pattern tests measure."""
    p = Params(days=120, seed=42, end_date=END)
    return p, generate(p)


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _local(s: str) -> datetime:
    return _dt(s).astimezone(TZ)


def _index(ds):
    patients = {p["ref"]: p for p in ds.rows["patients"]}
    records = defaultdict(dict)
    for r in ds.rows["sim_department_records"]:
        records[r["patient_ref"]][r["department"]] = r["data"]
    return patients, records


def _day(ds, when: datetime) -> int:
    return (when.astimezone(TZ).date() - ds.start_date).days


# ---- determinism and safety --------------------------------------------------------------------------------------
def test_same_seed_gives_the_same_data():
    a = generate(Params(days=20, seed=7, end_date=END))
    b = generate(Params(days=20, seed=7, end_date=END))
    c = generate(Params(days=20, seed=8, end_date=END))
    assert a.rows == b.rows
    assert a.rows["workflows"] != c.rows["workflows"]


def test_refuses_production_unless_explicitly_allowed():
    check_allowed("dev", "anything", False)  # fine outside production
    with pytest.raises(GeneratorRefused):
        check_allowed("production", "synthetic-hospital", False)
    with pytest.raises(GeneratorRefused):
        check_allowed("production", "city-hospital", True)  # allowed only for synthetic- slugs
    check_allowed("production", "synthetic-hospital", True)


def test_cli_refuses_production_before_touching_the_database(monkeypatch):
    monkeypatch.setattr(get_settings(), "env", "production")
    assert gen.main(["--days", "1", "--truth", ""]) == 2


async def test_writes_only_its_own_hospital_and_replaces_its_data(sm, seeded):
    async def counts(slug):
        async with sm() as s:
            tid = await s.scalar(select(Tenant.id).where(Tenant.slug == slug))
            return {m.__tablename__: await s.scalar(select(func.count()).select_from(m).where(m.tenant_id == tid)) for m in (Patient, Workflow, AuditLog)}

    async with sm() as s:
        real_slug = await s.scalar(select(Tenant.slug).limit(1))
    before = await counts(real_slug)
    scenario = load_scenario("default")
    p = Params(tenant="synthetic-test", days=10, seed=3, end_date=END)
    ds, tenant = await write(sm, p, scenario)
    first = await counts("synthetic-test")
    await write(sm, p, scenario)  # running again replaces, never duplicates
    assert await counts("synthetic-test") == first
    assert first["patients"] == len(ds.rows["patients"]) > 0
    assert await counts(real_slug) == before  # the real hospital is untouched
    with pytest.raises(GeneratorRefused):  # and it refuses to write into a hospital with real patients
        await write(sm, Params(tenant=real_slug, days=2, seed=1, end_date=END), scenario)


async def test_120_days_write_in_under_two_minutes(sm):
    started = time.perf_counter()
    ds, _ = await write(sm, Params(tenant="synthetic-speed", days=120, seed=42, end_date=END), load_scenario("default"))
    elapsed = time.perf_counter() - started
    assert len(ds.rows["workflows"]) > 1000
    assert elapsed < 120, f"took {elapsed:.0f} s"


# ---- planted patterns are measurably present ----------------------------------------------------------------------
def test_tpa_gamma_is_slow_on_mondays_from_day_60(full):
    _, ds = full
    patients, records = _index(ds)
    mon, other = [], []
    for ref, recs in records.items():
        ins = recs.get("insurance")
        if not ins or patients[ref]["payer"] != "TPA Gamma" or ins["queries"]:
            continue
        submitted = _dt(ins["submitted_at"])
        if _day(ds, submitted) < 60:
            continue
        minutes = (_dt(ins["approved_at"]) - submitted).total_seconds() / 60
        (mon if submitted.astimezone(TZ).weekday() == 0 else other).append(minutes)
    ratio = statistics.median(mon) / statistics.median(other)
    assert len(mon) >= 8 and len(other) >= 30
    assert 1.8 <= ratio <= 3.5, ratio


def test_pharmacy_is_slow_on_weekend_afternoons(full):
    _, ds = full
    _, records = _index(ds)
    hit, rest = [], []
    for recs in records.values():
        ph = recs["pharmacy"]
        ordered = _local(ph["ordered_at"])
        minutes = (_dt(ph["ready_at"]) - _dt(ph["ordered_at"])).total_seconds() / 60
        (hit if ordered.weekday() >= 5 and 14 <= ordered.hour < 18 else rest).append(minutes)
    extra = statistics.median(hit) - statistics.median(rest)
    assert len(hit) >= 50
    assert 40 <= extra <= 100, extra


def test_ward_3b_documentation_backlog_in_weeks_10_to_12(full):
    _, ds = full
    _, records = _index(ds)
    window, outside = [], []
    by_id = {p["id"]: p for p in ds.rows["patients"]}
    for wf in ds.rows["workflows"]:
        pt = by_id[wf["patient_id"]]
        if pt["ward"] != "3B":
            continue
        missing = "attending_signature" in records[pt["ref"]]["documentation"]["missing_at_first_check"]
        (window if 63 <= _day(ds, wf["authorized_at"]) <= 83 else outside).append(missing)
    in_rate, out_rate = sum(window) / len(window), sum(outside) / len(outside)
    assert in_rate > 0.3 and out_rate < 0.2, (in_rate, out_rate)


def test_tpa_beta_asks_for_the_itemised_pharmacy_bill_more_often(full):
    _, ds = full
    patients, records = _index(ds)
    asked = defaultdict(lambda: [0, 0])
    for ref, recs in records.items():
        ins = recs.get("insurance")
        if not ins or patients[ref]["payer_type"] != "cashless":
            continue
        group = "beta" if patients[ref]["payer"] == "TPA Beta" else "others"
        asked[group][0] += "itemised_pharmacy_bill" in ins["queries"]
        asked[group][1] += 1
    beta, others = (asked[g][0] / asked[g][1] for g in ("beta", "others"))
    assert beta >= 2.5 * others, (beta, others)


def test_billing_gets_faster_from_day_90(full):
    _, ds = full
    _, records = _index(ds)
    before, after = [], []
    for recs in records.values():
        bill = recs["billing"]
        started = _dt(bill["started_at"])
        minutes = (_dt(bill["finalized_at"]) - started).total_seconds() / 60
        (after if _day(ds, started) >= 90 else before).append(minutes)
    ratio = statistics.median(after) / statistics.median(before)
    assert 0.6 <= ratio <= 0.8, ratio


def test_red_herring_day_has_only_two_slow_discharges(full):
    _, ds = full
    day_of = defaultdict(list)
    for wf in ds.rows["workflows"]:
        day_of[_day(ds, wf["authorized_at"])].append((wf["discharged_at"] - wf["authorized_at"]).total_seconds() / 60)
    assert len(day_of[47]) == 2
    overall = statistics.median(m for d, ms in day_of.items() if d != 47 for m in ms)
    assert min(day_of[47]) > 2 * overall


# ---- consistency and privacy --------------------------------------------------------------------------------------
def test_billing_never_clears_before_insurance_for_insured_patients(full):
    _, ds = full
    patients, records = _index(ds)
    tasks = defaultdict(dict)
    for t in ds.rows["workflow_tasks"]:
        tasks[t["workflow_id"]][t["department"]] = t["cleared_at"]
    deps = {d["workflow_id"] for d in ds.rows["task_dependencies"] if (d["department"], d["depends_on"]) == ("billing", "insurance")}
    insured = 0
    for wf in ds.rows["workflows"]:
        t = tasks[wf["id"]]
        if "insurance" not in t:
            continue
        insured += 1
        assert wf["id"] in deps
        assert t["billing"] >= t["insurance"]
    for ref, recs in records.items():
        if "insurance" in recs:
            assert _dt(recs["billing"]["started_at"]) >= _dt(recs["insurance"]["approved_at"])
    assert insured > 500
    for wf in ds.rows["workflows"]:  # timelines run forward
        assert wf["authorized_at"] < wf["cleared_at"] <= wf["discharged_at"]


def test_no_real_looking_names_or_mrns(full):
    _, ds = full
    for p in ds.rows["patients"]:
        assert re.fullmatch(r"Synthetic Patient \d{5}", p["display_name"])
        assert re.fullmatch(r"SYN-\d{5}", p["mrn"])
        assert p["attending"].startswith("Dr. Synthetic ")
    people = {a["actor"] for a in ds.rows["audit_logs"] if a["actor_kind"] == "human"}
    people |= {d["reviewer_name"] for d in ds.rows["approval_decisions"]}
    assert people and all("Synthetic" in n for n in people), people - {n for n in people if "Synthetic" in n}
