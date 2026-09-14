"""add refund_entitlements.approved_by_admin_id/approved_at

Revision ID: 3f8b1c6d9a24
Revises: 1b7e4f92a3c6
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f8b1c6d9a24'
down_revision: Union[str, None] = '1b7e4f92a3c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-006 Section 16.1: a real APPROVED step between CALCULATED
    # and EXECUTED (models/refund_entitlement.py:REFUND_ENTITLEMENT_STATUSES's
    # own docstring) -- nullable, since every entitlement row that already
    # exists predates the approval step and was never approved under it.
    op.add_column('refund_entitlements', sa.Column('approved_by_admin_id', sa.Integer(), sa.ForeignKey('admin_users.id'), nullable=True))
    op.add_column('refund_entitlements', sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('refund_entitlements', 'approved_at')
    op.drop_column('refund_entitlements', 'approved_by_admin_id')
