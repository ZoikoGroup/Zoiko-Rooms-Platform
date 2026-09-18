"""add host decision to application_decisions

ZR-ENG-CLR-011 Section 10/AC-06: the docs' Host Notification Center lists
"Applications to review" as a Host action item, and require the Host (not
only a Super Admin) to be able to decide a renter's application on their own
self-service (party-owned) listing. application_decisions previously
required decided_by_admin_id (NOT NULL), so a UserAccount host -- who has no
admin_users row -- could never be recorded as the decider. Exactly one of
decided_by_admin_id / decided_by_user_id is set per row (enforced at the
crud layer, same convention as verification_credentials' two nullable
source-record FKs -- no DB-level CHECK constraint).

Revision ID: 78beaf55ea4c
Revises: 21432d2045c7
Create Date: 2026-09-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '78beaf55ea4c'
down_revision: Union[str, None] = '21432d2045c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("application_decisions", "decided_by_admin_id", existing_type=sa.Integer(), nullable=True)
    op.add_column(
        "application_decisions",
        sa.Column("decided_by_user_id", sa.Integer(), sa.ForeignKey("user_accounts.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("application_decisions", "decided_by_user_id")
    op.alter_column("application_decisions", "decided_by_admin_id", existing_type=sa.Integer(), nullable=False)
