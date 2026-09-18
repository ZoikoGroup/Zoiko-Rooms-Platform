"""extend agreement engine for AC-06/08/10/11/15/16/17/20/25/30 gaps

Revision ID: e232ff7d2f4a
Revises: e67c30764cbb
Create Date: 2026-09-09 16:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e232ff7d2f4a'
down_revision: Union[str, None] = 'e67c30764cbb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # AC-25: clause_id is no longer unique alone -- (clause_id, version) is,
    # so a clause can have real version history (see
    # crud/agreement_clauses.py).
    op.drop_constraint('uq_agreement_clause_definitions_clause_id', 'agreement_clause_definitions', type_='unique')
    op.create_index(op.f('ix_agreement_clause_definitions_clause_id'), 'agreement_clause_definitions', ['clause_id'])
    op.create_unique_constraint(
        'uq_agreement_clause_definitions_clause_id_version', 'agreement_clause_definitions', ['clause_id', 'version'],
    )
    op.add_column('agreement_clause_definitions', sa.Column('effective_from', sa.Date(), nullable=True))
    op.add_column('agreement_clause_definitions', sa.Column('effective_to', sa.Date(), nullable=True))

    # AC-16: per-party delivery tracking + a real attached document per
    # disclosure.
    op.add_column(
        'disclosure_requirements',
        sa.Column('delivered_to_party', sa.String(length=20), nullable=False, server_default='renter'),
    )
    op.add_column(
        'disclosure_requirements', sa.Column('document_storage_ref', sa.String(length=255), nullable=False, server_default=''),
    )
    op.add_column(
        'disclosure_requirements', sa.Column('document_content_hash', sa.String(length=64), nullable=False, server_default=''),
    )

    # AC-11: evidence file for a WET_INK signature.
    op.add_column(
        'signature_events', sa.Column('evidence_storage_ref', sa.String(length=255), nullable=False, server_default=''),
    )

    # AC-20: notice/liability/termination-effective dates, distinct from
    # move_out_date.
    op.add_column('occupancies', sa.Column('notice_given_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('occupancies', sa.Column('liability_end_date', sa.Date(), nullable=True))
    op.add_column('occupancies', sa.Column('termination_effective_date', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('occupancies', 'termination_effective_date')
    op.drop_column('occupancies', 'liability_end_date')
    op.drop_column('occupancies', 'notice_given_at')

    op.drop_column('signature_events', 'evidence_storage_ref')

    op.drop_column('disclosure_requirements', 'document_content_hash')
    op.drop_column('disclosure_requirements', 'document_storage_ref')
    op.drop_column('disclosure_requirements', 'delivered_to_party')

    op.drop_column('agreement_clause_definitions', 'effective_to')
    op.drop_column('agreement_clause_definitions', 'effective_from')
    op.drop_constraint('uq_agreement_clause_definitions_clause_id_version', 'agreement_clause_definitions', type_='unique')
    op.drop_index(op.f('ix_agreement_clause_definitions_clause_id'), 'agreement_clause_definitions')
    op.create_unique_constraint('uq_agreement_clause_definitions_clause_id', 'agreement_clause_definitions', ['clause_id'])
