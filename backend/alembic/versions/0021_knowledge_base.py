"""add knowledge base tables (phase 5 KB+RAG)

Revision ID: 0021_knowledge_base
Revises: 0020_ai_handoff
Create Date: 2026-09-03 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0021_knowledge_base"
down_revision: Union[str, None] = "0020_ai_handoff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kb_releases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("market", sa.String(length=20), nullable=False, server_default="GLOBAL"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="DRAFT"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "kb_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False, server_default="KNOWLEDGE"),
        sa.Column("author", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("owner", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("market", sa.String(length=20), nullable=False, server_default="GLOBAL"),
        sa.Column("jurisdiction", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("domain", sa.String(length=40), nullable=False, server_default="general"),
        sa.Column("access_class", sa.String(length=20), nullable=False, server_default="K0_PUBLIC"),
        sa.Column("trust_tier", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="DRAFT"),
        sa.Column("release_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["release_id"], ["kb_releases.id"]),
    )
    op.create_index(op.f("ix_kb_documents_slug"), "kb_documents", ["slug"], unique=True)
    op.create_index(op.f("ix_kb_documents_domain"), "kb_documents", ["domain"])
    op.create_index(op.f("ix_kb_documents_access_class"), "kb_documents", ["access_class"])
    op.create_index(op.f("ix_kb_documents_status"), "kb_documents", ["status"])
    op.create_index(op.f("ix_kb_documents_release_id"), "kb_documents", ["release_id"])

    op.create_table(
        "kb_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("section", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_search", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["kb_documents.id"], ondelete="CASCADE"),
    )
    op.create_index(op.f("ix_kb_chunks_document_id"), "kb_chunks", ["document_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_kb_chunks_document_id"), table_name="kb_chunks")
    op.drop_table("kb_chunks")
    op.drop_index(op.f("ix_kb_documents_release_id"), table_name="kb_documents")
    op.drop_index(op.f("ix_kb_documents_status"), table_name="kb_documents")
    op.drop_index(op.f("ix_kb_documents_access_class"), table_name="kb_documents")
    op.drop_index(op.f("ix_kb_documents_domain"), table_name="kb_documents")
    op.drop_index(op.f("ix_kb_documents_slug"), table_name="kb_documents")
    op.drop_table("kb_documents")
    op.drop_table("kb_releases")
