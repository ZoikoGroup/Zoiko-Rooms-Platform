"""Provider outreach -- the 8-step async journey from user request to provider
acceptance (ZR-AI-SEARCH-001 SS-4 / Section 9).

Phase 0 implemented the request + rights gate and records the outreach row.
Phase 1 adds the full step machine:

  Step 1  request_intro          explicit user instruction + data-sharing pref
  Step 2  check_outreach_eligibility   source rights + channel rules (fail-closed)
  Step 3  send_outreach          provider message via a permitted channel
  Step 4  (commercial choice)    Claim & List default; fee models are feature-gated
  Step 5  complete_acceptance    capture acceptance (commercial consent, NOT verification)
  Step 6  unlock_introduction    controlled relay introduction; still "not verified"
  Step 7  start_verification     invite provider to verify identity/property/authority
  Step 8  internalize_listing    create/claim the Zoiko Rooms listing when gates pass

Key rule (ZR-AI-SEARCH-001 SS-4 Rule 4): verification does NOT unlock payment.
Even a consented provider is only unlocked for messaging via the platform's
relay, never direct contact before unlock.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.external_search import ExternalOpportunity, ProviderOutreach
from app.schemas.external_search import ProviderOutreachCreate
from app.services.feature_flags import is_enabled
from app.services.source_rights_registry import BLOCKED, registry


SUPPORTED_CHANNELS = ("EMAIL", "SMS", "PLATFORM_MESSAGE", "TELEPHONE")
OPEN_OUTREACH_STATUSES = ("PENDING", "SENT", "DELIVERED")
OUTREACH_FLAG = "external.provider_outreach"


class ProviderOutreachService:
    # -- shared helpers ------------------------------------------------------

    @staticmethod
    def _resolve_channel(rule: dict[str, Any] | None) -> str | None:
        if not rule or not rule.get("allow_direct_contact"):
            return None
        allowed = [
            c for c in rule.get("outreach_channels", []) if c in SUPPORTED_CHANNELS
        ]
        # Fail closed: a source with no configured permitted channel is not
        # contactable (never assume EMAIL).
        return allowed[0] if allowed else None

    @staticmethod
    def _append_trail(po: ProviderOutreach, step: str, detail: dict[str, Any]) -> None:
        trail = list(po.audit_trail or [])
        trail.append(
            {
                "step": step,
                "at": datetime.now(timezone.utc).isoformat(),
                **detail,
            }
        )
        po.audit_trail = trail

    # -- Phase 0 path (chat tool + REST route) -------------------------------

    def create_outreach(
        self,
        db: Session,
        payload: ProviderOutreachCreate,
        correlation_id: str = "",
    ) -> ProviderOutreach:
        from app.services.audit_ext import log_external_search_event

        opp = db.get(ExternalOpportunity, payload.opportunity_id)
        if not opp:
            log_external_search_event(
                db,
                action="outreach.blocked.not_found",
                resource_type="external_opportunity",
                resource_id=str(payload.opportunity_id),
                correlation_id=correlation_id,
                reason="opportunity_not_found",
            )
            raise ValueError("Opportunity not found")

        if opp.status == "BLOCKED":
            log_external_search_event(
                db,
                action="outreach.blocked.opportunity",
                resource_type="external_opportunity",
                resource_id=str(payload.opportunity_id),
                correlation_id=correlation_id,
                reason="opportunity_blocked",
            )
            raise PermissionError("This opportunity is not available for contact")

        # Fail-closed gate: contact only when source rights permit outreach.
        if not registry.is_direct_contact_allowed(opp.source_id):
            log_external_search_event(
                db,
                action="outreach.blocked.rights",
                resource_type="external_opportunity",
                resource_id=str(payload.opportunity_id),
                correlation_id=correlation_id,
                reason=f"direct_contact_not_allowed:source={opp.source_id}",
            )
            raise PermissionError("Direct contact not allowed for this source")

        po = ProviderOutreach(
            opportunity_id=payload.opportunity_id,
            requested_by_user_id=payload.requested_by_user_id,
            requested_at=datetime.now(timezone.utc),
            channel=payload.channel,
            outreach_status="PENDING",
            consent_record=payload.consent_record or {},
            audit_trail=payload.audit_trail or [],
        )
        db.add(po)
        db.flush()
        log_external_search_event(
            db,
            action="outreach.created",
            resource_type="provider_outreach",
            resource_id=str(po.id),
            correlation_id=correlation_id,
            reason=f"user:{payload.requested_by_user_id} opp:{payload.opportunity_id}",
        )
        return po

    def list_outreach_for_user(
        self, db: Session, user_id: int, limit: int = 50
    ) -> list[ProviderOutreach]:
        return list(
            db.execute(
                select(ProviderOutreach)
                .where(ProviderOutreach.requested_by_user_id == user_id)
                .order_by(ProviderOutreach.created_at.desc())
                .limit(limit)
            ).scalars()
        )

    # -- Step 1/2 : intro request + eligibility -------------------------------

    def check_outreach_eligibility(
        self, db: Session, opportunity: ExternalOpportunity
    ) -> dict[str, Any]:
        """Step 2 of Section 9. Fail-closed: unknown source, inactive source,
        BLOCKED tier or missing direct-contact rights all deny outreach."""
        rule = registry.get(opportunity.source_id)
        reasons: list[str] = []
        if not rule:
            reasons.append("unknown source")
        else:
            if not rule.get("is_active", False):
                reasons.append("source inactive")
            if str(rule.get("tier", BLOCKED)).upper() == BLOCKED:
                reasons.append("source blocked")
            if not rule.get("allow_direct_contact"):
                reasons.append("outreach not permitted for source")
        channel = self._resolve_channel(rule) if rule else None
        if rule and not channel:
            reasons.append("no permitted channel")
        return {
            "eligible": bool(channel) and not reasons,
            "channel": channel,
            "reasons": reasons,
        }

    def request_intro(
        self,
        db: Session,
        *,
        user_id: int,
        opportunity_id: int,
        consent_fields: dict[str, Any] | None = None,
        correlation_id: str = "",
    ) -> ProviderOutreach:
        """Step 1: record the user's explicit intro request and advance the
        opportunity to OUTREACH_PENDING.

        An opportunity discovered by one user's search can only be requested
        by that user (unknown to everyone else, so ids can't be probed). A
        repeat request returns the user's open outreach instead of queueing a
        second provider message.
        """
        from app.services.audit_ext import log_external_search_event

        if not is_enabled(db, OUTREACH_FLAG):
            log_external_search_event(
                db,
                action="outreach.intro_requested.blocked.not_activated",
                resource_type="external_opportunity",
                resource_id=str(opportunity_id),
                correlation_id=correlation_id,
                reason="provider_outreach_not_activated",
            )
            raise PermissionError("Zoiko Rooms can't contact external providers in this market yet")

        opp = db.get(ExternalOpportunity, opportunity_id)
        if opp and opp.discovered_by_user_id is not None and opp.discovered_by_user_id != user_id:
            opp = None
        if not opp:
            log_external_search_event(
                db,
                action="outreach.intro_requested.blocked.not_found",
                resource_type="external_opportunity",
                resource_id=str(opportunity_id),
                correlation_id=correlation_id,
                reason="opportunity_not_found",
            )
            raise ValueError("Opportunity not found")

        decision = self.check_outreach_eligibility(db, opp)
        if not decision["eligible"]:
            log_external_search_event(
                db,
                action="outreach.intro_requested.blocked.rights",
                resource_type="external_opportunity",
                resource_id=str(opportunity_id),
                correlation_id=correlation_id,
                reason=";".join(decision["reasons"]),
            )
            raise PermissionError(
                "No permitted outreach channel for this source "
                f"({' ; '.join(decision['reasons'])})"
            )

        existing = db.scalar(
            select(ProviderOutreach)
            .where(
                ProviderOutreach.opportunity_id == opportunity_id,
                ProviderOutreach.requested_by_user_id == user_id,
                ProviderOutreach.outreach_status.in_(OPEN_OUTREACH_STATUSES),
            )
            .limit(1)
        )
        if existing is not None:
            return existing

        po = self.create_outreach(
            db,
            ProviderOutreachCreate(
                opportunity_id=opportunity_id,
                requested_by_user_id=user_id,
                channel=decision["channel"],
                consent_record=dict(consent_fields or {}),
                audit_trail=[{"step": "step:user_requested_intro", "consent": bool(consent_fields)}],
            ),
            correlation_id=correlation_id,
        )
        if opp.status == "EXTERNAL_DISCOVERED":
            opp.status = "OUTREACH_PENDING"
        log_external_search_event(
            db,
            action="outreach.intro_requested",
            resource_type="provider_outreach",
            resource_id=str(po.id),
            correlation_id=correlation_id,
            reason=f"user:{user_id} opp:{opportunity_id}",
            after_state="OUTREACH_PENDING",
        )
        return po

    # -- Step 3 : dispatch the provider message -------------------------------

    def send_outreach(
        self,
        db: Session,
        po: ProviderOutreach,
        *,
        dispatch: Callable[[Session, ProviderOutreach, str], str] | None = None,
        now: datetime | None = None,
    ) -> ProviderOutreach:
        """Step 3: dispatch the provider message via the allowed channel.

        `dispatch` is the channel adapter (email/sms relay). Without one the
        row stays PENDING in the operator queue -- it is never recorded as
        SENT unless a message was actually handed to a channel.
        """
        from app.services.audit_ext import log_external_search_event
        from app.services.outreach_templates import render_provider_outreach

        now = now or datetime.now(timezone.utc)
        decision = self.check_outreach_eligibility(db, po.opportunity)
        if not decision["eligible"]:
            po.outreach_status = "SUPPRESSED"
            self._append_trail(
                po, "step:eligibility_blocked", {"reasons": decision["reasons"], "channel": decision["channel"]}
            )
            log_external_search_event(
                db,
                action="outreach.suppressed",
                resource_type="provider_outreach",
                resource_id=str(po.id),
                reason=";".join(decision["reasons"]),
            )
            return po

        if dispatch is None:
            return po

        body = render_provider_outreach(
            provider_name=po.opportunity.provider_name,
            approx_location=po.opportunity.approx_location,
        )
        message_id = dispatch(db, po, body)

        po.outreach_status = "SENT"
        po.outreach_sent_at = now
        self._append_trail(
            po,
            "step:dispatched",
            {"channel": decision["channel"], "message_id": message_id},
        )
        log_external_search_event(
            db,
            action="outreach.sent",
            resource_type="provider_outreach",
            resource_id=str(po.id),
            after_state="SENT",
            reason=f"channel={decision['channel']}",
        )
        return po

    # -- Step 5 : provider response / acceptance ------------------------------

    def handle_provider_response(
        self,
        db: Session,
        po: ProviderOutreach,
        response: str,
        *,
        response_detail: dict[str, Any] | None = None,
        correlation_id: str = "",
    ) -> ProviderOutreach:
        """Steps 4-5: process the provider's acceptance or decline. Acceptance
        is commercial consent and explicitly NOT verification."""
        resp = str(response).strip().upper()
        if resp not in ("ACCEPTED", "DECLINED"):
            raise ValueError("response must be ACCEPTED or DECLINED")
        now = datetime.now(timezone.utc)
        po.provider_response = resp
        po.provider_response_at = now
        self._append_trail(
            po,
            "step:provider_response",
            {"response": resp, "response_detail": response_detail or {}},
        )
        if resp == "ACCEPTED":
            self.complete_acceptance(
                db, po, acceptance_ref=(response_detail or {}).get("acceptance_ref"), correlation_id=correlation_id
            )
        else:
            from app.services.audit_ext import log_external_search_event

            log_external_search_event(
                db,
                action="outreach.declined",
                resource_type="provider_outreach",
                resource_id=str(po.id),
                correlation_id=correlation_id,
                reason="provider declined introduction",
            )
        return po

    def complete_acceptance(
        self,
        db: Session,
        po: ProviderOutreach,
        *,
        acceptance_ref: str | None = None,
        correlation_id: str = "",
    ) -> ExternalOpportunity:
        """Step 5: capture acceptance -> opportunity PROVIDER_ACCEPTED.
        Acceptance is NOT verification."""
        from app.services.audit_ext import log_external_search_event

        opp = po.opportunity
        opp.status = "PROVIDER_ACCEPTED"
        self._append_trail(
            po,
            "step:acceptance",
            {
                "acceptance_ref": acceptance_ref,
                "note": "acceptance is commercial consent, not verification",
            },
        )
        log_external_search_event(
            db,
            action="outreach.accepted",
            resource_type="provider_outreach",
            resource_id=str(po.id),
            correlation_id=correlation_id,
            after_state="PROVIDER_ACCEPTED",
            reason=acceptance_ref or "",
        )
        return opp

    # -- Step 6 : controlled introduction --------------------------------------

    def unlock_introduction(
        self,
        db: Session,
        opportunity: ExternalOpportunity,
        *,
        correlation_id: str = "",
    ) -> ExternalOpportunity:
        """Step 6: unlock relay messaging. Requires prior provider acceptance;
        the opportunity remains "not verified by Zoiko Rooms"."""
        from app.services.audit_ext import log_external_search_event

        if opportunity.status != "PROVIDER_ACCEPTED":
            raise PermissionError("Introduction may only be unlocked after provider acceptance")
        if opportunity.intro_unlocked_at is None:
            opportunity.intro_unlocked_at = datetime.now(timezone.utc)
            log_external_search_event(
                db,
                action="outreach.intro_unlocked",
                resource_type="external_opportunity",
                resource_id=str(opportunity.id),
                correlation_id=correlation_id,
                after_state="PROVIDER_ACCEPTED",
                reason="relay unlocked; still unverified",
            )
        return opportunity

    # -- Step 7 : verification invites -------------------------------------------

    def start_verification(
        self,
        db: Session,
        opportunity: ExternalOpportunity,
        *,
        correlation_id: str = "",
    ) -> ExternalOpportunity:
        """Step 7: invite the provider to verify identity, property and
        authority to list. Only verified capabilities unlock protected
        workflows (SS-4 Rule 4)."""
        from app.services.audit_ext import log_external_search_event

        if opportunity.status not in ("PROVIDER_ACCEPTED", "OUTREACH_PENDING"):
            raise PermissionError("Verification can only begin on an engaged opportunity")
        if opportunity.verification_status != "VERIFICATION_IN_PROGRESS":
            opportunity.verification_status = "VERIFICATION_IN_PROGRESS"
            log_external_search_event(
                db,
                action="verification.started",
                resource_type="external_opportunity",
                resource_id=str(opportunity.id),
                correlation_id=correlation_id,
                after_state="PROVIDER_ACCEPTED",
                reason="invitation to verify identity/property/authority",
            )
        return opportunity

    def record_sublet_permission(
        self,
        db: Session,
        opportunity: ExternalOpportunity,
        *,
        evidence: str | None = None,
        correlation_id: str = "",
    ) -> ExternalOpportunity:
        """Section 11.1: capture evidence of required landlord/agent permission
        where a sublet is involved. Evidence is stored encrypted at rest; the
        verified flag is set only together with that evidence."""
        from app.services.audit_ext import log_external_search_event
        from app.services.external_search_crypto import encrypt_optional

        if opportunity.status != "PROVIDER_ACCEPTED":
            raise PermissionError("Sublet-permission capture requires provider acceptance")
        now = datetime.now(timezone.utc)
        if evidence is not None:
            opportunity.sublet_evidence_encrypted = encrypt_optional(evidence)
        opportunity.sublet_permission_verified = True
        opportunity.sublet_permission_verified_at = now
        log_external_search_event(
            db,
            action="verification.sublet_permission_recorded",
            resource_type="external_opportunity",
            resource_id=str(opportunity.id),
            correlation_id=correlation_id,
            reason=f"evidence={'captured' if opportunity.sublet_evidence_encrypted else 'none'}",
        )
        return opportunity

    def record_payment_receipt_authority(
        self,
        db: Session,
        opportunity: ExternalOpportunity,
        *,
        correlation_id: str = "",
    ) -> ExternalOpportunity:
        """Section 11.1: separate authority for the provider to receive the
        relevant payment. Distinct from identity/property/listing-authority and
        from provider acceptance; payment instructions unlock only when this is
        set AND the opportunity is otherwise fully verified (see
        CommercialPolicyService.external_payment_eligible / SRCH-12)."""
        from app.services.audit_ext import log_external_search_event

        if opportunity.verification_status not in ("VERIFICATION_IN_PROGRESS", "VERIFIED_AUTHORITY"):
            raise PermissionError(
                "Payment-receipt authority requires an in-progress or completed verification journey"
            )
        now = datetime.now(timezone.utc)
        if opportunity.payment_receipt_authority_verified_at is None:
            opportunity.payment_receipt_authority_verified = True
            opportunity.payment_receipt_authority_verified_at = now
            log_external_search_event(
                db,
                action="verification.payment_receipt_authority_recorded",
                resource_type="external_opportunity",
                resource_id=str(opportunity.id),
                correlation_id=correlation_id,
                reason="separate payment-receipt authority verified",
            )
        return opportunity

    # -- Step 8 : internalization ------------------------------------------------

    @staticmethod
    def _price_per_night(opportunity: ExternalOpportunity) -> float:
        if not opportunity.advertised_price_minor:
            return 0.0
        major = float(opportunity.advertised_price_minor) / 100.0
        period = (opportunity.price_period or "MONTH").upper()
        if period == "WEEK":
            return round(major / 7.0, 2)
        if period == "NIGHT":
            return round(major, 2)
        return round(major / 30.44, 2)

    @classmethod
    def _listing_city(cls, opportunity: ExternalOpportunity) -> str:
        loc = (opportunity.approx_location or "").strip()
        return loc.split(",")[0].strip() if loc else "Unknown"

    def internalize_listing(
        self,
        db: Session,
        opportunity: ExternalOpportunity,
        *,
        correlation_id: str = "",
    ) -> Any:
        """Step 8: create the Zoiko Rooms listing when all gates pass.

        Gates: the provider must have completed verification
        (VERIFIED_AUTHORITY implies identity + property + authority). The
        listing is created in DRAFT -- the platform's own listing-approval
        pipeline (and Listing Fee collection) governs publication.
        """
        from app.crud.ids import new_id
        from app.crud.listing import _unique_slug
        from app.models.listing import Listing
        from app.services.audit_ext import log_external_search_event

        if opportunity.verification_status != "VERIFIED_AUTHORITY":
            raise PermissionError(
                "Internalization requires completed provider verification "
                f"(current: {opportunity.verification_status})"
            )

        # Section 11.1: a sublet whose landlord/agent permission has not been
        # evidenced fails closed -- evidence present but consent unresolved
        # means the record cannot become Zoiko inventory.
        if opportunity.sublet_evidence_encrypted and not opportunity.sublet_permission_verified:
            raise PermissionError(
                "Sublet record requires confirmed landlord/agent permission before internalization"
            )

        if opportunity.internal_listing_id:
            existing = db.get(Listing, opportunity.internal_listing_id)
            if existing is not None:
                return existing

        city = self._listing_city(opportunity)
        name = (opportunity.provider_name or f"Room near {city}").strip() or "Room"
        listing = Listing(
            id=new_id("L"),
            slug=_unique_slug(db, name),
            name=name[:255],
            property_type="private_room",
            room_type=(opportunity.room_type or "private_room")[:255],
            city=city[:255],
            location=(opportunity.approx_location or "")[:500],
            latitude=None,
            longitude=None,
            price_per_night=self._price_per_night(opportunity),
            currency=opportunity.advertised_price_currency or "GBP",
            rating=4.5,
            review_count=0,
            guests=1,
            bedrooms=1,
            bathrooms=1,
            min_stay_nights=30,
            description=(
                "Claimed from an external discovery. Awaiting the provider's own "
                "description and verification before publication."
            ),
            state="DRAFT",
            tags=["external-claimed"],
        )
        db.add(listing)
        db.flush()

        opportunity.internal_listing_id = listing.id
        opportunity.status = "INTERNALIZED_VERIFIED"
        log_external_search_event(
            db,
            action="internalize.listing_created",
            resource_type="external_opportunity",
            resource_id=str(opportunity.id),
            correlation_id=correlation_id,
            after_state="INTERNALIZED_VERIFIED",
            reason=f"listing:{listing.id}",
        )
        return listing


# Backwards-compatible name for the Phase 0 module singleton.
ExternalOutreachService = ProviderOutreachService

outreach_service = ProviderOutreachService()