"""ZR-AI-SEARCH-001 protocol hardening

Section 5.1 freshness + Section 11.1 verification-extension domains for the
external search protocol:

* listings.availability_confirmed_at — host/party reconfirmation timestamp a
  fresh inventory ranking is computed from (stale-unknown records are
  downgraded, never presented as "confirmed available").
* external_opportunities.payment_receipt_authority_verified(_at) — separate
  authority-to-receive-payment verification domain (payment instructions only
  unlock when this is set; never implied by provider acceptance).
* external_opportunities.sublet_permission_verified(_at) +
  sublet_evidence_encrypted — required landlord/agent permission evidence for
  sublet records; stored encrypted at rest.

Revision ID: 0032_search_protocol_hardening
Revises: 0031_external_relay_messaging
Create Date: 2026-10-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0032_search_protocol_hardening"
down_revision: Union[str, None] = "0031_external_relay_messaging"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "listings",
        sa.Column("availability_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column(
        "external_opportunities",
        sa.Column(
            "payment_receipt_authority_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "external_opportunities",
        sa.Column("payment_receipt_authority_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "external_opportunities",
        sa.Column(
            "sublet_permission_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "external_opportunities",
        sa.Column("sublet_permission_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "external_opportunities",
        sa.Column("sublet_evidence_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("external_opportunities", "sublet_evidence_encrypted")
    op.drop_column("external_opportunities", "sublet_permission_verified_at")
    op.drop_column("external_opportunities", "sublet_permission_verified")
    op.drop_column("external_opportunities", "payment_receipt_authority_verified_at")
    op.drop_column("external_opportunities", "payment_receipt_authority_verified")
    op.drop_column("listings", "availability_confirmed_at")