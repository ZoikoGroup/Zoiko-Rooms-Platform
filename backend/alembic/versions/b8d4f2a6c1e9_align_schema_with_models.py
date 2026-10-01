"""align schema with models: form template index, NOT NULL on defaulted columns

Revision ID: b8d4f2a6c1e9
Revises: a7c3e91b2d45
Create Date: 2026-09-29 18:00:00.000000

The models declare these columns NOT NULL (Mapped[str]/[list]/[dict] with a
Python-side default) but their original migrations created them nullable.
Any NULLs are backfilled with the model default before tightening.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8d4f2a6c1e9'
down_revision: Union[str, None] = 'a7c3e91b2d45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, column, existing type, SQL backfill value for NULLs)
_NOT_NULL_COLUMNS = [
    ("listing_approvals", "market_profile", sa.String(length=50), "''"),
    ("listing_approvals", "decision_reason_code", sa.String(length=100), "''"),
    ("listing_approvals", "reason_note", sa.String(length=2000), "''"),
    ("listing_approvals", "reviewer_authority_scope", sa.String(length=30), "''"),
    ("listing_approvals", "evidence_refs", sa.JSON(), "'[]'"),
    ("listing_approvals", "policy_version", sa.String(length=20), "'1.0'"),
    ("listing_versions", "material_change_flags", sa.JSON(), "'{}'"),
    ("rental_payment_returns", "created_at", sa.DateTime(timezone=True), "now()"),
    ("room_holds", "release_reason", sa.String(length=200), "''"),
]


def upgrade() -> None:
    op.create_index(
        "ix_agreement_form_templates_jurisdiction_scope", "agreement_form_templates", ["jurisdiction_scope"],
    )
    for table, column, type_, backfill in _NOT_NULL_COLUMNS:
        op.execute(f"UPDATE {table} SET {column} = {backfill} WHERE {column} IS NULL")
        op.alter_column(table, column, existing_type=type_, nullable=False)


def downgrade() -> None:
    for table, column, type_, _ in reversed(_NOT_NULL_COLUMNS):
        op.alter_column(table, column, existing_type=type_, nullable=True)
    op.drop_index("ix_agreement_form_templates_jurisdiction_scope", table_name="agreement_form_templates")
