"""add booking_change_requests table (ZR-ENG-CLR-008 Section 8 MVP: renter-initiated move-in date change)

Revision ID: 5cdcb0af9c8b
Revises: 45fc1881d4f3
Create Date: 2026-09-10 11:17:29.362758

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5cdcb0af9c8b'
down_revision: Union[str, None] = '45fc1881d4f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'booking_change_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('agreement_id', sa.Integer(), nullable=False),
        sa.Column('requested_by_guest_id', sa.String(), nullable=False),
        sa.Column('change_type', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('original_start_date', sa.Date(), nullable=False),
        sa.Column('proposed_start_date', sa.Date(), nullable=False),
        sa.Column('reason', sa.String(length=500), nullable=False),
        sa.Column('decision_note', sa.String(length=500), nullable=False),
        sa.Column('decided_by_admin_id', sa.Integer(), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resulting_amendment_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['agreement_id'], ['agreements.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['requested_by_guest_id'], ['guests.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['decided_by_admin_id'], ['admin_users.id']),
        sa.ForeignKeyConstraint(['resulting_amendment_id'], ['agreement_amendments.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_booking_change_requests_agreement_id', 'booking_change_requests', ['agreement_id'])


def downgrade() -> None:
    op.drop_index('ix_booking_change_requests_agreement_id', table_name='booking_change_requests')
    op.drop_table('booking_change_requests')
