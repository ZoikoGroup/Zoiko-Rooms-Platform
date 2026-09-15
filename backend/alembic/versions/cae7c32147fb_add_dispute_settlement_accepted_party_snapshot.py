"""add dispute_settlements.accepted_party_snapshot

ZR-ENG-CLR-010 Section 23: freezes who accepted a settlement and the terms
they accepted (responder role/id, terms_hash, amount, currency, timestamp)
at the moment of acceptance, rather than leaving that reachable only via a
live join to the responding guest/party. See
app/crud/dispute_settlement.py:respond_settlement's ACCEPT branch.

Revision ID: cae7c32147fb
Revises: 20b054b68f14
Create Date: 2026-09-15 00:00:24.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cae7c32147fb'
down_revision: Union[str, None] = '20b054b68f14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dispute_settlements",
        sa.Column("accepted_party_snapshot", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("dispute_settlements", "accepted_party_snapshot")
