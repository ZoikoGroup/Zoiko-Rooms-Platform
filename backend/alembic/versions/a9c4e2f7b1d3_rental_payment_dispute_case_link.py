"""rental payment dispute -> dispute case link

Revision ID: a9c4e2f7b1d3
Revises: f2b5d8e1a4c7
Create Date: 2026-10-01 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9c4e2f7b1d3'
down_revision: Union[str, None] = 'f2b5d8e1a4c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("rental_payment_disputes", sa.Column("dispute_case_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_rental_payment_disputes_dispute_case_id", "rental_payment_disputes", "dispute_resolution_cases",
        ["dispute_case_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_rental_payment_disputes_dispute_case_id", "rental_payment_disputes", ["dispute_case_id"])


def downgrade() -> None:
    op.drop_index("ix_rental_payment_disputes_dispute_case_id", table_name="rental_payment_disputes")
    op.drop_constraint("fk_rental_payment_disputes_dispute_case_id", "rental_payment_disputes", type_="foreignkey")
    op.drop_column("rental_payment_disputes", "dispute_case_id")
