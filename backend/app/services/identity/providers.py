"""The identity check's normalized result (ZR-IDENTITY-001 Section 8.2).

Identity is now decided by a face match run on this server (services/identity/face_match.py), not by an outside
vendor. What remains here is the small result type the session / profile logic consumes, so everything downstream
(states, gates, credentials, notifications) is unchanged: only a normalized PASS can lead to Identity Verified."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

FACE_MATCH = "face_match"  # provider_code recorded on sessions and profiles


@dataclass
class NormalizedResult:
    """ZR-IDENTITY-001 Section 8.2 normalized result."""

    provider_code: str
    normalized_outcome: str  # PASS | REVIEW | ACTION_REQUIRED | FAIL ("" = not decided yet)
    reason_codes: list[str] = field(default_factory=list)
    provider_session_id: str = ""
    provider_attempt_id: str = ""
    provider_reason_code: str = ""
    provider_decision: str = ""
    provider_status: str = ""
    method_type: str = "DOCUMENT"
    verified_attributes: dict = field(default_factory=dict)  # legal_name (+ date_of_birth only if required)
    document_metadata: dict = field(default_factory=dict)
    # document_authenticity / person_document_binding / liveness_or_alternative_binding:
    # PASS | FAIL | NOT_CHECKED | NOT_AVAILABLE
    match_results: dict = field(default_factory=dict)
    risk_signals: list[str] = field(default_factory=list)
    provider_reviewing: bool = False
    provider_decided_at: str = ""
    provider_completed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> "NormalizedResult":
        return cls(**json.loads(raw))


def mask_number(number: str) -> str:
    """Last four characters only: "••••4567"."""
    cleaned = "".join(ch for ch in number if ch.isalnum())
    return f"••••{cleaned[-4:]}" if cleaned else ""


PROVIDER_CODES = (FACE_MATCH,)
