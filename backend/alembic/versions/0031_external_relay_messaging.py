"""Phase 2 controlled-introduction tables for ZR-AI-SEARCH-001

Adds the Zoiko-mediated relay between renters and unverified external
providers:
* relay_messages — masked in-platform messages (no direct contact details)
* direct_contact_release — bilateral consent before any contact release

Revision ID: 0031_external_relay_messaging
Revises: 0030_external_outreach_phase1
Create Date: 2026-10-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0031_external_relay_messaging"
down_revision: Union[str, None] = "0030_external_outreach_phase1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "relay_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), sa.ForeignKey("external_opportunities.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("sender_topic", sa.String(20), nullable=False),
        sa.Column("sender_user_id", sa.Integer(), sa.ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("sender_handle", sa.String(120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_check_constraint(
        "ck_rm_sender_topic",
        "relay_messages",
        "sender_topic IN ('RENTER', 'PROVIDER')",
    )

    op.create_table(
        "direct_contact_release",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), sa.ForeignKey("external_opportunities.id", ondelete="CASCADE"), nullable=False, index=True, unique=True),
        sa.Column("renter_consented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_consented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("both_consented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_mask", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("direct_contact_release")
    op.drop_table("relay_messages")