"""add listing_versions and listing_approvals (ZR-ENG-CLR-001 Section 1)

Revision ID: a1f2c3d4e5f6
Revises: 4ce282ecddbb
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1f2c3d4e5f6'
down_revision: Union[str, None] = '4ce282ecddbb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'listing_versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('listing_id', sa.String(length=20), nullable=False),
        sa.Column('version_no', sa.Integer(), nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('material_change_flags', sa.JSON(), nullable=True),
        sa.Column('is_material', sa.Boolean(), nullable=False),
        sa.Column('approval_status', sa.String(length=20), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['listing_id'], ['listings.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('listing_id', 'version_no', name='uq_listing_version_no'),
    )
    op.create_index(op.f('ix_listing_versions_listing_id'), 'listing_versions', ['listing_id'], unique=False)

    op.create_table(
        'listing_approvals',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('listing_version_id', sa.Integer(), nullable=False),
        sa.Column('market_profile', sa.String(length=50), nullable=True),
        sa.Column('decision', sa.String(length=30), nullable=False),
        sa.Column('decision_reason_code', sa.String(length=100), nullable=True),
        sa.Column('reason_note', sa.String(length=2000), nullable=True),
        sa.Column('reviewer_admin_id', sa.Integer(), nullable=True),
        sa.Column('reviewer_authority_scope', sa.String(length=30), nullable=True),
        sa.Column('evidence_refs', sa.JSON(), nullable=True),
        sa.Column('policy_version', sa.String(length=20), nullable=True),
        sa.Column('is_override', sa.Boolean(), nullable=False),
        sa.Column('supersedes_approval_id', sa.Integer(), nullable=True),
        sa.Column('review_by', sa.DateTime(timezone=True), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['listing_version_id'], ['listing_versions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reviewer_admin_id'], ['admin_users.id']),
        sa.ForeignKeyConstraint(['supersedes_approval_id'], ['listing_approvals.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_listing_approvals_listing_version_id'), 'listing_approvals', ['listing_version_id'], unique=False)

    op.add_column('listings', sa.Column('current_draft_version_id', sa.Integer(), nullable=True))
    op.add_column('listings', sa.Column('current_public_version_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_listings_current_draft_version_id', 'listings', 'listing_versions', ['current_draft_version_id'], ['id']
    )
    op.create_foreign_key(
        'fk_listings_current_public_version_id', 'listings', 'listing_versions', ['current_public_version_id'], ['id']
    )


def downgrade() -> None:
    op.drop_constraint('fk_listings_current_public_version_id', 'listings', type_='foreignkey')
    op.drop_constraint('fk_listings_current_draft_version_id', 'listings', type_='foreignkey')
    op.drop_column('listings', 'current_public_version_id')
    op.drop_column('listings', 'current_draft_version_id')
    op.drop_index(op.f('ix_listing_approvals_listing_version_id'), table_name='listing_approvals')
    op.drop_table('listing_approvals')
    op.drop_index(op.f('ix_listing_versions_listing_id'), table_name='listing_versions')
    op.drop_table('listing_versions')
