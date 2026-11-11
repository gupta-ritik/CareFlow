"""Phone alerts (app/notify.py): Web Push and Telegram. Telegram and the push services are faked; nothing leaves the test."""
import asyncio
import base64
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from redis.asyncio import Redis
from sqlalchemy import select

from app import notify
from app.config import get_settings
from app.engine import start_for_patient
from app.models import NotificationChannel, User

PUSH_ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"
KEYS = {"p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM", "auth": "tBHItJI5svbpez7KI4CCXg"}


def _vapid():
    k = ec.generate_private_key(ec.SECP256R1())
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
    return (b64(k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)),
            b64(k.private_numbers().private_value.to_bytes(32, "big")))


@pytest.fixture
def configured(monkeypatch):
    """Both channels switched on, with Telegram's API replaced by a recorder."""
    s = get_settings()
    pub, priv = _vapid()
    monkeypatch.setattr(s, "vapid_public_key", pub)
    monkeypatch.setattr(s, "vapid_private_key", priv)
    monkeypatch.setattr(s, "telegram_bot_token", "123:test-token")
    monkeypatch.setattr(s, "public_web_url", "https://careflow.example.org")
    monkeypatch.setattr(notify, "_bot_username", "careflow_test_bot")
    calls: list[tuple[str, dict]] = []

    async def fake_telegram(_s, method, payload):
        calls.append((method, payload))
        return {"ok": True, "result": {}}

    monkeypatch.setattr(notify, "telegram_call", fake_telegram)
    return calls


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


async def _drain():
    while notify._tasks:
        await asyncio.gather(*list(notify._tasks))


def ok(r, code=200):
    assert r.status_code == code, f"{r.status_code}: {r.text}"
    return r.json() if r.content else None


async def _admin(c):
    t = ok(await c.post("/api/setup", json={"hospitalName": "Sunrise Hospital", "adminName": "Asha Rao", "adminEmail": "asha@sunrise.in",
                                            "password": "a-strong-password"}), 201)
    return {"authorization": f"Bearer {t['accessToken']}"}


# ---- pure helpers ---------------------------------------------------------------------------------------------------
def test_telegram_link_codes_are_signed_and_expire():
    s = get_settings()
    uid = uuid.uuid4()
    code = notify.link_code(uid, s, now=1_000_000)
    assert len(code) == 56 and notify.read_link_code(code, s, now=1_000_000 + 60) == uid
    assert notify.read_link_code(code, s, now=1_000_000 + notify.LINK_TTL_SECONDS + 1) is None  # expired
    forged = uuid.uuid4().hex + code[32:]
    assert notify.read_link_code(forged, s, now=1_000_000) is None  # someone else's id with this signature
    assert notify.read_link_code("not-a-code", s) is None


def test_only_real_push_services_are_accepted():
    assert notify.push_host_allowed(PUSH_ENDPOINT)
    assert notify.push_host_allowed("https://web.push.apple.com/QGuQyavXut")
    assert not notify.push_host_allowed("http://fcm.googleapis.com/x")  # not https
    assert not notify.push_host_allowed("https://169.254.169.254/latest")  # internal address
    assert not notify.push_host_allowed("https://fcm.googleapis.com.evil.example/x")


def test_alerts_never_carry_patient_identity():
    a = notify.reminder_alert("AC-1", "pharmacy", "Ward 4B", "Bed 12", "Meera Iyer MRN 4471209: medicines not ready",
                              ["Meera Iyer", "MRN 4471209", "Meera", "Iyer"])
    assert "Meera" not in a.body and "4471209" not in a.body and a.body.startswith("Ward 4B · Bed 12:")
    assert notify.dept_roles("pharmacy") == ["Pharmacy"]  # the Administrator isn't paged for every department


# ---- API --------------------------------------------------------------------------------------------------------------
async def test_web_push_subscription_and_test_alert(api, sm, configured, monkeypatch):
    admin = await _admin(api)
    info = ok(await api.get("/api/notifications", headers=admin))
    assert info["webPush"]["enabled"] and info["webPush"]["publicKey"] and info["telegram"]["enabled"] and info["channels"] == []

    bad = await api.post("/api/notifications/webpush", headers=admin, json={"endpoint": "https://10.0.0.5/push", "keys": KEYS})
    assert bad.status_code == 422
    ch = ok(await api.post("/api/notifications/webpush", headers=admin, json={"endpoint": PUSH_ENDPOINT, "keys": KEYS, "label": "Chrome on Android"}), 201)

    sent = []
    monkeypatch.setattr("pywebpush.webpush", lambda **kw: sent.append(kw))
    r = ok(await api.post("/api/notifications/test", headers=admin))
    assert r == {"sent": 1, "channels": 1}
    assert sent[0]["subscription_info"]["endpoint"] == PUSH_ENDPOINT and '"CareFlow test alert"' in sent[0]["data"]

    from pywebpush import WebPushException

    def gone(**kw):
        raise WebPushException("gone", response=httpx.Response(410))

    monkeypatch.setattr("pywebpush.webpush", gone)
    assert ok(await api.post("/api/notifications/test", headers=admin))["sent"] == 0
    async with sm() as s:  # the uninstalled subscription was cleaned up
        assert await s.get(NotificationChannel, uuid.UUID(ch["id"])) is None


async def test_telegram_connect_and_disconnect(api, sm, configured):
    admin = await _admin(api)
    link = ok(await api.post("/api/notifications/telegram/link", headers=admin))
    assert link["url"].startswith("https://t.me/careflow_test_bot?start=")
    code = link["url"].split("start=")[1]

    s = get_settings()
    hook = "/api/telegram/webhook"
    update = {"message": {"chat": {"id": 555, "type": "private"}, "from": {"username": "asha"}, "text": f"/start {code}"}}
    assert (await api.post(hook, json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})).status_code == 403
    ok(await api.post(hook, json=update, headers={"X-Telegram-Bot-Api-Secret-Token": notify.webhook_secret(s)}))
    channels = ok(await api.get("/api/notifications", headers=admin))["channels"]
    assert [(c["kind"], c["label"]) for c in channels] == [("telegram", "Telegram @asha")]
    assert "Connected to CareFlow as Asha Rao" in configured[-1][1]["text"]

    bad = {"message": {"chat": {"id": 556, "type": "private"}, "text": "/start " + "0" * 56}}
    ok(await api.post(hook, json=bad, headers={"X-Telegram-Bot-Api-Secret-Token": notify.webhook_secret(s)}))
    assert "expired" in configured[-1][1]["text"]

    stop = {"message": {"chat": {"id": 555, "type": "private"}, "text": "/stop"}}
    ok(await api.post(hook, json=stop, headers={"X-Telegram-Bot-Api-Secret-Token": notify.webhook_secret(s)}))
    assert ok(await api.get("/api/notifications", headers=admin))["channels"] == []


async def test_demo_accounts_cannot_add_alerts(api, configured, monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_mode", True)
    t = ok(await api.post("/api/demo/login", json={"role": "pharmacy"}))
    demo = {"authorization": f"Bearer {t['accessToken']}"}
    assert ok(await api.get("/api/notifications", headers=demo))["demo"] is True
    assert (await api.post("/api/notifications/webpush", headers=demo, json={"endpoint": PUSH_ENDPOINT, "keys": KEYS})).status_code == 403
    assert (await api.post("/api/notifications/telegram/link", headers=demo)).status_code == 403


async def test_people_can_only_remove_their_own_channels(api, sm, configured):
    admin = await _admin(api)
    ch = ok(await api.post("/api/notifications/webpush", headers=admin, json={"endpoint": PUSH_ENDPOINT, "keys": KEYS}), 201)
    ok(await api.post("/api/users", headers=admin, json={"name": "Ravi Menon", "email": "ravi@sunrise.in", "role": "Discharge coordinator",
                                                        "password": "a-strong-password"}), 201)
    t = ok(await api.post("/api/auth/login", json={"email": "ravi@sunrise.in", "password": "a-strong-password"}))
    ravi = {"authorization": f"Bearer {t['accessToken']}"}
    assert (await api.delete(f"/api/notifications/{ch['id']}", headers=ravi)).status_code == 404
    assert (await api.delete(f"/api/notifications/{ch['id']}", headers=admin)).status_code == 204


# ---- the workflow sends alerts ----------------------------------------------------------------------------------------
async def test_approval_needed_alerts_the_approver_role(sm, tenant, seeded, engine, configured):
    async with sm() as s:  # the demo coordinator (seed) has Telegram connected; the pharmacist has too
        for email, chat in (("priya@demo-hospital.test", "900"), ("neha@demo-hospital.test", "901")):
            user = await s.scalar(select(User).where(User.email == email))
            await notify.save_channel(s, user, "telegram", chat, None, "Telegram")
        await s.commit()

    ref, attending, minutes = next(p for p in seeded if p[0] == "pt-8812")  # Meera: pharmacy delay -> escalation needs coordinator approval
    await start_for_patient(engine, tenant.id, ref, attending, (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat())
    await _drain()

    sent = [p for m, p in configured if m == "sendMessage"]
    to_coordinator = [p for p in sent if p["chat_id"] == "900"]
    assert to_coordinator, sent
    text = to_coordinator[0]["text"]
    assert "Approval needed · Pharmacy" in text and "Ward 4B · Bed 12" in text and "Meera" not in text and "4471209" not in text
    assert to_coordinator[0]["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://careflow.example.org/approvals"
    assert not any(p["chat_id"] == "901" and "Approval needed" in p["text"] for p in sent)  # pharmacy can't approve it


async def test_nothing_is_sent_when_alerts_are_not_configured(sm, tenant, seeded, engine, monkeypatch):
    called = []
    monkeypatch.setattr(notify, "deliver", lambda *a, **k: called.append(a))
    ref, attending, minutes = seeded[0]
    await start_for_patient(engine, tenant.id, ref, attending, (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat())
    assert not notify._tasks and called == []


async def test_load_examples_third_patient_alerts_the_coordinator(api, sm, configured):
    """`python -m app.cli load-examples` + authorizing Test Patient Three = a coordinator approval alert (manual phone test)."""
    import argparse

    from app import cli

    admin = await _admin(api)
    for name, email, role in (("Ravi Menon", "ravi@sunrise.in", "Discharge coordinator"), ("Dr. Kavya Iyer", "kavya@sunrise.in", "Clinician")):
        ok(await api.post("/api/users", headers=admin, json={"name": name, "email": email, "role": role, "password": "a-strong-password"}), 201)
    async with sm() as s:
        ravi = await s.scalar(select(User).where(User.email == "ravi@sunrise.in"))
        await notify.save_channel(s, ravi, "telegram", "777", None, "Telegram @ravi")
        await s.commit()

    await cli.load_examples(argparse.Namespace())
    t = ok(await api.post("/api/auth/login", json={"email": "kavya@sunrise.in", "password": "a-strong-password"}))
    doctor = {"authorization": f"Bearer {t['accessToken']}"}
    patients = ok(await api.get("/api/patients", headers=admin))
    three = next(p for p in patients if p["mrn"] == "MRN TEST-0003")
    ok(await api.post(f"/api/patients/{three['ref']}/authorize-discharge", headers=doctor, json={}))
    for _ in range(100):  # the workflow runs on the event consumer; wait for its alert
        await _drain()
        if any(p.get("chat_id") == "777" for m, p in configured if m == "sendMessage"):
            break
        await asyncio.sleep(0.1)
    alert = next(p for m, p in configured if m == "sendMessage" and p.get("chat_id") == "777")
    assert "Approval needed · Pharmacy" in alert["text"] and "Ward 3A · Bed 11" in alert["text"] and "Escalate pharmacy task" in alert["text"]
