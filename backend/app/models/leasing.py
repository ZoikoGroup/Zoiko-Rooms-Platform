from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

APPLICATION_STATUSES = ("SUBMITTED", "WITHDRAWN", "DECIDED")
APPLICATION_DECISIONS = ("APPROVED", "REJECTED")
OFFER_STATUSES = ("DRAFT", "SENT", "ACCEPTED", "DECLINED", "EXPIRED", "WITHDRAWN")
AGREEMENT_STATUSES = ("DRAFT", "SENT", "SIGNED", "VOID")


class Application(Base):
    """A renter's application to a listing. Submission alone never creates a rent
    obligation -- that only happens once an Offer is accepted and an Agreement is
    signed, several stages later."""

    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), nullable=False)
    guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="SUBMITTED")
    message: Mapped[str] = mapped_column(String(2000), default="")
    desired_move_in: Mapped[date | None] = mapped_column(Date, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    listing: Mapped["Listing"] = relationship()
    guest: Mapped["Guest"] = relationship()
    decisions: Mapped[list["ApplicationDecision"]] = relationship(back_populates="application", cascade="all, delete-orphan")
    offer: Mapped["Offer"] = relationship(back_populates="application", uselist=False)


class ApplicationDecision(Base):
    """Append-only decision log -- a reversal or override is a new row, never an
    overwrite of a prior decision."""

    __tablename__ = "application_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(50), default="")
    note: Mapped[str] = mapped_column(String(2000), default="")
    decided_by_admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    application: Mapped["Application"] = relationship(back_populates="decisions")
    decided_by: Mapped["AdminUser"] = relationship()


class Offer(Base):
    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), unique=True, nullable=False)
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), nullable=False)
    guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # ZR-ENG-CLR-001 Rule 7: set together the moment the offer becomes
    # ACCEPTED (see crud/leasing.py:_accept_offer_and_hold_room). Both stay
    # None for an offer that never reached ACCEPTED. The renter-facing
    # countdown must read confirmation_expires_at directly, never derive it
    # client-side from accepted_at + a hardcoded duration (10.1: "User-facing
    # countdown must derive from the server-side expiry timestamp").
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    application: Mapped["Application"] = relationship(back_populates="offer")
    listing: Mapped["Listing"] = relationship()
    guest: Mapped["Guest"] = relationship()
    terms: Mapped[list["OfferTerms"]] = relationship(back_populates="offer", cascade="all, delete-orphan", order_by="OfferTerms.version")
    agreement: Mapped["Agreement"] = relationship(back_populates="offer", uselist=False)


class OfferTerms(Base):
    """Append-only versioned terms -- a new negotiation round adds a new version row,
    the previous one is never edited in place."""

    __tablename__ = "offer_terms"

    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_rent: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    deposit_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    term_months: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    offer: Mapped["Offer"] = relationship(back_populates="terms")


class Agreement(Base):
    __tablename__ = "agreements"

    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id", ondelete="CASCADE"), unique=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    content_ref: Mapped[str] = mapped_column(String(1024), default="")
    signed_by_provider_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_by_renter_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signature_ref: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    offer: Mapped["Offer"] = relationship(back_populates="agreement")
    obligations: Mapped[list["Obligation"]] = relationship(back_populates="agreement")
