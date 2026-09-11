"""merge anil section6 heads with dev activation-gate and booking-change heads

Revision ID: 2be6a6a9dfea
Revises: 4bc310467149, 9f2b_activation_gate_handover, b0e804f385a8
Create Date: 2026-09-11 18:33:43.488698

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2be6a6a9dfea'
down_revision: Union[str, None] = ('4bc310467149', '9f2b_activation_gate_handover', 'b0e804f385a8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
