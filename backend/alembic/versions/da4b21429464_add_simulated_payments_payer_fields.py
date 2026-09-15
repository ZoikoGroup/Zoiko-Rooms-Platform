"""add simulated_payments payer fields

Revision ID: da4b21429464
Revises: aa95deb2ca4a
Create Date: 2026-09-10 18:37:25.677488

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'da4b21429464'
down_revision: Union[str, None] = 'aa95deb2ca4a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-005 AC-03/Section 4.1: payer may differ from the occupant
    # (guest_id) -- payer_guest_id when the payer is itself a registered guest,
    # payer_name/email/phone when they aren't.
    op.add_column('simulated_payments', sa.Column('payer_guest_id', sa.String(), nullable=True))
    op.add_column('simulated_payments', sa.Column('payer_name', sa.String(length=255), nullable=True))
    op.add_column('simulated_payments', sa.Column('payer_email', sa.String(length=255), nullable=True))
    op.add_column('simulated_payments', sa.Column('payer_phone', sa.String(length=50), nullable=True))
    op.create_index('ix_simulated_payments_payer_guest_id', 'simulated_payments', ['payer_guest_id'])
    op.create_foreign_key(
        'fk_simulated_payments_payer_guest_id', 'simulated_payments', 'guests',
        ['payer_guest_id'], ['id'], ondelete='SET NULL',
    )

    # Backfill: every payment that existed before this migration was, by
    # definition, paid by the occupant themselves (no other payer concept
    # existed) -- make that explicit rather than leaving historical rows with
    # payer_guest_id null (which would otherwise misleadingly read as "unknown
    # payer" rather than "payer == occupant").
    op.execute("UPDATE simulated_payments SET payer_guest_id = guest_id WHERE payer_guest_id IS NULL")


def downgrade() -> None:
    op.drop_constraint('fk_simulated_payments_payer_guest_id', 'simulated_payments', type_='foreignkey')
    op.drop_index('ix_simulated_payments_payer_guest_id', table_name='simulated_payments')
    op.drop_column('simulated_payments', 'payer_phone')
    op.drop_column('simulated_payments', 'payer_email')
    op.drop_column('simulated_payments', 'payer_name')
    op.drop_column('simulated_payments', 'payer_guest_id')
