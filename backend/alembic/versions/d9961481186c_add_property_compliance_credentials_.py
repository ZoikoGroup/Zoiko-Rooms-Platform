"""add property compliance credentials registry

Revision ID: d9961481186c
Revises: 80db3688dee5
Create Date: 2026-09-15 10:40:52.383000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9961481186c'
down_revision: Union[str, None] = '80db3688dee5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "market_policy_packs",
        sa.Column("required_property_compliance_codes", sa.JSON(), nullable=False, server_default="[]"),
    )

    op.create_table(
        "property_compliance_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("room_id", sa.Integer(), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_code", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="VALID"),
        sa.Column("issuer_source", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("evidence_ref", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("method", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("jurisdiction_code", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("issued_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_property_compliance_credentials_room_id", "property_compliance_credentials", ["room_id"])


def downgrade() -> None:
    op.drop_index("ix_property_compliance_credentials_room_id", table_name="property_compliance_credentials")
    op.drop_table("property_compliance_credentials")
    op.drop_column("market_policy_packs", "required_property_compliance_codes")
