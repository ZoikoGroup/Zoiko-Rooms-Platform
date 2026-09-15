"""add audit_events previous_hash/new_hash

ZR-ENG-CLR-010 Section 23/28: a tamper-evident hash chain over the
single, global, append-only audit_events sequence -- see
app/crud/audit.py:log_audit_event's own docstring. Additive and
platform-wide (audit_events is shared by every domain, not just
disputes) but low-risk: two new nullable columns, populated going
forward only; no existing reader of AuditEvent is affected (grep found
no schema/route exposing this model at all).

Revision ID: c47cf8fd3652
Revises: 3c55e315945c
Create Date: 2026-09-15 00:00:25.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c47cf8fd3652'
down_revision: Union[str, None] = '3c55e315945c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("previous_hash", sa.String(length=64), nullable=True))
    op.add_column("audit_events", sa.Column("new_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_events", "new_hash")
    op.drop_column("audit_events", "previous_hash")
