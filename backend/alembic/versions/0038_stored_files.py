"""Keep every file in the database: the stored_files table

Generated PDFs, uploaded evidence and listing photos move from directories on
the server's disk into stored_files (see app/models/stored_file.py). The
table is created, then every file already on disk is copied in and its hash
checked (app/services/file_copy.py -- idempotent; copy_files_to_database.py
re-runs it). The files are left on disk.

Revision ID: 0038_stored_files
Revises: 0037_merge_sublet_risk_search
Create Date: 2026-10-09 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0038_stored_files"
down_revision: Union[str, None] = "0037_merge_sublet_risk_search"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stored_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("storage_ref", sa.String(length=255), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("encrypted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("category", "storage_ref", name="uq_stored_files_category_ref"),
    )

    from app.services.file_copy import copy_disk_files_to_database

    copy_disk_files_to_database(op.get_bind())


def downgrade() -> None:
    op.drop_table("stored_files")
