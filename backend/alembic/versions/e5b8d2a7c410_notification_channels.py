"""Notification channels: Web Push subscriptions and Telegram chats per user

Revision ID: e5b8d2a7c410
Revises: c4a91f2e7b30
Create Date: 2026-11-11 13:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import app.db  # noqa: F401  (JSONType)

revision: str = "e5b8d2a7c410"
down_revision: Union[str, Sequence[str], None] = "c4a91f2e7b30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("keys", app.db.JSONType, nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kind", "address", name="uq_notification_channel"),
    )
    op.create_index("ix_notification_channels_tenant_id", "notification_channels", ["tenant_id"])
    op.create_index("ix_notification_channels_user_id", "notification_channels", ["user_id"])


def downgrade() -> None:
    op.drop_table("notification_channels")
