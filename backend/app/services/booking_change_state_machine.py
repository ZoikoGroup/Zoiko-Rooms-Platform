"""ZR-ENG-CLR-008 Section 16: the Booking Change Request state machine.

Single source of truth for BookingChangeRequest.status and which jumps
between statuses are legal, replacing the old flat PENDING/APPROVED/DECLINED
set. That set collapsed two doc-distinct moments -- "awaiting host decision"
and "awaiting the required agreement action" -- into one overloaded
APPROVED, and had no terminal state at all for an approval that was granted
but whose downstream amendment/agreement step then failed (it just stayed
silently APPROVED forever, indistinguishable from a healthy in-flight one).

This module only implements the states that correspond to a real,
already-reachable step in this codebase:
  AWAITING_HOST              -- created; waiting on host/admin decision.
  AWAITING_RENTER            -- host proposed alternative terms (see
                                 crud/booking_change_requests.py:
                                 propose_alternative_terms); waiting on the
                                 renter's fresh consent to those terms. A
                                 material change (different proposed_* field
                                 values) always lands here rather than
                                 AWAITING_AGREEMENT_ACTION, precisely because
                                 AC-16 says a material change invalidates the
                                 renter's prior consent -- their previous
                                 submission never covered these terms.
  AWAITING_AGREEMENT_ACTION  -- the exact currently-proposed terms have been
                                 agreed by both sides; waiting on the
                                 required agreement action (re-signature for
                                 DATE_SHIFT/EXTENSION/SHORTENING/
                                 FINANCIAL_CHANGE, or a fresh agreement
                                 reaching SIGNED for PREMISES_CHANGE).
  EFFECTIVE                  -- the required agreement action completed.
  REJECTED                   -- host declined (renamed from DECLINED to
                                 match the doc's own vocabulary).
  WITHDRAWN                  -- renter withdrew (either their own original
                                 request, or a host's alternative proposal)
                                 before an irreversible step.
  EXPIRED                    -- expires_at passed before a host decision.
  CONFLICT                   -- approval was granted but the downstream
                                 amendment/agreement step then failed with a
                                 diagnosed HTTP error (room-hold conflict,
                                 listing no longer published, ...) --
                                 Section 24 edge cases, e.g. "Extension date
                                 taken while renter is reviewing proposal:
                                 commit fails with CONFLICT; original
                                 booking stays valid".
  FAILED                      -- approval failed for an undiagnosed reason
                                 not covered by CONFLICT.

Reserved but NOT implemented here (no code path in this codebase produces
them, so they are deliberately absent from BOOKING_CHANGE_STATUSES rather
than added as decorative unused values): DRAFT, SUBMITTED, VALIDATING,
AWAITING_OTHER_CONSENT, AWAITING_FINANCIAL_CONFIRMATION, READY_TO_COMMIT,
COMMITTING -- this MVP's create/approve/sign steps are each a single
synchronous transaction with no separate persisted step for these, so they
would always be instantaneous no-ops if added now. ADMIN_REVIEW and the
ROUTED_TO_* outcomes are also reserved: occupant/party changes and
cancellation/termination already have their own dedicated entry points
(crud/sublet.py, crud/occupancy.py) and never enter this BCR flow to begin
with, so nothing here would ever set them."""

from __future__ import annotations

from fastapi import HTTPException, status

BOOKING_CHANGE_STATUSES = (
    "AWAITING_HOST", "AWAITING_RENTER", "AWAITING_AGREEMENT_ACTION", "EFFECTIVE",
    "REJECTED", "WITHDRAWN", "EXPIRED", "CONFLICT", "FAILED",
)

# Every status below has an empty destination set -- none of them is
# reachable from anywhere once entered.
TERMINAL_STATUSES = frozenset({"EFFECTIVE", "REJECTED", "WITHDRAWN", "EXPIRED", "CONFLICT", "FAILED"})

_TRANSITIONS: dict[str, frozenset[str]] = {
    # AWAITING_HOST -> EFFECTIVE directly is DEPOSIT_CHANGE's own path only:
    # approving a deposit-change request never drives this codebase's own
    # amendment/agreement engine (Section 2 -- crud/finance.py -- remains the
    # sole authority on actually moving deposit money, deliberately kept
    # separate rather than giving Section 8 a second way to touch it), so
    # there is no "agreement action" gate to wait through -- the admin's
    # decision, with its mandatory reason, IS the whole recorded gate.
    "AWAITING_HOST": frozenset({"AWAITING_RENTER", "AWAITING_AGREEMENT_ACTION", "EFFECTIVE", "REJECTED", "WITHDRAWN", "EXPIRED", "CONFLICT", "FAILED"}),
    "AWAITING_RENTER": frozenset({"AWAITING_AGREEMENT_ACTION", "WITHDRAWN", "CONFLICT", "FAILED"}),
    "AWAITING_AGREEMENT_ACTION": frozenset({"EFFECTIVE", "WITHDRAWN", "CONFLICT", "FAILED"}),
    "EFFECTIVE": frozenset(),
    "REJECTED": frozenset(),
    "WITHDRAWN": frozenset(),
    "EXPIRED": frozenset(),
    "CONFLICT": frozenset(),
    "FAILED": frozenset(),
}


def transition(bcr, to_status: str, *, note: str | None = None) -> None:
    """Mutates bcr.status in place iff the jump is legal from its current
    status; raises 409 otherwise. Callers still own db.commit()/refresh()
    and any status-specific fields (decided_at, decision_note, ...) -- this
    function only owns the legality of the jump itself, which used to be
    re-derived ad hoc (`if bcr.status != "PENDING": raise ...`) separately
    at every call site with no single place enforcing it consistently."""
    allowed = _TRANSITIONS.get(bcr.status, frozenset())
    if to_status not in allowed:
        detail = f"Cannot move a booking change request from {bcr.status} to {to_status}"
        if note:
            detail += f" ({note})"
        raise HTTPException(status.HTTP_409_CONFLICT, detail)
    bcr.status = to_status
