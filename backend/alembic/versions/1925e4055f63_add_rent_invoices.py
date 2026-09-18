"""add rent_invoices

Revision ID: 1925e4055f63
Revises: ee6ae69538b9
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1925e4055f63'
down_revision: Union[str, None] = 'ee6ae69538b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-005 Section 13.1: one immutable, host-issued PDF per RENT
    # Obligation -- see models/finance.py:RentInvoice.
    op.create_table(
        'rent_invoices',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'obligation_id', sa.Integer(), sa.ForeignKey('obligations.id', ondelete='CASCADE'),
            nullable=False, unique=True,
        ),
        sa.Column('invoice_number', sa.String(length=30), nullable=False, unique=True),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('storage_ref', sa.String(length=255), nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('rent_invoices')
