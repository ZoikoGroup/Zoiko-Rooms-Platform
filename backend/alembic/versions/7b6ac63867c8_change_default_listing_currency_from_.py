"""change default listing currency from INR to GBP

Revision ID: 7b6ac63867c8
Revises: fe9dba16d20e
Create Date: 2026-09-17 16:12:46.198064

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7b6ac63867c8'
down_revision: Union[str, None] = 'fe9dba16d20e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # This platform targets foreign markets, not India -- "GBP" matches
    # "England", the only jurisdiction with a real market-pack/agreement-
    # clause registry (services/agreement_profile.py:SUPPORTED_JURISDICTION).
    op.alter_column("listings", "currency", server_default="GBP")


def downgrade() -> None:
    op.alter_column("listings", "currency", server_default="INR")
