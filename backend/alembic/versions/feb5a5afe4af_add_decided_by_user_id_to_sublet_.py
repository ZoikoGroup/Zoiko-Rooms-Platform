"""add decided_by_user_id to sublet_requests

Revision ID: feb5a5afe4af
Revises: 294dc9f9f823
Create Date: 2026-09-19 10:04:28.186394

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'feb5a5afe4af'
down_revision: Union[str, None] = '294dc9f9f823'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sublet_requests", sa.Column("decided_by_user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_sublet_requests_decided_by_user_id_user_accounts",
        "sublet_requests", "user_accounts", ["decided_by_user_id"], ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_sublet_requests_decided_by_user_id_user_accounts", "sublet_requests", type_="foreignkey")
    op.drop_column("sublet_requests", "decided_by_user_id")
