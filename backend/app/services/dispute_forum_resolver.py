"""ZR-ENG-CLR-010 Section 6/7/8: the Authority/Forum Resolver and severity
triage that run before substantive case handling (Section 6: "executes
before substantive case handling. It determines who may decide each claim
and what Zoiko may do while the claim is pending").

JURISDICTION AWARENESS (Section 7): a claim family's authority class can be
overridden per jurisdiction via `MarketPolicyPack`'s
`dispute_*_authority_class` fields (Phase 13) -- the same versioned,
effective-dated policy engine `resolve_market_policy` (crud/market_policy.py)
already is the mandatory single entry point for elsewhere in this codebase
("never branch on jurisdiction_code directly"). When no
`market_policy_pack` is passed in (an unresolvable jurisdiction, or a claim
with no occupancy to resolve one from), this falls back to the static
defaults below, which exactly match every `MarketPolicyPack` field's own
default -- so "no jurisdiction override configured" and "no market pack at
all" produce identical results. `ZOIKO_SERVICE` always stays `A0`
regardless of jurisdiction (Section 6: that's Zoiko's own authority, not a
market question), and claim families with no forum mapping at all
(`PROTECTED_SAFETY`, `VERIFICATION_FRAUD`, `MARKETPLACE_CONDUCT`,
`PAYMENT`, `REFUND_PAYOUT`) are not jurisdiction-configurable either --
jurisdiction can't manufacture a mapping this MVP has no adapter for.

Fail-closed discipline (AC-42, Section 7: "Resolver failure is fail-closed
for coercive or irreversible actions... the system must not invent a generic
resolution path"): any claim family (or safety-flagged claim) this mapping
does not cover a real internal authority for resolves with
confidence=LEGAL_REVIEW_REQUIRED. LEGAL_REVIEW_REQUIRED describes the
resolver's own confidence, never the claim's merits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.models.dispute import DISPUTE_CLAIM_FAMILIES

if TYPE_CHECKING:
    from app.models.market_policy import MarketPolicyPack


@dataclass(frozen=True)
class ForumResolution:
    authority_class: str | None
    confidence: str  # "RESOLVED" | "LEGAL_REVIEW_REQUIRED"
    notes: str
    # Section 7: which MarketPolicyPack (if any) actually informed
    # authority_class, as real, queryable columns on the claim -- not just
    # embedded in `notes`' free text. None whenever no market_policy_pack
    # was resolved (an unconfigured jurisdiction, a claim with no
    # occupancy to resolve one from, or a family/safety-forced path that
    # never consults a market pack at all, e.g. ZOIKO_SERVICE).
    policy_pack_id: int | None = None
    policy_pack_version: int | None = None


# Section 6 authority classes this MVP can honestly resolve without a real
# jurisdiction forum pack: A0 (Zoiko's own fee/service issues), A1
# (bilateral/negotiable, no statutory scheme implied), A2 (deposit --
# Section 2/14's existing scheme-controlled custody model already exists in
# this codebase, so a deposit claim can be routed there with confidence).
# Every other family (PAYMENT/REFUND_PAYOUT chargebacks stay on the existing
# finance mechanism -- see models/dispute.py's own docstring;
# MARKETPLACE_CONDUCT, PROTECTED_SAFETY, VERIFICATION_FRAUD have no A3-A5
# adapter built yet) falls through to LEGAL_REVIEW_REQUIRED rather than
# being guessed at.
_FAMILY_AUTHORITY: dict[str, str] = {
    "ZOIKO_SERVICE": "A0",
    "BOOKING_AGREEMENT": "A1",
    "PROPERTY_CONDITION": "A1",
    "SUBLET_OCCUPANCY": "A1",
    "DEPOSIT": "A2",
}

# The subset of _FAMILY_AUTHORITY a MarketPolicyPack can override --
# ZOIKO_SERVICE is deliberately absent (see module docstring).
_FAMILY_TO_POLICY_FIELD: dict[str, str] = {
    "DEPOSIT": "dispute_deposit_authority_class",
    "BOOKING_AGREEMENT": "dispute_booking_agreement_authority_class",
    "PROPERTY_CONDITION": "dispute_property_condition_authority_class",
    "SUBLET_OCCUPANCY": "dispute_sublet_occupancy_authority_class",
}

# Section 18/19: illegal lockout and credible violence risk are never
# ordinary bilateral/internal matters regardless of claim family -- they
# force A6 (emergency/criminal, Section 6) and SEV-0 (Section 8) outright.
_SAFETY_FORCED_CODES = frozenset({"ILLEGAL_LOCKOUT", "VIOLENCE_RISK"})

_SEV2_FAMILIES = frozenset({"DEPOSIT", "PAYMENT", "REFUND_PAYOUT", "BOOKING_AGREEMENT"})
_SEV3_FAMILIES = frozenset({"ZOIKO_SERVICE"})


def resolve_claim_authority(
    claim_family: str, claim_code: str, *, safety_flag: bool = False, market_policy_pack: "MarketPolicyPack | None" = None,
) -> ForumResolution:
    if safety_flag or claim_code in _SAFETY_FORCED_CODES:
        return ForumResolution(
            authority_class="A6",
            confidence="LEGAL_REVIEW_REQUIRED",
            notes="Safety-flagged claim -- routed for specialist/emergency handling, never an ordinary internal or bilateral decision.",
        )
    if claim_family not in DISPUTE_CLAIM_FAMILIES:
        return ForumResolution(None, "LEGAL_REVIEW_REQUIRED", f"Unrecognized claim family '{claim_family}'.")
    if claim_family == "ZOIKO_SERVICE":
        return ForumResolution("A0", "RESOLVED", "")

    policy_field = _FAMILY_TO_POLICY_FIELD.get(claim_family)
    if policy_field is None:
        return ForumResolution(
            None, "LEGAL_REVIEW_REQUIRED",
            f"No MVP forum mapping exists yet for claim family '{claim_family}' -- routed to legal review rather than assumed.",
        )

    if market_policy_pack is not None:
        authority_class = getattr(market_policy_pack, policy_field)
        notes = f"Resolved from jurisdiction '{market_policy_pack.jurisdiction_code}' market policy pack v{market_policy_pack.version}."
        return ForumResolution(
            authority_class, "RESOLVED", notes,
            policy_pack_id=market_policy_pack.id, policy_pack_version=market_policy_pack.version,
        )
    authority_class = _FAMILY_AUTHORITY[claim_family]
    return ForumResolution(authority_class, "RESOLVED", "")


def compute_severity(claim_family: str, claim_code: str, *, safety_flag: bool) -> str:
    if safety_flag or claim_code in _SAFETY_FORCED_CODES:
        return "SEV-0"
    if claim_family in _SEV2_FAMILIES:
        return "SEV-2"
    if claim_family in _SEV3_FAMILIES:
        return "SEV-3"
    return "SEV-1"
