"""Record when the renter reviewed the verified payment recipient

The renter's move-in checklist asks them to review who they'll be paying
before the first payment. That step was only held in the browser, so it
reset on every reload; occupancies.payment_recipient_reviewed_at stores it.

Revision ID: 0039_recipient_reviewed
Revises: 0038_stored_files
Create Date: 2026-10-14 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0039_recipient_reviewed"
down_revision: Union[str, None] = "0038_stored_files"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("occupancies", sa.Column("payment_recipient_reviewed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("occupancies", "payment_recipient_reviewed_at")
