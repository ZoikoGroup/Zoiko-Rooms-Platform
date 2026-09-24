"""add provider applicant id to identity verifications

Revision ID: 0db3bd46e0a1
Revises: 27c38b20381c
Create Date: 2026-09-22 16:00:27.993786

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0db3bd46e0a1'
down_revision: Union[str, None] = '27c38b20381c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('identity_verifications', sa.Column('provider_applicant_id', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('identity_verifications', 'provider_applicant_id')
