"""Archiving a (test) patient: Administrator only, hidden everywhere, nothing deleted, recorded in the audit trail."""
import argparse
import asyncio

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select

from app import cli
from app.config import get_settings
from app.models import AuditLog, Bed, Patient, Workflow


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
    return r.json() if r.content else None


async def _login(c, email):
    t = ok(await c.post("/api/auth/login", json={"email": email, "password": "a-strong-password"}))
    return {"authorization": f"Bearer {t['accessToken']}"}


async def test_archive_hides_a_test_patient_everywhere_but_keeps_its_history(api, sm):
    c = api
    t = ok(await c.post("/api/setup", json={"hospitalName": "Sunrise Hospital", "adminName": "Asha Rao", "adminEmail": "asha@sunrise.in",
                                            "password": "a-strong-password"}), 201)
    admin = {"authorization": f"Bearer {t['accessToken']}"}
    for name, email, role in (("Ravi Menon", "ravi@sunrise.in", "Discharge coordinator"), ("Dr. Kavya Iyer", "kavya@sunrise.in", "Clinician")):
        ok(await c.post("/api/users", headers=admin, json={"name": name, "email": email, "role": role, "password": "a-strong-password"}), 201)
    coord, doctor = await _login(c, "ravi@sunrise.in"), await _login(c, "kavya@sunrise.in")

    await cli.load_examples(argparse.Namespace())
    three = next(p for p in ok(await c.get("/api/patients", headers=admin)) if p["mrn"] == "MRN TEST-0003")
    ok(await c.post(f"/api/patients/{three['ref']}/authorize-discharge", headers=doctor, json={}))
    for _ in range(100):  # wait for the workflow to reach its coordinator approval
        if any(a["status"] == "pending" for a in ok(await c.get("/api/approvals", headers=coord))):
            break
        await asyncio.sleep(0.1)
    assert any(a["status"] == "pending" for a in ok(await c.get("/api/approvals", headers=coord)))

    assert (await c.post(f"/api/patients/{three['ref']}/archive", headers=coord, json={})).status_code == 403  # admins only
    r = ok(await c.post(f"/api/patients/{three['ref']}/archive", headers=admin, json={"reason": "Phone alert test"}))
    assert r["status"] == "archived" and r["workflowsArchived"] == 1
    assert (await c.post(f"/api/patients/{three['ref']}/archive", headers=admin, json={})).status_code == 409

    # Gone from the patient lists, the board, approvals and department queues.
    mrns = {p["mrn"] for p in ok(await c.get("/api/patients", headers=admin))} | {p["mrn"] for p in ok(await c.get("/api/patients?status=discharged", headers=admin))}
    assert "MRN TEST-0003" not in mrns and {"MRN TEST-0001", "MRN TEST-0002"} <= mrns
    assert all(w["patient"]["mrn"] != "MRN TEST-0003" for w in ok(await c.get("/api/workflows", headers=admin)))
    assert ok(await c.get("/api/approvals", headers=coord)) == []
    queue = ok(await c.get("/api/departments/pharmacy/queue", headers=admin))
    assert "MRN TEST-0003" not in str(queue)

    # Nothing deleted; the bed is free; the archive is audited.
    async with sm() as s:
        pt = await s.scalar(select(Patient).where(Patient.mrn == "MRN TEST-0003"))
        assert pt.status == "archived"
        assert await s.scalar(select(func.count()).select_from(Workflow).where(Workflow.patient_id == pt.id, Workflow.deleted_at.is_not(None))) == 1
        assert await s.scalar(select(func.count()).select_from(Bed).where(Bed.patient_ref == pt.ref)) == 0
        entry = await s.scalar(select(AuditLog).where(AuditLog.event == "Archived patient"))
        assert entry is not None and "Phone alert test" in entry.detail and "Asha Rao" in entry.actor
    archived = ok(await c.get("/api/patients?status=archived", headers=admin))
    assert [p["mrn"] for p in archived] == ["MRN TEST-0003"]
