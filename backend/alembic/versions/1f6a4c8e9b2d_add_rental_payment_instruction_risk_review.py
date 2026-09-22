"""add rental_payment_instructions risk/review columns

Revision ID: 1f6a4c8e9b2d
Revises: 4d8b6e1a9c3f
Create Date: 2026-09-21 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1f6a4c8e9b2d'
down_revision: Union[str, None] = '4d8b6e1a9c3f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-PAY-002 Section 9.1 steps 3/7: risk signal + manual-review outcome
    # for a payment-instruction change.
    op.add_column('rental_payment_instructions', sa.Column('is_high_risk', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('rental_payment_instructions', sa.Column('high_risk_reason', sa.String(length=255), nullable=False, server_default=''))
    op.add_column('rental_payment_instructions', sa.Column('reviewed_by_admin_id', sa.Integer(), nullable=True))
    op.add_column('rental_payment_instructions', sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('rental_payment_instructions', sa.Column('review_reason', sa.String(length=2000), nullable=False, server_default=''))
    op.create_foreign_key(
        'fk_rental_payment_instructions_reviewed_by_admin_id', 'rental_payment_instructions', 'admin_users',
        ['reviewed_by_admin_id'], ['id'],
    )


def downgrade() -> None:
    op.drop_constraint('fk_rental_payment_instructions_reviewed_by_admin_id', 'rental_payment_instructions', type_='foreignkey')
    op.drop_column('rental_payment_instructions', 'review_reason')
    op.drop_column('rental_payment_instructions', 'reviewed_at')
    op.drop_column('rental_payment_instructions', 'reviewed_by_admin_id')
    op.drop_column('rental_payment_instructions', 'high_risk_reason')
    op.drop_column('rental_payment_instructions', 'is_high_risk')
