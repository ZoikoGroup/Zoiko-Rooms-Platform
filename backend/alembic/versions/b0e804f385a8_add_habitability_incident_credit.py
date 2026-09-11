"""add habitability_incidents credit columns

Revision ID: b0e804f385a8
Revises: 3e59defa2964
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b0e804f385a8'
down_revision: Union[str, None] = '3e59defa2964'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-006 Section 9.1 H1: "possible rent adjustment/credit" -- an
    # admin-applied amount, not a computed abatement formula. See
    # models/habitability_incident.py.
    op.add_column('habitability_incidents', sa.Column('credited_amount', sa.Numeric(12, 2), nullable=False, server_default='0'))
    op.add_column('habitability_incidents', sa.Column('credited_refund_request_id', sa.Integer(), sa.ForeignKey('refund_requests.id'), nullable=True))
    op.add_column('habitability_incidents', sa.Column('credit_reason', sa.String(length=2000), nullable=False, server_default=''))
    op.add_column('habitability_incidents', sa.Column('credited_by_admin_id', sa.Integer(), sa.ForeignKey('admin_users.id'), nullable=True))
    op.add_column('habitability_incidents', sa.Column('credited_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('habitability_incidents', 'credited_at')
    op.drop_column('habitability_incidents', 'credited_by_admin_id')
    op.drop_column('habitability_incidents', 'credit_reason')
    op.drop_column('habitability_incidents', 'credited_refund_request_id')
    op.drop_column('habitability_incidents', 'credited_amount')
