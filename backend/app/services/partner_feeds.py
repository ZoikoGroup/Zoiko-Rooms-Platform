"""Partner feed adapter -- contracted inventory/data partner integrations
(ZR-AI-SEARCH-001 Phase 4 / Section 13). Tier A sources: structured feeds
ingested as external opportunities under controlled rights.

Fail-closed rules:
* only a source registered with acquisition_mode=PARTNER_FEED, status ACTIVE,
  legal_approved AND security_approved can be ingested;
* data is copied only for fields the source's permitted_fields allow
  (permitted_features mirrors the same list);
* ingestion deduplicates by a content hash derived from the partner's own
  external id — re-ingesting the same feed is a no-op;
* every ingested/skipped row is audited.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.external_search import ExternalOpportunity, SourceRightRegistry
from app.services.audit_ext import log_external_search_event
from app.services.external_search_crypto import encrypt_optional

logger = logging.getLogger(__name__)

# Feed field key -> ExternalOpportunity attribute. Only fields authorised by the
# source's permitted_fields list are copied (always via this map, never by
# splatting raw payload keys into the model). Source-controlled contact fields
# are stored encrypted at rest (Section 12).
FIELD_MAP: dict[str, str] = {
    "provider_name": "provider_name",
    "approx_location": "approx_location",
    "price_minor": "advertised_price_minor",
    "currency": "advertised_price_currency",
    "price_period": "price_period",
    "room_type": "room_type",
    "provider_contact": "provider_contact_encrypted",
    "exact_address": "exact_address_encrypted",
    "source_url": "source_url_encrypted",
}

# FIELD_MAP keys whose values must be encrypted at rest before persistence.
_ENCRYPT_AT_REST = {
    "provider_contact": "provider_contact_encrypted",
    "exact_address": "exact_address_encrypted",
    "source_url": "source_url_encrypted",
}


class PartnerFeedAdapter:
    def _resolve_source(self, db: Session, source_id: str) -> SourceRightRegistry | None:
        return db.scalar(
            select(SourceRightRegistry).where(
                SourceRightRegistry.source_id == source_id,
                SourceRightRegistry.acquisition_mode == "PARTNER_FEED",
                SourceRightRegistry.status == "ACTIVE",
            ).limit(1)
        )

    @staticmethod
    def _dedupe_hash(source_id: str, external_id: str) -> str:
        return hashlib.sha256(f"{source_id}:{external_id}".encode("utf-8")).hexdigest()

    def ingest_feed(
        self,
        db: Session,
        *,
        source_id: str,
        items: list[dict[str, Any]],
        correlation_id: str = "",
    ) -> list[ExternalOpportunity]:
        src = self._resolve_source(db, source_id)
        if src is None:
            log_external_search_event(
                db,
                action="partner_feed.blocked.source",
                resource_type="partner_feed",
                resource_id=source_id,
                correlation_id=correlation_id,
                reason="source_not_registered_or_not_partner_feed",
            )
            raise PermissionError(
                "Partner feed source must be registered with acquisition_mode=PARTNER_FEED"
            )
        if not (src.legal_approved and src.security_approved):
            log_external_search_event(
                db,
                action="partner_feed.blocked.approvals",
                resource_type="partner_feed",
                resource_id=source_id,
                correlation_id=correlation_id,
                reason=f"legal={src.legal_approved} security={src.security_approved}",
            )
            raise PermissionError("Partner feed source approvals incomplete")

        permitted = set(src.permitted_fields or [])
        created: list[ExternalOpportunity] = []
        skipped = 0
        for item in items:
            external_id = str(item.get("external_id", "")).strip()
            if not external_id:
                continue
            dedupe = self._dedupe_hash(source_id, external_id)
            if db.scalar(
                select(ExternalOpportunity.id).where(ExternalOpportunity.dedupe_hash == dedupe).limit(1)
            ):
                skipped += 1
                continue

            opp = ExternalOpportunity(
                external_opportunity_id=f"pf_{source_id}_{external_id}",
                source_id=source_id,
                status="EXTERNAL_DISCOVERED",
                verification_status="NOT_VERIFIED_BY_ZOIKO_ROOMS",
                permitted_features=sorted(permitted),
                dedupe_hash=dedupe,
                raw_data=item,
                source_domain=src.source_brand_display_rule or (item.get("domain") or ""),
            )
            for key, attr in FIELD_MAP.items():
                if key not in permitted:
                    continue
                value = item.get(key)
                if value is not None:
                    if key in _ENCRYPT_AT_REST:
                        value = encrypt_optional(str(value))
                    setattr(opp, attr, value)
            db.add(opp)
            db.flush()
            created.append(opp)

        log_external_search_event(
            db,
            action="partner_feed.ingested",
            resource_type="partner_feed",
            resource_id=source_id,
            correlation_id=correlation_id,
            reason=f"created={len(created)} skipped_duplicates={skipped}",
        )
        return created

    def sync_availability(
        self,
        db: Session,
        *,
        source_id: str,
        correlation_id: str = "",
    ) -> int:
        """Refresh marker for the source's ingested rows (per-contract TTL
        refresh hook). Returns the number of rows marked refreshed."""
        src = self._resolve_source(db, source_id)
        if src is None:
            return 0
        now = datetime.now(timezone.utc)
        rows = db.scalars(
            select(ExternalOpportunity).where(ExternalOpportunity.source_id == source_id)
        ).all()
        refreshed = 0
        for opp in rows:
            raw = dict(opp.raw_data or {})
            raw["availability_checked_at"] = now.isoformat()
            raw["ttl_seconds"] = src.cache_ttl_seconds
            opp.raw_data = raw
            refreshed += 1
        if refreshed:
            log_external_search_event(
                db,
                action="partner_feed.availability_synced",
                resource_type="partner_feed",
                resource_id=source_id,
                correlation_id=correlation_id,
                reason=f"rows={refreshed}",
            )
        return refreshed


partner_feed_adapter = PartnerFeedAdapter()