"""add guarantor consent fields and proposed guarantor on amendments

Revision ID: e9ed45a3aff8
Revises: a86208d4c278
Create Date: 2026-09-15 11:29:46.447378

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9ed45a3aff8'
down_revision: Union[str, None] = 'a86208d4c278'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agreement_parties", sa.Column("consent_method", sa.String(length=30), nullable=False, server_default=""))
    op.add_column("agreement_parties", sa.Column("consent_evidence_ref", sa.String(length=500), nullable=False, server_default=""))
    op.add_column("agreement_parties", sa.Column("consented_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agreement_amendments", sa.Column("proposed_guarantor", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("agreement_amendments", "proposed_guarantor")
    op.drop_column("agreement_parties", "consented_at")
    op.drop_column("agreement_parties", "consent_evidence_ref")
    op.drop_column("agreement_parties", "consent_method")
