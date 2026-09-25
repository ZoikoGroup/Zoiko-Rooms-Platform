"""allow draft sublet requests

Revision ID: 8f2a9b6c1d3e
Revises: 3259cd2709c6
Create Date: 2026-09-21 18:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8f2a9b6c1d3e"
down_revision: Union[str, None] = "3259cd2709c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "sublet_requests",
        "proposed_renter_party_id",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "sublet_requests",
        "proposed_renter_party_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
