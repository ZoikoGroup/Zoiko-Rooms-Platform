from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# "pending_step_up" is entered only for a *change* -- a room that already has
# a live verified authority and is getting a new one -- never for a room's
# first-ever declaration (see crud/payment_recipient_authority.py's own
# docstring on declare_payment_recipient_authority for why). It always
# resolves to "pending" (via confirm_payment_recipient_authority_change),
# never directly to "verified" -- the existing admin verify step is
# unchanged, just now step-up-confirmed and risk-flagged first.
PAYMENT_RECIPIENT_AUTHORITY_STATUSES = ("pending_step_up", "pending", "verified", "failed", "revoked")

# ZR-PAY-LINK-003 Section 1.1/2: "'Authority to list' and 'authority to
# receive payments' are separate claims." Same OWNER/AGENT/MANAGER vocabulary
# AuthorityRecord.relationship_type already uses for the list-authority claim
# -- this is the parallel claim for the payment-receipt one, deliberately its
# own table (not a new column/status on AuthorityRecord) so a room's listing
# authority and its payment-receipt authority can never be silently conflated
# or accidentally satisfied by the same verification action. See
# crud/payment_recipient_authority.py's own module docstring for the
# resolver this backs.
PAYMENT_RECIPIENT_RELATIONSHIP_TYPES = ("OWNER", "AGENT", "MANAGER", "OTHER")


class PaymentRecipientAuthority(Base):
    """ZR-PAY-LINK-003 Section 2/Wireframe A.1: 'Create a separate
    PAYMENT_RECEIPT authority claim and verify it before payment details are
    shown.' Mirrors AuthorityRecord's own shape closely (same party_id+
    room_id scoping, same pending/verified/failed/revoked lifecycle, same
    admin-decision pattern) rather than inventing a new one -- see
    crud/property_verification.py's own docstring for the same rationale
    applied to a different claim.

    Exactly one row should be VERIFIED per room at a time in practice (the
    resolver in crud/payment_recipient_authority.py takes the most recently
    verified, unexpired one), but this table does not enforce that with a
    partial unique index the way RentalPaymentInstruction does for its own
    'one ACTIVE per party' rule -- a room legitimately having its recipient
    change over time (Wireframe J) means multiple historical VERIFIED rows
    are expected, not an anomaly."""

    __tablename__ = "payment_recipient_authorities"

    id: Mapped[int] = mapped_column(primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=False, index=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False, index=True)
    relationship_type: Mapped[str] = mapped_column(String(20), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(1024), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verifier_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    # ZR-PAY-LINK-003 Section 14.1 "Step-up authentication is mandatory" --
    # only ever populated for a "pending_step_up" row, same mailed-one-time-
    # code mechanic as models/rental_payment.py:RentalPaymentInstruction's
    # own verification_code_hash/_expires_at/_attempts.
    verification_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_attempts: Mapped[int] = mapped_column(default=0)
    # Section 14.1 "Risk engine may require a hold, second approver or manual
    # review" -- the existing admin verify_payment_recipient_authority step
    # already is that manual review for every row, so this is informational
    # for the admin rather than a second gate (see crud module docstring).
    is_high_risk: Mapped[bool] = mapped_column(default=False)
    high_risk_reason: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    party: Mapped["Party"] = relationship()
    room: Mapped["Room"] = relationship(back_populates="payment_recipient_authorities")
    verifier: Mapped["AdminUser"] = relationship()
