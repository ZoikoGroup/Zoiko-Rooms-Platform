"""make termination_cases.earliest_effective_date nullable

Revision ID: 1efea5419c66
Revises: 1bc4f36cb249
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '1efea5419c66'
down_revision: Union[str, None] = '1bc4f36cb249'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-006 AC-35: a PENDING_REVIEW case (a cause this build can't
    # auto-resolve a date for) has no earliest_effective_date until a Super
    # Admin decides it -- see models/termination_case.py's own docstring.
    op.alter_column(
        'termination_cases', 'earliest_effective_date',
        existing_type=sa.Date(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        'termination_cases', 'earliest_effective_date',
        existing_type=sa.Date(),
        nullable=False,
    )
