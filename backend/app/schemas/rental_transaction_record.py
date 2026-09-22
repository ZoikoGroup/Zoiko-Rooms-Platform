"""Rental Transaction Record wireframe: a computed, read-only composite over
existing authoritative records (Occupancy is the root -- see
crud/rental_transaction_record.py:build_rental_transaction_record). Every
field here is read from, never a duplicate of, its own authoritative table
(Application/Offer/Agreement, Obligation/SimulatedPayment/DepositRecord,
Occupancy's own handover/activation/termination rows, SubletRequest,
verification). Nothing on this schema is persisted anywhere.

Reuses existing response shapes wherever one already exists (ApplicationRead
already nests Offer/Agreement/OfferTerms/Decisions; PaymentTimelineEntryRead's
shape is mirrored, not reused directly, since this timeline merges different
resource types than crud/finance.py:get_payment_timeline's single-payment
one) rather than redeclaring their fields."""

from datetime import datetime

from app.schemas.common import CamelModel
from app.schemas.finance import DepositRecordRead, ObligationRead, SimulatedPaymentRead
from app.schemas.leasing import AgreementAmendmentRead, ApplicationRead, SubletRequestRead
from app.schemas.activation_gate import ActivationDecisionRead, HandoverEventRead
from app.schemas.occupancy import OccupancyRead, TerminationRecordRead
from app.schemas.rental_payment import RentalPaymentObligationRead
from app.schemas.termination import TerminationCaseRead
from app.schemas.verification import RenterVerificationStatusItem


class RentalTransactionTimelineEntryRead(CamelModel):
    """Same shape as crud/finance.py:get_payment_timeline's own entries --
    one merged, chronological view built from this rental's own
    DomainEvent rows, instead of the caller re-deriving order from several
    separate lists."""

    timestamp: datetime
    source: str
    event_type: str
    detail: dict


class RentalTransactionRecordRead(CamelModel):
    """See build_rental_transaction_record for exactly which verification
    fields are populated for a renter vs. a host viewer -- identity is never
    shown to a host, matching schemas/verification.py's own sharing_scope
    facts."""

    # --- Rental / occupancy summary ---
    occupancy: OccupancyRead

    # --- Application / Offer / Agreement (Offer and Agreement, when they
    # exist, are already nested inside ApplicationRead -- see
    # schemas/leasing.py) ---
    application: ApplicationRead | None = None
    amendments: list[AgreementAmendmentRead] = []

    # --- Payments / financial history ---
    obligations: list[ObligationRead] = []
    payments: list[SimulatedPaymentRead] = []
    deposit: DepositRecordRead | None = None
    # ZR-PAY-002's own record/evidence-layer obligations (models/rental_payment.py)
    # -- kept alongside `obligations` above rather than replacing it; the two
    # domains deliberately coexist (see RentalPaymentObligation's own model
    # docstring). Never money-moving -- declarations/confirmations/disputes
    # only, each record nesting its own disputes/corrections.
    rental_payment_obligations: list[RentalPaymentObligationRead] = []

    # --- Move-in / handover ---
    handover_events: list[HandoverEventRead] = []
    activation_decisions: list[ActivationDecisionRead] = []

    # --- Sublet activity ---
    sublet_requests: list[SubletRequestRead] = []

    # --- Move-out / termination ---
    termination_cases: list[TerminationCaseRead] = []
    termination_record: TerminationRecordRead | None = None

    # --- Permitted verification status ---
    property_verification: RenterVerificationStatusItem | None = None
    authority_to_list: RenterVerificationStatusItem | None = None
    identity_verification: RenterVerificationStatusItem | None = None

    # --- Chronological timeline ---
    timeline: list[RentalTransactionTimelineEntryRead] = []
