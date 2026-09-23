"""add payment recipient authority change step-up columns

Revision ID: 0b6d187351e9
Revises: 48904ca4e0b4
Create Date: 2026-09-22 16:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0b6d187351e9'
down_revision: Union[str, None] = '48904ca4e0b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('payment_recipient_authorities', sa.Column('verification_code_hash', sa.String(length=64), nullable=True))
    op.add_column('payment_recipient_authorities', sa.Column('verification_code_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('payment_recipient_authorities', sa.Column('verification_attempts', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('payment_recipient_authorities', sa.Column('is_high_risk', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('payment_recipient_authorities', sa.Column('high_risk_reason', sa.String(length=255), nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column('payment_recipient_authorities', 'high_risk_reason')
    op.drop_column('payment_recipient_authorities', 'is_high_risk')
    op.drop_column('payment_recipient_authorities', 'verification_attempts')
    op.drop_column('payment_recipient_authorities', 'verification_code_expires_at')
    op.drop_column('payment_recipient_authorities', 'verification_code_hash')
