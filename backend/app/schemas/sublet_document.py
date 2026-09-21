from datetime import datetime

from app.schemas.common import CamelModel


class SubletDocumentRead(CamelModel):
    """ZR-SUB-003 Section 10: stored_filename is deliberately never exposed
    -- download is always by document id through a signed, time-limited
    download_url (Section 10: 'never expose storage bucket paths'), not by
    filename."""

    id: int
    original_filename: str
    content_type: str
    file_size: int
    sha256_hash: str
    scan_status: str
    uploaded_by_admin_id: int | None
    uploaded_by_user_id: int | None
    created_at: datetime
    download_url: str
