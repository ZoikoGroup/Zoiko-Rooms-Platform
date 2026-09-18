"""merge venky1 deposit/sublet/policy heads with dev agreement engine heads

Revision ID: 45fc1881d4f3
Revises: 0028_sublet_payee_model, de4f1a3ff66a
Create Date: 2026-09-10 10:19:34.632075

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '45fc1881d4f3'
down_revision: Union[str, None] = ('0028_sublet_payee_model', 'de4f1a3ff66a')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
