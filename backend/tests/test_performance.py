"""Database round-trip reductions: block-reserved codes, batched list screens, the Director dashboard cache."""
import sys

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select

from app.cache import Cache
from app.config import get_settings
from app.models import Tenant
from app.repo import next_code


@pytest.fixture
async def api(sm, monkeypatch):
    monkeypatch.setattr(get_settings(), "seed_demo", False)
    monkeypatch.setattr(get_settings(), "db_stats", True)
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
    return r.json()


async def _setup(c):
    t = ok(await c.post("/api/setup", json={"hospitalName": "Perf Hospital", "adminName": "Asha Rao",
                                            "adminEmail": "asha@perf.in", "password": "a-strong-password"}), 201)
    return {"authorization": f"Bearer {t['accessToken']}"}


async def _admit(c, admin, n: int, start: int = 0):
    for i in range(start, start + n):
        ok(await c.post("/api/patients", headers=admin, json={"displayName": f"Patient {i}", "mrn": f"P-{i:04d}", "ward": "Ward 1", "bed": "",
                                                               "payer": "Star Health", "payerType": "cashless", "attending": "Dr. A"}), 201)


async def test_block_codes_are_unique_and_survive_rollback(sm, tenant):
    seen = []
    for i in range(6):
        async with sm() as s:
            seen += [await next_code(s, "AU") for _ in range(20)]
            seen.append(await next_code(s, "EV"))
            if i % 2:
                await s.rollback()  # the reserved block must not return to the counter, or another process could reuse it
            else:
                await s.commit()
    assert len(seen) == len(set(seen))
    async with sm() as s:
        wf = [await next_code(s, "WF") for _ in range(3)]
        await s.commit()
    nums = [int(c.split("-")[1]) for c in wf]
    assert nums == list(range(nums[0], nums[0] + 3))  # workflow codes people quote stay gapless


async def test_patient_list_query_count_does_not_grow_with_patients(api):
    c = api
    admin = await _setup(c)
    await _admit(c, admin, 2)
    r1 = await c.get("/api/patients", headers=admin)
    await _admit(c, admin, 6, start=2)
    r2 = await c.get("/api/patients", headers=admin)
    assert len(ok(r1)) == 2 and len(ok(r2)) == 8
    assert r1.headers["X-DB-Queries"] == r2.headers["X-DB-Queries"]
    assert int(r2.headers["X-DB-Queries"]) <= 6


async def test_director_dashboard_is_cached_and_invalidated(api, sm):
    c = api
    admin = await _setup(c)
    ok(await c.post("/api/users", headers=admin, json={"name": "Dean", "email": "dean@perf.in", "role": "Hospital director",
                                                       "password": "a-strong-password"}), 201)
    t = ok(await c.post("/api/auth/login", json={"email": "dean@perf.in", "password": "a-strong-password"}))
    dean = {"authorization": f"Bearer {t['accessToken']}"}
    await _admit(c, admin, 2)

    first = await c.get("/api/executive/overview?days=30", headers=dean)
    second = await c.get("/api/executive/overview?days=30", headers=dean)
    assert ok(first)["census"]["inHospital"] == 2 and ok(second) == ok(first)
    assert int(second.headers["X-DB-Queries"]) < int(first.headers["X-DB-Queries"]) - 5  # aggregates came from Redis
    stats = ok(await c.get("/api/executive/cache-stats", headers=dean))
    assert stats["hits"] == 1 and stats["misses"] == 1

    # Each hospital has its own scope; bumping it makes the next view rebuild with fresh numbers.
    await _admit(c, admin, 1, start=2)
    assert ok(await c.get("/api/executive/overview?days=30", headers=dean))["census"]["inHospital"] == 2  # still cached
    async with sm() as s:
        tenant_id = await s.scalar(select(Tenant.id))
    r = Redis.from_url("redis://localhost:6379/15")
    await Cache(r).bump(f"exec:{tenant_id}")
    await r.aclose()
    assert ok(await c.get("/api/executive/overview?days=30", headers=dean))["census"]["inHospital"] == 3
    ok(await c.post("/api/users", headers=admin, json={"name": "Lata W", "email": "lata@perf.in", "role": "Ward clerk",
                                                       "password": "a-strong-password"}), 201)
    t = ok(await c.post("/api/auth/login", json={"email": "lata@perf.in", "password": "a-strong-password"}))
    assert (await c.get("/api/executive/cache-stats", headers={"authorization": f"Bearer {t['accessToken']}"})).status_code == 403


class _BrokenRedis:
    async def get(self, *a, **k):
        raise ConnectionError("redis down")

    incr = set = delete = get


async def test_cache_falls_back_to_the_database_when_redis_fails():
    calls = []

    async def load():
        calls.append(1)
        return {"ok": True}

    cache = Cache(_BrokenRedis())
    assert await cache.get_or_load("exec:x", "a", 15, load) == {"ok": True}
    await cache.bump("exec:x")  # must not raise
    assert await Cache(None).get_or_load("exec:x", "a", 15, load) == {"ok": True}
    assert len(calls) == 2


async def _terminate(sm, pid: int) -> None:
    from sqlalchemy import text

    async with sm() as s:
        await s.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})


async def test_app_pool_recovers_when_the_server_closes_an_idle_connection(sm, monkeypatch):
    """Neon closes idle connections when it suspends; the next request must still work."""
    from sqlalchemy import text

    from app import db as dbmod

    monkeypatch.setattr(dbmod, "IDLE_PING_SECONDS", 0)
    eng = dbmod.engine()
    async with eng.connect() as c:
        pid = await c.scalar(text("SELECT pg_backend_pid()"))
    # The connection is now idle in the pool. Kill it from outside the pool, as Neon does when it suspends.
    import asyncpg

    from tests.conftest import TEST_DB

    killer = await asyncpg.connect(TEST_DB.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        assert await killer.fetchval("SELECT pg_terminate_backend($1)", pid)
    finally:
        await killer.close()
    for _ in range(6):  # every pooled connection is handed out at least once; none may fail
        async with eng.connect() as c:
            assert await c.scalar(text("SELECT 1")) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="psycopg async needs a selector event loop; runs in the Linux container")
async def test_checkpointer_pool_recovers_after_the_server_closes_its_connection(sm, monkeypatch):
    from app import checkpoint as cpmod
    from app.config import get_settings

    monkeypatch.setattr(cpmod, "IDLE_CHECK_SECONDS", 0)
    async with cpmod.postgres_checkpointer(get_settings().database_url, max_size=1) as saver:
        async with saver.conn.connection() as conn:
            pid = conn.info.backend_pid
        await _terminate(sm, pid)
        cfg = {"configurable": {"thread_id": "pool-recovery-test", "checkpoint_ns": ""}}
        assert await saver.aget_tuple(cfg) is None  # works on a fresh connection instead of "the connection is closed"
