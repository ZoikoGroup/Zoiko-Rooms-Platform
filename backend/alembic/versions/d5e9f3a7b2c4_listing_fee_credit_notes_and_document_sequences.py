"""listing fee credit notes and gap-free document sequences

Revision ID: d5e9f3a7b2c4
Revises: c4d8e2f6a1b3
Create Date: 2026-09-28 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e9f3a7b2c4'
down_revision: Union[str, None] = 'c4d8e2f6a1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'document_sequences',
        sa.Column('name', sa.String(length=50), primary_key=True),
        sa.Column('next_value', sa.BigInteger(), nullable=False, server_default='1'),
    )
    op.add_column('listing_fee_refunds', sa.Column('credit_note_number', sa.String(length=30), nullable=True))
    op.add_column('listing_fee_refunds', sa.Column('credit_note_storage_ref', sa.String(length=255), nullable=True))
    op.add_column('listing_fee_refunds', sa.Column('credit_note_content_hash', sa.String(length=64), nullable=True))
    op.add_column('listing_fee_refunds', sa.Column('credit_note_issued_at', sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint('uq_listing_fee_refunds_credit_note_number', 'listing_fee_refunds', ['credit_note_number'])


def downgrade() -> None:
    op.drop_constraint('uq_listing_fee_refunds_credit_note_number', 'listing_fee_refunds', type_='unique')
    op.drop_column('listing_fee_refunds', 'credit_note_issued_at')
    op.drop_column('listing_fee_refunds', 'credit_note_content_hash')
    op.drop_column('listing_fee_refunds', 'credit_note_storage_ref')
    op.drop_column('listing_fee_refunds', 'credit_note_number')
    op.drop_table('document_sequences')
