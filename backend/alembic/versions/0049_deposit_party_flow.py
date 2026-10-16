"""Deposit claims settled between host and renter

deposit_claims: a host user can submit (submitted_by_admin_id becomes
nullable, submitted_by_user_id added), plus the renter response deadline,
the negotiation window, referral to the deposit scheme and the scheme
decision recorded by a party. Claims still waiting on an admin (DISPUTED)
get a fresh negotiation window; open claims get a response deadline.

Revision ID: 0049_deposit_party_flow
Revises: 0048_dispute_party_filing
Create Date: 2026-10-15 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0049_deposit_party_flow"
down_revision: Union[str, None] = "0048_dispute_party_filing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("deposit_claims") as batch:
        batch.alter_column("submitted_by_admin_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("deposit_claims", sa.Column("submitted_by_user_id", sa.Integer(), sa.ForeignKey("user_accounts.id"), nullable=True))
    op.add_column("deposit_claims", sa.Column("response_due_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("deposit_claims", sa.Column("negotiation_ends_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("deposit_claims", sa.Column("referred_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("deposit_claims", sa.Column("resolved_by_user_id", sa.Integer(), sa.ForeignKey("user_accounts.id"), nullable=True))
    op.add_column("deposit_claims", sa.Column("scheme_reference", sa.String(200), nullable=False, server_default=""))

    op.execute("""
        UPDATE deposit_claims SET response_due_at = CURRENT_TIMESTAMP + INTERVAL '14 days'
        WHERE status = 'RENTER_RESPONSE_PENDING'
    """)
    op.execute("""
        UPDATE deposit_claims SET negotiation_ends_at = CURRENT_TIMESTAMP + INTERVAL '21 days'
        WHERE status = 'DISPUTED'
    """)


def downgrade() -> None:
    op.execute("UPDATE deposit_claims SET status = 'DISPUTED' WHERE status = 'REFERRED'")
    for column in ("scheme_reference", "resolved_by_user_id", "referred_at", "negotiation_ends_at", "response_due_at", "submitted_by_user_id"):
        op.drop_column("deposit_claims", column)
