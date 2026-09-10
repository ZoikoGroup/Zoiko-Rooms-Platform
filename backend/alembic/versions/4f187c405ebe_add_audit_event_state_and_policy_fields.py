"""add audit event state and policy fields (ZR-ENG-CLR-001 Section 15: before/after
state, object version, and policy/ruleset version on every audit event)

Revision ID: 4f187c405ebe
Revises: d4e5f6a7b8c9
Create Date: 2026-09-09 09:19:03.064224

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4f187c405ebe'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('audit_events', sa.Column('before_state', sa.String(length=30), nullable=True))
    op.add_column('audit_events', sa.Column('after_state', sa.String(length=30), nullable=True))
    op.add_column('audit_events', sa.Column('object_version', sa.String(length=50), nullable=True))
    op.add_column('audit_events', sa.Column('policy_version', sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column('audit_events', 'policy_version')
    op.drop_column('audit_events', 'object_version')
    op.drop_column('audit_events', 'after_state')
    op.drop_column('audit_events', 'before_state')
