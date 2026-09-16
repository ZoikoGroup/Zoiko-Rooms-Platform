"""add dispute_evidence_items.captured_at

ZR-ENG-CLR-010 Section 21/23: "received_at, captured_at" -- when the
underlying fact actually happened (a photo taken, a message sent) as
distinct from created_at (when it was uploaded to this platform). This
build has no EXIF/metadata extraction, so it's only ever the uploader's
own optional, self-reported value. See
app/models/dispute_evidence.py:DisputeEvidenceItem.captured_at.

Revision ID: fb76a9c7eeb7
Revises: cae7c32147fb
Create Date: 2026-09-15 00:00:25.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fb76a9c7eeb7'
down_revision: Union[str, None] = 'cae7c32147fb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dispute_evidence_items",
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dispute_evidence_items", "captured_at")
