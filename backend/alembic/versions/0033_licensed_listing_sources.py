"""ZR-AI-SEARCH-001 licensed listing API sources (Section 6.1 Tier B)

Registers the two licensed listing APIs that have adapters in
services/external_providers.py, fail-closed:

* status REVIEW with legal_approved / security_approved false, so neither is
  ever called until Legal and Security approve the row and set it ACTIVE;
* rentcast (US): display/masking/outreach left off until its licence is
  reviewed for masked display and contact use;
* domain_au (Australia): Domain's API terms require attribution and a link to
  the original listing, so clickthrough_required/attribution_required are set
  and masked display stays off -- listings can only become internal
  opportunities (Section 15.4) unless Legal records a different decision;
* brave_web: the Brave Search API, used only to find listings on websites
  that are themselves approved PUBLIC_FETCH registry sources. Brave's terms
  ask for "Powered by Brave" attribution of the search, not of listings;
* parallel_web: the Parallel Search API, same approved-websites-only rule.

Revision ID: 0033_licensed_listing_sources
Revises: 0032_search_protocol_hardening
Create Date: 2026-10-08 00:00:00.000000

"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0033_licensed_listing_sources"
down_revision: Union[str, None] = "0032_search_protocol_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SOURCES = (
    {
        "source_id": "rentcast",
        "source_name_internal": "RentCast rental listings API",
        "territories": ["US"],
        "terms_reference": "https://www.rentcast.io/api",
        "permitted_fields": [],
        "attribution_required": False,
        "clickthrough_required": False,
        "cache_ttl_seconds": 86400,
    },
    {
        "source_id": "domain_au",
        "source_name_internal": "Domain residential listings API",
        "territories": ["AU"],
        "terms_reference": "https://www.domain.com.au/group/api-terms-and-conditions/",
        "permitted_fields": [],
        "attribution_required": True,
        "clickthrough_required": True,
        "cache_ttl_seconds": 3600,
    },
    {
        "source_id": "brave_web",
        "source_name_internal": "Brave Search API (approved websites only)",
        "territories": ["GB", "US"],
        "terms_reference": "https://brave.com/search/api/",
        "permitted_fields": [],
        "attribution_required": True,
        "clickthrough_required": False,
        "cache_ttl_seconds": 3600,
    },
    {
        "source_id": "parallel_web",
        "source_name_internal": "Parallel Search API (approved websites only)",
        "territories": ["GB", "US"],
        "terms_reference": "https://parallel.ai/terms-of-service",
        "permitted_fields": [],
        "attribution_required": False,
        "clickthrough_required": False,
        "cache_ttl_seconds": 3600,
    },
)

registry = sa.table(
    "source_right_registry",
    sa.column("source_id", sa.String),
    sa.column("source_name_internal", sa.String),
    sa.column("territories", sa.JSON),
    sa.column("acquisition_mode", sa.String),
    sa.column("terms_reference", sa.String),
    sa.column("legal_approved", sa.Boolean),
    sa.column("security_approved", sa.Boolean),
    sa.column("permitted_fields", sa.JSON),
    sa.column("display_permitted", sa.Boolean),
    sa.column("attribution_required", sa.Boolean),
    sa.column("clickthrough_required", sa.Boolean),
    sa.column("masking_permitted", sa.Boolean),
    sa.column("cache_ttl_seconds", sa.Integer),
    sa.column("contact_extraction_permitted", sa.Boolean),
    sa.column("outreach_permitted", sa.Boolean),
    sa.column("outreach_channels", sa.JSON),
    sa.column("status", sa.String),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    conn = op.get_bind()
    existing = set(conn.execute(sa.select(registry.c.source_id)).scalars())
    now = datetime.now(timezone.utc)
    rows = [
        {
            **src,
            "acquisition_mode": "LICENSED_API",
            "legal_approved": False,
            "security_approved": False,
            "display_permitted": False,
            "masking_permitted": False,
            "contact_extraction_permitted": False,
            "outreach_permitted": False,
            "outreach_channels": [],
            "status": "REVIEW",
            "created_at": now,
            "updated_at": now,
        }
        for src in SOURCES
        if src["source_id"] not in existing
    ]
    if rows:
        op.bulk_insert(registry, rows)


def downgrade() -> None:
    op.execute(
        registry.delete().where(
            registry.c.source_id.in_([s["source_id"] for s in SOURCES]),
            registry.c.legal_approved.is_(False),
        )
    )
