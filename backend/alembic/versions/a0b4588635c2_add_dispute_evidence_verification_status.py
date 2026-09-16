"""add dispute evidence verification_status

ZR-ENG-CLR-010 Phase 16: Section 22 evidence verification state machine,
see app/models/dispute_evidence.py.

Revision ID: a0b4588635c2
Revises: ea18de309776
Create Date: 2026-09-15 00:00:13.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a0b4588635c2'
down_revision: Union[str, None] = 'ea18de309776'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dispute_evidence_items", sa.Column("verification_status", sa.String(length=10), nullable=False, server_default="RECEIVED"))


def downgrade() -> None:
    op.drop_column("dispute_evidence_items", "verification_status")
