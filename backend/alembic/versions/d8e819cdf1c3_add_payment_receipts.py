"""add payment_receipts

Revision ID: d8e819cdf1c3
Revises: aed73528053a
Create Date: 2026-09-10 13:35:48.983052

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8e819cdf1c3'
down_revision: Union[str, None] = 'aed73528053a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'payment_receipts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'payment_id', sa.Integer(), sa.ForeignKey('simulated_payments.id', ondelete='CASCADE'),
            nullable=False, unique=True,
        ),
        sa.Column('receipt_number', sa.String(length=30), nullable=False, unique=True),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('storage_ref', sa.String(length=255), nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('payment_receipts')
