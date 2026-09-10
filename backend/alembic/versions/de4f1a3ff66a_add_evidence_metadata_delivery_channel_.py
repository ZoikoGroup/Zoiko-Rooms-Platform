"""add evidence_metadata, delivery_channel, manual_agreement_only

Revision ID: de4f1a3ff66a
Revises: 0879aafb5b22
Create Date: 2026-09-09 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'de4f1a3ff66a'
down_revision: Union[str, None] = '0879aafb5b22'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # AC-11: method-specific structured evidence (consent_statement,
    # trust_service_certificate_ref, witness_name/contact, notary_name/
    # license_ref) for ACKNOWLEDGMENT/QUALIFIED_ESIGN/WITNESSED_ESIGN/
    # NOTARIZED signatures.
    op.add_column('signature_events', sa.Column('evidence_metadata', sa.JSON(), nullable=False))

    # AC-28: which alternate delivery path (IN_APP/EMAIL/POST/ACCESSIBLE_TEXT)
    # a disclosure was actually delivered through.
    op.add_column(
        'disclosure_requirements', sa.Column('delivery_channel', sa.String(length=20), nullable=False, server_default='IN_APP'),
    )

    # AC-04 mode E: a market flagged manual-only fails closed regardless of
    # clause registry state.
    op.add_column(
        'market_releases', sa.Column('manual_agreement_only', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('market_releases', 'manual_agreement_only')
    op.drop_column('disclosure_requirements', 'delivery_channel')
    op.drop_column('signature_events', 'evidence_metadata')
