from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import CamelModel

PaymentRecipientRelationshipType = Literal["OWNER", "AGENT", "MANAGER", "OTHER"]


class PaymentRecipientAuthorityDeclare(CamelModel):
    """Host self-service submission -- ZR-PAY-LINK-003 Wireframe A/A.1.
    Scoped server-side to a room the calling host's own party actually owns
    (see api/routes/user_hosting.py's own declare_hosted_* pattern), but
    recipient_party_id may name a DIFFERENT party than the submitter (an
    agent/property manager/other authorized recipient) -- unlike
    AuthorityRecordDeclare, which is always the submitter's own claim about
    themselves.

    recipient_party_id is optional -- Wireframe A's default choice is
    "Me / the property owner", and the frontend has no reason to otherwise
    know its own party id; omitting it resolves to the calling host's own
    party server-side (see api/routes/user_hosting.py's route). Only the
    "authorized agent/property manager/another authorized recipient"
    options need to supply a different one explicitly."""

    room_id: int
    recipient_party_id: int | None = None
    relationship_type: PaymentRecipientRelationshipType
    evidence_ref: str = Field(min_length=1)


class PaymentRecipientAuthorityRevoke(CamelModel):
    reason: str = Field(min_length=1)


class PaymentRecipientAuthorityConfirmChange(CamelModel):
    """ZR-PAY-LINK-003 Section 14.1's step-up code confirmation for a
    recipient CHANGE -- never used for a room's first-ever declaration,
    which has no code to confirm (see declare_payment_recipient_authority's
    own docstring)."""

    code: str = Field(min_length=1)


class PaymentRecipientAuthorityRead(CamelModel):
    id: int
    party_id: int
    room_id: int
    relationship_type: str
    evidence_ref: str
    verified_at: datetime | None
    expires_at: datetime | None
    status: str
    is_high_risk: bool = False
    high_risk_reason: str = ""
    created_at: datetime
