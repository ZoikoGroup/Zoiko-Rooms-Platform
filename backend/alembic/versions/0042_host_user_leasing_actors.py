"""Host-user actors on amendments, change requests and termination cases

The self-service host can now drive the provider side of an amendment,
decide a renter's change request, open a termination case and answer a
renter's surrender proposal -- previously admin-only. Each table gets a
user_accounts column alongside its admin one; agreement_amendments'
requested_by_admin_id becomes nullable since a host-requested amendment has
no admin.

Revision ID: 0042_host_user_leasing_actors
Revises: 0041_support_tickets
Create Date: 2026-10-15 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0042_host_user_leasing_actors"
down_revision: Union[str, None] = "0041_support_tickets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("agreement_amendments", "requested_by_admin_id", existing_type=sa.Integer(), nullable=True)
    op.add_column(
        "agreement_amendments",
        sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("user_accounts.id"), nullable=True),
    )
    op.add_column(
        "booking_change_requests",
        sa.Column("decided_by_user_id", sa.Integer(), sa.ForeignKey("user_accounts.id"), nullable=True),
    )
    op.add_column(
        "termination_cases",
        sa.Column("initiator_host_user_id", sa.Integer(), sa.ForeignKey("user_accounts.id"), nullable=True),
    )
    op.add_column(
        "termination_decisions",
        sa.Column("approved_by_user_id", sa.Integer(), sa.ForeignKey("user_accounts.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("termination_decisions", "approved_by_user_id")
    op.drop_column("termination_cases", "initiator_host_user_id")
    op.drop_column("booking_change_requests", "decided_by_user_id")
    op.drop_column("agreement_amendments", "requested_by_user_id")
    op.alter_column("agreement_amendments", "requested_by_admin_id", existing_type=sa.Integer(), nullable=False)
