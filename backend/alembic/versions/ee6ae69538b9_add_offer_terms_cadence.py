"""add offer_terms cadence

Revision ID: ee6ae69538b9
Revises: da4b21429464
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ee6ae69538b9'
down_revision: Union[str, None] = 'da4b21429464'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-005 AC-06: the cadence a host picks at offer-terms time,
    # carried forward into the PaymentSchedule create_agreement builds
    # (payment_schedules.cadence already exists -- see bd93a59c37d6). Every
    # pre-existing row was implicitly monthly (the only cadence this
    # codebase supported before AC-06), hence the server_default backfill.
    op.add_column('offer_terms', sa.Column('cadence', sa.String(length=20), nullable=False, server_default='MONTHLY'))


def downgrade() -> None:
    op.drop_column('offer_terms', 'cadence')
