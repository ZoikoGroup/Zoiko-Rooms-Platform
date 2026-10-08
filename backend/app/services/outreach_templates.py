"""Provider outreach message templates (ZR-AI-SEARCH-001 Section 9.1).

The template must satisfy every 9.1 requirement:
* sender identity: Zoiko Rooms, a trading name of the correct local entity
  (the market Legal Pack's sender_legal_entity)
* purpose: a prospective renter asked Zoiko Rooms to contact the provider
  about a room that appears to be advertised -- never an impersonated
  "availability enquiry"
* a summary of the renter's demand, limited to what the renter consented to
  share (Section 7.4)
* no false claims: never "on Zoiko Rooms", "verified", "approved" or
  "partnered"; no unsupported claims about renters
* clear choices: decline, a one-off controlled introduction where the market
  enables it, or claim/list the room on Zoiko Rooms
* privacy: the category of source the contact came from, a privacy notice,
  and a working opt-out that is honoured (suppression list)
* no renter fee, no rent collection, no undisclosed commission

TEMPLATE_COMPLIANCE_NOTES documents each invariant so tests can assert the
rendered message stays compliant even after future edits.
"""

from __future__ import annotations

TEMPLATE_VERSION = "ZR-OUTREACH-2.0"

SUBJECT = "Zoiko Rooms: a renter asked us to contact you about your room advert"

PROVIDER_OUTREACH_TEMPLATE = """Subject: {subject}

Dear {provider_name},

We are Zoiko Rooms, a trading name of {sender_entity}. A prospective renter
using Zoiko Rooms asked us to contact you about a room that appears to be
advertised in {approx_location}. We found your contact details on {source_category}.

What the renter is looking for:
{demand_lines}

You can choose what happens next:
• List with Zoiko Rooms: claim this room and list it yourself after completing
  Zoiko Rooms' standard identity, property and authority checks. Providers pay
  only the applicable Listing Fee; Zoiko Rooms charges no rental commission.
{one_off_line}• Alternatively, decline this introduction. Nothing further happens.

The renter pays Zoiko Rooms nothing for this introduction, and Zoiko Rooms
does not collect rent. Your contact details are not shared with the renter
unless you accept and you both agree to share them.

Respond here: {respond_url}

If you do not want Zoiko Rooms to contact you again, opt out here: {opt_out_url}
How we handle your information: {privacy_url}

Zoiko Rooms
{sender_entity}
"""

ONE_OFF_INTRO_LINE = (
    "• Accept a one-off introduction: talk to the renter through Zoiko Rooms'\n"
    "  messaging without listing the room. You stay unverified until you\n"
    "  complete the checks.\n"
)

# Automated checks (Section 9.1) that must hold on every rendered message.
TEMPLATE_COMPLIANCE_NOTES = {
    "identifies_zoiko_rooms": True,
    "trading_name_of_legal_entity": True,
    "genuine_purpose_stated": True,
    "renter_demand_summarised": True,
    "no_verified_or_approved_claim": True,
    "no_partnered_claim": True,
    "no_impersonation": True,
    "no_renter_fee": True,
    "no_rent_collection": True,
    "decline_option_offered": True,
    "opt_out_offered": True,
    "privacy_information_offered": True,
    "source_category_disclosed": True,
}

# Simple marker list used for explicit assertion loops in tests.
FORBIDDEN_PHRASES = [
    "your listing is verified",
    "zoiko rooms verified",
    "approved by zoiko rooms",
    "partnered",
    "book now on zoiko rooms",
    "verified, qualified renters",
    "is your room still available",
]

# Section 7.4 consent fields -> wording used in the demand summary.
_DEMAND_LABELS = {
    "desired_area": "Area",
    "move_in_window": "Move-in",
    "budget_band": "Budget",
    "room_type": "Room type",
    "occupants": "Occupants",
    "requirements": "Requirements",
}


def render_provider_outreach(
    *,
    provider_name: str | None,
    approx_location: str | None,
    claim_url: str = "https://zoikorooms.com/providers/claim",
    respond_url: str | None = None,
    opt_out_url: str | None = None,
    privacy_url: str | None = None,
    sender_entity: str = "Zoiko Realty Group Inc.",
    renter_demand: dict[str, str] | None = None,
    source_category: str = "a publicly advertised room listing",
    one_off_intro_enabled: bool = False,
) -> str:
    """Render the provider outreach message for a discovered opportunity.

    ``renter_demand`` holds only the fields the renter consented to share;
    anything else is never included."""
    demand = renter_demand or {}
    lines = [f"• {_DEMAND_LABELS[k]}: {v}" for k, v in demand.items() if k in _DEMAND_LABELS and v]
    respond = respond_url or claim_url
    return PROVIDER_OUTREACH_TEMPLATE.format(
        subject=SUBJECT,
        provider_name=(provider_name or "Provider").strip() or "Provider",
        sender_entity=sender_entity,
        approx_location=(approx_location or "your area").strip() or "your area",
        source_category=source_category,
        demand_lines="\n".join(lines) if lines else "• A room in your area",
        one_off_line=ONE_OFF_INTRO_LINE if one_off_intro_enabled else "",
        respond_url=respond,
        opt_out_url=opt_out_url or f"{respond}&action=opt-out",
        privacy_url=privacy_url or "https://www.zoikorooms.com/privacy",
    )
