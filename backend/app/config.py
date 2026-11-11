from functools import lru_cache

from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Reads the repository-root .env (where `neon env pull` writes DATABASE_URL) and then the working directory's .env.
    model_config = SettingsConfigDict(env_file=(Path(__file__).resolve().parents[2] / ".env", ".env"), env_prefix="CAREFLOW_", extra="ignore")

    env: str = "dev"  # dev creates tables automatically and allows weak dev secrets; production requires a strong JWT secret and real CORS origins
    timezone: str = "Asia/Kolkata"  # the hospital's local time zone (IANA name); used for weekdays, hours and "today"
    # CAREFLOW_DATABASE_URL wins; otherwise Neon's DATABASE_URL (pooled) is used. Any libpq/asyncpg URL works:
    # app.dburl derives the asyncpg, direct (migrations) and checkpointer variants from it.
    database_url: str = Field("postgresql+asyncpg://careflow:careflow@localhost:5432/careflow",
                              validation_alias=AliasChoices("CAREFLOW_DATABASE_URL", "DATABASE_URL"))  # PostgreSQL connection string; CAREFLOW_DATABASE_URL wins over Neon's DATABASE_URL
    redis_url: str = "redis://localhost:6379/0"  # Redis for the event stream, locks, rate limits and the dashboard cache; optional (events run inline without it)

    # Auth. In production set jwt_secret from a secret manager, or point oidc_jwks_url at Keycloak.
    jwt_secret: str = "dev-only-insecure-secret-change-me-0123456789"  # signs sign-in tokens; required outside dev: 32+ random characters (openssl rand -hex 32)
    jwt_issuer: str = "careflow"  # issuer claim written into and checked on local tokens
    jwt_ttl_minutes: int = 60  # how long a sign-in lasts before the user must sign in again
    oidc_jwks_url: str | None = None  # accept tokens from an OIDC provider (e.g. Keycloak) verified against this JWKS URL
    oidc_audience: str | None = None  # expected audience of OIDC tokens

    # LLM. "auto" uses Groq when a key is set and falls back to deterministic agents otherwise.
    llm_mode: str = "auto"  # auto | off | groq
    groq_api_key: str | None = None  # turns on the Groq LLM agents; without it the deterministic fallbacks run
    groq_model_small: str = "openai/gpt-oss-20b"  # Groq model for planning and reporting
    groq_model_large: str = "openai/gpt-oss-120b"  # Groq model for blocker analysis
    llm_requests_per_second: float = 0.5  # client-side rate limit for Groq calls (free tier: about 0.5)
    llm_timeout_seconds: float = 30.0  # per-call timeout before an agent falls back to its deterministic version

    # Integrations
    fhir_base_url: str | None = None  # e.g. http://hapi:8080/fhir ; unset = local patient store
    fhir_token: str | None = None  # bearer token sent to the FHIR server

    # Hospital system integrations (all optional)
    hl7_mllp_port: int = 2575  # the `hl7` service listens here (python -m app.integrations.mllp)
    hl7_tenant: str | None = None  # tenant slug for MLLP messages; defaults to the only/first hospital
    hl7_allowed_hosts: list[str] = []  # empty = accept from any host (restrict this in production)
    hl7_outbound_host: str | None = None  # HIS MLLP listener that receives ADT^A03 on discharge
    hl7_outbound_port: int = 2575  # port of the HIS MLLP listener for outbound ADT^A03
    his_webhook_url: str | None = None  # outbound JSON notifications per system
    tpa_webhook_url: str | None = None  # outbound JSON notifications to the TPA / payer system
    pharmacy_webhook_url: str | None = None  # outbound JSON notifications to the pharmacy system
    lis_webhook_url: str | None = None  # outbound JSON notifications to the laboratory system (LIS)
    outbound_secret: str | None = None  # HMAC-SHA256 key for the X-CareFlow-Signature header
    outbound_timeout_seconds: float = 10.0  # per-attempt timeout for outbound webhooks and HL7

    # Security
    login_max_failures: int = 5  # wrong passwords in a row before an account is locked
    login_lockout_minutes: int = 15  # how long a locked account stays locked
    api_docs: bool = True  # /docs and /openapi.json; production compose turns this off

    # Runtime
    seed_demo: bool = False  # tests only: load the 8 synthetic patients and demo users on an empty database
    # Public demo ("Try the demo" buttons on the sign-in page, app/demo.py): a separate synthetic hospital anyone can
    # explore as any role except Administrator. Never enable it on a server that holds real patient data.
    demo_mode: bool = False  # CAREFLOW_DEMO_MODE=true turns it on
    demo_history_days: int = 60  # days of synthetic discharge history behind the Director dashboard and Ask CareFlow (0 = none)
    demo_reset_hours: int = 24  # rebuild the demo data when it is older than this
    embedded_worker: bool = True  # run the event consumer inside the API process (dev)
    checkpointer: str = "postgres"  # postgres | memory
    max_iterations: int = 4  # re-plan loops allowed per graph run
    cors_origins: list[str] = ["http://localhost:3000"]  # web origins allowed to call the API (JSON list); '*' is refused outside dev
    executive_cache_seconds: int = 15  # Director dashboard cache in Redis; 0 turns it off
    db_pool_size: int = 5  # per process; keep (pool + overflow) x processes under the database's connection limit
    db_max_overflow: int = 5  # extra connections a process may open above the pool size under load
    db_stats: bool = False  # add X-DB-Queries / X-DB-Time-Ms headers to every response (benchmarking, not production)

    # Ask CareFlow (the director's question-answering agent)
    ask_timeout: float = Field(30.0, validation_alias=AliasChoices("CAREFLOW_ASK_TIMEOUT", "CAREFLOW_ASK_TIMEOUT_SECONDS"))  # seconds per question; on timeout the verified part is returned, marked partial
    ask_rate_per_hour: int = 30  # questions per user per hour (Redis; in-memory if Redis is down)
    ask_cache_seconds: int = 300  # identical questions from the same hospital on the same day reuse the answer; 0 turns it off

    # Administrative SLAs (minutes) used to decide when "in progress" becomes a blocker.
    sla_pharmacy: int = 60  # minutes after the order before unready medicines become a pharmacy delay
    sla_documentation: int = 45  # not used yet: no check reads it (documentation has no time limit today)
    sla_insurance: int = 90  # minutes after submission before a silent payer becomes a payer delay
    sla_billing: int = 45  # minutes since the last billing update before a bill in preparation becomes a billing delay
    sla_laboratory: int = 60  # not used yet: no check reads it (an unacknowledged final report is flagged at once)

    @field_validator("jwt_secret", mode="before")
    @classmethod
    def _blank_secret_is_unset(cls, v):
        # A blank line copied from .env.example must not become an empty signing key.
        return v if (v or "").strip() else "dev-only-insecure-secret-change-me-0123456789"

    @field_validator("groq_api_key", "fhir_base_url", "fhir_token", "oidc_jwks_url", "hl7_tenant", "hl7_outbound_host", "his_webhook_url",
                     "tpa_webhook_url", "pharmacy_webhook_url", "lis_webhook_url", "outbound_secret", mode="before")
    @classmethod
    def _blank_is_none(cls, v):
        return v if (v is None or str(v).strip()) else None


@lru_cache
def get_settings() -> Settings:
    return Settings()
