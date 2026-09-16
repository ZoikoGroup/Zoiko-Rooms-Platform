"""generalize payout beneficiary ifsc code to bank identifier code

Revision ID: d771b91b6e72
Revises: b55945ee016f
Create Date: 2026-09-16 13:30:34.974321

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd771b91b6e72'
down_revision: Union[str, None] = 'b55945ee016f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "payout_beneficiaries", "ifsc_code", new_column_name="bank_identifier_code",
        existing_type=sa.String(11), type_=sa.String(34),
    )


def downgrade() -> None:
    op.alter_column(
        "payout_beneficiaries", "bank_identifier_code", new_column_name="ifsc_code",
        existing_type=sa.String(34), type_=sa.String(11),
    )
