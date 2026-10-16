"""Shared-room co-tenancy (ADD_CO_TENANT)

The named co-tenant now accepts or declines before the Host decides, and an
approved shared room can later be ended. Statuses live in a plain string
column, so only the response/end timestamps and who ended it are new.

Revision ID: 0052_shared_room_co_tenancy
Revises: 0051_global_payment_methods
Create Date: 2026-10-16 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0052_shared_room_co_tenancy"
down_revision: Union[str, None] = "0051_global_payment_methods"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sublet_requests", sa.Column("co_tenant_responded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sublet_requests", sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sublet_requests", sa.Column("ended_by_user_id", sa.Integer(), sa.ForeignKey("user_accounts.id"), nullable=True))
    op.add_column("sublet_requests", sa.Column("end_reason", sa.String(length=2000), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("sublet_requests", "end_reason")
    op.drop_column("sublet_requests", "ended_by_user_id")
    op.drop_column("sublet_requests", "ended_at")
    op.drop_column("sublet_requests", "co_tenant_responded_at")
