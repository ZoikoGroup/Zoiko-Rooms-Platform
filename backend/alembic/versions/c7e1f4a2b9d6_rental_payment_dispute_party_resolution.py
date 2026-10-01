"""rental payment disputes: resolved by the tenant or host

Revision ID: c7e1f4a2b9d6
Revises: a9c4e2f7b1d3
Create Date: 2026-10-01 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e1f4a2b9d6'
down_revision: Union[str, None] = 'a9c4e2f7b1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("rental_payment_disputes", sa.Column("outcome", sa.String(length=30), nullable=True))
    op.add_column("rental_payment_disputes", sa.Column("resolved_by_guest_id", sa.String(length=20), nullable=True))
    op.add_column("rental_payment_disputes", sa.Column("resolved_by_party_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_rental_payment_disputes_resolved_by_guest_id", "rental_payment_disputes", "guests",
        ["resolved_by_guest_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_rental_payment_disputes_resolved_by_party_id", "rental_payment_disputes", "parties",
        ["resolved_by_party_id"], ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_rental_payment_disputes_resolved_by_party_id", "rental_payment_disputes", type_="foreignkey")
    op.drop_constraint("fk_rental_payment_disputes_resolved_by_guest_id", "rental_payment_disputes", type_="foreignkey")
    op.drop_column("rental_payment_disputes", "resolved_by_party_id")
    op.drop_column("rental_payment_disputes", "resolved_by_guest_id")
    op.drop_column("rental_payment_disputes", "outcome")
