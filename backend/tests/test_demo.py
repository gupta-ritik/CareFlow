"""Public demo mode (app/demo.py): one-click sign-in per role, its own synthetic hospital, never touching real data."""
import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select

from app import demo
from app.config import get_settings
from app.models import Patient, Tenant


async def _client(monkeypatch, demo_mode: bool):
    s = get_settings()
    monkeypatch.setattr(s, "seed_demo", False)
    monkeypatch.setattr(s, "demo_mode", demo_mode)
    monkeypatch.setattr(s, "demo_history_days", 3)  # keep the test fast; production uses 60
    r = Redis.from_url("redis://localhost:6379/15")
    await r.flushdb()
    await r.aclose()
    from app.main import create_app

    return create_app()


@pytest.fixture
async def demo_api(sm, monkeypatch):
    app = await _client(monkeypatch, True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c, app


@pytest.fixture
async def plain_api(sm, monkeypatch):
    app = await _client(monkeypatch, False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c


async def _built():
    if demo._task is not None:
        await demo._task


def ok(r, code=200):
    assert r.status_code == code, f"{r.status_code}: {r.text}"
    return r.json()


async def test_demo_is_off_by_default(plain_api):
    assert ok(await plain_api.get("/api/demo")) == {"enabled": False}
    assert (await plain_api.post("/api/demo/login", json={"role": "director"})).status_code == 404


async def test_demo_sign_in_builds_its_own_hospital(demo_api, sm):
    c, _ = demo_api
    info = ok(await c.get("/api/demo"))
    keys = [r["key"] for r in info["roles"]]
    assert info["enabled"] is True and keys[0] == "director"
    assert "Administrator" not in [r["role"] for r in info["roles"]]  # visitors can't manage users or integration keys

    t = ok(await c.post("/api/demo/login", json={"role": "director"}))
    assert t["user"]["demo"] is True and t["user"]["role"] == "Hospital director" and t["home"] == "/director"
    await _built()
    director = {"authorization": f"Bearer {t['accessToken']}"}
    workflows = ok(await c.get("/api/workflows", headers=director))
    assert len(workflows) >= 8  # the scripted live patients are moving through the real workflow

    async with sm() as s:
        tenant = await s.scalar(select(Tenant).where(Tenant.slug == demo.DEMO_SLUG))
        history = await s.scalar(select(func.count()).select_from(Patient).where(Patient.tenant_id == tenant.id, Patient.status == "discharged"))
    assert history > 0  # synthetic history behind the Director dashboard and Ask CareFlow

    for key in keys:
        assert ok(await c.post("/api/demo/login", json={"role": key}))["user"]["demoRole"] == key
    assert (await c.post("/api/demo/login", json={"role": "admin"})).status_code == 422


async def test_demo_accounts_have_no_usable_password(demo_api):
    c, _ = demo_api
    ok(await c.post("/api/demo/login", json={"role": "coordinator"}))
    r = await c.post("/api/auth/login", json={"email": demo.demo_email("coordinator"), "password": "careflow-demo"})
    assert r.status_code == 401


async def test_demo_never_touches_the_real_hospital(demo_api, sm):
    c, app = demo_api
    # Demo accounts don't count as "set up": a fresh install still shows the first-run setup page.
    ok(await c.post("/api/demo/login", json={"role": "ward-clerk"}))
    await _built()
    assert ok(await c.get("/api/setup/status"))["needsSetup"] is True

    t = ok(await c.post("/api/setup", json={"hospitalName": "Sunrise Hospital", "adminName": "Asha Rao",
                                            "adminEmail": "asha@sunrise.in", "password": "a-strong-password"}), 201)
    admin = {"authorization": f"Bearer {t['accessToken']}"}
    ok(await c.post("/api/patients", headers=admin, json={"displayName": "Suresh Babu", "mrn": "88120", "ward": "Ward 3", "bed": "12",
                                                         "payer": "Self", "payerType": "self_pay", "attending": "Dr. Kavya Iyer"}), 201)
    assert ok(await c.get("/api/workflows", headers=admin)) == []  # demo workflows are invisible to the real hospital

    async with sm() as s:
        real = await s.scalar(select(Tenant).where(Tenant.slug == "sunrise-hospital"))
        before = await s.scalar(select(func.count()).select_from(Patient).where(Patient.tenant_id == real.id))
    await demo.rebuild(app.state.engine, sm, get_settings())  # a daily reset
    async with sm() as s:
        after = await s.scalar(select(func.count()).select_from(Patient).where(Patient.tenant_id == real.id))
    assert before == after == 1
