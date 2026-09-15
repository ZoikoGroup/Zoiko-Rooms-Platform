"""add occupancy_co_tenants table

Revision ID: 9e1b6c4a8d37
Revises: 8a4d3f01c7e2
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9e1b6c4a8d37'
down_revision: Union[str, None] = '8a4d3f01c7e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-006 AC-25: 'Joint-tenancy ... relationships route through
    # Section 3/4 relationship data instead of assuming one renter = one
    # agreement.' The minimal Section 3/4 fact needed to make that
    # assumption checkable -- Occupancy.guest_id remains the sole tenant of
    # record; a row here marks an additional co-tenant.
    op.create_table(
        'occupancy_co_tenants',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('occupancy_id', sa.Integer(), sa.ForeignKey('occupancies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('guest_id', sa.String(), sa.ForeignKey('guests.id', ondelete='CASCADE'), nullable=False),
        sa.Column('added_by_admin_id', sa.Integer(), sa.ForeignKey('admin_users.id'), nullable=True),
        sa.Column('added_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('occupancy_id', 'guest_id', name='uq_occupancy_co_tenants_occupancy_guest'),
    )
    op.create_index('ix_occupancy_co_tenants_occupancy_id', 'occupancy_co_tenants', ['occupancy_id'])


def downgrade() -> None:
    op.drop_index('ix_occupancy_co_tenants_occupancy_id', table_name='occupancy_co_tenants')
    op.drop_table('occupancy_co_tenants')
