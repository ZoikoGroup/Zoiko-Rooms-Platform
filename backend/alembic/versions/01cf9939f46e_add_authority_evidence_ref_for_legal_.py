"""add authority evidence ref for legal order change

Revision ID: 01cf9939f46e
Revises: be5ae21f499e
Create Date: 2026-09-11 11:46:30.704902

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '01cf9939f46e'
down_revision: Union[str, None] = 'be5ae21f499e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "booking_change_requests", sa.Column("authority_evidence_ref", sa.String(length=1024), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("booking_change_requests", "authority_evidence_ref")
