from collections.abc import AsyncIterator

import time

from sqlalchemy import JSON, event
from sqlalchemy import exc as exc_mod
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

import uuid

from .config import get_settings
from .dburl import app_url, is_pooled, is_postgres

# JSONB on Postgres, plain JSON elsewhere (tests can run on SQLite).
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(url: str | None = None):
    global _engine, _sessionmaker
    raw = url or get_settings().database_url
    url = app_url(raw)
    kwargs: dict = {}
    if is_postgres(url):
        # pre_ping: Neon computes scale to zero and drop idle connections; recycle keeps pooled connections fresh.
        s = get_settings()
        # No pool_pre_ping: it costs a round trip on EVERY checkout (about 0.3 s each to a far-away database, and an
        # authorization checks out a connection a dozen times). _ping_if_idle only checks connections that sat unused.
        # pool_recycle replaces connections before Neon's 5-minute idle suspend can close them under us.
        kwargs = {"pool_recycle": 240, "pool_size": s.db_pool_size, "max_overflow": s.db_max_overflow}
        if is_pooled(raw):
            # PgBouncer (transaction mode): no client-side statement cache, and unique prepared-statement names.
            kwargs["connect_args"] = {"statement_cache_size": 0, "prepared_statement_cache_size": 0,
                                      "prepared_statement_name_func": lambda: f"__cf_{uuid.uuid4().hex}__"}
    _engine = create_async_engine(url, **kwargs)
    if is_postgres(url):
        _ping_if_idle(_engine)
    if get_settings().db_stats:
        from .dbstats import install_engine

        install_engine(_engine)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


IDLE_PING_SECONDS = 30


def _ping_if_idle(async_engine) -> None:
    """Pessimistic disconnect handling, but only for connections idle longer than IDLE_PING_SECONDS.

    A connection used a moment ago is almost certainly alive; one that sat idle may have been closed by the server.
    Raising DisconnectionError makes the pool discard it and hand out a fresh one (SQLAlchemy retries up to 3 times).
    """
    sync_engine = async_engine.sync_engine

    @event.listens_for(sync_engine, "checkin")
    def _checkin(dbapi_connection, record):
        record.info["careflow_last_used"] = time.monotonic()

    @event.listens_for(sync_engine, "checkout")
    def _checkout(dbapi_connection, record, proxy):
        last = record.info.get("careflow_last_used")
        if last is None or time.monotonic() - last < IDLE_PING_SECONDS:
            return  # brand-new or recently used connection
        try:
            sync_engine.dialect.do_ping(dbapi_connection)
        except Exception as exc:  # noqa: BLE001
            raise exc_mod.DisconnectionError(f"idle connection was closed: {exc}") from exc


def engine():
    if _engine is None:
        init_engine()
    return _engine


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        init_engine()
    return _sessionmaker  # type: ignore[return-value]


async def get_session() -> AsyncIterator[AsyncSession]:
    async with sessionmaker()() as session:
        yield session
