"""add autopay mandates table

Revision ID: 3a7ec40f000a
Revises: d55c63c74896
Create Date: 2026-09-16 18:28:59.862938

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '3a7ec40f000a'
down_revision: Union[str, None] = 'd55c63c74896'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: autogenerate also detected unrelated pre-existing drift (an index
    # on agreement_form_templates/evidence_artifacts/property_compliance_
    # credentials, NOT NULL changes on listing_approvals/listing_versions/
    # room_holds) that has nothing to do with this table and predates this
    # change -- deliberately excluded from this migration so it isn't
    # applied without its own separate review.
    op.create_table(
        'autopay_mandates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('occupancy_id', sa.Integer(), nullable=False),
        sa.Column('payer_guest_id', sa.String(length=20), nullable=False),
        sa.Column('provider_ref', sa.String(length=100), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('consent_snapshot', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['occupancy_id'], ['occupancies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['payer_guest_id'], ['guests.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_autopay_mandates_occupancy_id'), 'autopay_mandates', ['occupancy_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_autopay_mandates_occupancy_id'), table_name='autopay_mandates')
    op.drop_table('autopay_mandates')
