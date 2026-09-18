"""add termination_decisions table and domain_event actor/state/hash fields

Revision ID: 5c2a7e9f14b6
Revises: 3f8b1c6d9a24
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5c2a7e9f14b6'
down_revision: Union[str, None] = '3f8b1c6d9a24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-006 Section 19's own termination_decision entity: how
    # effective_termination_date actually got decided at a given point in a
    # case's life, distinct from TerminationCase's own flattened
    # current-value columns.
    op.create_table(
        'termination_decisions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('termination_case_id', sa.Integer(), sa.ForeignKey('termination_cases.id', ondelete='CASCADE'), nullable=False),
        sa.Column('effective_termination_at', sa.Date(), nullable=True),
        sa.Column('decision_basis', sa.String(40), nullable=False),
        sa.Column('authority', sa.String(20), nullable=False),
        sa.Column('approved_by_admin_id', sa.Integer(), sa.ForeignKey('admin_users.id'), nullable=True),
        sa.Column('decision_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('external_order_ref', sa.String(100), nullable=False, server_default=''),
        sa.Column('reason', sa.String(500), nullable=False, server_default=''),
    )
    op.create_index('ix_termination_decisions_termination_case_id', 'termination_decisions', ['termination_case_id'])

    # ZR-ENG-CLR-006 Section 19's own termination_event_log entity's
    # remaining fields, added to the existing domain_events outbox rather
    # than a second parallel table -- see models/domain_event.py's own
    # field docstring. Nullable/blank-default so every existing row and
    # every other emit_event call site across the app is unaffected.
    op.add_column('domain_events', sa.Column('actor_kind', sa.String(20), nullable=False, server_default=''))
    op.add_column('domain_events', sa.Column('actor_id', sa.String(50), nullable=False, server_default=''))
    op.add_column('domain_events', sa.Column('previous_state', sa.String(30), nullable=True))
    op.add_column('domain_events', sa.Column('new_state', sa.String(30), nullable=True))
    op.add_column('domain_events', sa.Column('payload_hash', sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column('domain_events', 'payload_hash')
    op.drop_column('domain_events', 'new_state')
    op.drop_column('domain_events', 'previous_state')
    op.drop_column('domain_events', 'actor_id')
    op.drop_column('domain_events', 'actor_kind')
    op.drop_index('ix_termination_decisions_termination_case_id', table_name='termination_decisions')
    op.drop_table('termination_decisions')
