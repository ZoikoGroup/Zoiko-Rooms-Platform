"""add named occupant and offer occupant risk tier

Revision ID: 7a5b929a868c
Revises: 0933376a65c2
Create Date: 2026-09-09 10:17:11.146081

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a5b929a868c'
down_revision: Union[str, None] = '0933376a65c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('applications', sa.Column('named_occupant_guest_id', sa.String(length=20), nullable=True))
    op.create_foreign_key(
        'fk_applications_named_occupant_guest_id', 'applications', 'guests',
        ['named_occupant_guest_id'], ['id'], ondelete='SET NULL',
    )
    op.add_column('offers', sa.Column('occupant_risk_tier', sa.String(length=20), nullable=False, server_default='NONE'))
    op.add_column('offers', sa.Column('occupant_risk_reason', sa.String(length=500), nullable=False, server_default=''))
    op.alter_column('offers', 'occupant_risk_tier', server_default=None)
    op.alter_column('offers', 'occupant_risk_reason', server_default=None)


def downgrade() -> None:
    op.drop_column('offers', 'occupant_risk_reason')
    op.drop_column('offers', 'occupant_risk_tier')
    op.drop_constraint('fk_applications_named_occupant_guest_id', 'applications', type_='foreignkey')
    op.drop_column('applications', 'named_occupant_guest_id')
