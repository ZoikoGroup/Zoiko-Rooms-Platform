"""add dispute fields to screening checks and expand state model

Revision ID: 0d55742be987
Revises: 765593867b73
Create Date: 2026-09-15 18:06:53.198374

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0d55742be987'
down_revision: Union[str, None] = '765593867b73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("screening_checks", sa.Column("dispute_reason", sa.String(length=1000), nullable=False, server_default=""))
    op.add_column("screening_checks", sa.Column("disputed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE screening_checks SET decision_status = 'AUTHORIZED' WHERE decision_status = 'PENDING'")


def downgrade() -> None:
    op.execute("UPDATE screening_checks SET decision_status = 'PENDING' WHERE decision_status = 'AUTHORIZED'")
    op.drop_column("screening_checks", "disputed_at")
    op.drop_column("screening_checks", "dispute_reason")
