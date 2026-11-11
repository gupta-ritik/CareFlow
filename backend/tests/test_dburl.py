"""Database URL translation for Neon (pooled/direct, libpq vs asyncpg) and plain local Postgres."""
from app.dburl import app_url, checkpoint_url, direct_url, is_pooled

NEON = "postgresql://neondb_owner:p%40ss@ep-cool-name-123-pooler.c-8.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
LOCAL = "postgresql+asyncpg://careflow:careflow@localhost:5432/careflow"


def test_neon_pooled_url_for_the_app_uses_asyncpg_ssl():
    u = app_url(NEON)
    assert u == "postgresql+asyncpg://neondb_owner:p%40ss@ep-cool-name-123-pooler.c-8.us-east-2.aws.neon.tech/neondb?ssl=require"
    assert is_pooled(NEON) and not is_pooled(LOCAL)


def test_migrations_and_checkpointer_use_the_direct_host():
    assert "-pooler" not in direct_url(NEON) and direct_url(NEON).endswith("ep-cool-name-123.c-8.us-east-2.aws.neon.tech/neondb?ssl=require")
    cp = checkpoint_url(NEON)
    assert cp.startswith("postgresql://") and "-pooler" not in cp and "sslmode=require" in cp and "channel_binding=require" in cp


def test_local_urls_pass_through():
    assert app_url(LOCAL) == LOCAL and direct_url(LOCAL) == LOCAL
    assert checkpoint_url(LOCAL) == "postgresql://careflow:careflow@localhost:5432/careflow"
    assert app_url("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert checkpoint_url("postgresql+asyncpg://u:p@h/db?ssl=require") == "postgresql://u:p@h/db?sslmode=require"
    assert app_url("sqlite+aiosqlite:///x.db") == "sqlite+aiosqlite:///x.db"
