"""add termination_cases

Revision ID: 238312c8f891
Revises: d45a37ff54df
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '238312c8f891'
down_revision: Union[str, None] = 'd45a37ff54df'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-006 Section 6/AC-03: the configurable notice period the
    # Termination Policy Resolver reads -- never hard-coded in application code.
    op.add_column('market_policy_packs', sa.Column('termination_notice_days', sa.Integer(), nullable=False, server_default='30'))

    # ZR-ENG-CLR-006 Section 5/19: one renter-initiated early-termination
    # request -- see models/termination_case.py.
    op.create_table(
        'termination_cases',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('occupancy_id', sa.Integer(), sa.ForeignKey('occupancies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('agreement_id', sa.Integer(), sa.ForeignKey('agreements.id', ondelete='CASCADE'), nullable=False),
        sa.Column('initiator_guest_id', sa.String(), sa.ForeignKey('guests.id', ondelete='SET NULL'), nullable=True),
        sa.Column('initiator_admin_id', sa.Integer(), sa.ForeignKey('admin_users.id'), nullable=True),
        sa.Column('cause_code', sa.String(length=40), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='OPENED'),
        sa.Column('notes', sa.String(length=2000), nullable=False, server_default=''),
        sa.Column('notice_created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('earliest_effective_date', sa.Date(), nullable=False),
        sa.Column('effective_termination_date', sa.Date(), nullable=True),
        sa.Column('withdrawn_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_termination_cases_occupancy_id', 'termination_cases', ['occupancy_id'])

    # ZR-ENG-CLR-006 Section 19: links a TerminationRecord back to the case
    # that led to it, when one exists.
    op.add_column(
        'termination_records',
        sa.Column('termination_case_id', sa.Integer(), sa.ForeignKey('termination_cases.id', ondelete='SET NULL'), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('termination_records', 'termination_case_id')
    op.drop_index('ix_termination_cases_occupancy_id', table_name='termination_cases')
    op.drop_table('termination_cases')
    op.drop_column('market_policy_packs', 'termination_notice_days')
