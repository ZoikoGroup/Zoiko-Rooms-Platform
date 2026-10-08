"""ZR-AI-SEARCH-001 partner feed pull settings (Section 6.1 Tier A)

Adds the per-source settings the partner feed sync job needs:

* feed_url -- where the partner's feed is pulled from (checked by the
  broker's rights + SSRF gate before every request);
* feed_format -- JSON | CSV | BLM (UK portal feed) | RESO (US MLS Web API);
* feed_credential_env -- NAME of the environment variable holding the
  partner's token; the secret itself is never stored in the database;
* site_domain -- for PUBLIC_FETCH sources, the approved website that web
  search results may come from.

Revision ID: 0034_partner_feed_settings
Revises: 0033_licensed_listing_sources
Create Date: 2026-10-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0034_partner_feed_settings"
down_revision: Union[str, None] = "0033_licensed_listing_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("source_right_registry", sa.Column("feed_url", sa.String(1000), nullable=True))
    op.add_column("source_right_registry", sa.Column("feed_format", sa.String(10), nullable=True))
    op.add_column("source_right_registry", sa.Column("feed_credential_env", sa.String(100), nullable=True))
    op.add_column("source_right_registry", sa.Column("site_domain", sa.String(253), nullable=True))
    op.create_index("ix_source_right_registry_site_domain", "source_right_registry", ["site_domain"])
    op.create_check_constraint(
        "ck_srr_feed_format",
        "source_right_registry",
        "feed_format IS NULL OR feed_format IN ('JSON', 'CSV', 'BLM', 'RESO')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_srr_feed_format", "source_right_registry", type_="check")
    op.drop_index("ix_source_right_registry_site_domain", table_name="source_right_registry")
    op.drop_column("source_right_registry", "site_domain")
    op.drop_column("source_right_registry", "feed_credential_env")
    op.drop_column("source_right_registry", "feed_format")
    op.drop_column("source_right_registry", "feed_url")
