"""add refund_requests.idempotency_key

Revision ID: 2b8af67b188f
Revises: d8e819cdf1c3
Create Date: 2026-09-10 14:01:07.783378

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2b8af67b188f'
down_revision: Union[str, None] = 'd8e819cdf1c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # AC-14: nullable first so existing rows (which never had a key) can be
    # backfilled with a synthesized, unique value before the NOT NULL/UNIQUE
    # constraints are enforced.
    op.add_column('refund_requests', sa.Column('idempotency_key', sa.String(length=255), nullable=True))
    op.execute("UPDATE refund_requests SET idempotency_key = 'legacy-' || id::text WHERE idempotency_key IS NULL")
    op.alter_column('refund_requests', 'idempotency_key', nullable=False)
    op.create_unique_constraint('uq_refund_requests_idempotency_key', 'refund_requests', ['idempotency_key'])


def downgrade() -> None:
    op.drop_constraint('uq_refund_requests_idempotency_key', 'refund_requests', type_='unique')
    op.drop_column('refund_requests', 'idempotency_key')
