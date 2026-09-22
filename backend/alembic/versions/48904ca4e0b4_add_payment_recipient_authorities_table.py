"""add payment_recipient_authorities table

Revision ID: 48904ca4e0b4
Revises: ac43bdf3f43b
Create Date: 2026-09-22 15:47:03.923582

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '48904ca4e0b4'
down_revision: Union[str, None] = 'ac43bdf3f43b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'payment_recipient_authorities',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('party_id', sa.Integer(), nullable=False),
        sa.Column('room_id', sa.Integer(), nullable=False),
        sa.Column('relationship_type', sa.String(length=20), nullable=False),
        sa.Column('evidence_ref', sa.String(length=1024), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('verifier_admin_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['party_id'], ['parties.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['verifier_admin_id'], ['admin_users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_payment_recipient_authorities_party_id'), 'payment_recipient_authorities', ['party_id'], unique=False,
    )
    op.create_index(
        op.f('ix_payment_recipient_authorities_room_id'), 'payment_recipient_authorities', ['room_id'], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_payment_recipient_authorities_room_id'), table_name='payment_recipient_authorities')
    op.drop_index(op.f('ix_payment_recipient_authorities_party_id'), table_name='payment_recipient_authorities')
    op.drop_table('payment_recipient_authorities')
