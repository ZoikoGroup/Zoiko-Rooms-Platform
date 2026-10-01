"""ZR-IDENTITY-001 API shapes. Server-authoritative status only -- never raw
provider payloads, full document numbers, dates of birth outside the
person's own profile, or internal scores (Sections 8.3, 9.2)."""

from datetime import date, datetime

from app.schemas.common import CamelModel


class IdentityPackRead(CamelModel):
    # UPLOAD (document uploaded to Zoiko) or PROVIDER_HOSTED (captured inside
    # the provider's own document + selfie flow, e.g. Veriff).
    capture_mode: str = "UPLOAD"
    provider_available: bool = True
    selfie_check: bool = False
    country_code: str
    country_name: str
    version: int
    accepted_document_types: list[str]
    available_methods: list[str]
    date_of_birth_required: bool
    minimum_age: int | None = None
    biometric_consent_text: str = ""
    privacy_notice_text: str = ""


class IdentityCountryRead(CamelModel):
    country_code: str
    country_name: str


class IdentityDashboardRead(CamelModel):
    state: str
    header: str
    primary_action: str | None = None
    attention: bool
    message: str = ""
    actions: list[str] = []


class IdentitySessionRead(CamelModel):
    id: int
    state: str
    method: str
    role_context: str = ""
    document_type: str = ""
    masked_document_number: str = ""
    has_document: bool
    document_original_name: str = ""
    attested: bool
    reason_codes: list[str] = []
    message: str = ""
    actions: list[str] = []
    escalated: bool = False
    # Hosted capture: whether the person can (re)open the provider flow now,
    # and the URL to do so (only on submit / launch responses).
    launch_available: bool = False
    launch_url: str | None = None
    can_restart: bool = False
    created_at: datetime
    submitted_at: datetime | None = None
    decided_at: datetime | None = None


class IdentityProfileRead(CamelModel):
    state: str
    assurance_level: str
    given_name: str
    middle_names: str
    family_name: str
    date_of_birth: date | None = None
    country_code: str
    verified_legal_name: str = ""
    verified_at: datetime | None = None
    reverification_required_at: datetime | None = None
    verification_method: str = ""
    dashboard: IdentityDashboardRead
    pack: IdentityPackRead
    current_session: IdentitySessionRead | None = None


class IdentityDetailsUpdate(CamelModel):
    given_name: str
    middle_names: str = ""
    family_name: str
    date_of_birth: date | None = None
    country_code: str
    # Section 9.3 step-up: required to change a verified legal name / date of birth.
    current_password: str = ""


class IdentitySessionCreate(CamelModel):
    method: str = "DOCUMENT"
    role_context: str = ""


class IdentitySubmit(CamelModel):
    attested: bool = False


class IdentityAlternativeRequest(CamelModel):
    reason_code: str
    note: str = ""


class IdentityHandoffRead(CamelModel):
    token: str
    expires_in_seconds: int


class IdentityHandoffClaim(CamelModel):
    token: str


class IdentityGateUpdate(CamelModel):
    confirmed: bool
    note: str = ""
    evidence_reference: str = ""


class IdentityReasonMappingUpsert(CamelModel):
    provider_code: str = "veriff"
    provider_decision: str
    provider_reason_code: str
    zoiko_reason_code: str
    description: str = ""


class IdentityErasureRequest(CamelModel):
    reason: str


class IdentityPackUpdate(CamelModel):
    country_name: str | None = None
    accepted_document_types: list[str] | None = None
    available_methods: list[str] | None = None
    date_of_birth_required: bool | None = None
    minimum_age: int | None = None
    biometric_consent_text: str | None = None
    privacy_notice_text: str | None = None
    evidence_retention_days: int | None = None
    reverification_interval_days: int | None = None
    reverify_on_account_recovery: bool | None = None
    document_provider_code: str | None = None
    max_attempts_per_day: int | None = None


class IdentityReviewerDecision(CamelModel):
    decision: str
    reason_code: str
    note: str


class IdentityReviewRead(CamelModel):
    """Section 13 reviewer view: case header + normalized checks. No raw
    scores, no full document number."""

    id: int
    party_id: int
    account_name: str = ""
    country_code: str = ""
    role_context: str = ""
    method: str
    provider_code: str = ""
    state: str
    assurance_level: str
    reason_codes: list[str] = []
    match_results: dict = {}
    legal_name: str = ""
    document_type: str = ""
    masked_document_number: str = ""
    has_document: bool
    alternative_reason: str = ""
    escalated: bool = False
    verifier_notes: str = ""
    submitted_at: datetime | None = None
    decided_at: datetime | None = None
