"""add version columns to dispute case/claim/settlement/external_proceeding

ZR-ENG-CLR-010 Phase 26: AC-39 "All material state changes are...
protected by optimistic/version concurrency controls" -- extends the
version_id_col mechanism (previously only on dispute_resolution_holds,
b6833bf3ba62) to the other objects the AC names. See
app/models/dispute.py's DisputeResolutionCase/DisputeResolutionClaim,
app/models/dispute_settlement.py and
app/models/dispute_external_proceeding.py.

Revision ID: 24b100f1591e
Revises: 568dce952e84
Create Date: 2026-09-15 00:00:22.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '24b100f1591e'
down_revision: Union[str, None] = '568dce952e84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dispute_resolution_cases", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("dispute_resolution_claims", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("dispute_settlements", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("dispute_external_proceedings", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    op.drop_column("dispute_external_proceedings", "version")
    op.drop_column("dispute_settlements", "version")
    op.drop_column("dispute_resolution_claims", "version")
    op.drop_column("dispute_resolution_cases", "version")
