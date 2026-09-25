"""add authority relationship type and property verifications

Revision ID: 449f80bfb6ad
Revises: 7f656f3fef0b
Create Date: 2026-09-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '449f80bfb6ad'
down_revision: Union[str, None] = '7f656f3fef0b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("authority_records", sa.Column("relationship_type", sa.String(20), nullable=True))

    op.create_table(
        "property_verifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("party_id", sa.Integer(), sa.ForeignKey("parties.id", ondelete="CASCADE"), nullable=False),
        sa.Column("room_id", sa.Integer(), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_ref", sa.String(1024), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.String(30), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("verifier_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("verifier_notes", sa.String(1000), nullable=False, server_default=sa.text("''")),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("property_verifications")
    op.drop_column("authority_records", "relationship_type")
