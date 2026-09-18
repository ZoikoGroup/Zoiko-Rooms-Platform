"""change default jurisdiction from IN to England

Revision ID: fe9dba16d20e
Revises: db82dd795801
Create Date: 2026-09-17 15:55:45.521067

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fe9dba16d20e'
down_revision: Union[str, None] = 'db82dd795801'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # This platform targets foreign markets, not India -- "England" is the
    # only jurisdiction with a real market-pack/agreement-clause registry
    # (services/agreement_profile.py:SUPPORTED_JURISDICTION). "IN" must
    # never be the silent default a new row falls back to.
    op.alter_column("properties", "jurisdiction_code", server_default="England")
    op.alter_column("parties", "jurisdiction", server_default="England")
    op.alter_column("occupancy_classifications", "jurisdiction", server_default="England")


def downgrade() -> None:
    op.alter_column("properties", "jurisdiction_code", server_default="IN")
    op.alter_column("parties", "jurisdiction", server_default="IN")
    op.alter_column("occupancy_classifications", "jurisdiction", server_default="IN")
