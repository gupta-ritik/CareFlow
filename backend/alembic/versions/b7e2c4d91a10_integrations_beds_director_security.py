import app.db  # noqa: F401  (JSONType)
"""integrations, beds, account lockout

Revision ID: b7e2c4d91a10
Revises: dd1484a87746
Create Date: 2026-11-06 16:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7e2c4d91a10"
down_revision: Union[str, Sequence[str], None] = "dd1484a87746"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "integration_keys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("system", sa.String(32), nullable=False),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_integration_keys_tenant_id", "integration_keys", ["tenant_id"])

    op.create_table(
        "integration_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("system", sa.String(32), nullable=False),
        sa.Column("protocol", sa.String(16), nullable=False),
        sa.Column("message_type", sa.String(64), nullable=False),
        sa.Column("control_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("patient_ref", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw", sa.Text(), nullable=False, server_default=""),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_intmsg_tenant_at", "integration_messages", ["tenant_id", "created_at"])
    op.create_index("ix_integration_messages_control_id", "integration_messages", ["control_id"])

    op.create_table(
        "beds",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("ward", sa.String(64), nullable=False),
        sa.Column("bed", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="available"),
        sa.Column("patient_ref", sa.String(64), nullable=True),
        sa.Column("updated_by", sa.String(200), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "ward", "bed", name="uq_beds_tenant_ward_bed"),
    )
    op.create_index("ix_beds_tenant_id", "beds", ["tenant_id"])
    # Fill the bed board from patients already in hospital.
    op.execute("""
        INSERT INTO beds (id, tenant_id, ward, bed, status, patient_ref, updated_by, updated_at)
        SELECT DISTINCT ON (tenant_id, ward, bed) gen_random_uuid(), tenant_id, ward, bed, 'occupied', ref, 'migration', now()
        FROM patients WHERE status = 'admitted' AND ward <> '' AND bed <> ''
        ORDER BY tenant_id, ward, bed, admitted_at DESC
    """) if op.get_bind().dialect.name == "postgresql" else None


def downgrade() -> None:
    op.drop_index("ix_beds_tenant_id", table_name="beds")
    op.drop_table("beds")
    op.drop_index("ix_integration_messages_control_id", table_name="integration_messages")
    op.drop_index("ix_intmsg_tenant_at", table_name="integration_messages")
    op.drop_table("integration_messages")
    op.drop_index("ix_integration_keys_tenant_id", table_name="integration_keys")
    op.drop_table("integration_keys")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_logins")
