from datetime import date, datetime, timezone

from pydantic import field_validator

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
    message: str = ""
    desired_move_in: date | None = None

    _validate_desired_move_in = field_validator("desired_move_in")(_reject_past_date)


class ApplicationDecisionRead(CamelModel):
    id: int
    decision: str
    reason_code: str
    note: str
    decided_by_admin_id: int
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


class OfferTermsRead(CamelModel):
    id: int
    version: int
    monthly_rent: float
    deposit_amount: float
    start_date: date
    term_months: int
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


class AmendmentClassifyRequest(CamelModel):
    amendment_type: str


class AmendmentProposeTermsRequest(CamelModel):
    proposed_terms: dict


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


class SubletRequestCreate(CamelModel):
    """User submitting a sublet request."""

    occupancy_id: int
    proposed_renter_party_id: int
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
    proposed_renter_party_id: int
    status: str
    authority_evidence_ref: str
    admin_decision: str
    admin_notes: str
    decided_by_admin_id: int | None
    created_at: datetime
    decided_at: datetime | None

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


class SubletRequestDecision(CamelModel):
    """Optional review notes recorded with a sublet approval or rejection."""

    notes: str = ""
