"""API, event bus and LLM-guard tests (Postgres + Redis db 15)."""
import uuid

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select

from app import events
from app.engine import DecisionError
from app.models import ApprovalRequest, AuditLog, Blocker, ExecutionRecord, Workflow, WorkflowEvent
from app.security import ROLE_PERMISSIONS, Principal
from app.seed import seed_patients, seed_tenant


# ----- API ---------------------------------------------------------------------------------------
@pytest.fixture
async def client(sm):
    from app.main import create_app

    r = Redis.from_url("redis://localhost:6379/15")
    await r.flushdb()
    await r.aclose()
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            c.app = app
            yield c


async def login(c, email):
    r = await c.post("/api/auth/login", json={"email": email, "password": "careflow-demo"})
    assert r.status_code == 200, r.text
    return {"authorization": f"Bearer {r.json()['accessToken']}"}


async def test_auth_required_and_bad_password(client):
    assert (await client.get("/api/workflows")).status_code == 401
    r = await client.post("/api/auth/login", json={"email": "priya@demo-hospital.test", "password": "nope"})
    assert r.status_code == 401 and r.json()["error"]["message"] == "Email or password is incorrect."


async def test_board_matches_seed(client):
    h = await login(client, "priya@demo-hospital.test")
    wfs = (await client.get("/api/workflows", headers=h)).json()
    by_name = {w["patient"]["displayName"]: w for w in wfs}
    assert by_name["Vikram Singh"]["status"] == "cleared"
    assert by_name["Meera Iyer"]["status"] == "awaiting_approval"
    assert {b["kind"] for b in by_name["Meera Iyer"]["blockers"]} == {"pharmacy_delay", "waiting_on_dependency"}
    for w in wfs:  # every blocker's evidence is returned with the workflow
        ev = {e["id"] for e in w["evidence"]}
        assert all(set(b["evidenceIds"]) <= ev and b["evidenceIds"] for b in w["blockers"])


async def test_role_and_validation(client):
    h_pharm = await login(client, "neha@demo-hospital.test")
    h_coord = await login(client, "priya@demo-hospital.test")
    approvals = (await client.get("/api/approvals", headers=h_coord)).json()
    esc = next(a for a in approvals if a["action"] == "escalate_pharmacy_task")
    assert (await client.post(f"/api/approvals/{esc['id']}/approve", headers=h_pharm, json={})).status_code == 403
    r = await client.post(f"/api/approvals/{esc['id']}/reject", headers=h_coord, json={})
    assert r.status_code == 422 and "comment" in r.json()["error"]["message"]
    r = await client.post(f"/api/approvals/{esc['id']}/approve", headers=h_coord, json={"comment": "ok"})
    assert r.status_code == 200 and r.json()["status"] == "verified"


async def test_auditor_sees_masked_identity(client):
    h = await login(client, "sara@demo-hospital.test")
    w = (await client.get("/api/workflows", headers=h)).json()[0]
    assert w["patient"]["mrn"].startswith("MRN ••••") and "." in w["patient"]["displayName"]


async def test_tenant_isolation(client, sm):
    async with sm() as s:
        other = await seed_tenant(s, slug="other-hospital", name="Other")
        await seed_patients(s, other, [])
        await s.commit()
    h_other = await login(client, "priya@other-hospital.test")
    assert (await client.get("/api/workflows", headers=h_other)).json() == []
    assert (await client.get("/api/workflows/WF-2000", headers=h_other)).status_code == 404
    approvals = (await client.get("/api/approvals", headers=await login(client, "priya@demo-hospital.test"))).json()
    pending = next(a for a in approvals if a["status"] == "pending")
    assert (await client.post(f"/api/approvals/{pending['id']}/approve", headers=h_other, json={})).status_code == 404


async def test_login_rate_limited(client):
    codes = [(await client.post("/api/auth/login", json={"email": "x@y.z", "password": "x"})).status_code for _ in range(12)]
    assert 429 in codes


async def test_analytics_and_agents(client):
    h = await login(client, "priya@demo-hospital.test")
    d = (await client.get("/api/analytics/dashboard", headers=h)).json()
    assert d["activeWorkflows"] >= 5 and d["readyToDischarge"] == 1
    agents = {a["agent"]: a for a in (await client.get("/api/agents", headers=h)).json()}
    assert {"Workflow Planner", "Blocker Analyzer", "Resolution Planner", "Reporter"} <= set(agents)


# ----- events ------------------------------------------------------------------------------------
@pytest.fixture
async def redis():
    r = Redis.from_url("redis://localhost:6379/15")
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


async def test_stream_processing_idempotent_and_dlq(sm, tenant, seeded, engine, redis):
    await events.ensure_group(redis)
    auth = events.make_event("CLINICAL_DISCHARGE_AUTHORIZED", tenant.slug, "pt-8722", {"authorized_by": "Dr. P. Das"}, event_id="evt-dup-1")
    await events.publish(redis, auth)
    await events.publish(redis, auth)  # duplicate delivery
    await events.publish(redis, events.make_event("NOT_A_REAL_EVENT", tenant.slug, "pt-8722"))  # poison
    for _ in range(5):
        await events.process_batch(redis, engine, sm, block_ms=100)
    async with sm() as s:
        wfs = (await s.scalars(select(Workflow))).all()
        evs = (await s.scalars(select(WorkflowEvent).where(WorkflowEvent.event_id == "evt-dup-1"))).all()
    assert len(wfs) == 1 and wfs[0].status == "cleared"
    assert len(evs) == 1 and evs[0].status == "processed"
    dead = await redis.xrange(events.DLQ)
    assert len(dead) == 1 and dead[0][1][b"type"] == b"NOT_A_REAL_EVENT" and dead[0][1][b"attempt"] == b"3"


async def test_deferred_event_replayed_after_decision(sm, tenant, seeded, engine):
    from app.engine import start_for_patient
    from app.sim import patch_record

    await start_for_patient(engine, tenant.id, "pt-8812", "Dr. A. Rao")
    async with sm() as s:
        await patch_record(s, tenant.id, "pt-8812", "pharmacy", {"status": "DONE"})
        await s.commit()
    outcome = await engine.handle_event(tenant.id, {"event_id": "e-ph-1", "type": "PHARMACY_TASK_UPDATED", "patient_ref": "pt-8812"})
    assert outcome == "deferred"
    async with sm() as s:
        a = await s.scalar(select(ApprovalRequest).where(ApprovalRequest.status == "pending"))
    async with sm() as s:
        from app.models import User

        u = await s.scalar(select(User).where(User.email == "priya@demo-hospital.test"))
    p = Principal(u.id, tenant.id, "Priya", "p@x", "Discharge coordinator", tuple(ROLE_PERMISSIONS["Discharge coordinator"]))
    await engine.decide(p, a.code, "reject", "Pharmacy already on it")
    async with sm() as s:
        open_kinds = {b.kind for b in (await s.scalars(select(Blocker).where(Blocker.status == "open"))).all()}
        executed = (await s.scalars(select(ExecutionRecord))).all()
    assert "pharmacy_delay" not in open_kinds  # the deferred re-check saw pharmacy done
    assert not executed  # rejected action never ran


async def test_wrong_role_decision_raises(sm, tenant, seeded, engine):
    from app.engine import start_for_patient

    await start_for_patient(engine, tenant.id, "pt-8764", "Dr. A. Rao")
    async with sm() as s:
        a = await s.scalar(select(ApprovalRequest).where(ApprovalRequest.status == "pending"))
    p = Principal(uuid.uuid4(), tenant.id, "Priya", "p@x", "Discharge coordinator", ())
    with pytest.raises(DecisionError) as e:
        await engine.decide(p, a.code, "approve", None)
    assert e.value.status == 403


# ----- LLM guards ---------------------------------------------------------------------------------
async def test_llm_hallucinations_and_forbidden_actions_are_rejected(sm, tenant, seeded, engine, fake_llm):
    from app.engine import start_for_patient

    fake = fake_llm({
        "PlanOut": {"departments": ["documentation", "pharmacy", "insurance", "billing"], "dependencies": [{"department": "billing", "depends_on": "insurance"}], "rationale": "ok"},
        "AnalysisOut": {"blockers": [
            {"department": "pharmacy", "kind": "pharmacy_delay", "summary": "Pharmacy late", "evidence_ids": ["EV-1000"], "priority": 1},
            {"department": "insurance", "kind": "claim_denied", "summary": "Claim denied", "evidence_ids": ["EV-9999"], "priority": 1},
        ]},
        "ResolutionOut": {"action": "approve_claim", "description": "Approve the claim", "expected_impact": "done", "evidence_ids": ["EV-1000"]},
        "ReportOut": {"summary": "Summary from the model."},
    })
    await start_for_patient(engine, tenant.id, "pt-8812", "Dr. A. Rao")
    async with sm() as s:
        kinds = {b.kind for b in (await s.scalars(select(Blocker))).all()}
        actions = {a.action for a in (await s.scalars(select(ApprovalRequest))).all()}
        from app.models import AgentExecution

        modes = {(e.agent, e.mode) for e in (await s.scalars(select(AgentExecution))).all()}
    assert "claim_denied" not in kinds  # hallucinated blocker dropped
    assert "approve_claim" not in actions and actions <= {"escalate_pharmacy_task"}
    assert ("Workflow Planner", "llm") in modes and ("Blocker Analyzer", "fallback") in modes and ("Resolution Planner", "fallback") in modes
    for prompt in fake.prompts:  # no patient identity ever reaches the model
        assert "Meera" not in prompt and "4471209" not in prompt and "Iyer" not in prompt


async def test_no_consent_stops_before_reading_departments(sm, tenant, seeded, engine):
    from app.engine import start_for_patient
    from app.models import Patient

    async with sm() as s:
        p = await s.scalar(select(Patient).where(Patient.ref == "pt-8790"))
        p.consent_for_coordination = False
        await s.commit()
    await start_for_patient(engine, tenant.id, "pt-8790", "Dr. S. Menon")
    async with sm() as s:
        actors = {a.actor for a in (await s.scalars(select(AuditLog))).all()}
        wf = await s.scalar(select(Workflow))
    assert "Consent check" in actors and not any(a.endswith(" check") and a != "Consent check" for a in actors)
    assert wf.status == "blocked"
