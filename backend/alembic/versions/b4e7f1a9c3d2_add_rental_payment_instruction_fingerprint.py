"""add rental_payment_instructions destination_fingerprint

ZR-PAY-LINK-003 Section 14.1/21 destination-novelty risk signal -- see
app/models/rental_payment.py:RentalPaymentInstruction.destination_fingerprint.

Revision ID: b4e7f1a9c3d2
Revises: 9d3f5a8c2e1b
Create Date: 2026-09-23 00:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4e7f1a9c3d2'
down_revision: Union[str, None] = '9d3f5a8c2e1b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("rental_payment_instructions", sa.Column("destination_fingerprint", sa.String(length=64), nullable=True))
    op.create_index(
        "ix_rental_payment_instructions_destination_fingerprint", "rental_payment_instructions", ["destination_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index("ix_rental_payment_instructions_destination_fingerprint", table_name="rental_payment_instructions")
    op.drop_column("rental_payment_instructions", "destination_fingerprint")
