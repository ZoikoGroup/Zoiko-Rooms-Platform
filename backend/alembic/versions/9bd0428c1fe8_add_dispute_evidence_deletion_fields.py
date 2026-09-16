"""add dispute evidence deletion fields

ZR-ENG-CLR-010 Phase 24: QA-Q16 privacy-deletion vs. legal-hold --
crud/dispute_evidence.py:request_deletion, gated by DisputeEvidenceItem.legal_hold.

Revision ID: 9bd0428c1fe8
Revises: 220c8a008bf3
Create Date: 2026-09-15 00:00:20.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9bd0428c1fe8'
down_revision: Union[str, None] = '220c8a008bf3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dispute_evidence_items", sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("dispute_evidence_items", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("dispute_evidence_items", sa.Column("deletion_refused_reason", sa.String(length=500), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("dispute_evidence_items", "deletion_refused_reason")
    op.drop_column("dispute_evidence_items", "deleted_at")
    op.drop_column("dispute_evidence_items", "deletion_requested_at")
