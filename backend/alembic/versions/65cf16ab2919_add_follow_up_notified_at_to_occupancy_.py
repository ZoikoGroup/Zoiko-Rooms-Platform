"""add follow up notified at to occupancy eligibility checks

Revision ID: 65cf16ab2919
Revises: d9961481186c
Create Date: 2026-09-15 10:42:34.574972

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '65cf16ab2919'
down_revision: Union[str, None] = 'd9961481186c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "occupancy_eligibility_checks",
        sa.Column("follow_up_notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("occupancy_eligibility_checks", "follow_up_notified_at")
