"""add premises change fields to booking_change_requests

Revision ID: 458ec13890f5
Revises: a86dc3c74ff1
Create Date: 2026-09-10 14:35:58.734931

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '458ec13890f5'
down_revision: Union[str, None] = 'a86dc3c74ff1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('booking_change_requests', sa.Column('target_listing_id', sa.String(), nullable=True))
    op.add_column('booking_change_requests', sa.Column('resulting_application_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_booking_change_requests_target_listing', 'booking_change_requests', 'listings',
        ['target_listing_id'], ['id'],
    )
    op.create_foreign_key(
        'fk_booking_change_requests_resulting_application', 'booking_change_requests', 'applications',
        ['resulting_application_id'], ['id'],
    )


def downgrade() -> None:
    op.drop_constraint('fk_booking_change_requests_resulting_application', 'booking_change_requests', type_='foreignkey')
    op.drop_constraint('fk_booking_change_requests_target_listing', 'booking_change_requests', type_='foreignkey')
    op.drop_column('booking_change_requests', 'resulting_application_id')
    op.drop_column('booking_change_requests', 'target_listing_id')
