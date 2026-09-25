"""drop unused sumsub provider_applicant_id column

Revision ID: 1676e9d51d5e
Revises: 8f03d3264bde
Create Date: 2026-09-23 14:32:01.811239

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1676e9d51d5e'
down_revision: Union[str, None] = '8f03d3264bde'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('identity_verifications', 'provider_applicant_id')


def downgrade() -> None:
    op.add_column('identity_verifications', sa.Column('provider_applicant_id', sa.String(length=64), nullable=True))
