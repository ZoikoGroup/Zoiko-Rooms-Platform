"""ZR-COMMS-EMAIL-001 Section 06 -- master template register (the templates
this build sends today). Each entry fixes the template's identity, tier,
sending stream, footer blocks, CTA label and subject/preheader copy; the body
copy lives with its builder in app/core/mailer.py.

Versioning (Section 8.2): a change to legal, payment, decision, security or
safety meaning bumps the major version; wording/accessibility fixes bump the
minor; pure formatting bumps the patch. Every sent message carries
X-Zoiko-Template: <id>@<version> so it can be reproduced later (Section 1.3)."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.email import streams

# Section 02 message classes.
TIER_0 = 0  # Critical security, safety, legal or financial action
TIER_1 = 1  # Transactional or workflow-critical service message
TIER_2 = 2  # Service utility or operational advisory
TIER_3 = 3  # Consent-based lifecycle or marketing

TIER_LABELS = {
    TIER_0: "Tier 0 — Critical security, safety, legal or financial action",
    TIER_1: "Tier 1 — Transactional or workflow-critical service message",
    TIER_2: "Tier 2 — Service utility or operational advisory",
    TIER_3: "Tier 3 — Consent-based lifecycle or marketing message",
}


@dataclass(frozen=True)
class TemplateSpec:
    template_id: str
    version: str
    family: str
    tier: int
    stream: streams.Stream
    # Section 05 disclosure/warning blocks (blocks.BLOCKS keys). The service or
    # marketing footer and the corporate identity line are added by tier.
    blocks: tuple[str, ...]
    cta_label: str | None
    subject: str     # str.format() template
    preheader: str   # str.format() template


_T = TemplateSpec

REGISTRY: dict[str, TemplateSpec] = {t.template_id: t for t in (
    # A -- Account, Identity and Security
    _T("ZR-EML-ID-002", "1.0.0", "Welcome to Zoiko Rooms", TIER_2, streams.TRANSACTIONS, (),
       "Choose Your Path", "Welcome to Zoiko Rooms",
       "Find a private room, list one you are authorized to offer, or join an organization."),
    _T("ZR-EML-ID-003", "1.0.0", "Sign-in link or one-time code", TIER_0, streams.SECURITY, ("security",),
       None, "Your Zoiko Rooms security code",
       "Use this one-time code to confirm the change. It expires soon."),
    _T("ZR-EML-ID-006", "1.0.0", "Account recovery status", TIER_0, streams.SECURITY, ("security",),
       "Continue Recovery", "Update on your Zoiko Rooms account recovery",
       "Review the recovery status and any action required."),
    # C -- Verification, Provider Authority, Room Passport and Listings
    _T("ZR-EML-VER-001", "1.0.0", "Identity verification status", TIER_1, streams.TRUST, ("verification",),
       "Review Verification", "Your Zoiko Rooms identity check is {verification_status_display}",
       "Review the result and any action needed."),
    _T("ZR-EML-VER-002", "1.0.0", "Listing authority status", TIER_1, streams.TRUST, ("verification",),
       "Review Authority", "Your authority to list a property: {authority_status_display}",
       "Review the status of your listing authority and any action needed."),
    _T("ZR-EML-LST-001", "1.0.0", "Listing submitted or returned", TIER_1, streams.TRANSACTIONS,
       ("verification", "decision"),
       "Review Listing", "Your listing for {room_label} is {listing_review_status}",
       "Review the listing status and any required changes."),
    _T("ZR-EML-LST-002", "1.0.0", "Listing published", TIER_1, streams.TRANSACTIONS, (),
       "View Live Listing", "Your room listing is live",
       "Review the public listing and manage availability from your dashboard."),
    # B -- Market Availability, Search and Discovery
    _T("ZR-EML-MKT-001", "1.0.0", "Saved search confirmed", TIER_2, streams.TRANSACTIONS, (),
       "View Saved Search", "Your Zoiko Rooms search is saved",
       "We will alert you when matching private rooms become available."),
    _T("ZR-EML-MKT-002", "1.0.0", "New matching rooms digest", TIER_2, streams.TRANSACTIONS, ("verification",),
       "View Matching Rooms", "{match_count} new rooms match your search",
       "Review evidence, availability and provider details before applying."),
    # E -- Applications and Offers
    _T("ZR-EML-APP-004", "1.0.0", "Application decision", TIER_1, streams.TRANSACTIONS, ("decision",),
       "Review Decision", "Update on your application for {listing_short_title}",
       "Review the decision, reason and available next step."),
    _T("ZR-EML-OFR-001", "1.0.0", "Conditional or final offer issued", TIER_1, streams.TRANSACTIONS, ("decision",),
       "Review Offer", "You have a {offer_type_display} offer for {listing_short_title}",
       "Review the terms, conditions and deadline before responding."),
    _T("ZR-EML-OFR-002", "1.0.0", "Offer outcome", TIER_1, streams.TRANSACTIONS, (),
       "View Offer", "The offer for {listing_short_title} is {offer_status_display}",
       "Review the confirmed status and next step."),
    # F -- Agreements and Booking
    _T("ZR-EML-AGR-001", "1.0.0", "Agreement ready for review", TIER_1, streams.TRANSACTIONS, (),
       "Review Agreement", "Your room agreement is ready to review",
       "Read the complete agreement and market-specific disclosures before signing."),
    _T("ZR-EML-AGR-002", "1.0.0", "Signature status or reminder", TIER_1, streams.TRANSACTIONS, (),
       "Review and Sign", "Action required: sign the agreement for {listing_short_title}",
       "Complete your signature by {signature_deadline_local}."),
    _T("ZR-EML-BKG-002", "1.0.0", "Booking confirmed", TIER_0, streams.TRANSACTIONS, ("payment",),
       "View Booking", "Your room booking is confirmed",
       "Review your move-in date, agreement, payment record and next steps."),
    # G -- Payments, Deposits, Rent, Refunds and Payouts
    _T("ZR-EML-PAY-001", "1.0.0", "Payment due or authorization required", TIER_0, streams.MONEY, ("payment",),
       "Make Secure Payment", "Payment due for {payment_purpose_display}",
       "Review the amount, provider and deadline before paying."),
    _T("ZR-EML-RENT-001", "1.0.0", "Upcoming rent reminder", TIER_1, streams.MONEY, ("payment",),
       "Review Rent Schedule", "Rent of {amount_localized} is due on {due_date_local}",
       "Review your payment method and rent schedule."),
    _T("ZR-EML-PAY-002", "1.0.0", "Payment received and receipt", TIER_0, streams.MONEY, ("payment",),
       "View Receipt", "Payment received: {amount_localized}",
       "Your Zoiko Rooms payment record and receipt are ready."),
    _T("ZR-EML-DEP-003", "1.0.0", "Deposit settlement", TIER_0, streams.MONEY, ("payment",),
       "Review Deposit Settlement", "Deposit settlement update for {room_or_booking_label}",
       "Review the proposed or completed deductions and return."),
    _T("ZR-EML-RFD-001", "1.0.0", "Refund status", TIER_0, streams.MONEY, ("payment",),
       "View Refund", "Your refund of {amount_localized} is {refund_status_display}",
       "Review the amount, destination and expected timing."),
    _T("ZR-EML-PYO-001", "1.0.0", "Provider payout status", TIER_0, streams.MONEY, ("payment",),
       "View Payout", "Payout of {amount_localized} is {payout_status_display}",
       "Review the destination, deductions and settlement status."),
)}


def get(template_id: str) -> TemplateSpec:
    return REGISTRY[template_id]
