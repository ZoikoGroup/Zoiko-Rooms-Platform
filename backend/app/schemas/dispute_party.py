from datetime import datetime

from app.schemas.common import CamelModel


class DisputePartyAddRepresentative(CamelModel):
    represents: str  # "RENTER" | "HOST"
    representation_type: str  # "PROPERTY_MANAGER" | "LEGAL_COUNSEL" | "OTHER_AUTHORIZED"
    authority_evidence_ref: str
    guest_id: str | None = None
    party_id: int | None = None


class DisputePartyRestrict(CamelModel):
    restrictions: str  # "" clears the restriction


class DisputePartyRead(CamelModel):
    id: int
    case_id: int
    party_role: str
    guest_id: str | None
    party_id: int | None
    represents: str | None
    representation_type: str
    authority_verified_at: datetime | None
    authority_evidence_ref: str
    communication_restrictions: str
    added_at: datetime
    added_by_admin_id: int | None
