"""add agreement engine foundation tables

Revision ID: 792ec39e0d10
Revises: af63da49be56
Create Date: 2026-09-09 11:55:05.396039

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '792ec39e0d10'
down_revision: Union[str, None] = 'af63da49be56'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'agreement_clause_definitions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('clause_id', sa.String(length=100), nullable=False),
        sa.Column('jurisdiction_scope', sa.String(length=50), nullable=False),
        sa.Column('agreement_class', sa.String(length=100), nullable=False),
        sa.Column('mandatory_level', sa.String(length=20), nullable=False, server_default='OPTIONAL'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='DRAFT'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('title', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('approval_note', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('clause_id', name='uq_agreement_clause_definitions_clause_id'),
    )

    op.create_table(
        'agreement_versions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('agreement_id', sa.Integer(), sa.ForeignKey('agreements.id', ondelete='CASCADE'), nullable=False),
        sa.Column('version_no', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='WORKING'),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('frozen_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('agreement_id', 'version_no', name='uq_agreement_version_no'),
    )

    op.create_table(
        'document_artifacts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'agreement_version_id', sa.Integer(),
            sa.ForeignKey('agreement_versions.id', ondelete='CASCADE'), nullable=False, unique=True,
        ),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('storage_ref', sa.String(length=255), nullable=False),
        sa.Column('mime_type', sa.String(length=100), nullable=False, server_default='application/pdf'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        'signature_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('agreement_id', sa.Integer(), sa.ForeignKey('agreements.id', ondelete='CASCADE'), nullable=False),
        sa.Column(
            'agreement_version_id', sa.Integer(),
            sa.ForeignKey('agreement_versions.id', ondelete='CASCADE'), nullable=False,
        ),
        sa.Column('signer_role', sa.String(length=20), nullable=False),
        sa.Column('signer_identifier', sa.String(length=50), nullable=False),
        sa.Column('method', sa.String(length=20), nullable=False, server_default='SIMPLE_ESIGN'),
        sa.Column('document_hash', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('consented_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('signature_events')
    op.drop_table('document_artifacts')
    op.drop_table('agreement_versions')
    op.drop_table('agreement_clause_definitions')
