from datetime import date, datetime

from pydantic import field_validator

from app.models.market_policy import DEPOSIT_INSTRUMENT_ALLOWED_VALUES
from app.schemas.common import CamelModel


def _validate_deposit_instrument_allowed(value: str | None) -> str | None:
    if value is not None and value not in DEPOSIT_INSTRUMENT_ALLOWED_VALUES:
        raise ValueError(f"depositInstrumentAllowed must be one of {DEPOSIT_INSTRUMENT_ALLOWED_VALUES}")
    return value


class MarketPolicyPackCreate(CamelModel):
    jurisdiction_code: str
    effective_from: date
    effective_to: date | None = None
    confidence: str = "REVIEW_REQUIRED"
    legal_source_note: str = ""

    deposit_instrument_allowed: str = "OPTIONAL"
    deposit_max_rent_multiple: float = 3.0
    deposit_custody_model: str = "HOST_OR_AGENT"
    deposit_protection_deadline_days: int | None = None
    deposit_release_deadline_days: int = 30
    advance_rent_max_months: int = 12

    sublet_consent_standard: str = "STATUTORY_RESPONSE_DEADLINE"
    sublet_consent_response_days: int = 14
    sublet_max_rent_multiple_of_original: float = 1.0
    sublet_assignment_payee_model: str = "HOST_OR_LANDLORD_PAYEE"
    sublet_sublease_payee_model: str = "ORIGINAL_RENTER_PAYEE"

    rent_change_min_interval_days: int = 365

    occupancy_eligibility_required: bool = False
    occupancy_eligibility_method_note: str = ""
    occupancy_eligibility_follow_up_days: int | None = None
    identity_evidence_retention_days: int = 90
    required_property_compliance_codes: list[str] = []
    identity_required_at_application: bool = False
    screening_prohibited_check_types: list[str] = []

    platform_fee_rate: float = 0.10
    funds_flow_profile: str = "DIRECT_SETTLEMENT"
    permitted_payment_method_classes: list[str] = []
    zoiko_legal_entity_name: str = "Zoiko Realty Group"
    zoiko_tax_registration_number: str = ""
    service_fee_tax_rate: float = 0.0

    termination_notice_days: int = 30
    align_termination_to_rent_cycle: bool = False
    termination_liability_model: str = "NOTICE_RENT"
    termination_break_fee_rent_multiple: float = 0.0
    termination_liability_cap_rent_multiple: float | None = None

    dispute_deposit_authority_class: str = "A2"
    dispute_booking_agreement_authority_class: str = "A1"
    dispute_property_condition_authority_class: str = "A1"
    dispute_sublet_occupancy_authority_class: str = "A1"
    dispute_response_window_days: int = 5
    dispute_evidence_window_days: int = 14
    dispute_external_filing_deadline_days: int | None = None
    dispute_conciliation_requirement: str = "NOT_REQUIRED"
    dispute_non_waivable_claim_families: list[str] = []

    _validate_deposit_instrument_allowed = field_validator("deposit_instrument_allowed")(_validate_deposit_instrument_allowed)


class MarketPolicyPackUpdate(CamelModel):
    """Every field optional -- only what's provided gets updated. Editing an
    existing pack in place (not creating a new version) is intentional here:
    version bumps are a deliberate separate action (create a new pack row
    with version+1), matching how every other jurisdiction-aware domain in
    this codebase already treats MarketPolicyPack as append-only-by-version."""

    effective_to: date | None = None
    confidence: str | None = None
    legal_source_note: str | None = None

    deposit_instrument_allowed: str | None = None
    deposit_max_rent_multiple: float | None = None
    deposit_custody_model: str | None = None
    deposit_protection_deadline_days: int | None = None
    deposit_release_deadline_days: int | None = None
    advance_rent_max_months: int | None = None

    sublet_consent_standard: str | None = None
    sublet_consent_response_days: int | None = None
    sublet_max_rent_multiple_of_original: float | None = None
    sublet_assignment_payee_model: str | None = None
    sublet_sublease_payee_model: str | None = None

    rent_change_min_interval_days: int | None = None

    occupancy_eligibility_required: bool | None = None
    occupancy_eligibility_method_note: str | None = None
    occupancy_eligibility_follow_up_days: int | None = None
    identity_evidence_retention_days: int | None = None
    required_property_compliance_codes: list[str] | None = None
    identity_required_at_application: bool | None = None
    screening_prohibited_check_types: list[str] | None = None

    platform_fee_rate: float | None = None
    funds_flow_profile: str | None = None
    permitted_payment_method_classes: list[str] | None = None
    zoiko_legal_entity_name: str | None = None
    zoiko_tax_registration_number: str | None = None
    service_fee_tax_rate: float | None = None

    termination_notice_days: int | None = None
    align_termination_to_rent_cycle: bool | None = None
    termination_liability_model: str | None = None
    termination_break_fee_rent_multiple: float | None = None
    termination_liability_cap_rent_multiple: float | None = None

    dispute_deposit_authority_class: str | None = None
    dispute_booking_agreement_authority_class: str | None = None
    dispute_property_condition_authority_class: str | None = None
    dispute_sublet_occupancy_authority_class: str | None = None
    dispute_response_window_days: int | None = None
    dispute_evidence_window_days: int | None = None
    dispute_external_filing_deadline_days: int | None = None
    dispute_conciliation_requirement: str | None = None
    dispute_non_waivable_claim_families: list[str] | None = None

    _validate_deposit_instrument_allowed = field_validator("deposit_instrument_allowed")(_validate_deposit_instrument_allowed)


class MarketPolicyPackRead(CamelModel):
    id: int
    jurisdiction_code: str
    version: int
    effective_from: date
    effective_to: date | None = None
    confidence: str
    legal_source_note: str

    deposit_instrument_allowed: str
    deposit_max_rent_multiple: float
    deposit_custody_model: str
    deposit_protection_deadline_days: int | None = None
    deposit_release_deadline_days: int
    advance_rent_max_months: int

    sublet_consent_standard: str
    sublet_consent_response_days: int
    sublet_max_rent_multiple_of_original: float
    sublet_assignment_payee_model: str
    sublet_sublease_payee_model: str

    rent_change_min_interval_days: int

    occupancy_eligibility_required: bool
    occupancy_eligibility_method_note: str
    occupancy_eligibility_follow_up_days: int | None = None
    identity_evidence_retention_days: int
    required_property_compliance_codes: list[str]
    identity_required_at_application: bool
    screening_prohibited_check_types: list[str]

    platform_fee_rate: float
    funds_flow_profile: str
    permitted_payment_method_classes: list[str]
    zoiko_legal_entity_name: str
    zoiko_tax_registration_number: str
    service_fee_tax_rate: float

    termination_notice_days: int
    align_termination_to_rent_cycle: bool
    termination_liability_model: str
    termination_break_fee_rent_multiple: float
    termination_liability_cap_rent_multiple: float | None = None

    dispute_deposit_authority_class: str
    dispute_booking_agreement_authority_class: str
    dispute_property_condition_authority_class: str
    dispute_sublet_occupancy_authority_class: str
    dispute_response_window_days: int
    dispute_evidence_window_days: int
    dispute_external_filing_deadline_days: int | None = None
    dispute_conciliation_requirement: str
    dispute_non_waivable_claim_families: list[str]

    created_at: datetime
