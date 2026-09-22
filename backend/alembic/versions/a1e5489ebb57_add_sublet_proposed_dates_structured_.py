"""add sublet proposed dates structured conditions and jurisdiction config

Revision ID: a1e5489ebb57
Revises: ea4660fda78b
Create Date: 2026-09-21 15:15:16.177748

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1e5489ebb57'
down_revision: Union[str, None] = 'ea4660fda78b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sublet_requests", sa.Column("proposed_start_date", sa.Date(), nullable=True))
    op.add_column("sublet_requests", sa.Column("proposed_end_date", sa.Date(), nullable=True))
    op.add_column("sublet_requests", sa.Column("info_requested_document_types", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("sublet_requests", sa.Column("info_request_due_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sublet_requests", sa.Column("approval_condition_list", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("sublet_requests", sa.Column("approved_with_authority_confirmation", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.add_column("market_policy_packs", sa.Column("sublet_ui_term", sa.String(length=30), nullable=False, server_default="sublet"))
    op.add_column("market_policy_packs", sa.Column("sublet_max_duration_months", sa.Integer(), nullable=True))
    op.add_column("market_policy_packs", sa.Column("sublet_required_fields", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("market_policy_packs", sa.Column("sublet_required_documents", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("market_policy_packs", sa.Column("sublet_signature_mode", sa.String(length=30), nullable=False, server_default="NONE"))
    op.add_column("market_policy_packs", sa.Column("sublet_notice_requirements", sa.String(length=500), nullable=False, server_default=""))
    op.add_column("market_policy_packs", sa.Column("sublet_retention_class", sa.String(length=30), nullable=False, server_default="STANDARD"))
    op.add_column("market_policy_packs", sa.Column("sublet_additional_gates", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("market_policy_packs", "sublet_additional_gates")
    op.drop_column("market_policy_packs", "sublet_retention_class")
    op.drop_column("market_policy_packs", "sublet_notice_requirements")
    op.drop_column("market_policy_packs", "sublet_signature_mode")
    op.drop_column("market_policy_packs", "sublet_required_documents")
    op.drop_column("market_policy_packs", "sublet_required_fields")
    op.drop_column("market_policy_packs", "sublet_max_duration_months")
    op.drop_column("market_policy_packs", "sublet_ui_term")

    op.drop_column("sublet_requests", "approved_with_authority_confirmation")
    op.drop_column("sublet_requests", "approval_condition_list")
    op.drop_column("sublet_requests", "info_request_due_at")
    op.drop_column("sublet_requests", "info_requested_document_types")
    op.drop_column("sublet_requests", "proposed_end_date")
    op.drop_column("sublet_requests", "proposed_start_date")
