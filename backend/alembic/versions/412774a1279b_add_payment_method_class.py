"""add payment method class

Revision ID: 412774a1279b
Revises: 3a7ec40f000a
Create Date: 2026-09-16 19:00:28.414357

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '412774a1279b'
down_revision: Union[str, None] = '3a7ec40f000a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: autogenerate also detected unrelated pre-existing drift (an index
    # on agreement_form_templates/evidence_artifacts/property_compliance_
    # credentials, NOT NULL changes on listing_approvals/listing_versions/
    # room_holds) that has nothing to do with this column and predates this
    # change -- deliberately excluded from this migration so it isn't
    # applied without its own separate review.
    #
    # server_default backfills every existing row as EXTERNAL (the same
    # value the ORM model defaults new rows to) -- this table already has
    # real rows in dev, so a bare NOT NULL add with no default would fail.
    op.add_column(
        'simulated_payments',
        sa.Column('method_class', sa.String(length=20), nullable=False, server_default='EXTERNAL'),
    )


def downgrade() -> None:
    op.drop_column('simulated_payments', 'method_class')
