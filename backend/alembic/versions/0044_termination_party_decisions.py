"""Termination decided between the parties; notice proof is self-service

termination_cases gains the response-round fields (proposer, round,
deadline, whether silence counts as acceptance, any outside order) and the
self-service notice fields; termination_case_responses holds each party's
answer. Cases still sitting in PENDING_REVIEW (the old super-admin queue)
move into the new flow: an end date 28 days out is proposed on the opening
side's behalf and everyone else has 14 days to accept, counter or contest.

Revision ID: 0044_termination_party_decisions
Revises: 0043_account_settings
Create Date: 2026-10-15 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0044_termination_party_decisions"
down_revision: Union[str, None] = "0043_account_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("termination_cases", sa.Column("proposer", sa.String(20), nullable=True))
    op.add_column("termination_cases", sa.Column("response_round", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("termination_cases", sa.Column("response_due_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("termination_cases", sa.Column("deemed_on_silence", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("termination_cases", sa.Column("external_outcome", sa.String(30), nullable=False, server_default=""))
    op.add_column("termination_cases", sa.Column("external_order_ref", sa.String(500), nullable=False, server_default=""))
    op.add_column(
        "termination_cases",
        sa.Column("notice_service_recorded_by_user_id", sa.Integer(), sa.ForeignKey("user_accounts.id"), nullable=True),
    )
    op.add_column("termination_cases", sa.Column("notice_acknowledged_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "termination_case_responses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("termination_case_id", sa.Integer(), sa.ForeignKey("termination_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("response_round", sa.Integer(), nullable=False),
        sa.Column("responder", sa.String(20), nullable=False),
        sa.Column("responder_user_id", sa.Integer(), sa.ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("response", sa.String(10), nullable=False),
        sa.Column("proposed_date", sa.Date(), nullable=True),
        sa.Column("note", sa.String(2000), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_termination_case_responses_termination_case_id", "termination_case_responses", ["termination_case_id"])

    # The old manual-review queue joins the party flow.
    op.execute("""
        UPDATE termination_cases
        SET status = 'AWAITING_RESPONSE',
            proposer = COALESCE(initiator_guest_id, 'HOST'),
            earliest_effective_date = CURRENT_DATE + 28,
            response_due_at = CURRENT_TIMESTAMP + INTERVAL '14 days',
            deemed_on_silence = (cause_code <> 'MUTUAL_SURRENDER')
        WHERE status = 'PENDING_REVIEW'
    """)
    # Every notice's portal copy counts as served at submission.
    op.execute("UPDATE termination_cases SET notice_served_at = notice_created_at WHERE notice_served_at IS NULL")


def downgrade() -> None:
    op.execute("""
        UPDATE termination_cases SET status = 'PENDING_REVIEW'
        WHERE status IN ('AWAITING_RESPONSE', 'CONTESTED')
    """)
    op.drop_index("ix_termination_case_responses_termination_case_id", table_name="termination_case_responses")
    op.drop_table("termination_case_responses")
    for column in (
        "notice_acknowledged_at", "notice_service_recorded_by_user_id", "external_order_ref", "external_outcome",
        "deemed_on_silence", "response_due_at", "response_round", "proposer",
    ):
        op.drop_column("termination_cases", column)
