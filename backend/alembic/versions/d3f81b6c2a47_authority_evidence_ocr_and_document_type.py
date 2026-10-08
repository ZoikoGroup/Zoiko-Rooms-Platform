"""authority evidence ocr and document type match

Revision ID: d3f81b6c2a47
Revises: c7e2a91d4f30
Create Date: 2026-10-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3f81b6c2a47'
down_revision: Union[str, None] = 'c7e2a91d4f30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """ZR-AUTHORITY-002 Sections 4 / 11.1: uploads are read by text layer or
    OCR and matched against the country's accepted-document keywords; only
    the outcome is stored, never the extracted text."""
    op.add_column("authority_evidence", sa.Column("text_source", sa.String(length=20), nullable=False,
                                                  server_default=""))
    op.add_column("authority_evidence", sa.Column("ocr_confidence", sa.Float(), nullable=True))
    op.add_column("authority_evidence", sa.Column("type_matched", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("authority_evidence", "type_matched")
    op.drop_column("authority_evidence", "ocr_confidence")
    op.drop_column("authority_evidence", "text_source")
