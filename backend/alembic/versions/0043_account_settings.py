"""Account settings: user_preferences, email_change_requests, user columns

user_preferences holds each user's display/workspace/privacy choices;
email_change_requests the pending sign-in email changes (token hash only).
user_accounts gains sessions_revoked_at ("sign out of other sessions"),
the profile photo reference, and self-service closure fields.

Revision ID: 0043_account_settings
Revises: 0042_host_user_leasing_actors
Create Date: 2026-10-15 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0043_account_settings"
down_revision: Union[str, None] = "0042_host_user_leasing_actors"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user_accounts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "email_change_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("new_email", sa.String(255), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_email_change_requests_user_id", "email_change_requests", ["user_id"])
    op.add_column("user_accounts", sa.Column("sessions_revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user_accounts", sa.Column("avatar_ref", sa.String(255), nullable=True))
    op.add_column("user_accounts", sa.Column("avatar_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user_accounts", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user_accounts", sa.Column("closure_reason", sa.String(500), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("user_accounts", "closure_reason")
    op.drop_column("user_accounts", "closed_at")
    op.drop_column("user_accounts", "avatar_updated_at")
    op.drop_column("user_accounts", "avatar_ref")
    op.drop_column("user_accounts", "sessions_revoked_at")
    op.drop_index("ix_email_change_requests_user_id", table_name="email_change_requests")
    op.drop_table("email_change_requests")
    op.drop_table("user_preferences")
