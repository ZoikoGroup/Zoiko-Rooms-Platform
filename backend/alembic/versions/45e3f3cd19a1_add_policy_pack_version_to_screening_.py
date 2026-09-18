"""add policy pack version to screening checks and property compliance credentials

Revision ID: 45e3f3cd19a1
Revises: e9ec683a098a
Create Date: 2026-09-15 15:37:14.143746

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '45e3f3cd19a1'
down_revision: Union[str, None] = 'e9ec683a098a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("screening_checks", sa.Column("policy_pack_version", sa.Integer(), nullable=True))
    op.add_column("property_compliance_credentials", sa.Column("policy_pack_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("property_compliance_credentials", "policy_pack_version")
    op.drop_column("screening_checks", "policy_pack_version")
