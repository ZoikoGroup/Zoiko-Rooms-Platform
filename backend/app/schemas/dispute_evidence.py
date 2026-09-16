from datetime import datetime

from app.schemas.common import CamelModel


class DisputeEvidenceRead(CamelModel):
    id: int
    case_id: int
    provenance: str
    uploaded_by_guest_id: str | None
    uploaded_by_party_id: int | None
    uploaded_by_admin_id: int | None
    # stored_filename is deliberately never exposed -- download is always by
    # evidence id (see the /evidence/{id}/file route), not by filename.
    original_filename: str
    content_type: str
    size_bytes: int
    sha256_hash: str
    note_text: str
    disclosure_class: str
    legal_hold: bool
    verification_status: str = "RECEIVED"
    redacted_of_evidence_id: int | None
    claim_ids: list[int] = []
    deletion_requested_at: datetime | None = None
    deleted_at: datetime | None = None
    deletion_refused_reason: str = ""
    created_at: datetime
    captured_at: datetime | None = None


class DisputeEvidenceLegalHoldUpdate(CamelModel):
    hold: bool
    reason: str = ""


class DisputeLegalHoldRead(CamelModel):
    id: int
    evidence_id: int
    case_id: int
    status: str
    reason: str
    placed_by_admin_id: int
    placed_at: datetime
    released_by_admin_id: int | None
    released_at: datetime | None


class DisputeEvidenceVerify(CamelModel):
    verified: bool
