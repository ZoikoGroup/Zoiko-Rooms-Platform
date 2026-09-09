from datetime import datetime

from app.schemas.common import CamelModel


class MarketReleaseCreate(CamelModel):
    jurisdiction: str
    min_stay_nights: int = 30


class MarketReleaseRead(CamelModel):
    id: int
    jurisdiction: str
    status: str
    min_stay_nights: int
    effective_from: datetime | None
    approved_at: datetime | None
    # ZR-ENG-CLR-001 Section 14: see app/services/policy.py for the known
    # keys/defaults. Empty means every policy uses the platform-wide default.
    policy_overrides: dict = {}
    created_at: datetime


class MarketReleasePolicyUpdate(CamelModel):
    """Full replacement of policy_overrides -- keys must be a subset of
    services.policy.POLICY_KEYS (validated server-side, not just here)."""

    overrides: dict


class PropertyCreate(CamelModel):
    address: str
    city: str


class PropertyRead(CamelModel):
    id: int
    owner_party_id: int
    address: str
    city: str
    status: str
    created_at: datetime


class RoomCreate(CamelModel):
    size: int = 0
    has_ensuite: bool = False


class RoomRead(CamelModel):
    id: int
    property_id: int
    room_type: str
    size: int
    has_ensuite: bool
    status: str
    created_at: datetime


class AuthorityRecordCreate(CamelModel):
    room_id: int
    authority_type: str
    evidence_ref: str = ""


class AuthorityRecordRead(CamelModel):
    id: int
    party_id: int
    room_id: int
    authority_type: str
    evidence_ref: str
    verified_at: datetime | None
    expires_at: datetime | None
    status: str
    created_at: datetime


class IdentityVerificationCreate(CamelModel):
    party_id: int | None = None
    document_type: str
    encrypted_reference: str
    evidence_ref: str = ""


class IdentityVerificationRead(CamelModel):
    """Admin-facing full read -- field names deliberately match the ORM columns
    (including the legacy `encrypted_reference` name) so this can be returned
    straight from the model via response_model, no manual mapping needed."""

    id: int
    party_id: int
    document_type: str
    document_category: str
    custom_document_name: str
    encrypted_reference: str | None
    evidence_ref: str
    verified_at: datetime | None
    expires_at: datetime | None
    verifier_admin_id: int | None
    verifier_notes: str
    status: str
    has_document: bool
    document_file_original_name: str
    document_file_content_type: str
    created_at: datetime
    updated_at: datetime


class IdentityVerificationReject(CamelModel):
    notes: str = ""


class IdentityVerificationUserRead(CamelModel):
    """User-facing identity verification response. Built from a hand-assembled
    dict rather than ORM passthrough, so field names here are independent of the
    ORM's column names (e.g. document_number vs. the legacy encrypted_reference)."""

    id: int
    document_type: str
    document_category: str
    custom_document_name: str
    document_number: str = ""
    evidence_ref: str
    status: str
    has_document: bool
    document_original_name: str
    document_content_type: str
    verified_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    verifier_notes: str = ""


class RoomPassportClaimCreate(CamelModel):
    claim_type: str
    value: str
    evidence_tier: str = "self_attested"


class RoomPassportClaimRead(CamelModel):
    id: int
    room_id: int
    claim_type: str
    value: str
    evidence_tier: str
    verified_at: datetime | None
    expires_at: datetime | None
    created_at: datetime


class OccupancyClassificationSet(CamelModel):
    classification: str
    confidence: float = 1.0
    evidence_ref: str = ""
    review_state: str = "APPROVED"


class OccupancyClassificationRead(CamelModel):
    id: int
    room_id: int
    classification: str
    confidence: float
    evidence_ref: str
    jurisdiction: str
    rule_version: int
    review_state: str
    updated_at: datetime
