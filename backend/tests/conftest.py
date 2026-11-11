import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_DB = os.getenv("CAREFLOW_TEST_DATABASE_URL", "postgresql+asyncpg://careflow:careflow@localhost:5432/careflow_test")
os.environ.setdefault("CAREFLOW_DATABASE_URL", TEST_DB)
os.environ.setdefault("CAREFLOW_REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("CAREFLOW_LLM_MODE", "off")
os.environ.setdefault("CAREFLOW_CHECKPOINTER", "memory")
os.environ.setdefault("CAREFLOW_SEED_DEMO", "true")

from app import db, llm  # noqa: E402
from app.ask.analytics_sql import ensure_analytics  # noqa: E402
from app.db import Base  # noqa: E402
from sqlalchemy import text  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.seed import seed_patients, seed_tenant  # noqa: E402


@pytest_asyncio.fixture
async def sm():
    db.init_engine(TEST_DB)
    async with db.engine().begin() as c:
        await c.execute(text("DROP SCHEMA IF EXISTS analytics CASCADE"))  # its views depend on the tables
        await c.run_sync(Base.metadata.drop_all)
        await c.run_sync(Base.metadata.create_all)
        await ensure_analytics(c)
    yield db.sessionmaker()
    await db.engine().dispose()


@pytest_asyncio.fixture
async def tenant(sm):
    async with sm() as s:
        t = await seed_tenant(s)
        await s.commit()
        return t


@pytest_asyncio.fixture
async def engine(sm):
    from langgraph.checkpoint.memory import InMemorySaver

    eng = Engine(sm, InMemorySaver())
    eng.deps_override = None
    return eng


@pytest_asyncio.fixture
async def seeded(sm, tenant):
    async with sm() as s:
        pts = await seed_patients(s, tenant)
        await s.commit()
    return pts


@pytest.fixture(autouse=True)
def no_llm():
    llm.set_backend(None)
    yield
    llm.set_backend(None)


class FakeLLM:
    """Returns scripted outputs per schema and records every prompt it sees."""

    name = "fake"

    def __init__(self, responses: dict | None = None):
        self.responses = responses or {}
        self.prompts: list[str] = []

    async def structured(self, schema, system, user, size="small"):
        self.prompts.append(user)
        fn = self.responses.get(schema.__name__)
        if fn is None:
            raise llm.LLMUnavailable("no scripted response")
        value = fn(user) if callable(fn) else fn
        return llm.LLMResult(schema.model_validate(value), tokens=100, latency_ms=5, model="fake")


@pytest.fixture
def fake_llm():
    def make(responses=None):
        f = FakeLLM(responses)
        llm.set_backend(f)
        return f

    return make
