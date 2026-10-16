"""Host authority verification per ZR-HAV-001

Jurisdiction rules on authority packs (landlord registration, agent licence,
HMO occupancy threshold), the versioned Host Declaration on each case, and
the "never ownership proof" document recognised on an upload. Nation / state
packs (GB-ENG, GB-WLS, GB-SCT, GB-NIR, US-NY, US-CA ...) are seeded by the
application on first use.

Revision ID: 0053_host_authority_hav
Revises: 0052_shared_room_co_tenancy
Create Date: 2026-10-16 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0053_host_authority_hav"
down_revision: Union[str, None] = "0052_shared_room_co_tenancy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("authority_regulatory_packs", sa.Column("rules", sa.JSON(), nullable=True))
    op.add_column("authority_verifications", sa.Column("declaration_version", sa.String(length=40), nullable=False, server_default=""))
    op.add_column("authority_verifications", sa.Column("declaration_items", sa.JSON(), nullable=True))
    op.add_column("authority_verifications", sa.Column("declaration_hash", sa.String(length=64), nullable=False, server_default=""))
    op.add_column("authority_evidence", sa.Column("non_proof_document", sa.String(length=120), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("authority_evidence", "non_proof_document")
    op.drop_column("authority_verifications", "declaration_hash")
    op.drop_column("authority_verifications", "declaration_items")
    op.drop_column("authority_verifications", "declaration_version")
    op.drop_column("authority_regulatory_packs", "rules")
