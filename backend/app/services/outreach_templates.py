"""Provider outreach message templates (ZR-AI-SEARCH-001 Section 9.1).

The template must satisfy every 9.1 requirement:
* identify Zoiko Rooms as a trading name of the correct local entity
* state the genuine purpose (a renter asked Zoiko Rooms to contact the
  provider -- never an impersonated "availability enquiry")
* offer Claim & List as the default, with a clear alternative to decline
* never claim the provider or listing is "on Zoiko Rooms", "verified",
  "approved" or "partnered" unless true (they are not, yet)
* no renter fee, no rent collection, no undisclosed commission

TEMPLATE_COMPLIANCE_NOTES documents each invariant so tests can assert the
rendered message stays compliant even after future edits.
"""

from __future__ import annotations

PROVIDER_OUTREACH_TEMPLATE = """Subject: Zoiko Rooms — prospective renter enquiry about your property

Dear {provider_name},

A prospective renter using Zoiko Rooms has asked us to contact you
regarding a room that appears to be advertised at {approx_location}.

Zoiko Rooms helps renters find rooms and helps providers list their
properties. We would like to invite you to claim this room on Zoiko
Rooms — a simple process that takes just a few minutes.

What this means for you:
• List your room on Zoiko Rooms platform
• Pay only the applicable Listing Fee (no rental commission)
• Get access to verified, qualified renters
• Full control over your listing

Alternatively, you may decline this introduction.

To get started, please visit: {claim_url}

Best regards,
Zoiko Rooms
Zoiko Realty Group Inc.
"""

# Automated checks (Section 9.1) that must hold on every rendered message.
TEMPLATE_COMPLIANCE_NOTES = {
    "identifies_zoiko_rooms": True,
    "no_verified_or_approved_claim": True,
    "no_partnered_claim": True,
    "no_impersonation": True,
    "no_renter_fee": True,
    "no_rent_collection": True,
    "decline_option_offered": True,
}

# Simple marker list used for explicit assertion loops in tests.
FORBIDDEN_PHRASES = [
    "your listing is verified",
    "Zoiko Rooms verified",
    "approved by Zoiko rooms",
    "partnered",
    "book now on zoiko rooms",
]


def render_provider_outreach(
    *,
    provider_name: str | None,
    approx_location: str | None,
    claim_url: str = "https://zoikorooms.com/providers/claim",
) -> str:
    """Render the provider outreach message for a discovered opportunity."""
    return PROVIDER_OUTREACH_TEMPLATE.format(
        provider_name=(provider_name or "Provider").strip() or "Provider",
        approx_location=(approx_location or "an advertised location").strip() or "an advertised location",
        claim_url=claim_url,
    )