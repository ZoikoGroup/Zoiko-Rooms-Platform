"""add refund_entitlements

Revision ID: 84aaf6a42670
Revises: 238312c8f891
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '84aaf6a42670'
down_revision: Union[str, None] = '238312c8f891'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-006 Section 12/19: one versioned, itemized refund
    # entitlement calculation per termination_case -- see
    # models/refund_entitlement.py.
    op.create_table(
        'refund_entitlements',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('termination_case_id', sa.Integer(), sa.ForeignKey('termination_cases.id', ondelete='CASCADE'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='INR'),
        sa.Column('gross_refundable', sa.Numeric(12, 2), nullable=False),
        sa.Column('net_refund', sa.Numeric(12, 2), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='CALCULATED'),
        sa.Column('calculated_by_admin_id', sa.Integer(), sa.ForeignKey('admin_users.id'), nullable=True),
        sa.Column('calculated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_refund_entitlements_termination_case_id', 'refund_entitlements', ['termination_case_id'])

    op.create_table(
        'refund_entitlement_line_items',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('entitlement_id', sa.Integer(), sa.ForeignKey('refund_entitlements.id', ondelete='CASCADE'), nullable=False),
        sa.Column('type', sa.String(length=30), nullable=False),
        sa.Column('source_obligation_id', sa.Integer(), sa.ForeignKey('obligations.id'), nullable=True),
        sa.Column('period_due_date', sa.Date(), nullable=True),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('basis_note', sa.String(length=500), nullable=False, server_default=''),
    )


def downgrade() -> None:
    op.drop_table('refund_entitlement_line_items')
    op.drop_index('ix_refund_entitlements_termination_case_id', table_name='refund_entitlements')
    op.drop_table('refund_entitlements')
