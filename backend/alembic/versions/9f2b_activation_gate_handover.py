"""add append-only occupancy handover evidence and activation decisions

Revision ID: 9f2b_activation_gate_handover
Revises: 4ce282ecddbb
Create Date: 2026-09-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "9f2b_activation_gate_handover"
down_revision: Union[str, None] = "4ce282ecddbb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "occupancy_handover_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("occupancy_id", sa.Integer(), sa.ForeignKey("occupancies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("actor_kind", sa.String(length=30), nullable=False),
        sa.Column("actor_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("user_accounts.id"), nullable=True),
        sa.Column("evidence_ref", sa.String(length=1024), nullable=False, server_default=sa.text("''")),
        sa.Column("notes", sa.String(length=2000), nullable=False, server_default=sa.text("''")),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=sa.text("''")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("occupancy_id", "event_type", name="uq_occupancy_handover_event_type"),
    )
    op.create_index("ix_occupancy_handover_events_occupancy_id", "occupancy_handover_events", ["occupancy_id"])
    op.create_table(
        "occupancy_activation_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("occupancy_id", sa.Integer(), sa.ForeignKey("occupancies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("decision_version", sa.Integer(), nullable=False),
        sa.Column("gate_rule_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("checks", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("trigger", sa.String(length=50), nullable=False),
        sa.Column("evaluating_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=sa.text("''")),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("occupancy_id", "decision_version", name="uq_occupancy_activation_decision_version"),
    )
    op.create_index("ix_occupancy_activation_decisions_occupancy_id", "occupancy_activation_decisions", ["occupancy_id"])


def downgrade() -> None:
    op.drop_index("ix_occupancy_activation_decisions_occupancy_id", table_name="occupancy_activation_decisions")
    op.drop_table("occupancy_activation_decisions")
    op.drop_index("ix_occupancy_handover_events_occupancy_id", table_name="occupancy_handover_events")
    op.drop_table("occupancy_handover_events")
