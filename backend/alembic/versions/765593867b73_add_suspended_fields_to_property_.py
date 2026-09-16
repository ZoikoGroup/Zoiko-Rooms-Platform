"""add suspended fields to property compliance credentials

Revision ID: 765593867b73
Revises: 97bd6e54f43c
Create Date: 2026-09-15 17:45:20.172081

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '765593867b73'
down_revision: Union[str, None] = '97bd6e54f43c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("property_compliance_credentials", sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("property_compliance_credentials", sa.Column("suspended_reason", sa.String(length=500), nullable=False, server_default=""))
    op.execute("UPDATE property_compliance_credentials SET status = 'UNDER_REVIEW' WHERE status = 'DECLARED'")


def downgrade() -> None:
    op.execute("UPDATE property_compliance_credentials SET status = 'DECLARED' WHERE status = 'UNDER_REVIEW'")
    op.drop_column("property_compliance_credentials", "suspended_reason")
    op.drop_column("property_compliance_credentials", "suspended_at")
