"""add more-info-request round trip fields to sublet_requests

Revision ID: 4e3c72779101
Revises: feb5a5afe4af
Create Date: 2026-09-19 10:37:12.179193

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4e3c72779101'
down_revision: Union[str, None] = 'feb5a5afe4af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sublet_requests", sa.Column("info_request_note", sa.String(length=2000), nullable=False, server_default=""))
    op.add_column("sublet_requests", sa.Column("info_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sublet_requests", sa.Column("info_response_note", sa.String(length=2000), nullable=False, server_default=""))
    op.add_column("sublet_requests", sa.Column("info_responded_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("sublet_requests", "info_request_note", server_default=None)
    op.alter_column("sublet_requests", "info_response_note", server_default=None)


def downgrade() -> None:
    op.drop_column("sublet_requests", "info_responded_at")
    op.drop_column("sublet_requests", "info_response_note")
    op.drop_column("sublet_requests", "info_requested_at")
    op.drop_column("sublet_requests", "info_request_note")
