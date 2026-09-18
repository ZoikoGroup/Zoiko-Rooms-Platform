"""add room_holds (ZR-ENG-CLR-001 Section 1, Rule 4 -- atomic inventory/hold service)

Revision ID: b2c3d4e5f6a7
Revises: a1f2c3d4e5f6
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1f2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'room_holds',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('room_id', sa.Integer(), nullable=False),
        sa.Column('source_type', sa.String(length=20), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('release_reason', sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    # Partial unique index: only one *active* (unreleased) hold per room at a
    # time. This is the actual atomicity guarantee -- a concurrent second
    # INSERT for the same room while a hold is active fails at the database
    # level, not via an application-side check-then-act race.
    op.create_index(
        'uq_room_holds_active_room', 'room_holds', ['room_id'], unique=True,
        postgresql_where=sa.text('released_at IS NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_room_holds_active_room', table_name='room_holds')
    op.drop_table('room_holds')
