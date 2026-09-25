from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

BILLING_ENTITY_STATUSES = ("ACTIVE", "INACTIVE")


class BillingEntity(Base):
    """ZR-PAY-CFG-001 Decision 5 / Section 7.1: the Billing Entity Registry.
    The legal Zoiko entity that issues a Listing Fee invoice/receipt, with its
    company and tax-registration details. Baseline is Zoiko Realty Group Inc.,
    trading as Zoiko Rooms; any other entity needs commercial/legal approval.
    Registration numbers and addresses are master data entered here by an
    admin -- never hard-coded in code or frontend templates. A Listing Fee
    price can only be activated for a market this entity supports, and each
    quote/receipt snapshots the entity's details at transaction time."""

    __tablename__ = "billing_entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable identifier used in quotes/receipts, e.g. "ZRG_INC".
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    trading_name: Mapped[str] = mapped_column(String(200), default="Zoiko Rooms")
    registered_address: Mapped[str] = mapped_column(String(500), default="")
    company_registration_number: Mapped[str] = mapped_column(String(80), default="")
    # e.g. VAT, GST, EIN, NONE -- whatever the jurisdiction's tax logic needs.
    tax_registration_type: Mapped[str] = mapped_column(String(40), default="")
    tax_registration_number: Mapped[str] = mapped_column(String(80), default="")
    # Market codes (MarketRelease.jurisdiction) and ISO 4217 currencies this
    # entity is approved to bill in. Empty means "none yet", never "all".
    supported_markets: Mapped[list[str]] = mapped_column(JSON, default=list)
    supported_currencies: Mapped[list[str]] = mapped_column(JSON, default=list)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def is_effective(self, as_of: date) -> bool:
        return (
            self.status == "ACTIVE"
            and self.effective_from <= as_of
            and (self.effective_to is None or self.effective_to >= as_of)
        )

    def supports(self, market: str, currency: str) -> bool:
        return market in (self.supported_markets or []) and currency.upper() in [c.upper() for c in (self.supported_currencies or [])]

    def customer_facing_name(self) -> str:
        """ZR-PAY-CFG-001 Section 7: 'Zoiko Rooms -- a trading name of Zoiko
        Realty Group Inc.'"""
        if self.trading_name and self.trading_name != self.legal_name:
            return f"{self.trading_name} — a trading name of {self.legal_name}"
        return self.legal_name
