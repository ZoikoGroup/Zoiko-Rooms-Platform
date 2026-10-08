"""Merge the dev branch migration chain with the ZR-AI-SEARCH-001 chain

Both chains branch from 4d26afb5d759: dev's identity/authority/property
verification migrations (head b2d7e4a9c613) and the external search protocol
migrations (0029 -> 0034). This revision joins them so `alembic upgrade head`
has a single head. It makes no schema changes.

Revision ID: 0035_merge_dev_search
Revises: 0034_partner_feed_settings, b2d7e4a9c613
Create Date: 2026-10-08 00:00:00.000000

"""
from typing import Sequence, Union


revision: str = "0035_merge_dev_search"
down_revision: Union[str, Sequence[str], None] = ("0034_partner_feed_settings", "b2d7e4a9c613")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
