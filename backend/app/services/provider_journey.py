"""External provider journey after a renter's request (ZR-AI-SEARCH-001
Section 9 steps 3-8, Sections 8 and 11).

* Signed provider links: the outreach email carries a link only that provider
  receives. Following it is the provider's proof of controlling that contact
  (step 5 "contact ownership"); no account is needed to respond.
* Response: accept (Claim & List, or a one-off introduction where the market
  enables it) or decline; or opt out of all contact (suppression list).
  Acceptance is commercial consent, never verification.
* Controlled introduction: once accepted, the renter and provider talk in a
  Zoiko-mediated thread (one per request), scrubbed of contact details.
* Direct contact release: only when both sides consent and the market's Legal
  Pack and the source's rights allow it.
* Claim & List: the provider signs in and claims the room, which creates a
  DRAFT listing they own; the platform's normal identity/property/authority
  verification, Listing Fee and publication gates apply. Publication
  internalises the lead (step 8).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.external_search import (
    DirectContactRelease,
    ExternalOpportunity,
    ProviderOutreach,
    ProviderSuppression,
    RelayMessage,
)
from app.services.anti_circumvention import sanitizer
from app.services.audit_ext import log_external_search_event
from app.services.external_search_crypto import decrypt_contact
from app.services.market_legal_pack import active_pack, contact_release_allowed
from app.services.source_rights_registry import registry

logger = logging.getLogger(__name__)

TOKEN_TTL_DAYS = 30
ACCEPTANCE_MODELS = ("CLAIM_AND_LIST", "ONE_OFF_INTRODUCTION")


# -- signed provider links ----------------------------------------------------------

def _key() -> bytes:
    return f"{settings.jwt_secret}:zr-provider-portal".encode()


def make_provider_token(outreach_id: int, *, ttl_days: int = TOKEN_TTL_DAYS) -> str:
    payload = f"po:{outreach_id}:{int(time.time()) + ttl_days * 86400}"
    sig = hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()[:40]
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=") + "." + sig


def read_provider_token(token: str) -> int:
    """Outreach id from a provider link; PermissionError if forged/expired."""
    try:
        encoded, sig = token.split(".", 1)
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        kind, outreach_id, expires = payload.split(":")
    except (ValueError, UnicodeDecodeError):
        raise PermissionError("This link is not valid.") from None
    expected = hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()[:40]
    if kind != "po" or not hmac.compare_digest(sig, expected):
        raise PermissionError("This link is not valid.")
    if int(expires) < time.time():
        raise PermissionError("This link has expired.")
    return int(outreach_id)


def provider_links(outreach_id: int) -> dict[str, str]:
    token = make_provider_token(outreach_id)
    base = f"{settings.frontend_url.rstrip('/')}/provider/respond?token={token}"
    return {"token": token, "respond_url": base, "opt_out_url": f"{base}&action=opt-out"}


# -- suppression ----------------------------------------------------------------------

def contact_hash(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    if not text:
        return None
    if "@" not in text:
        text = re.sub(r"\D", "", text)
    return hashlib.sha256(text.encode()).hexdigest() if text else None


def is_suppressed(db: Session, opportunity: ExternalOpportunity) -> bool:
    h = contact_hash(decrypt_contact(opportunity.provider_contact_encrypted))
    clauses = [ProviderSuppression.source_id == opportunity.source_id]
    if h:
        clauses.append(ProviderSuppression.contact_hash == h)
    from sqlalchemy import or_

    return db.scalar(select(ProviderSuppression.id).where(or_(*clauses)).limit(1)) is not None


def suppress(
    db: Session, *, contact: str | None = None, source_id: str | None = None,
    reason: str = "OPT_OUT", note: str = "", created_by: str = "provider",
) -> ProviderSuppression | None:
    h = contact_hash(contact)
    if not h and not source_id:
        return None
    if h:
        existing = db.scalar(select(ProviderSuppression).where(ProviderSuppression.contact_hash == h))
        if existing:
            return existing
    row = ProviderSuppression(contact_hash=h, source_id=None if h else source_id, reason=reason,
                              note=note[:500], created_by=created_by)
    db.add(row)
    db.flush()
    log_external_search_event(
        db, action="outreach.suppression_added", resource_type="provider_suppression",
        resource_id=str(row.id), reason=f"{reason};by={created_by}",
    )
    return row


# -- outreach content -------------------------------------------------------------------

def renter_demand(po: ProviderOutreach) -> dict[str, str]:
    """Only the Section 7.4 fields the renter consented to share."""
    consent = po.consent_record or {}
    allowed = set(consent.get("consent_fields") or [])
    details = consent.get("lead_details") or {}
    opp = po.opportunity
    values = {
        "desired_area": opp.approx_location,
        "room_type": opp.room_type,
        "move_in_window": details.get("move_in_window"),
        "budget_band": details.get("budget_band"),
        "occupants": details.get("occupants"),
        "requirements": details.get("requirements"),
    }
    return {k: str(v) for k, v in values.items() if k in allowed and v}


def one_off_intro_enabled(db: Session, market: str | None) -> bool:
    """A one-off controlled introduction is offered only where an ACTIVE
    commercial policy for that model exists for the market (Section 9 step 4)."""
    from app.models.external_search import ExternalCommercialPolicy

    if not market:
        return False
    return db.scalar(
        select(ExternalCommercialPolicy.id).where(
            ExternalCommercialPolicy.market_code == market,
            ExternalCommercialPolicy.model == "FIXED_QUALIFIED_INTRODUCTION_FEE",
            ExternalCommercialPolicy.status == "ACTIVE",
        ).limit(1)
    ) is not None


def render_for(db: Session, po: ProviderOutreach) -> tuple[str, str]:
    """(body, respond_url) for one outreach, per the market's Legal Pack."""
    from app.services.outreach_templates import render_provider_outreach

    opp = po.opportunity
    pack = active_pack(db, opp.market_code)
    links = provider_links(po.id)
    body = render_provider_outreach(
        provider_name=opp.provider_name,
        approx_location=opp.approx_location,
        respond_url=links["respond_url"],
        opt_out_url=links["opt_out_url"],
        privacy_url=pack.privacy_notice_url if pack else None,
        sender_entity=pack.sender_legal_entity if pack else "Zoiko Realty Group Inc.",
        renter_demand=renter_demand(po),
        source_category=_source_category(opp),
        one_off_intro_enabled=one_off_intro_enabled(db, opp.market_code),
    )
    return body, links["respond_url"]


def _source_category(opp: ExternalOpportunity) -> str:
    rule = registry.get(opp.source_id) or {}
    mode = rule.get("acquisition_mode")
    return {
        "PARTNER_FEED": "a listings feed from a partner agency",
        "LICENSED_API": "a licensed property listings service",
        "PUBLIC_FETCH": "your publicly advertised listing",
    }.get(mode, "a publicly advertised room listing")


def email_dispatch(db: Session, po: ProviderOutreach, body: str) -> str | None:
    """Outbound EMAIL channel. Returns a message id, or None when this
    outreach cannot be emailed (other channel / no email address), which keeps
    it in the operator queue rather than marking it sent."""
    from app.core.mailer import send_email
    from app.services.outreach_templates import SUBJECT

    if po.channel != "EMAIL":
        return None
    contact = decrypt_contact(po.opportunity.provider_contact_encrypted).strip()
    if "@" not in contact:
        return None
    respond_url = provider_links(po.id)["respond_url"]
    lines = [line for line in body.split("\n") if line and not line.startswith("Subject:")]
    if not send_email(contact, SUBJECT, heading="A renter asked us to contact you",
                      body_lines=lines, cta_label="Respond", cta_url=respond_url):
        raise RuntimeError("email delivery failed")
    return f"email:{po.id}"


# -- provider response ------------------------------------------------------------------

def _notify_renter(db: Session, po: ProviderOutreach, title: str, message: str) -> None:
    from app.crud.notification import notify_user

    notify_user(
        db, po.requested_by_user_id, title=title, message=message,
        notification_type="external_search.update",
        related_entity_type="provider_outreach", related_entity_id=str(po.id),
    )


def portal_view(db: Session, po: ProviderOutreach) -> dict[str, Any]:
    """What the provider sees on their response page (no renter identity)."""
    opp = po.opportunity
    pack = active_pack(db, opp.market_code)
    accepted = po.provider_response == "ACCEPTED"
    release = _release(db, po)
    return {
        "approx_location": opp.approx_location,
        "renter_demand": renter_demand(po),
        "renter_message": sanitizer.sanitize_text(str((po.consent_record or {}).get("message") or ""))[:1000],
        "status": po.provider_response or "AWAITING_RESPONSE",
        "acceptance_model": po.acceptance_model,
        "options": ["CLAIM_AND_LIST"] + (["ONE_OFF_INTRODUCTION"] if one_off_intro_enabled(db, opp.market_code) else []),
        "terms_version": pack.terms_version if pack else None,
        "lead_protection_days": pack.lead_protection_days if pack else None,
        "sender_entity": pack.sender_legal_entity if pack else "Zoiko Realty Group Inc.",
        "messages": [_message_view(m, "PROVIDER") for m in thread(db, po)] if accepted else [],
        "can_message": accepted,
        "release": _release_view(db, po, release, "PROVIDER"),
        "verification_status": "NOT_VERIFIED_BY_ZOIKO_ROOMS" if opp.verification_status == "NOT_VERIFIED_BY_ZOIKO_ROOMS" else opp.verification_status,
    }


def respond(
    db: Session, po: ProviderOutreach, *, decision: str, model: str | None = None,
    provider_name: str | None = None, accepted_terms: bool = False, correlation_id: str = "",
) -> ProviderOutreach:
    """Section 9 step 5. Accepting records the model, the Legal Pack terms
    version, the provider's name and contact ownership (they used the link
    sent to that contact), then unlocks the controlled introduction."""
    from app.services.external_outreach import outreach_service

    if po.provider_response in ("ACCEPTED", "DECLINED"):
        raise PermissionError("You have already responded to this request.")
    decision = decision.upper()
    if decision == "DECLINE":
        outreach_service.handle_provider_response(db, po, "DECLINED", correlation_id=correlation_id)
        _notify_renter(db, po, "Provider declined", "The provider declined this introduction. You can search for other rooms.")
        return po
    if decision != "ACCEPT":
        raise ValueError("decision must be ACCEPT or DECLINE")
    opp = po.opportunity
    allowed = ["CLAIM_AND_LIST"] + (["ONE_OFF_INTRODUCTION"] if one_off_intro_enabled(db, opp.market_code) else [])
    if model not in allowed:
        raise ValueError(f"Choose one of: {', '.join(allowed)}")
    if not accepted_terms:
        raise ValueError("Please accept the terms to continue.")
    pack = active_pack(db, opp.market_code)
    if pack is None:
        raise PermissionError("Introductions are not available in this market.")

    now = datetime.now(timezone.utc)
    po.acceptance_model = model
    po.acceptance_terms_version = pack.terms_version
    po.provider_display_name = sanitizer.sanitize_text((provider_name or "").strip())[:200] or None
    po.provider_contact_confirmed_at = now
    outreach_service.handle_provider_response(
        db, po, "ACCEPTED",
        response_detail={
            "acceptance_ref": f"{model}:terms-{pack.terms_version}",
            "provider_ref": (contact_hash(decrypt_contact(opp.provider_contact_encrypted)) or "")[:16],
            "lead_protection_days": pack.lead_protection_days,
        },
        correlation_id=correlation_id,
    )
    outreach_service.unlock_introduction(db, opp, correlation_id=correlation_id)
    _notify_renter(
        db, po, "Provider accepted your introduction",
        "The provider accepted. You can now message them through Zoiko Rooms. "
        "They are not verified by Zoiko Rooms.",
    )
    return po


def opt_out(db: Session, po: ProviderOutreach, *, correlation_id: str = "") -> None:
    """Honour an opt-out: suppress the contact (or whole source when there is
    no contact) and decline any open request."""
    opp = po.opportunity
    suppress(db, contact=decrypt_contact(opp.provider_contact_encrypted), source_id=opp.source_id,
             reason="OPT_OUT", created_by="provider")
    if po.provider_response not in ("ACCEPTED", "DECLINED"):
        from app.services.external_outreach import outreach_service

        outreach_service.handle_provider_response(db, po, "DECLINED", correlation_id=correlation_id)
        _notify_renter(db, po, "Provider declined", "The provider declined this introduction.")


# -- controlled introduction thread ---------------------------------------------------------

def thread(db: Session, po: ProviderOutreach) -> list[RelayMessage]:
    return list(db.scalars(
        select(RelayMessage).where(RelayMessage.outreach_id == po.id).order_by(RelayMessage.created_at, RelayMessage.id)
    ))


def _message_view(m: RelayMessage, viewer: str) -> dict[str, Any]:
    return {"id": m.id, "from": "you" if m.sender_topic == viewer else ("provider" if m.sender_topic == "PROVIDER" else "renter"),
            "body": m.body, "at": m.created_at.isoformat() if m.created_at else None}


def send_message(db: Session, po: ProviderOutreach, *, sender: str, body: str, user_id: int | None = None) -> RelayMessage:
    if po.provider_response != "ACCEPTED":
        raise PermissionError("Messaging opens once the provider accepts the introduction.")
    text = sanitizer.sanitize_text((body or "").strip())[:5000]
    if not text:
        raise ValueError("Message is empty.")
    msg = RelayMessage(
        opportunity_id=po.opportunity_id, outreach_id=po.id, sender_topic=sender,
        sender_user_id=user_id if sender == "RENTER" else None,
        sender_handle=f"{'Renter' if sender == 'RENTER' else 'Provider'} {po.id:06d}", body=text,
    )
    db.add(msg)
    db.flush()
    log_external_search_event(db, action="relay.message_sent", resource_type="relay_message",
                              resource_id=str(msg.id), reason=f"outreach:{po.id} sender:{sender}")
    if sender == "PROVIDER":
        _notify_renter(db, po, "New message from a provider", "You have a new message about your external room request.")
    return msg


# -- direct contact release -----------------------------------------------------------------

def _release(db: Session, po: ProviderOutreach) -> DirectContactRelease | None:
    return db.scalar(select(DirectContactRelease).where(DirectContactRelease.outreach_id == po.id))


def release_available(db: Session, po: ProviderOutreach) -> bool:
    opp = po.opportunity
    return (
        po.provider_response == "ACCEPTED"
        and contact_release_allowed(db, opp.market_code)
        and registry.is_direct_contact_allowed(opp.source_id)
    )


def consent_to_release(db: Session, po: ProviderOutreach, *, side: str) -> DirectContactRelease:
    if not release_available(db, po):
        raise PermissionError("Sharing contact details isn't available for this introduction.")
    release = _release(db, po)
    if release is None:
        release = DirectContactRelease(opportunity_id=po.opportunity_id, outreach_id=po.id)
        db.add(release)
        db.flush()
    now = datetime.now(timezone.utc)
    if side == "RENTER":
        release.renter_consented_at = release.renter_consented_at or now
    else:
        release.provider_consented_at = release.provider_consented_at or now
    if release.renter_consented_at and release.provider_consented_at and not release.both_consented_at:
        release.both_consented_at = now
        log_external_search_event(db, action="relay.release_granted", resource_type="direct_contact_release",
                                  resource_id=str(release.id), reason=f"outreach:{po.id} bilateral consent")
        _notify_renter(db, po, "Contact details shared", "You and the provider agreed to share contact details.")
    else:
        log_external_search_event(db, action="relay.release_consent_recorded", resource_type="direct_contact_release",
                                  resource_id=str(release.id), reason=f"outreach:{po.id} side:{side}")
    return release


def _release_view(db: Session, po: ProviderOutreach, release: DirectContactRelease | None, viewer: str) -> dict[str, Any]:
    both = bool(release and release.both_consented_at)
    contact = None
    if both:
        if viewer == "RENTER":
            contact = decrypt_contact(po.opportunity.provider_contact_encrypted) or None
        else:
            from app.models.user_account import UserAccount

            user = db.get(UserAccount, po.requested_by_user_id)
            contact = user.email if user else None
    return {
        "available": release_available(db, po),
        "you_consented": bool(release and (release.renter_consented_at if viewer == "RENTER" else release.provider_consented_at)),
        "other_consented": bool(release and (release.provider_consented_at if viewer == "RENTER" else release.renter_consented_at)),
        "released": both,
        "contact": contact,
    }


# -- renter view ------------------------------------------------------------------------------

def renter_requests(db: Session, user_id: int) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(ProviderOutreach).where(ProviderOutreach.requested_by_user_id == user_id)
        .order_by(ProviderOutreach.requested_at.desc()).limit(50)
    ).all()
    return [renter_request_view(db, po) for po in rows]


def renter_request_view(db: Session, po: ProviderOutreach) -> dict[str, Any]:
    opp = po.opportunity
    return {
        "outreach_id": po.id,
        "opportunity_id": opp.id,
        "external_opportunity_id": opp.external_opportunity_id,
        "approx_location": opp.approx_location,
        "verification_status": "NOT_VERIFIED_BY_ZOIKO_ROOMS" if opp.status != "INTERNALIZED_VERIFIED" else "INTERNALIZED",
        "outreach_status": po.outreach_status,
        "provider_response": po.provider_response or "AWAITING_RESPONSE",
        "provider_name": po.provider_display_name,
        "requested_at": po.requested_at.isoformat() if po.requested_at else None,
        "can_message": po.provider_response == "ACCEPTED",
        "messages": [_message_view(m, "RENTER") for m in thread(db, po)],
        "release": _release_view(db, po, _release(db, po), "RENTER"),
    }


# -- Claim & List (steps 7-8) ---------------------------------------------------------------------

def claim(db: Session, po: ProviderOutreach, user: Any, *, correlation_id: str = "") -> Any:
    """The provider (signed in) claims the room: a DRAFT listing they own is
    created; the platform's standard verification and Listing Fee gates then
    govern publication. Requires a Claim & List acceptance on this request."""
    from app.crud.ids import new_id
    from app.crud.listing import _unique_slug
    from app.models.listing import Listing

    if po.provider_response != "ACCEPTED" or po.acceptance_model != "CLAIM_AND_LIST":
        raise PermissionError("Accept the Claim & List option first.")
    if not getattr(user, "party_id", None):
        raise PermissionError("Your account needs a host profile before claiming a room.")
    opp = po.opportunity
    if opp.internal_listing_id:
        existing = db.get(Listing, opp.internal_listing_id)
        if existing is not None:
            if existing.party_id != user.party_id:
                raise PermissionError("This room has already been claimed.")
            return existing

    from app.services.external_outreach import outreach_service

    city = outreach_service._listing_city(opp)
    name = f"Room in {city}"
    listing = Listing(
        id=new_id("L"), slug=_unique_slug(db, name), name=name, property_type="private_room",
        room_type=(opp.room_type or "private_room")[:255], city=city[:255],
        location=(opp.approx_location or "")[:500], latitude=None, longitude=None,
        price_per_night=outreach_service._price_per_night(opp),
        currency=opp.advertised_price_currency or "GBP", rating=0.0, review_count=0,
        guests=1, bedrooms=1, bathrooms=1, min_stay_nights=30,
        description="Claimed from an external introduction. Add your own description, photos and checks to publish.",
        state="DRAFT", tags=["external-claimed"], party_id=user.party_id,
    )
    db.add(listing)
    db.flush()
    opp.internal_listing_id = listing.id
    opp.status = "VERIFICATION_IN_PROGRESS"
    opp.verification_status = "VERIFICATION_IN_PROGRESS"
    log_external_search_event(
        db, action="internalize.claimed", resource_type="external_opportunity", resource_id=str(opp.id),
        correlation_id=correlation_id, after_state="VERIFICATION_IN_PROGRESS",
        reason=f"listing:{listing.id};user:{user.id};model=CLAIM_AND_LIST",
    )
    return listing


def payment_block_reason(db: Session, party_id: int | None) -> str | None:
    """SRCH-12: a host whose room came from an external lead cannot set up
    Zoiko-issued payment instructions until every such lead is internalised,
    verified, holds the separate payment-receipt authority and (for sublets)
    has evidenced landlord permission. None = not blocked."""
    from app.models.listing import Listing
    from app.services.commercial_policy import CommercialPolicyService

    if not party_id:
        return None
    leads = db.scalars(
        select(ExternalOpportunity)
        .join(Listing, Listing.id == ExternalOpportunity.internal_listing_id)
        .where(Listing.party_id == party_id)
    ).all()
    if any(not CommercialPolicyService.external_payment_eligible(o) for o in leads):
        return (
            "Payment instructions aren't available yet for a room claimed from an external introduction. "
            "Complete verification, including the separate check of your authority to receive payment."
        )
    return None


def sync_internalised(db: Session) -> int:
    """Step 8: a claimed listing that passed the platform's gates and was
    PUBLISHED makes its lead INTERNALIZED_VERIFIED; future searches treat it as
    internal inventory. Returns how many leads moved."""
    from app.models.listing import Listing

    rows = db.execute(
        select(ExternalOpportunity, Listing)
        .join(Listing, Listing.id == ExternalOpportunity.internal_listing_id)
        .where(ExternalOpportunity.status != "INTERNALIZED_VERIFIED", Listing.state == "PUBLISHED")
    ).all()
    for opp, listing in rows:
        opp.status = "INTERNALIZED_VERIFIED"
        opp.verification_status = "VERIFIED_AUTHORITY"
        log_external_search_event(
            db, action="internalize.published", resource_type="external_opportunity", resource_id=str(opp.id),
            after_state="INTERNALIZED_VERIFIED", reason=f"listing:{listing.id} published",
        )
    if rows:
        db.commit()
    return len(rows)
