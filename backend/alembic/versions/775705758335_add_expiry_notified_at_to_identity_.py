"""add expiry notified at to identity verifications and property compliance credentials

Revision ID: 775705758335
Revises: 45e3f3cd19a1
Create Date: 2026-09-15 15:59:24.859486

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '775705758335'
down_revision: Union[str, None] = '45e3f3cd19a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("identity_verifications", sa.Column("expiry_notified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("property_compliance_credentials", sa.Column("expiry_notified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("property_compliance_credentials", "expiry_notified_at")
    op.drop_column("identity_verifications", "expiry_notified_at")
