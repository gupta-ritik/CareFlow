"""Synthetic discharge history with planted problems.

    python -m app.synthetic.generator --tenant synthetic-hospital --days 120 --seed 42 --scenario default

Writes months of realistic, entirely fictional discharge history into its own hospital (tenant), through the
existing models, so Ask CareFlow can be built and its answers checked against problems we planted on purpose.
Running it again for the same tenant replaces that tenant's data; no other hospital is touched.

Safety
- Patients are "Synthetic Patient 00421" with MRN "SYN-00421"; staff are "Synthetic ..." too. No real-looking names.
- It refuses to write into a hospital that has non-synthetic patients.
- It refuses when CAREFLOW_ENV=production, unless --allow-production is given AND the slug starts with "synthetic-".

Assumptions (all set in the scenario YAML, backend/app/synthetic/scenarios/<name>.yaml)
- Six wards. 8-25 discharges a day (uniform), about half on Sundays. Authorizations cluster around 11:00 and 16:00
  local time (a mixture of two normal curves, clipped to 07:00-21:00).
- Payer mix about 45% cashless insurance through six fictional TPAs ("TPA Alpha" ... "TPA Zeta", each with its own
  speed factor), 15% government scheme, 40% self-pay. 35% of patients have lab orders.
- Each department's time is log-normal (median and spread per department), which matches the long right tail seen
  in real discharge work. Durations are measured from clinical authorization, except billing for insured patients,
  which starts only when the payer approves (billing depends on insurance), as in CareFlow's real workflow.
- Blockers follow the real rules: pharmacy delay after CAREFLOW_SLA_PHARMACY minutes, payer delay after
  CAREFLOW_SLA_INSURANCE, billing delay after CAREFLOW_SLA_BILLING, incomplete documents, payer queries,
  unacknowledged lab reports, billing waiting on insurance. Approval requests, decisions and audit entries follow the
  same action catalog and approver roles the live agents use.
- Weekdays and hours are local to CAREFLOW_TIMEZONE (default Asia/Kolkata); timestamps are stored in UTC.

Planted patterns (the scenario's `patterns`) change the numbers for a date range and are written, with a plain-English
expected finding, to evals/ask/planted_truth.json so evals can score the agent. Supported effect types:
  tpa_turnaround_factor   multiply a TPA's final-authorization time on given weekdays
  department_add_minutes  add minutes to a department on given weekdays and local hours
  missing_field_rate      change how often a discharge-summary field is missing, for one ward
  payer_query_rate_factor multiply how often a TPA asks for one document
  department_time_factor  multiply a department's working time (e.g. 0.7 = 30% faster)
  slow_day                force a day with very few discharges, each much slower (a red herring)
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import random
import statistics
import sys
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy import delete, insert, select

from .. import db
from ..agents.checks import OWNERS, TITLES
from ..config import Settings, get_settings
from ..departments import DOC_FIELDS
from ..models import (AgentExecution, ApprovalDecision, ApprovalRequest, AuditLog, Bed, Blocker, Evidence, Patient,
                      SimDepartmentRecord, TaskDependency, Tenant, Workflow, WorkflowEvent, WorkflowTask)

SCENARIOS = Path(__file__).resolve().parent / "scenarios"
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TRUTH = REPO_ROOT / "evals" / "ask" / "planted_truth.json"
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DEPT_ACTOR = {"documentation": "Documentation system", "pharmacy": "Pharmacy system", "laboratory": "Lab system",
              "insurance": "Insurance system", "billing": "Billing system"}
KIND_SUMMARY = {
    "document_incomplete": "Discharge summary missing: {fields}",
    "pharmacy_delay": "Discharge medicines not ready {mins} minutes after the order",
    "reconciliation_incomplete": "Medication reconciliation form not completed",
    "lab_unacknowledged": "1 final lab report(s) not acknowledged by the ward",
    "not_submitted": "Final authorization not yet submitted to the payer",
    "payer_query": "Payer asked for the {doc}",
    "payer_delay": "Payer has not sent final authorization {mins} minutes after submission",
    "waiting_on_dependency": "Billing cannot start until insurance clears",
    "billing_delay": "Final bill in preparation for {mins} minutes",
}
BATCH = 2000


class GeneratorRefused(RuntimeError):
    """The generator won't write here (production, or a hospital with real patients)."""


# ---- configuration ----------------------------------------------------------------------------------------------
@dataclass
class Params:
    tenant: str = "synthetic-hospital"
    days: int = 120
    seed: int = 42
    scenario: str = "default"
    end_date: date | None = None  # last generated local day; defaults to today in the hospital's time zone
    timezone: str = "Asia/Kolkata"
    sla_pharmacy: int = 60
    sla_insurance: int = 90
    sla_billing: int = 45

    @classmethod
    def from_settings(cls, s: Settings, **kw) -> "Params":
        return cls(timezone=s.timezone, sla_pharmacy=s.sla_pharmacy, sla_insurance=s.sla_insurance, sla_billing=s.sla_billing, **kw)


def load_scenario(name: str) -> dict:
    path = Path(name) if name.endswith((".yaml", ".yml")) else SCENARIOS / f"{name}.yaml"
    if not path.exists():
        raise SystemExit(f"Scenario {name!r} not found (looked for {path}).")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    known = {"tpa_turnaround_factor", "department_add_minutes", "missing_field_rate", "payer_query_rate_factor", "department_time_factor", "slow_day"}
    for p in data.get("patterns", []):
        if p["effect"]["type"] not in known:
            raise SystemExit(f"Pattern {p['id']}: unknown effect type {p['effect']['type']!r}")
    return data


def check_allowed(env: str, slug: str, allow_production: bool) -> None:
    if env == "production" and not (allow_production and slug.startswith("synthetic-")):
        raise GeneratorRefused("Refusing to generate synthetic data with CAREFLOW_ENV=production. Pass --allow-production "
                               "and use a tenant slug that starts with 'synthetic-' if you really mean it.")


# ---- generation (pure: no database) ------------------------------------------------------------------------------
@dataclass
class Dataset:
    tenant_id: uuid.UUID
    start_date: date
    end_date: date
    rows: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))
    affected: Counter = field(default_factory=Counter)  # pattern id -> rows (discharges or events) it changed
    discharge_minutes: list[float] = field(default_factory=list)


class _Gen:
    def __init__(self, p: Params, scenario: dict, tenant_id: uuid.UUID):
        self.p, self.sc, self.base = p, scenario, scenario["baseline"]
        self.rng = random.Random(f"{p.seed}:{scenario['name']}")
        self.idrng = random.Random(f"{p.seed}:{scenario['name']}:{p.tenant}:ids")
        self.tz = ZoneInfo(p.timezone)
        self.tag = hashlib.sha1(p.tenant.encode()).hexdigest()[:4].upper()
        end = p.end_date or datetime.now(self.tz).date()
        self.ds = Dataset(tenant_id=tenant_id, start_date=end - timedelta(days=p.days - 1), end_date=end)
        self.n = Counter()

    # -- small helpers --
    def uid(self) -> uuid.UUID:
        return uuid.UUID(int=self.idrng.getrandbits(128), version=4)

    def code(self, prefix: str) -> str:
        self.n[prefix] += 1
        if prefix == "AC":  # approval codes are unique across all hospitals
            return f"AC-S{self.tag}-{self.n[prefix]:05d}"
        return f"{prefix}-S{self.n[prefix]:05d}"

    def ln(self, key: str) -> float:
        d = self.base["durations"][key]
        return d["median"] * math.exp(self.rng.gauss(0.0, d["sigma"]))

    def active(self, day: int):
        for pat in self.sc.get("patterns", []):
            end = pat.get("end_day")
            if pat["start_day"] <= day <= (end if end is not None else 10**9):
                yield pat

    def add(self, table: str, row: dict) -> None:
        self.ds.rows[table].append(row)

    # -- the whole history --
    def run(self) -> Dataset:
        wards = self.base["wards"]
        for w in wards:
            for b in range(1, self.base["beds_per_ward"] + 1):
                self.add("beds", {"id": self.uid(), "tenant_id": self.ds.tenant_id, "ward": w, "bed": str(b), "status": "available",
                                  "patient_ref": None, "updated_by": "Synthetic generator",
                                  "updated_at": datetime.combine(self.ds.end_date, datetime.min.time(), self.tz).astimezone(timezone.utc)})
        for day in range(self.p.days):
            local_date = self.ds.start_date + timedelta(days=day)
            slow = next((pat for pat in self.active(day) if pat["effect"]["type"] == "slow_day"), None)
            lo, hi = self.base["discharges_per_day"]
            count = self.rng.randint(lo, hi)
            if local_date.weekday() == 6:
                count = max(1, round(count * self.base["sunday_factor"]))
            if slow:
                count = slow["effect"]["discharges"]
            for _ in range(count):
                self.discharge(day, local_date, slow)
        return self.ds

    def auth_time(self, local_date: date) -> datetime:
        peaks = self.base["peaks"]
        peak = self.rng.choices(peaks, weights=[pk["weight"] for pk in peaks])[0]
        lo, hi = self.base["hours_open"]
        hour = min(hi - 0.01, max(lo, self.rng.gauss(peak["hour"], peak["sd"])))
        return datetime.combine(local_date, datetime.min.time(), self.tz) + timedelta(hours=hour)

    def discharge(self, day: int, local_date: date, slow: dict | None) -> None:
        b, rng, sla = self.base, self.rng, self.p
        num = self.n["patient"] = self.n["patient"] + 1
        ref, mrn, name = f"syn-pt-{num:05d}", f"SYN-{num:05d}", f"Synthetic Patient {num:05d}"
        auth_local = self.auth_time(local_date)
        auth = auth_local.astimezone(timezone.utc)
        weekday = WEEKDAYS[local_date.weekday()]
        ward = rng.choice(b["wards"])
        payer_type = rng.choices(list(b["payer_mix"]), weights=list(b["payer_mix"].values()))[0]
        tpa = rng.choice(list(b["tpas"])) if payer_type == "cashless" else None
        payer = tpa or (rng.choice(b["government_schemes"]) if payer_type == "government" else "Self-pay")
        has_lab = rng.random() < b["lab_orders_rate"]
        doctor = f"Dr. Synthetic Attending {rng.randint(1, 20):02d}"
        coordinator = f"Synthetic Coordinator {rng.randint(1, 4)}"
        wf_id, pt_id, wf_code = self.uid(), self.uid(), self.code("WF")
        at = lambda m: auth + timedelta(minutes=m)  # noqa: E731  minutes after authorization -> UTC time
        patterns = list(self.active(day))
        touched: set[str] = set()

        blockers: list[dict] = []   # {dept, kind, since, resolved, summary, waiting_on}
        approvals: list[dict] = []  # {blocker index, action, risk, approver, auto, created, decided, approved}
        clear: dict[str, float] = {}
        records: dict[str, dict] = {}

        def blocker(dept, kind, since, resolved, waiting_on=None, **fmt):
            blockers.append({"dept": dept, "kind": kind, "since": since, "resolved": resolved, "waiting_on": waiting_on,
                             "summary": KIND_SUMMARY[kind].format(**fmt)})
            return len(blockers) - 1

        def propose(bi, action, risk, approver, created):
            auto = approver is None
            decided = created + (0.5 if auto else self.ln("approval_decision"))
            ok = auto or rng.random() >= b["rates"]["approval_rejected"]
            approvals.append({"blocker": bi, "action": action, "risk": risk, "approver": approver, "auto": auto,
                              "created": created, "decided": decided, "approved": ok})
            return decided

        # Documentation: missing fields add a fix-up step and an "incomplete document" blocker.
        doc_time = self.ln("documentation")
        sig_rate = b["rates"]["missing_attending_signature"]
        for pat in patterns:
            e = pat["effect"]
            if e["type"] == "missing_field_rate" and e["ward"] == ward and e["field"] == "attending_signature":
                sig_rate = e["rate"]
                touched.add(pat["id"])
        missing = []
        if rng.random() < sig_rate:
            missing.append("attending_signature")
        if rng.random() < b["rates"]["missing_other_field"]:
            missing.append(rng.choice([f for f in DOC_FIELDS if f != "attending_signature"]))
        if missing:
            found = min(doc_time * 0.5, 15.0)
            doc_time += self.ln("documentation_fix")
            bi = blocker("documentation", "document_incomplete", found, doc_time, fields=", ".join(missing))
            propose(bi, "create_document_task", "low", None, found + 1)
        if slow and slow["effect"]["department"] == "documentation":
            doc_time += slow["effect"]["add_minutes"]
            touched.add(slow["id"])
        clear["documentation"] = doc_time
        records["documentation"] = {"doc_id": f"DS-S{num:05d}", "required": list(DOC_FIELDS), "present": list(DOC_FIELDS),
                                    "in_review": False, "missing_at_first_check": missing}

        # Pharmacy: ordered at authorization; delay blocker after the SLA; escalation if well past it.
        ready = self.ln("pharmacy_ready")
        for pat in patterns:
            e = pat["effect"]
            if e["type"] == "department_add_minutes" and e["department"] == "pharmacy" and weekday in e["weekdays"] \
                    and e["hours"][0] <= auth_local.hour < e["hours"][1]:
                ready += e["minutes"]
                touched.add(pat["id"])
            if e["type"] == "department_time_factor" and e["department"] == "pharmacy":
                ready *= e["factor"]
                touched.add(pat["id"])
        recon = ready + self.ln("pharmacy_reconciliation")
        if ready > sla.sla_pharmacy:
            bi = blocker("pharmacy", "pharmacy_delay", sla.sla_pharmacy, ready, mins=sla.sla_pharmacy)
            if ready > sla.sla_pharmacy + b["rates"]["pharmacy_escalation_after_minutes"]:
                propose(bi, "escalate_pharmacy_task", "medium", "Discharge coordinator", sla.sla_pharmacy + b["rates"]["pharmacy_escalation_after_minutes"])
        if recon - ready > 20:
            blocker("pharmacy", "reconciliation_incomplete", ready, recon)
        clear["pharmacy"] = recon
        records["pharmacy"] = {"task_id": f"PH-S{num:05d}", "status": "DONE", "ordered_at": auth.isoformat(),
                               "assigned_to": f"Synthetic Pharmacist {rng.randint(1, 6)}", "reconciliation": "DONE",
                               "ready_at": at(ready).isoformat(), "escalations": []}

        # Laboratory: reports become final, then the ward acknowledges them.
        if has_lab:
            final = self.ln("lab_final")
            ack = final + self.ln("lab_acknowledge")
            if ack - final > 15:
                bi = blocker("laboratory", "lab_unacknowledged", final, ack)
                propose(bi, "request_lab_acknowledgement", "low", None, final + 1)
            clear["laboratory"] = ack
            records["laboratory"] = {"reports": [{"id": f"LAB-S{num:05d}", "status": "final", "acknowledged": True,
                                                  "final_at": at(final).isoformat(), "acknowledged_at": at(ack).isoformat()}]}

        # Insurance: submit, wait for the payer (TPA speed, planted slowdowns), maybe a payer query for a document.
        query_doc = None
        if payer_type != "self_pay":
            submit = self.ln("insurance_submit")
            if submit > 30:
                blocker("insurance", "not_submitted", 5, submit)
            key = "insurance_turnaround_cashless" if payer_type == "cashless" else "insurance_turnaround_government"
            turnaround = self.ln(key) * (b["tpas"][tpa] if tpa else 1.0)
            submit_weekday = WEEKDAYS[at(submit).astimezone(self.tz).weekday()]
            docs = list(b["payer_query_documents"])
            doc_rates = {d: b["rates"]["payer_query"] / len(docs) for d in docs}
            for pat in patterns:
                e = pat["effect"]
                if e["type"] == "tpa_turnaround_factor" and tpa == e["tpa"] and submit_weekday in e["weekdays"]:
                    turnaround *= e["factor"]
                    touched.add(pat["id"])
                if e["type"] == "payer_query_rate_factor" and tpa == e["tpa"]:
                    doc_rates[e["document"]] *= e["factor"]
                    touched.add(pat["id"])
            if turnaround > sla.sla_insurance:
                blocker("insurance", "payer_delay", submit + sla.sla_insurance, submit + turnaround, mins=sla.sla_insurance)
            extra = 0.0
            if rng.random() < min(1.0, sum(doc_rates.values())):
                query_doc = rng.choices(docs, weights=[doc_rates[d] for d in docs])[0]
                q_at = submit + turnaround * rng.uniform(0.2, 0.6)
                extra = self.ln("payer_query_extra")
                bi = blocker("insurance", "payer_query", q_at, q_at, doc=query_doc.replace("_", " "))
                decided = propose(bi, "attach_document_to_claim", "high", "Insurance desk lead", q_at + 2)
                blockers[bi]["resolved"] = max(decided, q_at + extra * 0.5) + 1
                extra = max(extra, blockers[bi]["resolved"] - q_at)
            approved = submit + turnaround + extra
            clear["insurance"] = approved
            records["insurance"] = {"claim_id": f"CL-S{num:05d}", "status": "APPROVED", "submitted_at": at(submit).isoformat(),
                                    "approved_at": at(approved).isoformat(), "payer": payer,
                                    "queries": [query_doc] if query_doc else [], "available_documents": [query_doc] if query_doc else [],
                                    "requested_document": None}

        # Billing: self-pay starts soon after authorization; insured billing waits for the payer's approval.
        if payer_type != "self_pay":
            start = clear["insurance"]
            blocker("billing", "waiting_on_dependency", 5, start, waiting_on="insurance")
        else:
            start = self.ln("billing_selfpay_start")
        duration = self.ln("billing")
        for pat in patterns:
            e = pat["effect"]
            if e["type"] == "department_time_factor" and e["department"] == "billing":
                duration *= e["factor"]
                touched.add(pat["id"])
        if duration > sla.sla_billing:
            bi = blocker("billing", "billing_delay", start + sla.sla_billing, start + duration, mins=sla.sla_billing)
            propose(bi, "notify_department", "low", None, start + sla.sla_billing + 1)
        clear["billing"] = start + duration
        records["billing"] = {"bill_id": f"BIL-S{num:05d}", "status": "FINAL", "started_at": at(start).isoformat(),
                              "finalized_at": at(start + duration).isoformat()}

        cleared = max(clear.values())
        discharged = cleared + self.ln("exit")
        self.ds.discharge_minutes.append(discharged)
        for pid in touched:
            self.ds.affected[pid] += 1
        self.emit(num, ref, mrn, name, auth, at, ward, payer, payer_type, has_lab, doctor, coordinator, wf_id, pt_id, wf_code,
                  clear, cleared, discharged, blockers, approvals, records)

    # -- rows for one discharge --
    def emit(self, num, ref, mrn, name, auth, at, ward, payer, payer_type, has_lab, doctor, coordinator, wf_id, pt_id, wf_code,
             clear, cleared, discharged, blockers, approvals, records):
        rng, tid = self.rng, self.ds.tenant_id
        admitted = auth - timedelta(days=rng.uniform(1.0, 7.0))
        self.add("patients", {"id": pt_id, "tenant_id": tid, "ref": ref, "encounter_ref": f"syn-enc-{num:05d}", "display_name": name,
                              "mrn": mrn, "ward": ward, "bed": str(rng.randint(1, self.base["beds_per_ward"])), "payer": payer,
                              "payer_type": payer_type, "attending": doctor, "admitted_at": admitted, "has_lab_orders": has_lab,
                              "consent_for_coordination": True, "status": "discharged", "source": "synthetic",
                              "created_at": admitted, "updated_at": at(discharged)})
        waited = round(cleared)
        summary = (f"All {len(clear)} departments verified clear {waited // 60} h {waited % 60} min after clinical authorization. "
                   f"{len(blockers)} blocker(s) found and resolved.")
        self.add("workflows", {"id": wf_id, "tenant_id": tid, "code": wf_code, "patient_id": pt_id, "status": "discharged",
                               "authorized_at": auth, "authorized_by": doctor, "iteration": rng.randint(1, 3), "summary": summary,
                               "cleared_at": at(cleared), "discharged_at": at(discharged), "deleted_at": None,
                               "created_at": auth, "updated_at": at(discharged)})
        order = [d for d in ("documentation", "pharmacy", "laboratory", "insurance", "billing") if d in clear]
        for pos, dept in enumerate(order):
            self.add("workflow_tasks", {"id": self.uid(), "workflow_id": wf_id, "department": dept, "title": TITLES[dept],
                                        "status": "cleared", "owner": OWNERS[dept], "source_updated_at": at(clear[dept]),
                                        "cleared_at": at(clear[dept]), "position": pos, "created_at": auth, "updated_at": at(clear[dept])})
            data = dict(records[dept])
            data["updated_at"] = at(clear[dept]).isoformat()
            data["history"] = [{"at": at(clear[dept]).isoformat(), "by": f"Synthetic {OWNERS[dept]}", "action": "cleared",
                                "summary": f"{dept.capitalize()} complete"}]
            self.add("sim_department_records", {"id": self.uid(), "tenant_id": tid, "patient_ref": ref, "department": dept,
                                                "data": data, "updated_at": at(clear[dept])})
        if "insurance" in clear:
            self.add("task_dependencies", {"id": self.uid(), "workflow_id": wf_id, "department": "billing", "depends_on": "insurance"})

        audit = []

        def log(minutes, kind, actor, event, detail="", ev=None):
            audit.append({"id": self.uid(), "tenant_id": tid, "code": self.code("AU"), "workflow_code": wf_code, "at": at(minutes),
                          "actor_kind": kind, "actor": actor, "event": event, "detail": detail, "evidence_ids": ev or [],
                          "latency_ms": None, "tokens": None})

        def evidence(dept, minutes, observed):
            code = self.code("EV")
            self.add("evidence", {"id": self.uid(), "workflow_id": wf_id, "code": code, "department": dept, "source": DEPT_ACTOR[dept],
                                  "query": f"read({dept}, patient={ref})", "observed": observed, "observed_at": at(minutes), "untrusted": False})
            return code

        log(0, "human", doctor, "Clinical discharge authorized", "Recorded by the clinician; CareFlow does not make this decision")
        log(0.5, "agent", "Workflow Planner", f"Planned {len(order)} department tasks", ", ".join(order))
        codes = []
        for bl in blockers:
            ev = evidence(bl["dept"], bl["since"], bl["summary"])
            code = self.code("BL")
            codes.append(code)
            self.add("blockers", {"id": self.uid(), "workflow_id": wf_id, "code": code, "department": bl["dept"], "kind": bl["kind"],
                                  "summary": bl["summary"], "since": at(bl["since"]), "evidence_ids": [ev], "waiting_on": bl["waiting_on"],
                                  "status": "resolved", "resolved_at": at(bl["resolved"]), "created_at": at(bl["since"]),
                                  "updated_at": at(bl["resolved"])})
            log(bl["since"], "agent", "Blocker Analyzer", f"Found blocker {code}", f"{bl['dept']}: {bl['summary']}", [ev])
            log(bl["resolved"], "system", "Blocker Analyzer", f"Blocker {code} resolved", f"{bl['dept']}: no longer observed in the source system")
        for ap in approvals:
            bl = blockers[ap["blocker"]]
            code = self.code("AC")
            ap_id = self.uid()
            status = "verified" if ap["approved"] else "rejected"
            approver = ap["approver"] or "None (low risk)"
            reviewer = None if ap["auto"] else f"Synthetic {ap['approver']} {rng.randint(1, 3)}"
            self.add("approval_requests", {"id": ap_id, "workflow_id": wf_id, "code": code, "blocker_code": codes[ap["blocker"]],
                                           "action": ap["action"], "params": {}, "description": f"{ap['action'].replace('_', ' ').capitalize()} for {bl['dept']}",
                                           "expected_impact": "Clears the blocker", "risk": ap["risk"], "requires_approval": not ap["auto"],
                                           "approver_role": approver, "evidence_ids": [], "sop_refs": [], "status": status,
                                           "decided_by": reviewer, "decided_at": None if ap["auto"] else at(ap["decided"]),
                                           "comment": None if ap["approved"] else "Handled by phone instead",
                                           "created_at": at(ap["created"]), "updated_at": at(ap["decided"])})
            log(ap["created"], "agent", "Resolution Planner", f"Proposed {ap['action']}",
                f"{ap['risk'].capitalize()} risk; " + ("runs without a reviewer" if ap["auto"] else f"needs {approver.lower()} approval"))
            if not ap["auto"]:
                decision = "approve" if ap["approved"] else "reject"
                self.add("approval_decisions", {"id": self.uid(), "approval_id": ap_id, "decision": decision, "reviewer_id": None,
                                                "reviewer_name": reviewer, "reviewer_role": ap["approver"],
                                                "comment": None if ap["approved"] else "Handled by phone instead", "decided_at": at(ap["decided"])})
                log(ap["decided"], "human", reviewer, f"{'Approved' if ap['approved'] else 'Rejected'} {ap['action']}", "")
        for dept in order:
            ev = evidence(dept, clear[dept], f"{dept.capitalize()} cleared")
            log(clear[dept], "system", DEPT_ACTOR[dept], "Status read", f"{dept.capitalize()} cleared", [ev])
        log(cleared, "system", "Verify", "All departments cleared", f"Re-read {len(order)} source systems; workflow complete")
        log(cleared + 0.2, "agent", "Reporter", "Clearance summary written", summary)
        log(discharged, "human", coordinator, "Patient discharged", "Workflow closed")
        audit.sort(key=lambda r: r["at"])
        self.ds.rows["audit_logs"].extend(audit)

        for agent, minutes in (("Workflow Planner", 0.5), ("Blocker Analyzer", 2), ("Resolution Planner", 3), ("Reporter", cleared + 0.2)):
            fallback = rng.random() < self.base["rates"]["llm_fallback"]
            self.add("agent_executions", {"id": self.uid(), "tenant_id": tid, "workflow_code": wf_code, "agent": agent,
                                          "mode": "fallback" if fallback else "llm", "status": "ok", "input_summary": "", "output_summary": "",
                                          "latency_ms": rng.randint(5, 40) if fallback else rng.randint(400, 2500),
                                          "tokens": 0 if fallback else rng.randint(300, 1800), "retries": 0, "error": None,
                                          "started_at": at(minutes)})
        for etype, minutes in (("CLINICAL_DISCHARGE_AUTHORIZED", 0), ("PATIENT_DISCHARGED", discharged)):
            self.add("workflow_events", {"id": self.uid(), "tenant_id": tid, "event_id": f"syn-{etype[:4].lower()}-{num:05d}", "type": etype,
                                         "patient_ref": ref, "workflow_code": wf_code, "correlation_id": f"syn-{num:05d}", "payload": {},
                                         "occurred_at": at(minutes), "received_at": at(minutes), "status": "processed", "attempts": 1, "error": None})


def generate(p: Params, scenario: dict | None = None, tenant_id: uuid.UUID | None = None) -> Dataset:
    """Build every row in memory. Same parameters and seed -> identical rows (including ids)."""
    sc = scenario or load_scenario(p.scenario)
    tid = tenant_id or uuid.UUID(int=random.Random(f"{p.seed}:{p.tenant}:tenant").getrandbits(128), version=4)
    return _Gen(p, sc, tid).run()


# ---- writing ----------------------------------------------------------------------------------------------------
TABLES = [  # insert order respects foreign keys
    ("patients", Patient), ("workflows", Workflow), ("workflow_tasks", WorkflowTask), ("task_dependencies", TaskDependency),
    ("evidence", Evidence), ("blockers", Blocker), ("approval_requests", ApprovalRequest), ("approval_decisions", ApprovalDecision),
    ("audit_logs", AuditLog), ("agent_executions", AgentExecution), ("workflow_events", WorkflowEvent),
    ("sim_department_records", SimDepartmentRecord), ("beds", Bed),
]


async def _tenant(s, slug: str) -> Tenant | None:
    return await s.scalar(select(Tenant).where(Tenant.slug == slug))


async def _clear_tenant(s, tid: uuid.UUID) -> None:
    wf_ids = select(Workflow.id).where(Workflow.tenant_id == tid)
    ap_ids = select(ApprovalRequest.id).where(ApprovalRequest.workflow_id.in_(wf_ids))
    await s.execute(delete(ApprovalDecision).where(ApprovalDecision.approval_id.in_(ap_ids)))
    for model in (ApprovalRequest, Blocker, Evidence, WorkflowTask, TaskDependency):
        await s.execute(delete(model).where(model.workflow_id.in_(wf_ids)))
    for model in (Workflow, AuditLog, AgentExecution, WorkflowEvent, SimDepartmentRecord, Bed, Patient):
        await s.execute(delete(model).where(model.tenant_id == tid))


async def _ensure_director(s, tenant: Tenant) -> tuple[str, str | None]:
    """A Hospital director account for the synthetic hospital, so Ask CareFlow can be tried. Returns (email, new password or
    None if the account already existed). The password is CAREFLOW_SYNTHETIC_DIRECTOR_PASSWORD, or a random one."""
    import os
    import secrets

    from ..api.hospital import _ensure_roles
    from ..models import User
    from ..security import DIRECTOR, hash_password

    email = f"director@{tenant.slug}.test"
    if await s.scalar(select(User.id).where(User.email == email)):
        return email, None
    roles = await _ensure_roles(s)
    password = os.environ.get("CAREFLOW_SYNTHETIC_DIRECTOR_PASSWORD") or secrets.token_urlsafe(12)
    s.add(User(tenant_id=tenant.id, email=email, name="Synthetic Director", role_id=roles[DIRECTOR].id, password_hash=hash_password(password)))
    await s.flush()
    return email, password


async def write(sm, p: Params, scenario: dict, name: str = "Synthetic Hospital") -> tuple[Dataset, Tenant]:
    async with sm() as s:
        tenant = await _tenant(s, p.tenant)
        if tenant is not None:
            real = await s.scalar(select(Patient.id).where(Patient.tenant_id == tenant.id, ~Patient.mrn.startswith("SYN-")).limit(1))
            if real is not None:
                raise GeneratorRefused(f"Hospital {p.tenant!r} has non-synthetic patients; the generator only writes to its own hospital.")
        else:
            tenant = Tenant(slug=p.tenant, name=name)
            s.add(tenant)
            await s.flush()
        ds = generate(p, scenario, tenant.id)
        ds.director = await _ensure_director(s, tenant)
        await _clear_tenant(s, tenant.id)
        for table, model in TABLES:
            rows = ds.rows.get(table, [])
            for i in range(0, len(rows), BATCH):
                await s.execute(insert(model), rows[i:i + BATCH])
        await s.commit()
        return ds, tenant


def planted_truth(p: Params, scenario: dict, ds: Dataset) -> dict:
    out = []
    for pat in scenario.get("patterns", []):
        end = pat.get("end_day")
        out.append({
            "id": pat["id"], "title": pat["title"], "expected_finding": " ".join(pat["expected_finding"].split()),
            "keywords": pat.get("keywords", []), "measures": pat.get("measures", []),
            "expect_not_a_trend": bool(pat.get("expect_not_a_trend", False)), "effect": pat["effect"],
            "start_day": pat["start_day"], "end_day": end,
            "start_date": (ds.start_date + timedelta(days=pat["start_day"])).isoformat(),
            "end_date": (ds.start_date + timedelta(days=end)).isoformat() if end is not None else ds.end_date.isoformat(),
            "affected_discharges": ds.affected.get(pat["id"], 0),
        })
    return {"scenario": scenario["name"], "seed": p.seed, "tenant": p.tenant, "days": p.days, "timezone": p.timezone,
            "start_date": ds.start_date.isoformat(), "end_date": ds.end_date.isoformat(), "patterns": out}


def summary(ds: Dataset, truth: dict) -> str:
    n = len(ds.rows["workflows"])
    med = statistics.median(ds.discharge_minutes) if ds.discharge_minutes else 0
    lines = [f"Synthetic history {ds.start_date} to {ds.end_date}: {n} discharges, median discharge time "
             f"{int(med // 60)} h {int(med % 60)} min (authorization to patient leaving).",
             "Rows: " + ", ".join(f"{t}={len(r)}" for t, r in ds.rows.items()),
             "Planted patterns:"]
    for pat in truth["patterns"]:
        lines.append(f"  - {pat['id']} ({pat['start_date']} to {pat['end_date']}): {pat['affected_discharges']} discharges affected")
    return "\n".join(lines)


async def _main(args) -> int:
    s = get_settings()
    check_allowed(s.env, args.tenant, args.allow_production)
    params = Params.from_settings(s, tenant=args.tenant, days=args.days, seed=args.seed, scenario=args.scenario,
                                  end_date=date.fromisoformat(args.end_date) if args.end_date else None)
    scenario = load_scenario(args.scenario)
    db.init_engine(s.database_url)
    started = time.perf_counter()
    try:
        ds, tenant = await write(db.sessionmaker(), params, scenario)
    finally:
        await db.engine().dispose()
    truth = planted_truth(params, scenario, ds)
    if args.truth:
        path = Path(args.truth)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(truth, indent=2) + "\n", encoding="utf-8")
    print(summary(ds, truth))
    print(f"Written to hospital {tenant.slug!r} in {time.perf_counter() - started:.1f} s" + (f"; planted truth: {args.truth}" if args.truth else ""))
    email, password = getattr(ds, "director", (None, None))
    if email and password:
        print(f"Sign in to try Ask CareFlow as {email} with password {password} (shown once; change it under Users).")
    elif email:
        print(f"Sign in to try Ask CareFlow as {email} (existing account, password unchanged).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.synthetic.generator", description="Generate synthetic discharge history with planted problems.")
    ap.add_argument("--tenant", default="synthetic-hospital", help="hospital slug to write into (created if missing)")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--scenario", default="default", help="scenario name in app/synthetic/scenarios, or a path to a YAML file")
    ap.add_argument("--end-date", help="last day to generate, YYYY-MM-DD (default: today in the hospital's time zone)")
    ap.add_argument("--truth", default=str(DEFAULT_TRUTH), help="where to write the planted-truth JSON ('' to skip)")
    ap.add_argument("--allow-production", action="store_true", help="allow CAREFLOW_ENV=production (slug must start with 'synthetic-')")
    args = ap.parse_args(argv)
    if args.days < 1:
        ap.error("--days must be at least 1")
    try:
        return asyncio.run(_main(args))
    except GeneratorRefused as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
