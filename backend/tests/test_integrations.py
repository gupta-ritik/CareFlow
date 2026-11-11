"""HL7 v2, MLLP, JSON connectors, beds, outbound notifications, the director role, and login hardening."""
import asyncio
import socket

import httpx
import pytest
from redis.asyncio import Redis

from app.config import get_settings
from app.integrations import hl7, outbound

PW = "a-strong-password"


# ---- pure HL7 ------------------------------------------------------------------------------------------
A01 = "\r".join([
    "MSH|^~\\&|HIS|SUNRISE|CAREFLOW|SUNRISE|20261106083000||ADT^A01^ADT_A01|MSG0001|P|2.5",
    "EVN|A01|20261106083000",
    "PID|1||70001^^^SUNRISE^MR~X99^^^GOV^NI||Kumar^Arjun^R||19800101|M",
    "PV1|1|I|W3^12^B||||D123^Iyer^Kavya^^^Dr.|||MED|||||||||V5001|||||||||||||||||||||||||20261106080000",
    "IN1|1|STAR01|STAR|Star Health|||||||||||CASHLESS",
])


def test_parse_patient_fields():
    m = hl7.parse(A01)
    assert m.message_type == "ADT^A01" and m.control_id == "MSG0001" and m.sending_app == "HIS"
    f = hl7.patient_fields(m)
    assert f.mrn == "70001" and f.name == "Arjun R Kumar"
    assert (f.ward, f.bed) == ("W3", "B") and f.attending == "Dr. Kavya Iyer"
    assert f.payer == "Star Health" and f.payer_type == "cashless" and f.visit_number == "V5001"
    assert f.admitted_at.isoformat().startswith("2026-11-06T08:00")


def test_parse_handles_newlines_escapes_and_government_payer():
    text = A01.replace("\r", "\n").replace("Star Health", "PM-JAY \\T\\ State").replace("CASHLESS", "GOV")
    f = hl7.patient_fields(hl7.parse(text))
    assert f.payer == "PM-JAY & State" and f.payer_type == "government"


def test_parse_rejects_garbage():
    for bad in ("", "PID|1||x", "MSH|^~\\&|A|B|C|D|2026||ADT^A01|", "MSH|^~\\&|A|B|C|D|2026|||1|P|2.5"):
        with pytest.raises(hl7.Hl7Error):
            hl7.parse(bad)


def test_orders_lab_and_pharmacy():
    orm = "\r".join([
        "MSH|^~\\&|LIS|SUNRISE|CF|SUNRISE|20261106090000||ORU^R01|R1|P|2.5",
        "PID|1||70001^^^SUNRISE^MR||Kumar^Arjun",
        "OBR|1|PL1|LAB-CBC|CBC^Complete blood count|||20261106085000||||||||||||||||||F",
        "OBX|1|NM|HGB||13.2|g/dL",  # values are never read
        "OBR|2|PL2|RAD-1|XR^Chest||||||||||||||||||||RAD|F",
        "ORC|NW|RX9||||||||20261106091500|||||||||||||||||||O",
        "RXE|^^^20261106|PARA500^Paracetamol",
    ])
    items = hl7.orders(hl7.parse(orm))
    assert [(o.kind, o.order_id, o.status) for o in items] == [("lab", "LAB-CBC", "F"), ("pharmacy", "RX9", "")]


def test_ack_and_adt_a03_round_trip():
    m = hl7.parse(A01)
    ack = hl7.parse(hl7.ack(m, "AE", "Unknown | patient"))
    assert ack.message_type.startswith("ACK") and ack.value("MSA", 1) == "AE" and ack.value("MSA", 2) == "MSG0001"
    assert ack.value("MSA", 3) == "Unknown | patient"
    from datetime import datetime, timezone

    a03 = hl7.parse(hl7.adt_a03(facility="Sunrise", mrn="MRN 70001", name="Arjun Kumar", ward="W3", bed="B", attending="Dr. Kavya Iyer",
                                visit_number="V5001", admitted_at=None, discharged_at=datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc)))
    assert a03.message_type == "ADT^A03" and hl7.patient_fields(a03).mrn == "70001"
    assert a03.value("PV1", 19) == "V5001" and a03.value("PV1", 45) == "20261106150000"


def test_outbound_signature_is_hmac_sha256():
    import hashlib
    import hmac

    body = b'{"a":1}'
    assert outbound.sign(body, "k") == "sha256=" + hmac.new(b"k", body, hashlib.sha256).hexdigest()


# ---- API -----------------------------------------------------------------------------------------------
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
            c.app = app
            yield c


def ok(r, code=200):
    assert r.status_code == code, f"{r.status_code}: {r.text}"
    return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text


async def login(c, email, password=PW):
    t = ok(await c.post("/api/auth/login", json={"email": email, "password": password}))
    return {"authorization": f"Bearer {t['accessToken']}"}


async def setup(c):
    t = ok(await c.post("/api/setup", json={"hospitalName": "Sunrise Hospital", "adminName": "Asha Rao", "adminEmail": "asha@sunrise.in", "password": PW}), 201)
    admin = {"authorization": f"Bearer {t['accessToken']}"}
    for name, email, role in [("Dr. Kavya Iyer", "kavya@sunrise.in", "Clinician"), ("Dean Mehta", "dean@sunrise.in", "Hospital director"),
                              ("Ravi Menon", "ravi@sunrise.in", "Discharge coordinator")]:
        ok(await c.post("/api/users", headers=admin, json={"name": name, "email": email, "role": role, "password": PW}), 201)
    return admin


async def until(fn, timeout=20.0):
    """Integration events go through Redis to the embedded worker; poll until the effect shows."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        value = await fn()
        if value:
            return value
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.3)


async def test_hl7_and_connectors_drive_a_full_discharge(api, monkeypatch):
    c = api
    admin = await setup(c)
    sent = []
    monkeypatch.setattr(outbound, "notify", lambda sm, tid, system, event, data, ref=None: sent.append((system, event)))
    monkeypatch.setattr(outbound, "notify_discharge", lambda sm, tid, fac, pt, wf: sent.append(("his", "patient.discharged")))

    keys = {}
    for system in ("his", "tpa", "pharmacy", "lis"):
        k = ok(await c.post("/api/integrations/keys", headers=admin, json={"name": f"{system} interface", "system": system}), 201)
        assert k["key"].startswith("cfk_") and k["prefix"] == k["key"][:12]
        keys[system] = {"x-careflow-key": k["key"]}
    assert all("key" not in k for k in ok(await c.get("/api/integrations/keys", headers=admin)))

    # ---- ADT^A01 admits the patient and occupies the bed --------------------------------------------
    assert (await c.post("/api/integrations/hl7", content=A01)).status_code == 401
    assert (await c.post("/api/integrations/hl7", content=A01, headers=keys["tpa"])).status_code == 403  # wrong system
    r = await c.post("/api/integrations/hl7", content=A01, headers=keys["his"])
    assert r.status_code == 200 and r.headers["x-hl7-ack-code"] == "AA" and "MSA|AA|MSG0001" in r.text
    pts = ok(await c.get("/api/patients?q=70001", headers=admin))
    assert len(pts) == 1 and pts[0]["mrn"] == "MRN 70001" and pts[0]["payerType"] == "cashless" and pts[0]["source"] == "hl7"
    ref = pts[0]["ref"]
    beds = ok(await c.get("/api/beds", headers=admin))
    assert beds["summary"]["occupied"] == 1 and beds["beds"][0]["patientName"] == "Arjun R Kumar"
    dup = await c.post("/api/integrations/hl7", content=A01, headers=keys["his"])
    assert "Duplicate" in dup.text and len(ok(await c.get("/api/patients?q=70001", headers=admin))) == 1

    # Unknown patient -> AE; unsupported type -> AR
    unknown = A01.replace("ADT^A01^ADT_A01|MSG0001", "ORU^R01|MSG0002").replace("70001", "99999")
    assert (await c.post("/api/integrations/hl7", content=unknown, headers=keys["his"])).headers["x-hl7-ack-code"] == "AE"
    assert (await c.post("/api/integrations/hl7", content=A01.replace("ADT^A01^ADT_A01|MSG0001", "SIU^S12|MSG0003"),
                         headers=keys["his"])).headers["x-hl7-ack-code"] == "AR"

    # ---- lab order (ORM) creates a pending report; inpatient pharmacy order before authorization is ignored -------
    orm = "\r".join(["MSH|^~\\&|HIS|SUNRISE|CF|SUNRISE|20261106090000||ORM^O01|MSG0010|P|2.5", "PID|1||70001^^^SUNRISE^MR||Kumar^Arjun",
                     "ORC|NW|PL1", "OBR|1|PL1|LAB-CBC|CBC", "ORC|NW|RX1|||||||20261106090000", "RXO|PARA500"])
    r = await c.post("/api/integrations/hl7", content=orm, headers=keys["his"])
    assert r.headers["x-hl7-ack-code"] == "AA" and "not tracked" in r.text
    pts = ok(await c.get(f"/api/patients?q=70001", headers=admin))
    assert "laboratory" in pts[0]["departments"]

    # ---- clinician authorizes; the agents see insurance, documentation, lab --------------------------------
    doc = await login(c, "kavya@sunrise.in")
    wf = ok(await c.post(f"/api/patients/{ref}/authorize-discharge", headers=doc, json={}))["workflowId"]

    # ---- JSON connectors move each department -----------------------------------------------------------
    async def event(system, ev, data=None, eid=None, code=200):
        body = {"event": ev, "mrn": "70001", "data": data or {}, **({"eventId": eid} if eid else {})}
        return ok(await c.post(f"/api/integrations/{system}/events", headers=keys[system], json=body), code)

    assert (await event("tpa", "approved_typo", code=422)) is not None
    await event("tpa", "preauth_submitted", eid="tpa-1")
    assert (await event("tpa", "preauth_submitted", eid="tpa-1"))["status"] == "duplicate"
    await event("tpa", "approved", eid="tpa-2")
    await event("lis", "report_final", {"reportId": "LAB-CBC"})
    await event("lis", "report_acknowledged", {"reportId": "LAB-CBC"})
    await event("pharmacy", "order_placed")
    await event("pharmacy", "dispensed")
    await event("pharmacy", "reconciled")
    msgs = ok(await c.get("/api/integrations/messages?direction=inbound", headers=admin))
    assert any(m["status"] == "duplicate" for m in msgs) and any(m["status"] == "rejected" for m in msgs)

    # documentation and billing by staff, then the workflow clears
    fields = ok(await c.get("/api/departments/documentation/queue", headers=admin))[0]["record"]["required"]
    ok(await c.post(f"/api/departments/documentation/{ref}/actions", headers=admin, json={"action": "update_fields", "params": {"present": fields}}))
    ok(await c.post(f"/api/departments/billing/{ref}/actions", headers=admin, json={"action": "finalize"}))
    assert ("his", "final_bill.ready") in sent

    async def cleared():
        w = ok(await c.get(f"/api/workflows/{wf}", headers=admin))
        return w if w["status"] == "cleared" else None

    await until(cleared)

    # ---- the director sees everything, can't do department work, gets the executive overview --------------
    dean = await login(c, "dean@sunrise.in")
    assert ok(await c.get("/api/departments/pharmacy/queue", headers=dean))
    assert (await c.post(f"/api/departments/billing/{ref}/actions", headers=dean, json={"action": "start"})).status_code == 403
    assert (await c.post("/api/users", headers=dean, json={"name": "X", "email": "x@sunrise.in", "role": "Billing", "password": PW})).status_code == 403
    assert (await c.post(f"/api/patients/{ref}/authorize-discharge", headers=dean, json={})).status_code == 403
    depts = ok(await c.get("/api/departments", headers=dean))
    assert len(depts) == 5 and not any(d["canAct"] for d in depts)
    ov = ok(await c.get("/api/executive/overview", headers=dean))
    assert ov["census"]["readyToDischarge"] == 1 and ov["beds"]["occupied"] == 1 and ov["integrations"]["tpa"]["messages24h"] >= 2
    assert ov["longestWaits"][0]["patient"] == "Arjun R Kumar"
    assert ok(await c.get("/api/integrations/status", headers=dean))["systems"]
    coord = await login(c, "ravi@sunrise.in")
    assert (await c.get("/api/executive/overview", headers=coord)).status_code == 403

    # ---- the director closes the discharge; the bed goes to cleaning; HIS is told ---------------------------
    closed = ok(await c.post(f"/api/workflows/{wf}/discharge", headers=dean))
    assert closed["bedsReleased"] == ["W3/B"] and ("his", "patient.discharged") in sent
    bed = ok(await c.get("/api/beds", headers=admin))["beds"][0]
    assert bed["status"] == "cleaning" and bed["patientRef"] is None
    assert ok(await c.patch(f"/api/beds/{bed['id']}", headers=coord, json={"status": "available"}))["status"] == "available"


async def test_his_discharge_before_clearance_is_a_warning(api):
    c = api
    admin = await setup(c)
    ok(await c.post("/api/integrations/hl7", content=A01, headers=admin))
    ref = ok(await c.get("/api/patients?q=70001", headers=admin))[0]["ref"]
    ok(await c.post(f"/api/patients/{ref}/authorize-discharge", headers=await login(c, "kavya@sunrise.in"), json={}))
    a03 = A01.replace("ADT^A01^ADT_A01|MSG0001", "ADT^A03^ADT_A03|MSG0099")
    r = await c.post("/api/integrations/hl7", content=a03, headers=admin)
    assert r.headers["x-hl7-ack-code"] == "AA" and "left open for staff" in r.text
    assert ok(await c.get("/api/beds", headers=admin))["summary"]["cleaning"] == 1
    assert ok(await c.get("/api/integrations/messages", headers=admin))[0]["status"] == "warning"


async def test_bed_management(api):
    c = api
    admin = await setup(c)
    ok(await c.post("/api/beds", headers=admin, json={"ward": "W1", "beds": ["1", "2", "2"]}), 201)
    pt = ok(await c.post("/api/patients", headers=admin, json={"displayName": "Lata K", "mrn": "500", "ward": "W1", "bed": "1", "payer": "Self",
                                                              "payerType": "self_pay", "attending": "Dr. X"}), 201)
    beds = ok(await c.get("/api/beds", headers=admin))
    assert beds["summary"] == {"total": 2, "available": 1, "occupied": 1, "cleaning": 0, "blocked": 0, "occupancy": 0.5}
    occupied = next(b for b in beds["beds"] if b["status"] == "occupied")
    assert (await c.patch(f"/api/beds/{occupied['id']}", headers=admin, json={"status": "available"})).status_code == 409
    ok(await c.patch(f"/api/patients/{pt['ref']}", headers=admin, json={"bed": "2"}))  # transfer
    beds = {b["bed"]: b["status"] for b in ok(await c.get("/api/beds", headers=admin))["beds"]}
    assert beds == {"1": "cleaning", "2": "occupied"}
    dean = await login(c, "dean@sunrise.in")
    assert (await c.post("/api/beds", headers=dean, json={"ward": "W2", "beds": ["1"]})).status_code == 403


async def test_mllp_listener_round_trip(api):
    from app.integrations.mllp import Listener
    from app.integrations.service import Bus

    c = api
    admin = await setup(c)
    app = c.app
    listener = Listener(app.state.sessionmaker, Bus(app.state.redis, app.state.engine), None, [])
    server = await asyncio.start_server(listener.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        reply = hl7.parse(await outbound.mllp_send("127.0.0.1", port, A01))
        assert reply.value("MSA", 1) == "AA" and reply.value("MSA", 2) == "MSG0001"
        bad = await outbound.mllp_send("127.0.0.1", port, "MSH|^~\\&|X|Y|Z|W|2026||ADT^A01||P|2.5")
        assert hl7.parse(bad).value("MSA", 1) == "AR"
    finally:
        server.close()
        await server.wait_closed()
    assert ok(await c.get("/api/patients?q=70001", headers=admin))


async def test_outbound_webhook_delivery_is_signed_and_logged(api, monkeypatch):
    """A real local HTTP receiver gets the signed webhook; the attempt is logged."""
    import hashlib
    import hmac
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    received = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["content-length"]))
            received.append((self.headers["x-careflow-signature"], body))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    with socket.socket() as s_:
        s_.bind(("127.0.0.1", 0))
        port = s_.getsockname()[1]
    srv = HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = api
        admin = await setup(c)
        monkeypatch.setattr(get_settings(), "tpa_webhook_url", f"http://127.0.0.1:{port}/careflow")
        monkeypatch.setattr(get_settings(), "outbound_secret", "shared-secret")
        from app.models import Tenant
        from sqlalchemy import select

        async with c.app.state.sessionmaker() as s:
            tid = (await s.scalar(select(Tenant))).id
        assert await outbound.deliver_webhook(c.app.state.sessionmaker, tid, "tpa", "preauth.final_submitted", {"claim": "CL-1"}, backoff=0)
        sig, body = received[0]
        assert sig == "sha256=" + hmac.new(b"shared-secret", body, hashlib.sha256).hexdigest()
        assert json.loads(body)["event"] == "preauth.final_submitted"
        log = ok(await c.get("/api/integrations/messages?direction=outbound", headers=admin))
        assert log[0]["status"] == "sent" and log[0]["type"] == "preauth.final_submitted"
    finally:
        srv.shutdown()


async def test_login_lockout_and_password_policy(api):
    c = api
    admin = await setup(c)
    for _ in range(5):
        assert (await c.post("/api/auth/login", json={"email": "ravi@sunrise.in", "password": "wrong-password-1"})).status_code == 401
    r = await c.post("/api/auth/login", json={"email": "ravi@sunrise.in", "password": PW})
    assert r.status_code == 423 and "locked" in r.json()["error"]["message"]
    users = ok(await c.get("/api/users", headers=admin))
    ravi = next(u for u in users if u["email"] == "ravi@sunrise.in")
    assert ravi["locked"] is True
    ok(await c.patch(f"/api/users/{ravi['id']}", headers=admin, json={"isActive": True}))  # admin unlocks
    await login(c, "ravi@sunrise.in")
    for weak in ("short-1", "alllowercaseletters", "ravi2-likes-tea-123"):
        r = await c.post("/api/users", headers=admin, json={"name": "New", "email": "ravi2@sunrise.in", "role": "Billing", "password": weak})
        assert r.status_code == 422, weak
    r = await c.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"


async def test_director_can_decide_any_approval(api):
    c = api
    admin = await setup(c)
    pt = ok(await c.post("/api/patients", headers=admin, json={"displayName": "Mohan D", "mrn": "600", "ward": "W2", "bed": "4", "payer": "Star",
                                                              "payerType": "cashless", "attending": "Dr. X"}), 201)
    ref = pt["ref"]
    wf = ok(await c.post(f"/api/patients/{ref}/authorize-discharge", headers=await login(c, "kavya@sunrise.in"), json={}))["workflowId"]
    for action, params in (("add_document", {"document": "discharge summary"}), ("submit", {}), ("record_query", {"document": "discharge summary"})):
        ok(await c.post(f"/api/departments/insurance/{ref}/actions", headers=admin, json={"action": action, "params": params}))
    pending = [a for a in ok(await c.get("/api/approvals?status=pending", headers=admin)) if a["workflowId"] == wf]
    assert pending and pending[0]["approverRole"] == "Insurance desk lead" and pending[0]["risk"] == "high"
    coord = await login(c, "ravi@sunrise.in")
    assert (await c.post(f"/api/approvals/{pending[0]['id']}/approve", headers=coord, json={})).status_code == 403
    dean = await login(c, "dean@sunrise.in")
    assert ok(await c.post(f"/api/approvals/{pending[0]['id']}/approve", headers=dean, json={"comment": "ok"}))["status"] in ("verified", "executed", "approved")


def _adt(event: str, control_id: str, mrn: str = "80001", name: str = "Kumar^Ravi", location: str = "3A^^12",
         in1: str | None = "IN1|1|STAR01|STAR|Star Health", financial: str = "") -> str:
    """Build an ADT message like the hospital system sends (PV1-20 = financial class)."""
    pv1 = ["PV1", "1", "I", location, "", "", "", "D123^Iyer^Kavya^^^Dr."] + [""] * 11 + ["V5501", financial]
    segs = [f"MSH|^~\\&|HIS|CITYHOSP|CAREFLOW|CITYHOSP|20261107093000||ADT^{event}|{control_id}|P|2.5",
            f"EVN|{event}|20261107093000",
            f"PID|1||{mrn}^^^CITYHOSP^MR||{name}||19800512|M",
            "|".join(pv1)]
    if in1:
        segs.append(in1)
    return "\r".join(segs)


async def test_automatic_admission_from_the_hospital_system(api):
    """Manual test plan TC-71 to TC-80: what an HL7 admission creates, updates and refuses."""
    c = api
    admin = await setup(c)
    his = {"x-careflow-key": ok(await c.post("/api/integrations/keys", headers=admin, json={"name": "HIS", "system": "his"}), 201)["key"]}

    async def send(msg: str) -> httpx.Response:
        return await c.post("/api/integrations/hl7", content=msg, headers=his)

    async def patient(mrn: str) -> list[dict]:
        return ok(await c.get(f"/api/patients?status=all&q={mrn}", headers=admin))

    # TC-71: every field lands where it should; the right departments and the bed follow
    r = await send(_adt("A01", "ADM-1"))
    assert r.headers["x-hl7-ack-code"] == "AA" and "MSA|AA|ADM-1" in r.text
    [pt] = await patient("80001")
    assert (pt["displayName"], pt["mrn"], pt["ward"], pt["bed"]) == ("Ravi Kumar", "MRN 80001", "3A", "12")
    assert (pt["attending"], pt["payer"], pt["payerType"], pt["source"]) == ("Dr. Kavya Iyer", "Star Health", "cashless", "hl7")
    assert pt["departments"] == ["documentation", "pharmacy", "insurance", "billing"] and pt["workflow"] is None
    beds = ok(await c.get("/api/beds", headers=admin))["beds"]
    assert any(b["ward"] == "3A" and b["bed"] == "12" and b["status"] == "occupied" for b in beds)

    # TC-72 / TC-73: payment type is worked out from the insurance details
    await send(_adt("A01", "ADM-2", mrn="80002", name="Devi^Lakshmi", location="3A^^14", in1="IN1|1|PMJAY01|PMJAY|Ayushman Bharat PM-JAY"))
    await send(_adt("A01", "ADM-3", mrn="80003", name="Singh^Arjun", location="3A^^15", in1=None))
    assert (await patient("80002"))[0]["payerType"] == "government"
    [self_pay] = await patient("80003")
    assert self_pay["payerType"] == "self_pay" and self_pay["payer"] == "Self-pay" and "insurance" not in self_pay["departments"]

    # TC-74: the same patient sent again with a new message ID updates instead of duplicating
    await send(_adt("A01", "ADM-4", name="Kumar^Ravi^S"))
    assert [p["displayName"] for p in await patient("80001")] == ["Ravi S Kumar"]

    # TC-75: ADT^A08 updates details; a new bed moves the patient
    r = await send(_adt("A08", "UPD-1", location="4B^^20"))
    assert r.headers["x-hl7-ack-code"] == "AA"
    [pt] = await patient("80001")
    assert (pt["ward"], pt["bed"]) == ("4B", "20")
    beds = {(b["ward"], b["bed"]): b["status"] for b in ok(await c.get("/api/beds", headers=admin))["beds"]}
    assert beds[("4B", "20")] == "occupied" and beds[("3A", "12")] == "cleaning"

    # TC-76: a message without the patient ID is refused and creates nothing
    before = len(ok(await c.get("/api/patients?status=all", headers=admin)))
    r = await send(_adt("A01", "BAD-1").replace("PID|1||80001^^^CITYHOSP^MR|", "PID|1|||"))
    assert r.headers["x-hl7-ack-code"] == "AE" and "PID-3" in r.text
    assert len(ok(await c.get("/api/patients?status=all", headers=admin))) == before

    # TC-77: discharged in the hospital system, then admitted again = readmission of the same patient
    r = await send(_adt("A03", "DIS-1", mrn="80003", location="3A^^15", in1=None))
    assert r.headers["x-hl7-ack-code"] == "AA" and (await patient("80003"))[0]["status"] == "discharged"
    await send(_adt("A01", "ADM-5", mrn="80003", location="3A^^16", in1=None))
    [again] = await patient("80003")
    assert again["status"] == "admitted" and again["bed"] == "16" and again["ward"] == "3A"

    # TC-78: only the hospital-system key (or an administrator) may send HL7
    assert (await c.post("/api/integrations/hl7", content=_adt("A01", "SEC-1", mrn="80009"))).status_code == 401
    assert (await c.post("/api/integrations/hl7", content=_adt("A01", "SEC-2", mrn="80009"),
                         headers={"x-careflow-key": "cfk_not_a_real_key"})).status_code == 401
    coord = await login(c, "ravi@sunrise.in")
    assert (await c.post("/api/integrations/hl7", content=_adt("A01", "SEC-3", mrn="80009"), headers=coord)).status_code == 403
    assert await patient("80009") == []

    # TC-79: every message is in the log with what CareFlow did
    log = ok(await c.get("/api/integrations/messages?limit=50", headers=admin))
    rows = log if isinstance(log, list) else log.get("messages", log.get("items", []))
    by_id = {m["controlId"]: m["status"] for m in rows}
    assert by_id["ADM-1"] == "accepted" and by_id["BAD-1"] == "error" and by_id["DIS-1"] == "warning"
