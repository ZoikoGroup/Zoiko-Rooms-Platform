"""Shared-room requests now go to the host first: any still waiting on the co-tenant without a host decision go back to the host

Revision ID: 0062_cotenant_host_first
Revises: 0061_shared_room_rent_link
Create Date: 2026-10-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "0062_cotenant_host_first"
down_revision: Union[str, None] = "0061_shared_room_rent_link"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE sublet_requests SET status = 'pending_admin_review', co_tenant_responded_at = NULL "
        "WHERE status = 'awaiting_co_tenant' AND arrangement_type = 'ADD_CO_TENANT' "
        "AND (admin_decision IS NULL OR admin_decision <> 'approved')"
    )


def downgrade() -> None:
    pass
