"""add proposal hash to booking change requests

Revision ID: be5ae21f499e
Revises: e09dabbc0bd4
Create Date: 2026-09-11 11:08:29.145849

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'be5ae21f499e'
down_revision: Union[str, None] = 'e09dabbc0bd4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # AC-16/AC-17: empty string on existing rows means "no hash captured" --
    # app/services/booking_change_consent.py treats that as skip-the-check,
    # so old in-flight requests aren't retroactively blocked from approval.
    op.add_column(
        "booking_change_requests", sa.Column("proposal_hash", sa.String(length=64), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("booking_change_requests", "proposal_hash")
