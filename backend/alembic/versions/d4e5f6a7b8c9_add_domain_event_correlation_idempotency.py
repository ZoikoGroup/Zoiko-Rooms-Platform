"""add domain_events.correlation_id/idempotency_key (ZR-ENG-CLR-001 Section 1 -- audit/idempotency/correlation framework)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('domain_events', sa.Column('correlation_id', sa.String(length=64), nullable=False, server_default=''))
    op.add_column('domain_events', sa.Column('idempotency_key', sa.String(length=200), nullable=True))
    op.alter_column('domain_events', 'correlation_id', server_default=None)
    # Partial unique index: only enforced when a caller actually supplies an
    # idempotency_key -- most emit_event() call sites don't, and that's fine.
    op.create_index(
        'uq_domain_events_idempotency_key', 'domain_events', ['idempotency_key'], unique=True,
        postgresql_where=sa.text('idempotency_key IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_domain_events_idempotency_key', table_name='domain_events')
    op.drop_column('domain_events', 'idempotency_key')
    op.drop_column('domain_events', 'correlation_id')
