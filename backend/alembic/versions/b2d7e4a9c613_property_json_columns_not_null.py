"""property json columns not null

a9c4e2f61b58 added these JSON columns as nullable, but the models declare
them NOT NULL (list / dict defaults). Rows from before that migration --
older country-pack versions and existing property verifications -- were
left NULL, which code iterating them would trip over. Backfill the empty
value, then enforce NOT NULL so the schema matches the models.

Revision ID: b2d7e4a9c613
Revises: a9c4e2f61b58
Create Date: 2026-10-06
"""
from alembic import op
import sqlalchemy as sa

revision = "b2d7e4a9c613"
down_revision = "a9c4e2f61b58"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("property_location_verifications", "pin_adjust_history", "[]"),
    ("property_regulatory_packs", "address_field_order", "[]"),
    ("property_regulatory_packs", "address_labels", "{}"),
)


def upgrade() -> None:
    for table, column, empty in _COLUMNS:
        op.execute(sa.text(f"UPDATE {table} SET {column} = CAST(:empty AS JSON) WHERE {column} IS NULL")
                   .bindparams(empty=empty))
        op.alter_column(table, column, existing_type=sa.JSON(), nullable=False)


def downgrade() -> None:
    for table, column, _ in _COLUMNS:
        op.alter_column(table, column, existing_type=sa.JSON(), nullable=True)
