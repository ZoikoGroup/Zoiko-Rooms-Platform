"""property verification geocode: map check of the property address

Revision ID: e6a1c9d3b7f4
Revises: d4f8b2c6e1a3
Create Date: 2026-10-01 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6a1c9d3b7f4'
down_revision: Union[str, None] = 'd4f8b2c6e1a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TEXT_COLUMNS = [
    ("geocode_provider", 20),
    ("geocode_query", 500),
    ("geocode_formatted_address", 500),
    ("geocode_precision", 20),
    ("geocode_country_code", 2),
    ("geocode_detail", 500),
]


def upgrade() -> None:
    op.add_column("property_verifications", sa.Column("geocode_status", sa.String(length=20), nullable=True))
    for name, length in _TEXT_COLUMNS:
        op.add_column(
            "property_verifications",
            sa.Column(name, sa.String(length=length), nullable=False, server_default=""),
        )
    op.add_column("property_verifications", sa.Column("geocode_latitude", sa.Float(), nullable=True))
    op.add_column("property_verifications", sa.Column("geocode_longitude", sa.Float(), nullable=True))
    op.add_column("property_verifications", sa.Column("geocoded_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for name in ("geocoded_at", "geocode_longitude", "geocode_latitude"):
        op.drop_column("property_verifications", name)
    for name, _ in reversed(_TEXT_COLUMNS):
        op.drop_column("property_verifications", name)
    op.drop_column("property_verifications", "geocode_status")
