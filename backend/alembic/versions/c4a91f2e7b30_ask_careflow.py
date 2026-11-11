"""Ask CareFlow: ask_sessions, ask_turns, the analytics views and the read-only analytics role

Revision ID: c4a91f2e7b30
Revises: b7e2c4d91a10
Create Date: 2026-11-10 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import app.db  # noqa: F401  (JSONType)
from app.ask.analytics_sql import DROP, ROLE, ROLE_SQL, VIEWS

revision: str = "c4a91f2e7b30"
down_revision: Union[str, Sequence[str], None] = "b7e2c4d91a10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ask_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ask_sessions_user", "ask_sessions", ["tenant_id", "user_id", "updated_at"])
    op.create_table(
        "ask_turns",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("ask_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("partial", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("plan", app.db.JSONType, nullable=False),
        sa.Column("steps", app.db.JSONType, nullable=False),
        sa.Column("results", app.db.JSONType, nullable=False),
        sa.Column("answer", app.db.JSONType, nullable=False),
        sa.Column("claims", app.db.JSONType, nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("mode", sa.String(16), nullable=False, server_default="fallback"),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ask_turns_session_id", "ask_turns", ["session_id"])
    op.create_index("ix_ask_turns_tenant_id", "ask_turns", ["tenant_id"])
    for sql in VIEWS:
        op.execute(sql)
    # Creating a role needs CREATEROLE (Neon's owner role and the local Docker user have it).
    for sql in ROLE_SQL:
        op.execute(sql)


def downgrade() -> None:
    for sql in DROP:
        op.execute(sql)
    # The role is cluster-wide and other databases (e.g. tests) may still use it, so it is left in place; it has no
    # privileges here once the schema is gone. Remove it by hand with DROP ROLE careflow_analytics if you need to.
    op.execute(f"REVOKE {ROLE} FROM CURRENT_USER")
    op.drop_table("ask_turns")
    op.drop_table("ask_sessions")
