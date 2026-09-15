"""add habitability_incidents

Revision ID: 36a14b296606
Revises: cfe743f7f90a
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '36a14b296606'
down_revision: Union[str, None] = 'cfe743f7f90a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-006 Section 9: one habitability/unavailability incident per
    # report -- see models/habitability_incident.py.
    op.create_table(
        'habitability_incidents',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('room_id', sa.Integer(), sa.ForeignKey('rooms.id', ondelete='CASCADE'), nullable=False),
        sa.Column('occupancy_id', sa.Integer(), sa.ForeignKey('occupancies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('reported_by_guest_id', sa.String(), sa.ForeignKey('guests.id', ondelete='SET NULL'), nullable=True),
        sa.Column('reported_by_admin_id', sa.Integer(), sa.ForeignKey('admin_users.id'), nullable=True),
        sa.Column('severity', sa.String(length=2), nullable=False),
        sa.Column('description', sa.String(length=2000), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='OPEN'),
        sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_by_admin_id', sa.Integer(), sa.ForeignKey('admin_users.id'), nullable=True),
        sa.Column('resolution_notes', sa.String(length=2000), nullable=False, server_default=''),
    )
    op.create_index('ix_habitability_incidents_room_id', 'habitability_incidents', ['room_id'])


def downgrade() -> None:
    op.drop_index('ix_habitability_incidents_room_id', table_name='habitability_incidents')
    op.drop_table('habitability_incidents')
