"""add regex-extracted details to property verifications

Revision ID: a7c3e91b2d45
Revises: 50dbebdf1e01
Create Date: 2026-09-29 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3e91b2d45'
down_revision: Union[str, None] = '50dbebdf1e01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("property_verifications", sa.Column("extracted_owner_name", sa.String(length=200), nullable=True))
    op.add_column("property_verifications", sa.Column("extracted_address", sa.String(length=500), nullable=True))
    op.add_column("property_verifications", sa.Column("extracted_document_number", sa.String(length=64), nullable=True))
    op.add_column("property_verifications", sa.Column("name_matched", sa.Boolean(), nullable=True))
    op.add_column("property_verifications", sa.Column("address_matched", sa.Boolean(), nullable=True))
    op.add_column("property_verifications", sa.Column("document_sha256", sa.String(length=64), nullable=True))
    op.create_index(
        "ix_property_verifications_document_sha256", "property_verifications", ["document_sha256"],
    )


def downgrade() -> None:
    op.drop_index("ix_property_verifications_document_sha256", table_name="property_verifications")
    op.drop_column("property_verifications", "document_sha256")
    op.drop_column("property_verifications", "address_matched")
    op.drop_column("property_verifications", "name_matched")
    op.drop_column("property_verifications", "extracted_document_number")
    op.drop_column("property_verifications", "extracted_address")
    op.drop_column("property_verifications", "extracted_owner_name")
