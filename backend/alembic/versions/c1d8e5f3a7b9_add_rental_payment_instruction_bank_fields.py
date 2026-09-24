"""add rental_payment_instructions structured bank fields

ZR-PAY-LINK-003 Wireframe D: country_code + encrypted_bank_details
(Fernet-encrypted structured field values, see
app/core/field_encryption.py) + authorized_recipient_confirmed. See
app/models/rental_payment.py:RentalPaymentInstruction.

Revision ID: c1d8e5f3a7b9
Revises: b4e7f1a9c3d2
Create Date: 2026-09-23 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1d8e5f3a7b9'
down_revision: Union[str, None] = 'b4e7f1a9c3d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "rental_payment_instructions",
        sa.Column("country_code", sa.String(length=2), nullable=False, server_default=""),
    )
    op.add_column(
        "rental_payment_instructions",
        sa.Column("encrypted_bank_details", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "rental_payment_instructions",
        sa.Column("authorized_recipient_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("rental_payment_instructions", "authorized_recipient_confirmed")
    op.drop_column("rental_payment_instructions", "encrypted_bank_details")
    op.drop_column("rental_payment_instructions", "country_code")
