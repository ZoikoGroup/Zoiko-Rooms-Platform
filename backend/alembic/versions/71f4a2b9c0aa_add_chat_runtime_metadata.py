"""add chat assistant runtime metadata

Revision ID: 71f4a2b9c0aa
Revises: 022bda4d123a
Create Date: 2026-09-02 05:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '71f4a2b9c0aa'
down_revision: Union[str, None] = '022bda4d123a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("meta_json", sa.Text(), nullable=False, server_default=sa.text("''")),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "meta_json")
