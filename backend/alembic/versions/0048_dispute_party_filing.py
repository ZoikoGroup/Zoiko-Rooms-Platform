"""Disputes: the renter or host can record taking a claim to an outside body

dispute_external_proceedings.filed_by_admin_id becomes nullable -- a host<->
renter claim is filed with the outside body (tribunal, deposit scheme) by the
parties themselves (crud/dispute_party_flow.py).

Revision ID: 0048_dispute_party_filing
Revises: 0047_instruction_cooling_off
Create Date: 2026-10-15 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0048_dispute_party_filing"
down_revision: Union[str, None] = "0047_instruction_cooling_off"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("dispute_external_proceedings", "filed_by_admin_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.alter_column("dispute_external_proceedings", "filed_by_admin_id", existing_type=sa.Integer(), nullable=False)
