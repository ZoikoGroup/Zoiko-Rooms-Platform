"""add deleted_at to evidence artifacts

Revision ID: 51fe2deca6a8
Revises: e9ed45a3aff8
Create Date: 2026-09-15 14:59:04.399650

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '51fe2deca6a8'
down_revision: Union[str, None] = 'e9ed45a3aff8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("evidence_artifacts", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("evidence_artifacts", "deleted_at")
