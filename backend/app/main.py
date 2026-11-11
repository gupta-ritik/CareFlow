import asyncio
import contextlib
import logging
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from . import db
from .api.ask import router as ask_router
from .api.executive import router as executive_router
from .api.hospital import router as hospital_router
from .api.integrations import router as integrations_router
from .api.hospital import _ensure_roles
from .api.routes import router
from .config import get_settings
from .db import Base
from .dburl import checkpoint_url, is_postgres
from .engine import Engine, start_for_patient
from .events import consume_forever
from .models import Workflow
from .seed import seed_patients, seed_tenant
from .telemetry import setup_tracing

log = logging.getLogger("careflow")


def _pg_url(url: str) -> str:
    return checkpoint_url(url)


async def seed_demo(engine: Engine, sm) -> None:
    async with sm() as s:
        if await s.scalar(select(Workflow.id).limit(1)):
            return
        tenant = await seed_tenant(s)
        patients = await seed_patients(s, tenant)
        await s.commit()
    for ref, attending, minutes in patients:
        await start_for_patient(engine, tenant.id, ref, attending, (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat())


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if s.env != "dev" and (s.jwt_secret.startswith("dev-only") or len(s.jwt_secret) < 32):
        raise RuntimeError("Set CAREFLOW_JWT_SECRET to a random value of at least 32 characters before running outside dev.")
    if s.env != "dev" and "*" in s.cors_origins:
        raise RuntimeError("CAREFLOW_CORS_ORIGINS must list your real web origin(s) outside dev, not '*'.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    setup_tracing(app)
    db.init_engine(s.database_url)
    sm = db.sessionmaker()
    if s.env == "dev":
        async with db.engine().begin() as conn:  # production uses `alembic upgrade head`
            await conn.run_sync(Base.metadata.create_all)
    if is_postgres(s.database_url):
        from .ask.analytics_sql import ensure_analytics

        try:
            async with db.engine().begin() as conn:  # Ask CareFlow's views and read-only role (idempotent)
                await ensure_analytics(conn)
        except Exception as exc:  # noqa: BLE001 - Ask CareFlow then reports an error; the rest of the app still runs
            log.warning("Could not create the analytics views: %s", exc)

    redis = None
    try:
        from redis.asyncio import Redis

        redis = Redis.from_url(s.redis_url)
        await redis.ping()
    except Exception as exc:
        log.warning("Redis unavailable (%s); events will be processed inline", exc)
        redis = None

    stack = contextlib.AsyncExitStack()
    if s.checkpointer == "postgres" and is_postgres(s.database_url):
        from .checkpoint import postgres_checkpointer

        checkpointer = await stack.enter_async_context(postgres_checkpointer(s.database_url))
    else:
        checkpointer = InMemorySaver()

    engine = Engine(sm, checkpointer, s, redis)
    app.state.engine, app.state.sessionmaker, app.state.redis = engine, sm, redis
    async with sm() as session:  # keep role permissions in step with the code
        await _ensure_roles(session)
        await session.commit()
    if s.seed_demo:
        await seed_demo(engine, sm)

    stop = asyncio.Event()
    worker = asyncio.create_task(consume_forever(redis, engine, sm, "api-embedded", stop)) if (redis is not None and s.embedded_worker) else None
    try:
        yield
    finally:
        stop.set()
        if worker:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        if redis is not None:
            await redis.aclose()
        await stack.aclose()
        await db.engine().dispose()


def create_app() -> FastAPI:
    s = get_settings()
    docs = s.api_docs
    app = FastAPI(title="CareFlow API", version="1.0.0", lifespan=lifespan,
                  description="Administrative discharge clearance orchestration. Not a medical device; it never makes clinical decisions.",
                  docs_url="/docs" if docs else None, redoc_url=None, openapi_url="/openapi.json" if docs else None)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
                       allow_headers=["authorization", "content-type", "x-careflow-key", "accept"], expose_headers=["X-Total-Count", "X-HL7-Ack-Code", "X-DB-Queries", "X-DB-Time-Ms", "X-Server-Time-Ms"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.url.path.startswith("/api"):
            h.setdefault("Cache-Control", "no-store")
        if s.env != "dev":
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    if s.db_stats:
        from .dbstats import install_middleware

        install_middleware(app)

    app.include_router(router)
    app.include_router(hospital_router)
    app.include_router(integrations_router)
    app.include_router(executive_router)
    app.include_router(ask_router)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("Unhandled error")
        # Starlette sends unhandled errors from outside the CORS middleware. Without these headers the browser hides the
        # response and the web app wrongly reports that it can't reach the server.
        headers = {}
        origin = request.headers.get("origin")
        if origin and (origin in s.cors_origins or "*" in s.cors_origins):
            headers = {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
        return JSONResponse({"error": {"status": 500, "message": "Something went wrong on the server. It has been logged. Please try again."}},
                            status_code=500, headers=headers)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return JSONResponse({"error": {"status": exc.status_code, "message": exc.detail}}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        fields = "; ".join(f"{'.'.join(str(x) for x in e['loc'][1:])}: {e['msg']}" for e in exc.errors())
        return JSONResponse({"error": {"status": 422, "message": f"Request is invalid. {fields}"}}, status_code=422)

    return app


app = create_app()
