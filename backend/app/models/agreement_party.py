"""ZR-ENG-CLR-004 Section 13.1 'agreement_party: Legal party, role, authority
and signer relationship' -- a real per-party roster, replacing the two flat
signed_by_provider_at/signed_by_renter_at columns as the source of truth for
*who the contractual parties are* (those two columns remain the source of
truth for *whether they've signed yet*, since that's a timestamp, not an
identity -- see models/leasing.py:Agreement).

Populated once, at create_agreement time (crud/leasing.py), from the same
verified listing/guest data _build_agreement_snapshot already reads --
never Host free text (Section 5.2: 'Fields Host must not control' includes
the legal label and authority of a party)."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

AGREEMENT_PARTY_ROLES = ("provider", "renter", "guarantor", "witness", "notary")
AGREEMENT_PARTY_TYPES = ("individual", "company", "agent")


class AgreementParty(Base):
    __tablename__ = "agreement_parties"

    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_id: Mapped[int] = mapped_column(ForeignKey("agreements.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    party_type: Mapped[str] = mapped_column(String(20), default="individual")
    legal_name: Mapped[str] = mapped_column(String(255), default="")
    contact_email: Mapped[str] = mapped_column(String(255), default="")
    # Section 6.2 'Authority evidence request only when policy requires it' --
    # blank unless this row is an authorized agent/representative signing on
    # behalf of the actual legal party.
    authority_evidence_ref: Mapped[str] = mapped_column(String(255), default="")
    is_signatory: Mapped[bool] = mapped_column(Boolean, default=True)
    # ZR-ENG-CLR-012 Section 15: "Each person is its own Verification
    # Subject with role-scoped requirements and consent... Guarantor
    # screening, identity and agreement execution are separate from renter
    # screening." Nullable -- provider/renter already have their own Party
    # via the offer/listing they came from; this exists so a guarantor (or
    # any other agreement_party without an existing Party) can get one,
    # letting the exact same party_id-scoped IdentityVerification/
    # ScreeningCheck/VerificationCredential machinery apply to them
    # independently, with no guarantor-specific verification code needed.
    party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id"), nullable=True)
    # ZR-ENG-CLR-012 Section 15 consent requirement, recorded independently
    # of the core Agreement signature columns (see crud/agreement_party.py's
    # module docstring for why). Wet-ink is the only method used today --
    # method is still stored, not hardcoded, so a future self-service
    # consent path can populate the same fields.
    consent_method: Mapped[str] = mapped_column(String(30), default="")
    consent_evidence_ref: Mapped[str] = mapped_column(String(500), default="")
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    agreement: Mapped["Agreement"] = relationship(back_populates="parties")
    verification_party: Mapped["Party"] = relationship()
