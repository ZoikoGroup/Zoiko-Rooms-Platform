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

    created_at: datetime
