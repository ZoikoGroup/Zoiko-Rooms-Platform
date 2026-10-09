"""Merge the sublet occupant-risk migration with the ZR-AI-SEARCH-001 chain

Both branch from b2d7e4a9c613: the sublet occupant-overlap check
(c4e8a1d2f7b3, sublet_requests columns) and the search protocol chain
(0035 -> 0036, external search tables). They touch different tables. This
revision joins them so `alembic upgrade head` has a single head again. It
makes no schema changes.

Revision ID: 0037_merge_sublet_risk_search
Revises: 0036_search_protocol_completion, c4e8a1d2f7b3
Create Date: 2026-10-09 00:00:00.000000

"""
from typing import Sequence, Union


revision: str = "0037_merge_sublet_risk_search"
down_revision: Union[str, Sequence[str], None] = ("0036_search_protocol_completion", "c4e8a1d2f7b3")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
