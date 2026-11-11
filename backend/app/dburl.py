"""Database URL handling for local Postgres and Neon (Lakebase Postgres).

Neon hands out libpq-style URLs (`postgresql://...?sslmode=require&channel_binding=require`) in two flavours:
pooled (host contains `-pooler`, PgBouncer in transaction mode) and direct. CareFlow needs three views of the same
database:

  app_url(url)        SQLAlchemy + asyncpg, for normal request traffic. Pooled is fine (and preferred on Neon).
  direct_url(url)     SQLAlchemy + asyncpg on the *direct* host, for Alembic migrations.
  checkpoint_url(url) libpq (psycopg) on the *direct* host, for the LangGraph checkpointer, which prepares
                      statements and keeps session state that PgBouncer transaction pooling can't carry.

asyncpg doesn't understand libpq's `sslmode` / `channel_binding` parameters, so they're translated here.
A plain local URL (`postgresql+asyncpg://careflow:careflow@localhost/careflow`) passes through unchanged.
"""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_LIBPQ_ONLY = {"channel_binding", "sslrootcert", "sslcert", "sslkey", "sslcrl", "target_session_attrs", "gssencmode"}


def _split(url: str):
    parts = urlsplit(url)
    return parts, dict(parse_qsl(parts.query, keep_blank_values=True))


def is_postgres(url: str) -> bool:
    return url.split(":", 1)[0].split("+", 1)[0] in ("postgresql", "postgres")


def is_pooled(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return "-pooler" in host


def app_url(url: str) -> str:
    """SQLAlchemy URL for the asyncpg driver."""
    if not is_postgres(url):
        return url  # e.g. sqlite+aiosqlite for tests
    parts, q = _split(url)
    if "sslmode" in q:
        mode = q.pop("sslmode")
        if mode and mode != "disable":
            q.setdefault("ssl", mode)  # asyncpg accepts require / verify-ca / verify-full / prefer
    for k in _LIBPQ_ONLY:
        q.pop(k, None)
    return urlunsplit(("postgresql+asyncpg", parts.netloc, parts.path, urlencode(q), parts.fragment))


def _direct_netloc(netloc: str) -> str:
    return netloc.replace("-pooler.", ".", 1)


def direct_url(url: str) -> str:
    """asyncpg URL on the direct (unpooled) host: migrations and admin work."""
    u = app_url(url)
    if not is_postgres(u):
        return u
    parts = urlsplit(u)
    return urlunsplit((parts.scheme, _direct_netloc(parts.netloc), parts.path, parts.query, parts.fragment))


def checkpoint_url(url: str) -> str:
    """libpq URL (psycopg) on the direct host, keeping sslmode / channel_binding."""
    parts, q = _split(url)
    if "ssl" in q and "sslmode" not in q:
        q["sslmode"] = q.pop("ssl")
    return urlunsplit(("postgresql", _direct_netloc(parts.netloc), parts.path, urlencode(q), parts.fragment))
