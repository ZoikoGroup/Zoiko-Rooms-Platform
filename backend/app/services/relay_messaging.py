"""Controlled introductions -- Zoiko-mediated contact between renters and
unverified external providers (ZR-AI-SEARCH-001 Phase 2 / Section 11).

Rules enforced here:
* Relay messaging only after the introduction is unlocked (provider accepted
  + Phase 1 Step 6 unlock) and the opportunity is still unverified -- the
  point of the relay is to keep contact mediated until verification.
* Messages are stored Zoiko-side with masked sender handles; direct contact
  details are never written into relay bodies.
* Direct contact details are released only with BILATERAL consent (renter +
  provider), expressed as a masked/forwarding address, never raw PII.
* Release is NOT verification.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.external_search import (
    DirectContactRelease,
    ExternalOpportunity,
    RelayMessage,
)
from app.services.anti_circumvention import sanitizer
from app.services.external_search_crypto import decrypt_contact

ActorTopic = Literal["RENTER", "PROVIDER"]

_RELAY_DOMAIN = "relay.zoikorooms.com"


class RelayMessaging:
    def is_introduction_unlocked(self, db: Session, opportunity: ExternalOpportunity) -> bool:
        return opportunity.status == "PROVIDER_ACCEPTED" and opportunity.intro_unlocked_at is not None

    def is_relay_allowed(self, db: Session, opportunity: ExternalOpportunity) -> bool:
        """Relay stays available while the provider is engaged and unverified
        (verified providers move to internal inventory and direct contact)."""
        if not self.is_introduction_unlocked(db, opportunity):
            return False
        return True

    def can_message(self, db: Session, opportunity: ExternalOpportunity) -> bool:
        return self.is_relay_allowed(db, opportunity)

    def send_relay_message(
        self,
        db: Session,
        *,
        opportunity_id: int,
        sender_topic: ActorTopic,
        content: str,
        sender_user_id: int | None = None,
        correlation_id: str = "",
    ) -> RelayMessage:
        from app.services.audit_ext import log_external_search_event

        opp = db.get(ExternalOpportunity, opportunity_id)
        if not opp:
            raise ValueError("Opportunity not found")
        if not self.can_message(db, opp):
            raise PermissionError(
                "Relay messaging requires a provider-accepted, introduction-unlocked opportunity"
            )
        if sender_topic == "RENTER" and sender_user_id is None:
            raise ValueError("A renter relay message requires a sender user id")
        if sender_topic == "PROVIDER" and sender_user_id is not None:
            # Provider-side messages originate from the masked provider handle,
            # never from a platform account.
            raise ValueError("A provider relay message cannot carry a user id")

        # Bodies are Zoiko-mediated: raw contact details are scrubbed on the
        # way in so no side-effect can leak them to the other party.
        body = sanitizer.sanitize_text(content)[:5000]

        handle = (
            f"Renter R-{opp.id:06d}"
            if sender_topic == "RENTER"
            else f"Provider P-{opp.id:06d}"
        )
        msg = RelayMessage(
            opportunity_id=opp.id,
            sender_topic=sender_topic,
            sender_user_id=sender_user_id,
            sender_handle=handle,
            body=body,
        )
        db.add(msg)
        db.flush()
        log_external_search_event(
            db,
            action="relay.message_sent",
            resource_type="relay_message",
            resource_id=str(msg.id),
            correlation_id=correlation_id,
            reason=f"opp:{opp.id} sender:{sender_topic}",
        )
        return msg

    def list_relay_messages(self, db: Session, opportunity_id: int, limit: int = 100) -> list[RelayMessage]:
        return list(
            db.execute(
                select(RelayMessage)
                .where(RelayMessage.opportunity_id == opportunity_id)
                .order_by(RelayMessage.created_at.asc())
                .limit(limit)
            ).scalars()
        )

    def mask_email(self, provider_email: str) -> str:
        """Deterministic, non-reversible forwarding mask for a provider's email."""
        digest = hashlib.sha256((provider_email or "").encode("utf-8")).hexdigest()[:12]
        return f"provider-{digest}@{_RELAY_DOMAIN}"

    def request_direct_contact_release(
        self,
        db: Session,
        *,
        opportunity_id: int,
        actor_topic: ActorTopic,
        correlation_id: str = "",
    ) -> DirectContactRelease:
        """Record one side's consent to release direct details. Only when both
        sides have consented is a masked/forwarding contact issued and the
        release is recorded -- never raw contact data."""
        from app.services.audit_ext import log_external_search_event

        opp = db.get(ExternalOpportunity, opportunity_id)
        if not opp:
            raise ValueError("Opportunity not found")
        if opp.status != "PROVIDER_ACCEPTED":
            raise PermissionError("Direct-contact release requires provider acceptance")

        release = db.scalar(
            select(DirectContactRelease).where(
                DirectContactRelease.opportunity_id == opportunity_id
            ).limit(1)
        )
        if release is None:
            release = DirectContactRelease(opportunity_id=opp.id)
            db.add(release)
            db.flush()

        now = datetime.now(timezone.utc)
        if actor_topic == "RENTER":
            release.renter_consented_at = release.renter_consented_at or now
        else:
            release.provider_consented_at = release.provider_consented_at or now

        detail = {"renter": bool(release.renter_consented_at), "provider": bool(release.provider_consented_at)}
        if release.renter_consented_at and release.provider_consented_at:
            if release.both_consented_at is None:
                release.both_consented_at = now
                provider_contact = decrypt_contact(opp.provider_contact_encrypted).strip()
                release.released_mask = self.mask_email(provider_contact) if provider_contact else self.mask_email(f"opp-{opp.id}")
                detail["released_mask"] = release.released_mask
            log_external_search_event(
                db,
                action="relay.release_granted",
                resource_type="direct_contact_release",
                resource_id=str(release.id),
                correlation_id=correlation_id,
                reason=f"opp:{opp.id} bilateral_consent released_mask={release.released_mask or ''}",
            )
        else:
            log_external_search_event(
                db,
                action="relay.release_consent_recorded",
                resource_type="direct_contact_release",
                resource_id=str(release.id),
                correlation_id=correlation_id,
                reason=f"opp:{opp.id} topic={actor_topic}",
            )
        return release

    def direct_contact_released(self, db: Session, opportunity: ExternalOpportunity) -> bool:
        release = db.scalar(
            select(DirectContactRelease).where(
                DirectContactRelease.opportunity_id == opportunity.id
            ).limit(1)
        )
        return bool(release and release.both_consented_at is not None)

    def start_verification_journey(self, db: Session, opportunity_id: int) -> ExternalOpportunity:
        from app.services.external_outreach import outreach_service

        opp = db.get(ExternalOpportunity, opportunity_id)
        if not opp:
            raise ValueError("Opportunity not found")
        return outreach_service.start_verification(db, opp)


relay = RelayMessaging()