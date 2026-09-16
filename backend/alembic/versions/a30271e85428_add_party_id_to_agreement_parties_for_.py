"""add party id to agreement parties for guarantor verification subjects

Revision ID: a30271e85428
Revises: a9b088169196
Create Date: 2026-09-15 11:03:39.385003

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a30271e85428'
down_revision: Union[str, None] = 'a9b088169196'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agreement_parties",
        sa.Column("party_id", sa.Integer(), sa.ForeignKey("parties.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agreement_parties", "party_id")
