"""add host stripe connect accounts and payout stripe transfer id

Revision ID: 121d9ef1d1b3
Revises: eaadce89ec3f
Create Date: 2026-09-16 16:19:27.014269

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '121d9ef1d1b3'
down_revision: Union[str, None] = 'eaadce89ec3f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payout_records", sa.Column("stripe_transfer_id", sa.String(100), nullable=True))
    op.create_table(
        "host_stripe_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("party_id", sa.Integer(), sa.ForeignKey("parties.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stripe_account_id", sa.String(100), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'ONBOARDING'")),
        sa.Column("details_submitted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("charges_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payouts_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("party_id", name="uq_host_stripe_accounts_party"),
    )
    op.create_index("ix_host_stripe_accounts_party_id", "host_stripe_accounts", ["party_id"])


def downgrade() -> None:
    op.drop_index("ix_host_stripe_accounts_party_id", table_name="host_stripe_accounts")
    op.drop_table("host_stripe_accounts")
    op.drop_column("payout_records", "stripe_transfer_id")
