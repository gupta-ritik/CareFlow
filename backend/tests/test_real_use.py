"""The whole real-use flow from an empty database, through the HTTP API only."""
import httpx
import pytest
from redis.asyncio import Redis

from app.config import get_settings


@pytest.fixture
async def api(sm, monkeypatch):
    monkeypatch.setattr(get_settings(), "seed_demo", False)
    r = Redis.from_url("redis://localhost:6379/15")
    await r.flushdb()
    await r.aclose()
    from app.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c


def ok(r, code=200):
    assert r.status_code == code, f"{r.status_code}: {r.text}"
    return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else r


async def as_user(c, email, password="a-strong-password"):
    t = ok(await c.post("/api/auth/login", json={"email": email, "password": password}))
    return {"authorization": f"Bearer {t['accessToken']}"}


async def test_full_flow_from_empty_database(api):
    c = api
    # ---- first run -----------------------------------------------------------------------------
    assert ok(await c.get("/api/setup/status"))["needsSetup"] is True
    assert (await c.get("/api/workflows")).status_code == 401
    t = ok(await c.post("/api/setup", json={"hospitalName": "Sunrise Hospital", "adminName": "Asha Rao",
                                            "adminEmail": "asha@sunrise.in", "password": "a-strong-password"}), 201)
    admin = {"authorization": f"Bearer {t['accessToken']}"}
    assert t["user"]["role"] == "Administrator" and "user:manage" in t["user"]["permissions"]
    assert ok(await c.get("/api/setup/status"))["needsSetup"] is False
    assert (await c.post("/api/setup", json={"hospitalName": "Other", "adminName": "Someone", "adminEmail": "y@x.in", "password": "a-strong-password"})).status_code == 409
    assert ok(await c.get("/api/workflows", headers=admin)) == []  # no demo data

    # ---- users -----------------------------------------------------------------------------------
    for name, email, role in [("Ravi Menon", "ravi@sunrise.in", "Discharge coordinator"), ("Dr. Kavya Iyer", "kavya@sunrise.in", "Clinician"),
                              ("Joseph P", "joseph@sunrise.in", "Pharmacy"), ("Meena S", "meena@sunrise.in", "Billing"),
                              ("Farah K", "farah@sunrise.in", "Insurance desk lead"), ("Lata W", "lata@sunrise.in", "Ward clerk")]:
        ok(await c.post("/api/users", headers=admin, json={"name": name, "email": email, "role": role, "password": "a-strong-password"}), 201)
    coord, doc, pharm, bill, ins, clerk = [await as_user(c, f"{n}@sunrise.in") for n in ("ravi", "kavya", "joseph", "meena", "farah", "lata")]
    assert (await c.post("/api/users", headers=coord, json={"name": "X", "email": "x@sunrise.in", "role": "Billing", "password": "a-strong-password"})).status_code == 403

    # ---- admit a cashless patient ------------------------------------------------------------------
    pt = ok(await c.post("/api/patients", headers=clerk, json={"displayName": "Suresh Babu", "mrn": "88120", "ward": "Ward 3", "bed": "12",
                                                                "payer": "Star Health", "payerType": "cashless", "attending": "Dr. Kavya Iyer"}), 201)
    assert pt["mrn"] == "MRN 88120" and pt["departments"] == ["documentation", "pharmacy", "insurance", "billing"] and pt["workflow"] is None
    assert (await c.post("/api/patients", headers=clerk, json={"displayName": "Dup", "mrn": "88120", "ward": "W", "payer": "Self", "payerType": "self_pay",
                                                               "attending": "Dr. X"})).status_code == 409
    ref = pt["ref"]

    # documentation can be done before authorization
    doc_q = ok(await c.get("/api/departments/documentation/queue", headers=clerk))
    fields = doc_q[0]["record"]["required"]
    ok(await c.post(f"/api/departments/documentation/{ref}/actions", headers=clerk, json={"action": "update_fields", "params": {"present": fields[:-1]}}))

    # ---- only a clinician can authorize discharge ----------------------------------------------------
    assert (await c.post(f"/api/patients/{ref}/authorize-discharge", headers=coord, json={})).status_code == 403
    wf = ok(await c.post(f"/api/patients/{ref}/authorize-discharge", headers=doc, json={"note": "Stable, follow up in 1 week"}))
    code = wf["workflowId"]
    assert code and wf["status"] in ("blocked", "awaiting_approval")
    assert (await c.post(f"/api/patients/{ref}/authorize-discharge", headers=doc, json={})).status_code == 409

    w = ok(await c.get(f"/api/workflows/{code}", headers=coord))
    kinds = {(b["department"], b["kind"]) for b in w["blockers"]}
    assert ("documentation", "document_incomplete") in kinds and ("insurance", "not_submitted") in kinds and ("billing", "waiting_on_dependency") in kinds

    # CareFlow's low-risk reminder shows up in the insurance desk's queue
    ins_q = ok(await c.get("/api/departments/insurance/queue", headers=ins))
    assert ins_q[0]["requests"] and ins_q[0]["blockers"]

    # ---- departments do their work ----------------------------------------------------------------
    assert (await c.post(f"/api/departments/billing/{ref}/actions", headers=pharm, json={"action": "start"})).status_code == 403
    assert (await c.post(f"/api/departments/pharmacy/{ref}/actions", headers=pharm, json={"action": "fly"})).status_code == 422
    ok(await c.post(f"/api/departments/documentation/{ref}/actions", headers=doc, json={"action": "update_fields", "params": {"present": fields}}))
    ok(await c.post(f"/api/departments/pharmacy/{ref}/actions", headers=pharm, json={"action": "assign", "params": {"pharmacist": "Joseph P"}}))
    ok(await c.post(f"/api/departments/pharmacy/{ref}/actions", headers=pharm, json={"action": "mark_ready"}))
    ok(await c.post(f"/api/departments/pharmacy/{ref}/actions", headers=pharm, json={"action": "complete_reconciliation"}))
    ok(await c.post(f"/api/departments/insurance/{ref}/actions", headers=ins, json={"action": "submit"}))
    # payer raises a query for a document the hospital already has -> CareFlow proposes attaching it (high risk)
    ok(await c.post(f"/api/departments/insurance/{ref}/actions", headers=ins, json={"action": "add_document", "params": {"document": "itemised pharmacy bill"}}))
    ok(await c.post(f"/api/departments/insurance/{ref}/actions", headers=ins, json={"action": "record_query", "params": {"document": "itemised pharmacy bill"}}))
    pending = [a for a in ok(await c.get("/api/approvals", headers=ins)) if a["workflowId"] == code and a["status"] == "pending"]
    assert [a["action"] for a in pending] == ["attach_document_to_claim"]
    assert (await c.post(f"/api/approvals/{pending[0]['id']}/approve", headers=coord, json={})).status_code == 403
    assert ok(await c.post(f"/api/approvals/{pending[0]['id']}/approve", headers=ins, json={"comment": "Bill is correct"}))["status"] == "verified"

    ok(await c.post(f"/api/departments/insurance/{ref}/actions", headers=ins, json={"action": "record_approval"}))
    ok(await c.post(f"/api/departments/billing/{ref}/actions", headers=bill, json={"action": "start"}))
    assert (await c.post(f"/api/workflows/{code}/discharge", headers=coord)).status_code == 409  # billing not final yet
    ok(await c.post(f"/api/departments/billing/{ref}/actions", headers=bill, json={"action": "finalize"}))

    w = ok(await c.get(f"/api/workflows/{code}", headers=coord))
    assert w["status"] == "cleared" and not w["blockers"] and all(t["status"] == "cleared" for t in w["tasks"])

    # ---- report ---------------------------------------------------------------------------------
    rep = ok(await c.get(f"/api/workflows/{code}/report", headers=coord))
    assert rep["hospital"] == "Sunrise Hospital" and rep["clearanceMinutes"] is not None
    assert all(d["clearedAt"] for d in rep["departments"])
    assert any(b["kind"] == "payer_query" and b["status"] == "resolved" for b in rep["blockers"])
    assert any(a["action"] == "attach_document_to_claim" and a["decidedBy"].startswith("Farah K") for a in rep["actions"])
    assert any(e["event"] == "Clinical discharge authorized" and e["actor"] == "Dr. Kavya Iyer" for e in rep["timeline"])

    # ---- discharge closes everything ------------------------------------------------------------------
    ok(await c.post(f"/api/workflows/{code}/discharge", headers=coord))
    assert ok(await c.get("/api/workflows", headers=coord)) == []
    assert ok(await c.get("/api/workflows?status=discharged", headers=coord))[0]["id"] == code
    assert ok(await c.get("/api/patients", headers=coord)) == []
    assert ok(await c.get("/api/patients?status=discharged", headers=coord))[0]["workflow"]["status"] == "discharged"
    assert (await c.post(f"/api/departments/billing/{ref}/actions", headers=bill, json={"action": "finalize"})).status_code == 409

    csv = (await c.get("/api/reports/workflows.csv", headers=coord)).text.splitlines()
    assert csv[0].startswith("workflow,patient") and code in csv[1] and ",discharged," in csv[1]


async def test_users_and_passwords(api):
    c = api
    t = ok(await c.post("/api/setup", json={"hospitalName": "Hill Hospital", "adminName": "Admin", "adminEmail": "a@h.in", "password": "a-strong-password"}), 201)
    admin = {"authorization": f"Bearer {t['accessToken']}"}
    assert (await c.post("/api/users", headers=admin, json={"name": "Bina", "email": "b@h.in", "role": "Billing", "password": "short"})).status_code == 422
    u = ok(await c.post("/api/users", headers=admin, json={"name": "Bina", "email": "b@h.in", "role": "Billing", "password": "a-strong-password"}), 201)
    b = await as_user(c, "b@h.in")
    assert (await c.post("/api/auth/change-password", headers=b, json={"currentPassword": "wrong-password", "newPassword": "another-strong-one"})).status_code == 400
    assert (await c.post("/api/auth/change-password", headers=b, json={"currentPassword": "a-strong-password", "newPassword": "another-strong-one"})).status_code == 204
    await as_user(c, "b@h.in", "another-strong-one")
    ok(await c.patch(f"/api/users/{u['id']}", headers=admin, json={"isActive": False}))
    assert (await c.get("/api/workflows", headers=b)).status_code == 401  # deactivated mid-session
    me = [x for x in ok(await c.get("/api/users", headers=admin)) if x["email"] == "a@h.in"][0]
    assert (await c.patch(f"/api/users/{me['id']}", headers=admin, json={"isActive": False})).status_code == 409


async def test_fhir_import_requires_configuration(api):
    c = api
    t = ok(await c.post("/api/setup", json={"hospitalName": "Hill Hospital", "adminName": "Admin", "adminEmail": "a@h.in", "password": "a-strong-password"}), 201)
    admin = {"authorization": f"Bearer {t['accessToken']}"}
    r = await c.post("/api/patients/import-fhir", headers=admin, json={"patientId": "p1", "encounterId": "e1", "payer": "Self-pay", "payerType": "self_pay", "attending": "Dr. X"})
    assert r.status_code == 400 and "CAREFLOW_FHIR_BASE_URL" in r.json()["error"]["message"]


async def test_payer_denial_needs_a_person(api):
    c = api
    t = ok(await c.post("/api/setup", json={"hospitalName": "Hill Hospital", "adminName": "Admin", "adminEmail": "a@h.in", "password": "a-strong-password"}), 201)
    admin = {"authorization": f"Bearer {t['accessToken']}"}
    ok(await c.post("/api/users", headers=admin, json={"name": "Dr D", "email": "d@h.in", "role": "Clinician", "password": "a-strong-password"}), 201)
    doc = await as_user(c, "d@h.in")
    pt = ok(await c.post("/api/patients", headers=admin, json={"displayName": "Priya Q", "mrn": "4401", "ward": "W", "payer": "Insurer", "payerType": "cashless", "attending": "Dr D"}), 201)
    code = ok(await c.post(f"/api/patients/{pt['ref']}/authorize-discharge", headers=doc, json={}))["workflowId"]
    ok(await c.post(f"/api/departments/insurance/{pt['ref']}/actions", headers=admin, json={"action": "submit"}))
    ok(await c.post(f"/api/departments/insurance/{pt['ref']}/actions", headers=admin, json={"action": "record_denial", "params": {"reason": "policy lapsed"}}))
    w = ok(await c.get(f"/api/workflows/{code}", headers=admin))
    assert any(b["kind"] == "payer_denied" and "policy lapsed" in b["summary"] for b in w["blockers"])
    assert not [a for a in ok(await c.get("/api/approvals", headers=admin)) if a["workflowId"] == code and "insurance" in a["action"]]
    ok(await c.post(f"/api/departments/insurance/{pt['ref']}/actions", headers=admin, json={"action": "convert_self_pay"}))
    w = ok(await c.get(f"/api/workflows/{code}", headers=admin))
    assert not any(b["department"] == "insurance" for b in w["blockers"])
