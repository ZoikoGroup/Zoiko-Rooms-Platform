"""add provider_checkout_session_id to listing_fee_payments

Revision ID: ac43bdf3f43b
Revises: b4ea81bb68d3
Create Date: 2026-09-22 10:59:25.180881

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ac43bdf3f43b'
down_revision: Union[str, None] = 'b4ea81bb68d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "listing_fee_payments",
        sa.Column("provider_checkout_session_id", sa.String(length=100), nullable=True),
    )
    op.create_unique_constraint(
        "uq_listing_fee_payments_provider_checkout_session_id",
        "listing_fee_payments",
        ["provider_checkout_session_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_listing_fee_payments_provider_checkout_session_id",
        "listing_fee_payments",
        type_="unique",
    )
    op.drop_column("listing_fee_payments", "provider_checkout_session_id")
