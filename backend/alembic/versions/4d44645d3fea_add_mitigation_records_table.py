"""add mitigation_records table

Revision ID: 4d44645d3fea
Revises: 68a5bea43d9f
Create Date: 2026-09-22 09:26:57.522532

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4d44645d3fea'
down_revision: Union[str, None] = '68a5bea43d9f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'mitigation_records',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'termination_case_id', sa.Integer(), sa.ForeignKey('termination_cases.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('marketed_for_reletting_at', sa.Date(), nullable=True),
        sa.Column('listing_channels', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column(
            'replacement_booking_id', sa.Integer(), sa.ForeignKey('occupancies.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('replacement_occupancy_start', sa.Date(), nullable=True),
        sa.Column('replacement_rent_amount', sa.Numeric(12, 2), nullable=True),
        sa.Column('reasonable_reletting_costs', sa.Numeric(12, 2), nullable=True),
        sa.Column('evidence_refs', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('notes', sa.String(length=2000), nullable=False, server_default=''),
        sa.Column('recorded_by_admin_id', sa.Integer(), sa.ForeignKey('admin_users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_mitigation_records_termination_case_id', 'mitigation_records', ['termination_case_id'])


def downgrade() -> None:
    op.drop_index('ix_mitigation_records_termination_case_id', table_name='mitigation_records')
    op.drop_table('mitigation_records')
