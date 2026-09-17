"""add room_alerts table and listing published_at

Revision ID: d2a1ff9b9fc4
Revises: e49ffda0d565
Create Date: 2026-09-04 15:52:55.094391

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd2a1ff9b9fc4'
down_revision: Union[str, None] = 'e49ffda0d565'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('room_alerts',
    sa.Column('id', sa.String(length=20), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('city', sa.String(length=255), nullable=False),
    sa.Column('min_price', sa.Float(), nullable=True),
    sa.Column('max_price', sa.Float(), nullable=True),
    sa.Column('room_type', sa.String(length=255), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('unsubscribe_token', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_notified_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_room_alerts_email'), 'room_alerts', ['email'], unique=False)
    op.create_index(op.f('ix_room_alerts_unsubscribe_token'), 'room_alerts', ['unsubscribe_token'], unique=True)
    op.add_column('listings', sa.Column('published_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('listings', 'published_at')
    op.drop_index(op.f('ix_room_alerts_unsubscribe_token'), table_name='room_alerts')
    op.drop_index(op.f('ix_room_alerts_email'), table_name='room_alerts')
    op.drop_table('room_alerts')
