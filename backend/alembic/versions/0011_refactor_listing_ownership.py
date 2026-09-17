"""refactor listing ownership to use party_id

Revision ID: 0011_refactor_listing_ownership
Revises: 0010_link_user_to_party
Create Date: 2026-08-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_refactor_listing_ownership"
down_revision: Union[str, None] = "0010_link_user_to_party"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add party_id column to listings (nullable initially for backward compatibility)
    conn = op.get_context().connection
    inspector = sa.inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("listings")}
    if "party_id" not in existing_columns:
        op.add_column("listings", sa.Column("party_id", sa.Integer(), sa.ForeignKey("parties.id", ondelete="CASCADE"), nullable=True))

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("listings")}
    if "ix_listings_party_id" not in existing_indexes:
        op.create_index("ix_listings_party_id", "listings", ["party_id"])


def downgrade() -> None:
    op.drop_index("ix_listings_party_id")
    op.drop_column("listings", "party_id")
