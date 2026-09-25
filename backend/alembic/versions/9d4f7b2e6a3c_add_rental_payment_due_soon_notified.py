"""add rental_payment_obligations.due_soon_notified_at

Revision ID: 9d4f7b2e6a3c
Revises: 5c8d3f0a1b4e
Create Date: 2026-09-21 03:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d4f7b2e6a3c'
down_revision: Union[str, None] = '5c8d3f0a1b4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-PAY-002 Section 14: idempotency marker for
    # services/rental_payment_due_soon.py's sweep.
    op.add_column('rental_payment_obligations', sa.Column('due_soon_notified_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('rental_payment_obligations', 'due_soon_notified_at')
