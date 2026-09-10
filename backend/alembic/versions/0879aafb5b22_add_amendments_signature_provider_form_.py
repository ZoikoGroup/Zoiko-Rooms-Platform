"""add amendments, signature provider infra, form templates, translations, agreement_party/premises/commercial_terms/execution_certificate/termination_record

Revision ID: 0879aafb5b22
Revises: e232ff7d2f4a
Create Date: 2026-09-09 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0879aafb5b22'
down_revision: Union[str, None] = 'e232ff7d2f4a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'agreement_parties',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('agreement_id', sa.Integer(), sa.ForeignKey('agreements.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('party_type', sa.String(length=20), nullable=False, server_default='individual'),
        sa.Column('legal_name', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('contact_email', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('authority_evidence_ref', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('is_signatory', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        'agreement_premises',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'agreement_version_id', sa.Integer(),
            sa.ForeignKey('agreement_versions.id', ondelete='CASCADE'), nullable=False, unique=True,
        ),
        sa.Column('listing_id', sa.String(length=50), nullable=False),
        sa.Column('room_id', sa.Integer(), nullable=True),
        sa.Column('address', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('city', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('room_size', sa.Integer(), nullable=True),
        sa.Column('has_ensuite', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('shared_areas', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        'commercial_terms_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'agreement_version_id', sa.Integer(),
            sa.ForeignKey('agreement_versions.id', ondelete='CASCADE'), nullable=False, unique=True,
        ),
        sa.Column('monthly_rent', sa.Numeric(12, 2), nullable=False),
        sa.Column('deposit_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('term_months', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=10), nullable=False, server_default='INR'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        'execution_certificates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'agreement_version_id', sa.Integer(),
            sa.ForeignKey('agreement_versions.id', ondelete='CASCADE'), nullable=False, unique=True,
        ),
        sa.Column('document_hash', sa.String(length=64), nullable=False),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('signer_summary', sa.JSON(), nullable=False),
        sa.Column('provider_transaction_ids', sa.JSON(), nullable=False),
    )

    op.create_table(
        'termination_records',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('occupancy_id', sa.Integer(), sa.ForeignKey('occupancies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('agreement_id', sa.Integer(), sa.ForeignKey('agreements.id', ondelete='CASCADE'), nullable=False),
        sa.Column('basis', sa.String(length=30), nullable=False, server_default='OTHER'),
        sa.Column('notice_given_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('liability_end_date', sa.Date(), nullable=True),
        sa.Column('termination_effective_date', sa.Date(), nullable=True),
        sa.Column('physical_move_out_date', sa.Date(), nullable=True),
        sa.Column('created_by_admin_id', sa.Integer(), sa.ForeignKey('admin_users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        'agreement_amendments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('agreement_id', sa.Integer(), sa.ForeignKey('agreements.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_version_id', sa.Integer(), sa.ForeignKey('agreement_versions.id'), nullable=False),
        sa.Column('resulting_version_id', sa.Integer(), sa.ForeignKey('agreement_versions.id'), nullable=True),
        sa.Column('amendment_type', sa.String(length=30), nullable=True),
        sa.Column('status', sa.String(length=30), nullable=False, server_default='REQUESTED'),
        sa.Column('reason', sa.String(length=1000), nullable=False, server_default=''),
        sa.Column('proposed_terms', sa.JSON(), nullable=False),
        sa.Column('requested_by_admin_id', sa.Integer(), sa.ForeignKey('admin_users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('classified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('terms_proposed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('approvals_pending_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('execution_pending_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('executed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('effective_at', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        'signature_requests',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('agreement_id', sa.Integer(), sa.ForeignKey('agreements.id', ondelete='CASCADE'), nullable=False),
        sa.Column(
            'agreement_version_id', sa.Integer(),
            sa.ForeignKey('agreement_versions.id', ondelete='CASCADE'), nullable=False,
        ),
        sa.Column('party_role', sa.String(length=20), nullable=False),
        sa.Column('method', sa.String(length=20), nullable=False, server_default='SIMPLE_ESIGN'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='PENDING'),
        sa.Column('deadline', sa.DateTime(timezone=True), nullable=True),
        sa.Column('provider_transaction_id', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        'signature_provider_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('provider_event_id', sa.String(length=100), nullable=False),
        sa.Column('signature_request_id', sa.Integer(), sa.ForeignKey('signature_requests.id'), nullable=True),
        sa.Column('event_type', sa.String(length=30), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('provider_event_id', name='uq_signature_provider_events_provider_event_id'),
    )

    op.create_table(
        'signature_provider_status',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('healthy', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        'agreement_form_templates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('jurisdiction_scope', sa.String(length=50), nullable=False),
        sa.Column('agreement_class', sa.String(length=100), nullable=False),
        sa.Column('form_mode', sa.String(length=1), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='DRAFT'),
        sa.Column('effective_from', sa.Date(), nullable=True),
        sa.Column('effective_to', sa.Date(), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('source_document_storage_ref', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('source_document_content_hash', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('field_anchor_map', sa.JSON(), nullable=False),
        sa.Column('authoritative_content_text', sa.String(length=20000), nullable=False, server_default=''),
        sa.Column('approval_note', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            'jurisdiction_scope', 'agreement_class', 'form_mode', 'version',
            name='uq_form_template_scope_mode_version',
        ),
    )

    op.create_table(
        'agreement_clause_translations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'clause_definition_id', sa.Integer(),
            sa.ForeignKey('agreement_clause_definitions.id', ondelete='CASCADE'), nullable=False,
        ),
        sa.Column('language_code', sa.String(length=10), nullable=False),
        sa.Column('translated_title', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('translated_content', sa.String(length=20000), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='DRAFT'),
        sa.Column('approval_note', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            'clause_definition_id', 'language_code', name='uq_clause_translation_definition_language',
        ),
    )


def downgrade() -> None:
    op.drop_table('agreement_clause_translations')
    op.drop_table('agreement_form_templates')
    op.drop_table('signature_provider_status')
    op.drop_table('signature_provider_events')
    op.drop_table('signature_requests')
    op.drop_table('agreement_amendments')
    op.drop_table('termination_records')
    op.drop_table('execution_certificates')
    op.drop_table('commercial_terms_snapshots')
    op.drop_table('agreement_premises')
    op.drop_table('agreement_parties')
