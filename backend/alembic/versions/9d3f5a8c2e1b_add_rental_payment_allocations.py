"""add rental_payment_allocations table

ZR-PAY-LINK-003 Section 15: joint-tenancy payer allocation -- see
app/models/rental_payment.py:RentalPaymentAllocation.

Revision ID: 9d3f5a8c2e1b
Revises: 7a9c2e4f1b6d
Create Date: 2026-09-23 00:00:30.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d3f5a8c2e1b'
down_revision: Union[str, None] = '7a9c2e4f1b6d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rental_payment_allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "obligation_id", sa.Integer(),
            sa.ForeignKey("rental_payment_obligations.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("payer_guest_id", sa.String(length=20), sa.ForeignKey("guests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("allocated_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_rental_payment_allocations_obligation_id", "rental_payment_allocations", ["obligation_id"])
    op.create_index("ix_rental_payment_allocations_payer_guest_id", "rental_payment_allocations", ["payer_guest_id"])


def downgrade() -> None:
    op.drop_index("ix_rental_payment_allocations_payer_guest_id", table_name="rental_payment_allocations")
    op.drop_index("ix_rental_payment_allocations_obligation_id", table_name="rental_payment_allocations")
    op.drop_table("rental_payment_allocations")
