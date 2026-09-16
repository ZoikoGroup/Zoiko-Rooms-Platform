"""add payment provider dispatch and callback ingestion tables

Revision ID: eaadce89ec3f
Revises: d771b91b6e72
Create Date: 2026-09-16 15:47:02.973862

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'eaadce89ec3f'
down_revision: Union[str, None] = 'd771b91b6e72'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payment_provider_status",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("healthy", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "processor_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("simulated_payments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_transaction_id", sa.String(100), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("declared_allocations", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("dispatch_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_processor_transactions_payment_id", "processor_transactions", ["payment_id"])
    op.create_table(
        "payment_provider_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider_event_id", sa.String(100), nullable=False, unique=True),
        sa.Column("processor_transaction_id", sa.Integer(), sa.ForeignKey("processor_transactions.id"), nullable=True),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("payment_provider_events")
    op.drop_index("ix_processor_transactions_payment_id", table_name="processor_transactions")
    op.drop_table("processor_transactions")
    op.drop_table("payment_provider_status")
