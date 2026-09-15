"""add dispute_external_proceedings deadline fields

ZR-ENG-CLR-010 Phase 30: QA-Q21 "Claim filed after external deadline;
system does not invent extension; routes according to forum rules" --
see app/models/dispute_external_proceeding.py's
external_deadline_at/filed_after_deadline and
app/crud/dispute_external_proceeding.py:_compute_external_filing_deadline.

Revision ID: 9afdc221522c
Revises: feeca8c35d30
Create Date: 2026-09-15 00:00:27.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9afdc221522c'
down_revision: Union[str, None] = 'feeca8c35d30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dispute_external_proceedings", sa.Column("external_deadline_at", sa.Date(), nullable=True))
    op.add_column(
        "dispute_external_proceedings",
        sa.Column("filed_after_deadline", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("dispute_external_proceedings", "filed_after_deadline")
    op.drop_column("dispute_external_proceedings", "external_deadline_at")
