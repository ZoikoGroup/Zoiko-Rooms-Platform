"""add host_recoveries

Revision ID: 1bc4f36cb249
Revises: a5945facff7e
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1bc4f36cb249'
down_revision: Union[str, None] = 'a5945facff7e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-006 Section 18.4/AC-22: one row per refund that drove a
    # party's payable balance negative -- see models/finance.py:HostRecovery
    # for exactly what this build automates vs. leaves to a manual admin log.
    op.create_table(
        'host_recoveries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('party_id', sa.Integer(), sa.ForeignKey('parties.id', ondelete='CASCADE'), nullable=False),
        sa.Column('financial_hold_id', sa.Integer(), sa.ForeignKey('financial_holds.id', ondelete='CASCADE'), nullable=False),
        sa.Column('refund_request_id', sa.Integer(), sa.ForeignKey('refund_requests.id'), nullable=True),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('recovered_amount', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='INR'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='OPEN'),
        sa.Column('recovery_method', sa.String(length=30), nullable=False, server_default=''),
        sa.Column('notes', sa.String(length=2000), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_host_recoveries_party_id', 'host_recoveries', ['party_id'])


def downgrade() -> None:
    op.drop_index('ix_host_recoveries_party_id', table_name='host_recoveries')
    op.drop_table('host_recoveries')
