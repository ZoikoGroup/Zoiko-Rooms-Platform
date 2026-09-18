"""add host recovery psp reversal id

Revision ID: d55c63c74896
Revises: 121d9ef1d1b3
Create Date: 2026-09-16 17:25:41.475941

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd55c63c74896'
down_revision: Union[str, None] = '121d9ef1d1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: autogenerate also detected unrelated pre-existing drift (an index
    # on agreement_form_templates/evidence_artifacts/property_compliance_
    # credentials, NOT NULL changes on listing_approvals/listing_versions/
    # room_holds) that has nothing to do with this column and predates this
    # change -- deliberately excluded from this migration so it isn't
    # applied without its own separate review.
    op.add_column('host_recoveries', sa.Column('psp_reversal_id', sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column('host_recoveries', 'psp_reversal_id')
