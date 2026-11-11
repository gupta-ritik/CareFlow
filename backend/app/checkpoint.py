"""LangGraph Postgres checkpointer on a small self-healing connection pool.

`AsyncPostgresSaver.from_conn_string` holds ONE connection for the life of the process. Serverless Postgres (Neon)
closes idle connections when its compute suspends, and after that every graph run failed with
"the connection is closed" until the process restarted. A pool replaces dead connections instead.

Connections that have been idle for a while are checked before use (one round trip); busy ones are not, so the
check doesn't add latency to every checkpoint write.
"""
import contextlib
import time
import weakref

from .dburl import checkpoint_url

IDLE_CHECK_SECONDS = 30

_returned_at: "weakref.WeakKeyDictionary[object, float]" = weakref.WeakKeyDictionary()


async def _reset(conn) -> None:  # called when a connection goes back into the pool
    _returned_at[conn] = time.monotonic()


async def _check(conn) -> None:  # called when a connection is taken from the pool; raising discards it
    if time.monotonic() - _returned_at.get(conn, 0.0) > IDLE_CHECK_SECONDS:
        await conn.execute("SELECT 1")


@contextlib.asynccontextmanager
async def postgres_checkpointer(database_url: str, max_size: int = 4):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        checkpoint_url(database_url),
        min_size=1,
        max_size=max_size,
        open=False,
        # the same connection settings from_conn_string uses
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        check=_check,
        reset=_reset,
        max_idle=240,  # shrink idle extras before Neon's 5-minute suspend
        reconnect_timeout=60,
    )
    await pool.open(wait=True, timeout=60)
    try:
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        yield saver
    finally:
        await pool.close()
