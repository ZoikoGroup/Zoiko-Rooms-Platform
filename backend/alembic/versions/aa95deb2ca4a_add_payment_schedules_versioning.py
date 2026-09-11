"""add payment_schedules versioning

Revision ID: aa95deb2ca4a
Revises: ec2c29b24747
Create Date: 2026-09-10 17:05:46.184171

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aa95deb2ca4a'
down_revision: Union[str, None] = 'ec2c29b24747'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-005 Section 7.2: an agreement can now have more than one
    # PaymentSchedule row over its lifetime (one per rent-changing
    # amendment) -- drop the old one-per-agreement-ever constraint.
    op.drop_constraint('payment_schedules_agreement_id_key', 'payment_schedules', type_='unique')
    op.create_index('ix_payment_schedules_agreement_id', 'payment_schedules', ['agreement_id'])

    op.add_column('payment_schedules', sa.Column('version', sa.Integer(), nullable=False, server_default='1'))

    # Data-integrity backstop: at most one ACTIVE schedule per agreement at a
    # time, same partial-unique-index pattern as room_holds/domain_events.
    op.create_index(
        'uq_payment_schedules_active_agreement', 'payment_schedules', ['agreement_id'],
        unique=True, postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_index('uq_payment_schedules_active_agreement', table_name='payment_schedules')
    op.drop_column('payment_schedules', 'version')
    op.drop_index('ix_payment_schedules_agreement_id', table_name='payment_schedules')
    op.create_unique_constraint('payment_schedules_agreement_id_key', 'payment_schedules', ['agreement_id'])
