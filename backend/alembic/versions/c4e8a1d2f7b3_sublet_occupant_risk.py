"""sublet occupant risk

Sublet approval now runs the same occupant-overlap check as an ordinary
offer acceptance (services/overlap.py); record its outcome on the request.

Revision ID: c4e8a1d2f7b3
Revises: b2d7e4a9c613
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa

revision = "c4e8a1d2f7b3"
down_revision = "b2d7e4a9c613"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sublet_requests", sa.Column("occupant_risk_tier", sa.String(10), nullable=False, server_default="NONE"))
    op.add_column("sublet_requests", sa.Column("occupant_risk_reason", sa.String(500), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("sublet_requests", "occupant_risk_reason")
    op.drop_column("sublet_requests", "occupant_risk_tier")
