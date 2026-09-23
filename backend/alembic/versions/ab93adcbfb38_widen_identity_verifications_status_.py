"""widen identity_verifications status column to fit additional_evidence_required

Revision ID: ab93adcbfb38
Revises: f6c6202cb9fd
Create Date: 2026-09-23 15:37:08.294257

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ab93adcbfb38'
down_revision: Union[str, None] = 'f6c6202cb9fd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('identity_verifications', 'status', type_=sa.String(length=40), existing_type=sa.String(length=20))


def downgrade() -> None:
    op.alter_column('identity_verifications', 'status', type_=sa.String(length=20), existing_type=sa.String(length=40))
