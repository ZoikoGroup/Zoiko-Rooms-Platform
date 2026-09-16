"""add missing notice and adjudicated effective date columns to termination cases

Revision ID: b55945ee016f
Revises: b0bc6ccb5afd
Create Date: 2026-09-16 12:46:22.454006

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b55945ee016f'
down_revision: Union[str, None] = 'b0bc6ccb5afd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("termination_cases", sa.Column("notice_served_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("termination_cases", sa.Column("notice_method", sa.String(30), nullable=False, server_default=sa.text("'PORTAL'")))
    op.add_column("termination_cases", sa.Column("adjudicated_effective_date", sa.Date(), nullable=True))
    op.add_column(
        "termination_cases", sa.Column("adjudicated_effective_date_reason", sa.String(2000), nullable=False, server_default=sa.text("''")),
    )


def downgrade() -> None:
    op.drop_column("termination_cases", "adjudicated_effective_date_reason")
    op.drop_column("termination_cases", "adjudicated_effective_date")
    op.drop_column("termination_cases", "notice_method")
    op.drop_column("termination_cases", "notice_served_at")
