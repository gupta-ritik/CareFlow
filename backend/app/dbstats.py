"""Per-request SQL counter for benchmarking (CAREFLOW_DB_STATS=true). Adds X-DB-Queries and X-DB-Time-Ms to responses.

It counts statements run while the request is being handled, including the agent graph when the request runs it inline
(authorize, approve). Work done later by a background worker isn't counted. Leave it off in production: the headers
reveal timing to every client.
"""
import contextvars
import time

from sqlalchemy import event

_stats: contextvars.ContextVar[dict | None] = contextvars.ContextVar("careflow_db_stats", default=None)
_installed: set[int] = set()


def install_engine(engine) -> None:
    sync = getattr(engine, "sync_engine", engine)
    if id(sync) in _installed:
        return
    _installed.add(id(sync))

    @event.listens_for(sync, "before_cursor_execute")
    def _start(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault("careflow_t0", []).append(time.perf_counter())

    @event.listens_for(sync, "after_cursor_execute")
    def _end(conn, cursor, statement, parameters, context, executemany):
        t0 = conn.info.get("careflow_t0", [])
        started = t0.pop() if t0 else None
        st = _stats.get()
        if st is not None:
            st["queries"] += 1
            if started is not None:
                st["db_ms"] += (time.perf_counter() - started) * 1000


def install_middleware(app) -> None:
    @app.middleware("http")
    async def count_queries(request, call_next):
        st = {"queries": 0, "db_ms": 0.0}
        token = _stats.set(st)
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            _stats.reset(token)
        response.headers["X-DB-Queries"] = str(st["queries"])
        response.headers["X-DB-Time-Ms"] = f"{st['db_ms']:.0f}"
        response.headers["X-Server-Time-Ms"] = f"{(time.perf_counter() - t0) * 1000:.0f}"
        return response
