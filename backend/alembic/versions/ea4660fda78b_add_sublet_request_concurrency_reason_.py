"""add sublet request concurrency reason codes and lifecycle states

Revision ID: ea4660fda78b
Revises: a662e20a88e7
Create Date: 2026-09-21 14:34:55.969499

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ea4660fda78b'
down_revision: Union[str, None] = 'a662e20a88e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sublet_requests", sa.Column("idempotency_key", sa.String(length=255), nullable=True))
    op.create_unique_constraint("uq_sublet_requests_idempotency_key", "sublet_requests", ["idempotency_key"])
    op.add_column("sublet_requests", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("sublet_requests", sa.Column("decline_reason_code", sa.String(length=50), nullable=False, server_default=""))
    op.add_column("sublet_requests", sa.Column("superseded_by_sublet_request_id", sa.Integer(), sa.ForeignKey("sublet_requests.id"), nullable=True))
    op.add_column("sublet_requests", sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sublet_requests", sa.Column("cancelled_by_authority_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sublet_requests", sa.Column("cancelled_by_authority_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True))
    op.add_column("sublet_requests", sa.Column("cancelled_by_authority_reason", sa.String(length=2000), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("sublet_requests", "cancelled_by_authority_reason")
    op.drop_column("sublet_requests", "cancelled_by_authority_admin_id")
    op.drop_column("sublet_requests", "cancelled_by_authority_at")
    op.drop_column("sublet_requests", "expired_at")
    op.drop_column("sublet_requests", "superseded_by_sublet_request_id")
    op.drop_column("sublet_requests", "decline_reason_code")
    op.drop_column("sublet_requests", "version")
    op.drop_constraint("uq_sublet_requests_idempotency_key", "sublet_requests", type_="unique")
    op.drop_column("sublet_requests", "idempotency_key")
