"""Automatic refunds when a tenancy ends

host_refunds: rent the renter paid straight to the host for days after the
tenancy ended -- the host marks it paid, the renter confirms (or is deemed
to). refund_requests.requested_by_admin_id becomes nullable: the system now
raises a termination case's platform refund itself.

Revision ID: 0045_host_refunds
Revises: 0044_termination_party_decisions
Create Date: 2026-10-15 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0045_host_refunds"
down_revision: Union[str, None] = "0044_termination_party_decisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "host_refunds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("occupancy_id", sa.Integer(), sa.ForeignKey("occupancies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("termination_case_id", sa.Integer(), sa.ForeignKey("termination_cases.id", ondelete="SET NULL"), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False, server_default=""),
        sa.Column("due_by", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DUE"),
        sa.Column("payment_reference", sa.String(200), nullable=False, server_default=""),
        sa.Column("marked_paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deemed_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("not_received_note", sa.String(1000), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_host_refunds_occupancy_id", "host_refunds", ["occupancy_id"])
    op.create_index("ix_host_refunds_status", "host_refunds", ["status"])
    op.alter_column("refund_requests", "requested_by_admin_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.alter_column("refund_requests", "requested_by_admin_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index("ix_host_refunds_status", table_name="host_refunds")
    op.drop_index("ix_host_refunds_occupancy_id", table_name="host_refunds")
    op.drop_table("host_refunds")
