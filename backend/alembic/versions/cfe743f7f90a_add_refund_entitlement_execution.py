"""add refund entitlement execution

Revision ID: cfe743f7f90a
Revises: 84aaf6a42670
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cfe743f7f90a'
down_revision: Union[str, None] = '84aaf6a42670'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-006 Section 15/16.1: executing an entitlement creates real
    # Section 5 RefundRequest rows against each REFUNDABLE_UNEARNED_RENT line.
    op.add_column('refund_entitlements', sa.Column('executed_by_admin_id', sa.Integer(), sa.ForeignKey('admin_users.id'), nullable=True))
    op.add_column('refund_entitlements', sa.Column('executed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'refund_entitlement_line_items',
        sa.Column('refund_request_id', sa.Integer(), sa.ForeignKey('refund_requests.id'), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('refund_entitlement_line_items', 'refund_request_id')
    op.drop_column('refund_entitlements', 'executed_at')
    op.drop_column('refund_entitlements', 'executed_by_admin_id')
