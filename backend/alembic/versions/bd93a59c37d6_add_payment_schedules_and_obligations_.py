"""add payment_schedules and obligations.schedule_id

Revision ID: bd93a59c37d6
Revises: fd2dc05f06e5
Create Date: 2026-09-10 12:29:34.063158

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bd93a59c37d6'
down_revision: Union[str, None] = 'fd2dc05f06e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'payment_schedules',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('agreement_id', sa.Integer(), sa.ForeignKey('agreements.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('cadence', sa.String(length=20), nullable=False, server_default='MONTHLY'),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='INR'),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('first_due', sa.Date(), nullable=False),
        sa.Column('anchor_day', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='ACTIVE'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    # ZR-ENG-CLR-005 AC-02/AC-07: traces a RENT obligation back to the
    # PaymentSchedule it was generated from; nullable since DEPOSIT
    # obligations and anything pre-dating this migration have none.
    op.add_column('obligations', sa.Column('schedule_id', sa.Integer(), sa.ForeignKey('payment_schedules.id'), nullable=True))
    op.create_index('ix_obligations_schedule_id', 'obligations', ['schedule_id'])


def downgrade() -> None:
    op.drop_index('ix_obligations_schedule_id', table_name='obligations')
    op.drop_column('obligations', 'schedule_id')
    op.drop_table('payment_schedules')
