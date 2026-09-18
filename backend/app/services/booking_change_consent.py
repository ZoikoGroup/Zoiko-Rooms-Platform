"""ZR-ENG-CLR-008 AC-16/AC-17: "Consent records bind to exact proposal
hash/version" and "material proposal change invalidates prior consent".

This MVP's renter-initiated submission IS the renter's consent to the exact
proposal it describes (there is no separate later "confirm terms" step for
them to consent to) -- so the thing worth hashing and re-verifying is that
row's own proposed_* fields, to catch them being mutated between submission
and approval (directly in the DB, or by a future code path) rather than
silently approving different terms than what the renter actually consented
to. This is deliberately over the specific proposed fields only, not the
whole row -- decision_note/status/audit fields change legitimately as the
request moves through its lifecycle and must not affect the hash."""

from __future__ import annotations

import hashlib


def compute_proposal_hash(bcr) -> str:
    """Changing which fields feed this hash changes the hash for every row,
    including ones already sitting in AWAITING_HOST/AWAITING_RENTER under an
    older version of this function -- assert_proposal_unchanged would then
    read them as tampered and block their approval. Safe here only because
    no such rows exist yet each time this list has grown; a real production
    change would need to backfill proposal_hash on in-flight rows first."""
    parts = [
        bcr.change_type,
        bcr.proposed_start_date.isoformat() if bcr.proposed_start_date else "",
        bcr.proposed_end_date.isoformat() if bcr.proposed_end_date else "",
        str(bcr.additional_term_months) if bcr.additional_term_months is not None else "",
        bcr.target_listing_id or "",
        f"{bcr.proposed_monthly_rent:.2f}" if bcr.proposed_monthly_rent is not None else "",
        f"{bcr.proposed_deposit_amount:.2f}" if bcr.proposed_deposit_amount is not None else "",
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def assert_proposal_unchanged(bcr) -> None:
    """Raises if bcr's proposed_* fields no longer match the hash captured
    at submission -- called immediately before approval acts on them."""
    from fastapi import HTTPException, status

    if bcr.proposal_hash and compute_proposal_hash(bcr) != bcr.proposal_hash:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This request's proposed terms changed since the renter submitted it -- the renter's consent no longer "
            "covers the current proposal. Ask them to submit a fresh request.",
        )
