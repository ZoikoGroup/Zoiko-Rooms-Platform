from datetime import date, datetime, timezone

from pydantic import Field, field_validator

from app.schemas.booking import NewGuestInput
from app.schemas.common import CamelModel


def _reject_past_date(value: date | None) -> date | None:
    if value is not None and value < datetime.now(timezone.utc).date():
        raise ValueError("Desired move-in date cannot be in the past")
    return value


class ApplicationCreate(CamelModel):
    listing_id: str
    guest_id: str | None = None
    new_guest: NewGuestInput | None = None
    # ZR-ENG-CLR-001 Rule 6: an existing Guest id for the person who will
    # actually occupy the room, when that's someone other than guest_id/the
    # applicant (see Application.occupant_guest_id). None (the common case)
    # means the applicant is the occupant.
    named_occupant_guest_id: str | None = None
    # Matches models.leasing.Application.message's real String(2000) column --
    # an over-length value here would otherwise crash with a raw 500 at
    # insert time instead of a clean 422.
    message: str = Field(default="", max_length=2000)
    desired_move_in: date | None = None

    _validate_desired_move_in = field_validator("desired_move_in")(_reject_past_date)


class ApplicationDecisionRead(CamelModel):
    id: int
    decision: str
    reason_code: str
    note: str
    decided_by_admin_id: int | None = None
    decided_by_user_id: int | None = None
    decided_at: datetime


class ApplicationDecide(CamelModel):
    decision: str
    reason_code: str = ""
    note: str = ""


class ApplicationUpdate(CamelModel):
    message: str | None = None
    desired_move_in: date | None = None


class OfferAcceptRequest(CamelModel):
    """ZR-ENG-CLR-001 Rule 6/Section 9: only ever needed when the occupant-
    overlap check comes back BLOCK -- otherwise leave blank."""

    override_reason: str = ""


class OfferTermsCreate(CamelModel):
    monthly_rent: float
    deposit_amount: float
    start_date: date
    term_months: int
    # ZR-ENG-CLR-005 AC-06: one of app.models.finance.PAYMENT_SCHEDULE_CADENCES
    # ("MONTHLY", "FORTNIGHTLY", "WEEKLY", "UPFRONT", "CUSTOM"); validated in
    # crud/leasing.py:add_offer_terms.
    cadence: str = "MONTHLY"
    # Required (and only meaningful) when cadence == "CUSTOM": the admin-
    # specified billing interval in days.
    custom_interval_days: int | None = None


class OfferTermsRead(CamelModel):
    id: int
    version: int
    monthly_rent: float
    deposit_amount: float
    currency: str
    start_date: date
    term_months: int
    cadence: str
    custom_interval_days: int | None
    created_at: datetime


class AgreementRead(CamelModel):
    id: int
    offer_id: int
    version: int
    status: str
    content_ref: str
    signed_by_provider_at: datetime | None
    signed_by_renter_at: datetime | None
    signature_ref: str
    payment_session_expires_at: datetime | None = None
    created_at: datetime


class AgreementSign(CamelModel):
    as_party: str  # "provider" | "renter"
    # AC-11: ACKNOWLEDGMENT | SIMPLE_ESIGN | ADVANCED_ESIGN | QUALIFIED_ESIGN |
    # WITNESSED_ESIGN | NOTARIZED -- see crud/leasing.py:METHOD_REQUIRED_EVIDENCE
    # for which evidence_metadata keys each one requires. WET_INK has its own
    # dedicated upload route instead (POST .../sign/wet-ink).
    method: str = "SIMPLE_ESIGN"
    evidence_metadata: dict = {}


class AgreementCreateRequest(CamelModel):
    """ZR-ENG-CLR-004 AC-17: the only optional-term input the create-agreement
    call accepts -- ids only, each validated against the resolved profile's
    own approved optional-clause allow-list (see crud/leasing.py:create_agreement).
    There is no free-text field anywhere on this schema."""

    selected_optional_clause_ids: list[str] = []
    # Wires the previously-unused AgreementParty.party_type="agent"/
    # authority_evidence_ref columns: set when whoever is creating the
    # agreement is not the property's own legal owner/company but an
    # authorized agent signing on its behalf. create_agreement requires a
    # non-blank evidence_ref whenever this is true (see crud/leasing.py).
    signing_as_agent: bool = False
    agent_authority_evidence_ref: str = ""


class OptionalClauseChoiceRead(CamelModel):
    """The Host's resolved, pickable optional-clause set for Screen G --
    the id/title pairs that populate the checkboxes whose selections become
    AgreementCreateRequest.selected_optional_clause_ids."""

    clause_id: str
    title: str


class DisclosureRequirementRead(CamelModel):
    id: int
    agreement_id: int
    disclosure_type: str
    title: str
    required: bool
    status: str
    delivered_at: datetime | None = None
    delivered_to_party: str = "renter"
    delivery_channel: str = "IN_APP"
    acknowledged_at: datetime | None = None
    document_content_hash: str = ""
    created_at: datetime


class SignatureEventRead(CamelModel):
    id: int
    agreement_id: int
    signer_role: str
    signer_identifier: str
    method: str
    document_hash: str
    consented_at: datetime
    created_at: datetime


class ClauseDefinitionRead(CamelModel):
    id: int
    clause_id: str
    jurisdiction_scope: str
    agreement_class: str
    mandatory_level: str
    status: str
    version: int
    effective_from: date | None = None
    effective_to: date | None = None
    title: str
    approval_note: str
    created_at: datetime


class ClauseCopyDefaultsRequest(CamelModel):
    jurisdiction_scope: str


class ClauseDraftCreate(CamelModel):
    clause_id: str
    jurisdiction_scope: str
    agreement_class: str
    mandatory_level: str
    title: str
    approval_note: str = ""


class DisclosureDeliverRequest(CamelModel):
    to_party: str = "renter"
    delivery_channel: str = "IN_APP"


class UserAgreementSignRequest(CamelModel):
    """AC-11: same method/evidence_metadata options as the admin-facing
    AgreementSign, for a renter signing their own agreement."""

    method: str = "SIMPLE_ESIGN"
    evidence_metadata: dict = {}


class AgreementAmendmentRead(CamelModel):
    id: int
    agreement_id: int
    source_version_id: int
    resulting_version_id: int | None = None
    amendment_type: str | None = None
    status: str
    reason: str
    proposed_terms: dict = {}
    proposed_guarantor: dict = {}
    requested_by_admin_id: int
    created_at: datetime
    classified_at: datetime | None = None
    terms_proposed_at: datetime | None = None
    approvals_pending_at: datetime | None = None
    generated_at: datetime | None = None
    execution_pending_at: datetime | None = None
    executed_at: datetime | None = None
    effective_at: datetime | None = None


class AmendmentRequestCreate(CamelModel):
    reason: str = ""


class AgreementLegalHoldRead(CamelModel):
    id: int
    agreement_id: int
    status: str
    reason: str
    authority_evidence_ref: str
    placed_by_admin_id: int
    placed_at: datetime
    released_by_admin_id: int | None = None
    released_at: datetime | None = None


class AgreementLegalHoldCreate(CamelModel):
    reason: str
    authority_evidence_ref: str


class AmendmentClassifyRequest(CamelModel):
    amendment_type: str


class AmendmentProposeTermsRequest(CamelModel):
    proposed_terms: dict


class AmendmentProposeGuarantorRequest(CamelModel):
    legal_name: str
    contact_email: str = ""


class GuarantorConsentRequest(CamelModel):
    evidence_ref: str
    method: str = "WET_INK"


class AgreementPartyRead(CamelModel):
    id: int
    agreement_id: int
    role: str
    legal_name: str
    contact_email: str
    party_id: int | None = None
    consent_method: str = ""
    consent_evidence_ref: str = ""
    consented_at: datetime | None = None


class SignatureRequestRead(CamelModel):
    id: int
    agreement_id: int
    agreement_version_id: int
    party_role: str
    method: str
    status: str
    deadline: datetime | None = None
    provider_transaction_id: str = ""
    completed_at: datetime | None = None
    created_at: datetime


class SignatureProviderCallbackRequest(CamelModel):
    provider_event_id: str
    provider_transaction_id: str
    event_type: str


class SignatureProviderStatusRead(CamelModel):
    healthy: bool
    updated_at: datetime


class SetSignatureProviderHealthRequest(CamelModel):
    healthy: bool


class ClauseTranslationRead(CamelModel):
    id: int
    clause_definition_id: int
    language_code: str
    translated_title: str
    translated_content: str
    status: str
    created_at: datetime


class ClauseTranslationCreate(CamelModel):
    language_code: str
    translated_title: str
    translated_content: str


class MissingTranslationRead(CamelModel):
    clause_definition_id: int
    clause_id: str
    version: int
    title: str


class AgreementFormTemplateRead(CamelModel):
    id: int
    jurisdiction_scope: str
    agreement_class: str
    form_mode: str
    version: int
    status: str
    effective_from: date | None = None
    effective_to: date | None = None
    title: str
    source_document_content_hash: str = ""
    field_anchor_map: dict = {}
    authoritative_content_text: str = ""
    approval_note: str = ""
    created_at: datetime


class HostReadinessRead(CamelModel):
    """ZR-ENG-CLR-004 Section 5.3 host template completion states."""

    state: str
    missing_facts: list[str] = []


class OfferRead(CamelModel):
    id: int
    application_id: int
    listing_id: str
    guest_id: str
    status: str
    current_version: int
    created_at: datetime
    # ZR-ENG-CLR-001 Rule 7 (10.1): a renter-facing countdown must derive
    # from this server timestamp directly -- never compute it client-side
    # from accepted_at plus a hardcoded duration.
    accepted_at: datetime | None = None
    confirmation_expires_at: datetime | None = None
    # ZR-ENG-CLR-001 Rule 6/Section 9: set at acceptance by services/overlap.py.
    occupant_risk_tier: str = "NONE"
    occupant_risk_reason: str = ""
    terms: list[OfferTermsRead] = []
    agreement: AgreementRead | None = None
    guest_has_account: bool = False


class ApplicationRead(CamelModel):
    id: int
    listing_id: str
    listing_name: str = ""
    guest_id: str
    guest_name: str
    guest_email: str
    # The renter's Party id -- needed by admins to open an Occupancy
    # Eligibility check (Trust & Safety) or an Identity Verification lookup
    # for this applicant. None for a legacy walk-in Guest with no linked
    # self-service UserAccount (see models/guest.py's own user_account_id
    # docstring).
    guest_party_id: int | None = None
    named_occupant_guest_id: str | None = None
    status: str
    message: str
    desired_move_in: date | None
    submitted_at: datetime
    updated_at: datetime
    decisions: list[ApplicationDecisionRead] = []
    offer: OfferRead | None = None


class UserApplicationSubmitRequest(CamelModel):
    """User submitting a rental application."""

    listing_id: str
    message: str = ""
    desired_move_in: date | None = None
    named_occupant_guest_id: str | None = None

    _validate_desired_move_in = field_validator("desired_move_in")(_reject_past_date)


class UserApplicationRead(CamelModel):
    """User-facing application view.

    offer_status/agreement_status let the applicant actually track progress
    past "DECIDED" -- that status alone never changes again for the rest of
    the lifecycle, so without these the applicant has no way to tell an
    approved-but-nothing-yet-sent application apart from a signed, moved-in one."""

    id: int
    listing_id: str
    listing_name: str = ""
    property_address: str = ""
    property_city: str = ""
    host_name: str = ""
    status: str
    message: str
    desired_move_in: date | None
    submitted_at: datetime
    updated_at: datetime
    offer_id: int | None = None
    offer_status: str | None = None
    agreement_id: int | None = None
    agreement_status: str | None = None


class UserOccupancyRead(CamelModel):
    """User-facing occupancy/rental view."""

    id: int
    listing_id: str
    listing_name: str = ""
    room_id: int
    property_address: str = ""
    property_city: str = ""
    host_name: str = ""
    status: str
    move_in_date: date | None
    expected_end_date: date | None
    move_out_date: date | None
    created_at: datetime
    ended_at: datetime | None
    currency: str = "USD"
    # ZR-ENG-CLR-008 Section 8: needed so the renter's own rentals view can
    # link to /agreements/{id}/extension-requests etc. -- occupancy itself
    # has no direct reference to it.
    agreement_id: int | None = None
    # ZR-SUB-003 Section 3: mirrors OccupancyRead's own field -- an occupancy
    # already reassigned once via ASSIGNMENT_FULL/REPLACEMENT_OCCUPANT can't
    # be sublet onward again (crud/sublet.py:_assert_sublet_permitted already
    # enforces this server-side); the renter's own "Request to sublet" button
    # needs this to know not to offer an action that will just 409.
    reassigned_via_sublet_request_id: int | None = None


class SubletRequestCreate(CamelModel):
    """User submitting a sublet request."""

    occupancy_id: int
    proposed_renter_party_id: int | None
    # Defaults to the platform's existing behavior (a full occupant swap) so the
    # already-shipped frontend "Request to sublet" flow keeps working unchanged --
    # this field is additive for callers (like our own tests/API clients) that
    # want to specify REPLACEMENT_OCCUPANT explicitly.
    arrangement_type: str = "ASSIGNMENT_FULL"
    authority_evidence_ref: str = ""
    # Only meaningful for SUBLEASE_PARTIAL/ADD_CO_TENANT (the co-tenant's own new
    # agreement). Omitted -> mirrors the existing tenant's rent unchanged. When
    # provided, validated against the market pack's resolved cap
    # (ZR-ENG-CLR-003 Rule 4.4 / Section 6).
    proposed_monthly_rent: float | None = None
    # ZR-SUB-003 Section 3 Step 2 Wireframe B: "Proposed start date / Proposed
    # end date." Both optional (never in the original build) -- when given,
    # crud/sublet.py:submit_sublet_request validates start<end and both fall
    # within the current occupancy's own remaining lease window.
    proposed_start_date: date | None = None
    proposed_end_date: date | None = None
    # ZR-SUB-003 Section 3 Step 2 Wireframe B: the tenant's own stated reason.
    reason: str = ""
    # ZR-SUB-003 Section 12: "Creates draft; idempotency key required." A
    # retry with the same key returns the already-created row instead of a
    # duplicate -- see models/sublet_request.py's own field docstring.
    # Optional here (not "required") so this stays additive for existing
    # callers; a blank key just skips the replay-detection lookup.
    idempotency_key: str = ""


class SubletRenterLookup(CamelModel):
    """Result of resolving a proposed renter's email to a party, so the sublet
    form never requires the current tenant to already know a raw party ID."""

    found: bool
    party_id: int | None = None
    name: str | None = None
    identity_verified: bool = False


class SubletRequestRead(CamelModel):
    """Read view for sublet request.

    listing_*/current_tenant_name/proposed_renter_name are reviewer context --
    the raw IDs above are meaningless to whoever has to approve or reject this
    without knowing what room and which people are actually involved."""

    id: int
    current_occupancy_id: int
    # ZR-SUB-003 Section 3 Step 1: a DRAFT "carries no proposed occupant yet"
    # -- crud/sublet.py:create_draft_sublet_request sets this to None.
    proposed_renter_party_id: int | None
    status: str
    authority_evidence_ref: str
    admin_decision: str
    admin_notes: str
    decided_by_admin_id: int | None
    decided_by_user_id: int | None = None
    created_at: datetime
    decided_at: datetime | None
    info_request_note: str = ""
    info_requested_at: datetime | None = None
    info_response_note: str = ""
    info_responded_at: datetime | None = None
    info_requested_document_types: list[str] = []
    info_request_due_at: datetime | None = None
    proposed_start_date: date | None = None
    proposed_end_date: date | None = None
    approval_conditions: str = ""
    approval_condition_list: list[str] = []
    approved_with_authority_confirmation: bool = False
    approval_expires_at: datetime | None = None
    withdrawn_at: datetime | None = None
    reason: str = ""

    arrangement_type: str = "ASSIGNMENT_FULL"
    requested_by_guest_id: str | None = None
    original_renter_liability: str = "ACTIVE"
    new_occupant_liability: str = "NONE"
    deposit_disposition: str = ""
    policy_snapshot: dict = {}
    new_agreement_id: int | None = None
    payee_model: str = ""

    listing_name: str = ""
    listing_city: str = ""
    room_type: str = ""
    guests: int = 0
    bedrooms: int = 0
    bathrooms: int = 0
    current_tenant_name: str = ""
    proposed_renter_name: str = ""

    version: int = 1
    decline_reason_code: str = ""
    superseded_by_sublet_request_id: int | None = None
    expired_at: datetime | None = None
    cancelled_by_authority_at: datetime | None = None
    cancelled_by_authority_admin_id: int | None = None
    cancelled_by_authority_reason: str = ""


class SubletRequestDecision(CamelModel):
    """Optional review notes recorded with a sublet approval, decline, or
    request-for-more-information -- fields only meaningful for one of those
    three are simply ignored by the other two.
    conditions/condition_list/expires_at/authority_confirmed: approval only
    (ZR-SUB-003 Section 5.2). decline_reason_code: decline only (Section
    5.3 FAIRNESS CONTROL) -- must be one of models.sublet_request.
    SUBLET_DECLINE_REASON_CODES. requested_document_types/due_at: request-
    info only (Section 5.1 Wireframe G)."""

    notes: str = ""
    conditions: str = ""
    condition_list: list[str] = []
    expires_at: datetime | None = None
    # ZR-SUB-003 Section 5.2 Wireframe H: "[ ] I confirm I am authorized to
    # make this decision for this rental." crud/sublet.py:approve_sublet_request
    # requires this true.
    authority_confirmed: bool = False
    decline_reason_code: str = ""
    requested_document_types: list[str] = []
    due_at: datetime | None = None
    # ZR-SUB-003 Section 10: "Apply step-up authentication to approval/
    # decline actions where risk signals require it." This build has no
    # risk-scoring system, so the concrete, real "risk signal" used is the
    # existing REPLACING_ARRANGEMENT_TYPES distinction (ASSIGNMENT_FULL/
    # REPLACEMENT_OCCUPANT) -- approving one of those irreversibly hands the
    # whole tenancy to a new occupant, unlike a co-tenancy/additional-
    # occupant approval. Required (re-verified against the deciding actor's
    # own account password) only for that approval; ignored everywhere else.
    step_up_password: str = ""


class SubletDecisionAuthorityCancel(CamelModel):
    """ZR-SUB-003 Section 6 CANCELLED_BY_AUTHORITY -- a Super Admin's own
    record-level correction, never an automatic reversal (see the model's
    own field docstring)."""

    reason: str


class SubletSupersede(CamelModel):
    new_sublet_request_id: int


class SubletTerminologyRead(CamelModel):
    """ZR-SUB-003 Section 8: sublet.uiTerm -- 'Localized user-facing term:
    sublet, sublease or approved equivalent.' Resolved from the occupancy's
    own jurisdiction before the create flow renders, so the wizard can show
    the jurisdiction-correct word instead of a hardcoded "sublet"."""

    ui_term: str


class SubletChronologyEvent(CamelModel):
    """ZR-SUB-003 Section 12: 'GET /{id}/audit: Privileged audit view; not
    ordinary user endpoint.' Reconstructed from the request's own timestamp
    fields -- same approach as DisputeChronologyEvent
    (services/dispute_case_export.py), not a separate audit-event table."""

    timestamp: datetime
    event_type: str
    summary: str


class BookingChangeRequestCreate(CamelModel):
    """ZR-ENG-CLR-008 Section 8 MVP: renter requesting a new move-in date on
    their own signed, not-yet-moved-into agreement."""

    proposed_start_date: date
    reason: str = ""


class TermShiftRequestCreate(CamelModel):
    """ZR-ENG-CLR-008 Section 4: 'TERM_SHIFT | Both start and end move.'
    new_term_months is the new term's absolute length, not a delta -- see
    models/booking_change_request.py."""

    proposed_start_date: date
    new_term_months: int
    reason: str = ""

    @field_validator("new_term_months")
    @classmethod
    def _validate_new_term_months(cls, value: int) -> int:
        if value < 1:
            raise ValueError("newTermMonths must be at least 1")
        return value


class ExtensionRequestCreate(CamelModel):
    """ZR-ENG-CLR-008 Section 8 MVP: renter requesting to extend their stay
    on an active occupancy. Whole additional months, not an arbitrary end
    date -- term length is the actual contractual unit (OfferTerms.term_months),
    so this keeps the resulting agreement amendment exact rather than
    reverse-engineering a month count from an arbitrary date."""

    additional_term_months: int
    reason: str = ""

    @field_validator("additional_term_months")
    @classmethod
    def _validate_additional_term_months(cls, value: int) -> int:
        if value < 1:
            raise ValueError("additionalTermMonths must be at least 1")
        return value


class ShorteningRequestCreate(CamelModel):
    """ZR-ENG-CLR-008 Section 8 MVP/AC-10: renter requesting to shorten their
    stay -- only accepted before move-in (see crud/booking_change_requests.py:
    request_shortening). After move-in this routes to Section 6 termination
    instead, not this endpoint."""

    reduced_term_months: int
    reason: str = ""

    @field_validator("reduced_term_months")
    @classmethod
    def _validate_reduced_term_months(cls, value: int) -> int:
        if value < 1:
            raise ValueError("reducedTermMonths must be at least 1")
        return value


class PremisesChangeRequestCreate(CamelModel):
    """ZR-ENG-CLR-008 Section 14 MVP: renter requesting to move to a
    different published listing. Unlike the other three change types this
    never amends the current agreement -- approval opens a fresh
    Application/Offer/Agreement on the target listing instead (see
    crud/booking_change_requests.py:request_premises_change)."""

    target_listing_id: str
    reason: str = ""


class FinancialChangeRequestCreate(CamelModel):
    """ZR-ENG-CLR-008 Section 10/AC-24: renter requesting a new monthly rent
    on their signed agreement -- available before or after move-in. Gated by
    MarketPolicyPack.rent_change_min_interval_days (see
    crud/booking_change_requests.py:request_financial_change).

    proposed_deposit_amount optionally bundles a deposit top-up with the
    rent change -- unlike DepositChangeRequestCreate's own deposit-only,
    discuss-only path (Section 2 stays the sole authority on money moved
    outside an agreement's own obligations), a top-up bundled here flows
    through the real amendment engine like any other agreement term change,
    and generates an actual DEPOSIT Obligation for the difference once
    effective (see crud/leasing.py:_generate_deposit_topup_obligation)."""

    proposed_monthly_rent: float
    proposed_deposit_amount: float | None = None
    reason: str = ""

    @field_validator("proposed_monthly_rent")
    @classmethod
    def _validate_proposed_monthly_rent(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("proposedMonthlyRent must be greater than zero")
        return value

    @field_validator("proposed_deposit_amount")
    @classmethod
    def _validate_proposed_deposit_amount(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("proposedDepositAmount cannot be negative")
        return value


class BookingChangeRequestRead(CamelModel):
    id: int
    agreement_id: int
    requested_by_guest_id: str
    change_type: str
    status: str
    original_start_date: date
    proposed_start_date: date
    original_end_date: date | None = None
    proposed_end_date: date | None = None
    additional_term_months: int | None = None
    target_listing_id: str | None = None
    resulting_application_id: int | None = None
    original_monthly_rent: float | None = None
    proposed_monthly_rent: float | None = None
    original_deposit_amount: float | None = None
    proposed_deposit_amount: float | None = None
    currency: str = "USD"
    reason: str
    decision_note: str
    decided_by_admin_id: int | None = None
    decided_at: datetime | None = None
    resulting_amendment_id: int | None = None
    created_at: datetime
    expires_at: datetime
    authority_evidence_ref: str = ""

    listing_name: str = ""
    target_listing_name: str = ""
    guest_name: str = ""


class BookingChangeDecisionRequest(CamelModel):
    """Optional note recorded with a host/admin approval or decline."""

    decision_note: str = ""


class DepositChangeRequestCreate(CamelModel):
    """ZR-ENG-CLR-008 Section 4/11/DEPOSIT_CHANGE: request/decide only --
    see crud/booking_change_requests.py:request_deposit_change for why
    approval never itself moves deposit money."""

    proposed_deposit_amount: float
    reason: str = ""

    @field_validator("proposed_deposit_amount")
    @classmethod
    def _validate_proposed_deposit_amount(cls, value: float) -> float:
        if value < 0:
            raise ValueError("proposedDepositAmount cannot be negative")
        return value


class LegalOrderChangeCreate(CamelModel):
    """ZR-ENG-CLR-008 Section 4/LEGAL_ORDER_CHANGE: admin-initiated amendment
    driven by a court/regulator/statutory order. At least one of the three
    proposed fields must be given; authority_evidence_ref is mandatory."""

    proposed_start_date: date | None = None
    new_term_months: int | None = None
    proposed_monthly_rent: float | None = None
    authority_evidence_ref: str
    reason: str = ""


class BookingChangeAdminCorrectionCreate(CamelModel):
    """ZR-ENG-CLR-008 Section 9/ADMIN_CORRECTION: a narrowly-scoped,
    fully-audited fix to this BCR's own free-text fields only -- see
    crud/booking_change_requests.py:correct_change_request_metadata for why
    material fields (dates, term, rent, target listing, status) are never
    reachable through this endpoint."""

    corrected_reason: str | None = None
    corrected_decision_note: str | None = None
    evidence_ref: str
    correction_note: str = ""


class BookingChangeAlternativeProposalCreate(CamelModel):
    """ZR-ENG-CLR-008 Section 18: a host counter-proposal against a pending
    request -- exactly one of these fields applies, depending on the BCR's
    change_type (crud/booking_change_requests.py:propose_alternative_terms
    validates which). Not offered for PREMISES_CHANGE."""

    proposed_start_date: date | None = None
    additional_term_months: int | None = None
    proposed_monthly_rent: float | None = None
    decision_note: str = ""
