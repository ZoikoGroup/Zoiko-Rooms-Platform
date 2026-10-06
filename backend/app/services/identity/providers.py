"""Identity verification providers behind one Zoiko-owned interface
(ZR-IDENTITY-001 Section 8; ZR-IDV-ADR-001 Section 10.1).

    create_session(context)            -> ProviderSession     (hosted providers)
    verify_webhook / parse_webhook     -> list[WebhookEvent]
    get_decision(provider_session_id)  -> NormalizedResult | None
    normalize (inside each adapter)    -> NormalizedResult
    capabilities(country_code)         -> ProviderCapabilities

Nothing outside this module sees a provider's own payload or result codes:
every outcome becomes a NormalizedResult, and only a normalized PASS can
lead to Identity Verified.

Provider: VeriffProvider ("veriff") -- Document + Selfie IDV with a
decision webhook (ZR-IDV-ADR-001). The person takes the photos in Zoiko's
own capture screens; the backend relays each photo straight to Veriff's
media API (never stored by Zoiko) and then submits the session. Veriff
decides whether the document is genuine and belongs to the person; there is
no Zoiko manual-review route and no built-in fallback. When Veriff isn't configured, verification is
unavailable (nobody is verified). A future secondary vendor is added as
another IdentityProvider subclass.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from app.core.config import settings

logger = logging.getLogger("uvicorn.error")

PROVIDER_HOSTED = "PROVIDER_HOSTED"  # a provider session exists; the provider decides

# Photos the person takes in Zoiko's capture screens, as Veriff media contexts.
DOCUMENT_FRONT = "document-front"
DOCUMENT_BACK = "document-back"
FACE = "face"
CAPTURE_CONTEXTS = (DOCUMENT_FRONT, DOCUMENT_BACK, FACE)


class ProviderUnavailable(Exception):
    """The provider couldn't be reached or refused the request -- progress is
    kept and the person can retry or choose another method (Section 13)."""


class WebhookAuthError(Exception):
    """A webhook that failed authentication -- never parsed further."""


@dataclass
class NormalizedResult:
    """ZR-IDENTITY-001 Section 8.2 normalized provider result."""

    provider_code: str
    normalized_outcome: str  # PASS | REVIEW | ACTION_REQUIRED | FAIL ("" = not decided yet)
    reason_codes: list[str] = field(default_factory=list)
    provider_session_id: str = ""
    provider_attempt_id: str = ""
    # The provider's own reason code, translated to a Zoiko code from the
    # database mapping (services/identity/golive.py:map_reason).
    provider_reason_code: str = ""
    # The provider's own outcome as a constrained value (Section 11 provider_decision).
    provider_decision: str = ""
    provider_status: str = ""
    method_type: str = "DOCUMENT"
    verified_attributes: dict = field(default_factory=dict)  # legal_name (+ date_of_birth only if required)
    document_metadata: dict = field(default_factory=dict)  # masked_document_number, issuer_country, expires_at
    # document_authenticity / person_document_binding / liveness_or_alternative_binding:
    # PASS | FAIL | NOT_CHECKED | NOT_AVAILABLE
    match_results: dict = field(default_factory=dict)
    risk_signals: list[str] = field(default_factory=list)
    # True when the provider's own review team is looking (not a Zoiko reviewer).
    provider_reviewing: bool = False
    # When the provider itself made this decision (ISO 8601, "" if it didn't
    # say) -- used to drop decisions that arrive out of order (ADR Section 8).
    provider_decided_at: str = ""
    provider_completed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> "NormalizedResult":
        return cls(**json.loads(raw))


@dataclass
class ProviderSession:
    provider_session_id: str
    launch_url: str


@dataclass
class ProviderCapabilities:
    capture_mode: str
    document_and_selfie: bool
    checks_document_authenticity: bool
    checks_person_binding: bool
    asynchronous_decisions: bool


@dataclass
class WebhookEvent:
    provider_event_id: str
    event_type: str  # "decision" | "progress.started" | "progress.submitted" | ...
    provider_session_id: str
    vendor_data: str = ""
    result: NormalizedResult | None = None


class IdentityProvider:
    code = ""
    display_name = ""
    capture_mode = PROVIDER_HOSTED
    supports_webhooks = False
    checks_document_authenticity = False
    checks_person_binding = False

    def capabilities(self, country_code: str = "") -> ProviderCapabilities:
        return ProviderCapabilities(
            capture_mode=self.capture_mode,
            document_and_selfie=self.checks_person_binding,
            checks_document_authenticity=self.checks_document_authenticity,
            checks_person_binding=self.checks_person_binding,
            asynchronous_decisions=self.supports_webhooks,
        )

    def create_session(self, *, vendor_data: str, end_user_id: str, callback_url: str) -> ProviderSession:
        raise NotImplementedError

    def get_decision(self, provider_session_id: str) -> NormalizedResult | None:
        return None

    def delete_session(self, provider_session_id: str) -> bool:
        return False

    def upload_media(self, provider_session_id: str, context: str, content: bytes, content_type: str) -> None:
        """Relays one photo to the provider. Raises ProviderUnavailable."""
        raise NotImplementedError

    def submit_session(self, provider_session_id: str) -> None:
        """All photos are in: ask the provider to decide."""
        raise NotImplementedError

    # Webhooks
    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        return False

    def parse_webhook(self, body: bytes, kind: str = "decision") -> list[WebhookEvent]:
        return []


# -- Veriff (ZR-IDV-ADR-001) -----------------------------------------------------

# Veriff decision -> Zoiko normalized outcome (ADR Section 9). Only "approved"
# can ever become PASS; anything unknown is treated as not verified.
_VERIFF_OUTCOME = {
    "approved": ("PASS", None),
    "review": ("REVIEW", "PROVIDER_REVIEW"),
    "resubmission_requested": ("ACTION_REQUIRED", "RESUBMISSION_REQUESTED"),
    "declined": ("FAIL", "PROVIDER_DECLINED"),
    "expired": ("ACTION_REQUIRED", "SESSION_EXPIRED"),
    "abandoned": ("ACTION_REQUIRED", "SESSION_ABANDONED"),
}
# Veriff's own reason codes are mapped to Zoiko codes from the database
# (identity_provider_reason_mappings), because they depend on the contract.


class VeriffProvider(IdentityProvider):
    """Veriff Document + Selfie IDV. The backend creates the session with the
    integration's API key; the client only receives the session URL. The
    decision comes from the authenticated decision webhook (or, when a
    session is stale, the decision API) -- never from the browser."""

    code = "veriff"
    display_name = "Veriff"
    capture_mode = PROVIDER_HOSTED
    supports_webhooks = True
    checks_document_authenticity = True
    checks_person_binding = True

    def __init__(self, api_key: str, shared_secret: str, base_url: str, timeout: float):
        self._key = api_key
        self._secret = shared_secret
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def _sign(self, payload: bytes) -> str:
        return hmac.new(self._secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    def _request(self, method: str, path: str, *, body: bytes | None = None, signed_payload: bytes | None = None) -> dict:
        import httpx

        headers = {"X-AUTH-CLIENT": self._key, "Content-Type": "application/json"}
        if signed_payload is not None:
            headers["X-HMAC-SIGNATURE"] = self._sign(signed_payload)
        try:
            response = httpx.request(method, f"{self._base}{path}", content=body, headers=headers, timeout=self._timeout)
        except httpx.HTTPError as exc:
            logger.warning("veriff: %s %s failed: %s", method, path.split("/")[1:3], type(exc).__name__)
            raise ProviderUnavailable() from exc
        if response.status_code >= 400:
            # Status only -- never the response body (may hold personal data).
            logger.warning("veriff: %s %s returned HTTP %s", method, path.split("/")[1:3], response.status_code)
            raise ProviderUnavailable()
        return response.json() if response.content else {}

    def create_session(self, *, vendor_data: str, end_user_id: str, callback_url: str) -> ProviderSession:
        verification: dict = {"vendorData": vendor_data, "endUserId": end_user_id}
        if callback_url:
            verification["callback"] = callback_url
        body = json.dumps({"verification": verification}, separators=(",", ":")).encode("utf-8")
        data = self._request("POST", "/v1/sessions", body=body)
        session = data.get("verification") or {}
        if not session.get("id") or not session.get("url"):
            raise ProviderUnavailable()
        return ProviderSession(provider_session_id=str(session["id"]), launch_url=str(session["url"]))

    def get_decision(self, provider_session_id: str) -> NormalizedResult | None:
        data = self._request(
            "GET", f"/v1/sessions/{provider_session_id}/decision", signed_payload=provider_session_id.encode("utf-8"),
        )
        verification = data.get("verification")
        if not verification or not verification.get("status"):
            return None  # no decision yet
        return self._normalize(verification)

    def delete_session(self, provider_session_id: str) -> bool:
        try:
            self._request("DELETE", f"/v1/sessions/{provider_session_id}", signed_payload=provider_session_id.encode("utf-8"))
            return True
        except ProviderUnavailable:
            return False

    def upload_media(self, provider_session_id: str, context: str, content: bytes, content_type: str) -> None:
        """POST /v1/sessions/{id}/media -- the image as a data URI, the body
        HMAC-signed. The bytes are only passed through; nothing is kept."""
        if context not in CAPTURE_CONTEXTS:
            raise ValueError(f"context must be one of {CAPTURE_CONTEXTS}")
        data_uri = f"data:{content_type};base64," + base64.b64encode(content).decode("ascii")
        body = json.dumps({"image": {"context": context, "content": data_uri}}, separators=(",", ":")).encode("utf-8")
        self._request("POST", f"/v1/sessions/{provider_session_id}/media", body=body, signed_payload=body)

    def submit_session(self, provider_session_id: str) -> None:
        """PATCH /v1/sessions/{id} status=submitted: Veriff starts deciding."""
        body = json.dumps({"verification": {"status": "submitted"}}, separators=(",", ":")).encode("utf-8")
        self._request("PATCH", f"/v1/sessions/{provider_session_id}", body=body, signed_payload=body)

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """x-auth-client must be this environment's integration key and
        x-hmac-signature the HMAC-SHA256 of the exact raw body -- both
        compared in constant time, before anything is parsed."""
        lowered = {k.lower(): v for k, v in headers.items()}
        client = lowered.get("x-auth-client", "")
        signature = lowered.get("x-hmac-signature", "").strip().lower()
        if not client or not signature:
            return False
        if not hmac.compare_digest(client.encode("utf-8"), self._key.encode("utf-8")):
            return False
        return hmac.compare_digest(signature.encode("utf-8"), self._sign(body).encode("utf-8"))

    def parse_webhook(self, body: bytes, kind: str = "decision") -> list[WebhookEvent]:
        data = json.loads(body or b"{}")
        if kind == "full_auto":
            return [self._parse_full_auto(data)]
        if kind == "events":
            # Event webhook: progress only (started / submitted). Never a decision.
            session_id = str(data.get("id") or data.get("sessionId") or "")
            action = str(data.get("action") or "").lower()
            attempt = str(data.get("attemptId") or "")
            return [WebhookEvent(
                provider_event_id=f"event:{session_id}:{attempt}:{action}:{data.get('code', '')}",
                event_type=f"progress.{action or 'unknown'}", provider_session_id=session_id,
                vendor_data=str(data.get("vendorData") or ""), result=None,
            )]
        verification = data.get("verification") or {}
        session_id = str(verification.get("id") or "")
        status_value = str(verification.get("status") or "").lower()
        attempt = str(verification.get("attemptId") or "")
        return [WebhookEvent(
            provider_event_id=f"decision:{session_id}:{attempt}:{status_value}",
            event_type="decision", provider_session_id=session_id,
            vendor_data=str(verification.get("vendorData") or ""),
            result=self._normalize(verification) if status_value else None,
        )]

    def _parse_full_auto(self, data: dict) -> WebhookEvent:
        """Essential plan "Full Auto" webhook: session at the top level, the
        decision under data.verification, values possibly wrapped as
        {"value": ...}. Normalized into the same decision shape."""
        inner = (data.get("data") or {}).get("verification") or {}
        session_id = str(data.get("sessionId") or data.get("id") or "")
        attempt = str(data.get("attemptId") or "")
        decision = str(_unwrap(inner.get("decision") or inner.get("status")) or "").lower()
        person = {k: _unwrap(v) for k, v in (inner.get("person") or {}).items()}
        document = {k: _unwrap(v) for k, v in (inner.get("document") or {}).items()}
        verification = {
            "id": session_id, "attemptId": attempt, "status": decision,
            "vendorData": data.get("vendorData"), "reasonCode": _unwrap(inner.get("reasonCode")),
            "decisionTime": _unwrap(inner.get("decisionTime")) or data.get("time") or "",
            "person": person, "document": document,
        }
        return WebhookEvent(
            provider_event_id=f"fullauto:{session_id}:{attempt}:{decision}", event_type="decision",
            provider_session_id=session_id, vendor_data=str(data.get("vendorData") or ""),
            result=self._normalize(verification) if decision else None,
        )

    def _normalize(self, verification: dict) -> NormalizedResult:
        decision = str(verification.get("status") or "").lower()
        outcome, code = _VERIFF_OUTCOME.get(decision, ("FAIL", "PROVIDER_DECLINED"))
        reason_codes = [code] if code else []
        person = verification.get("person") or {}
        document = verification.get("document") or {}
        passed = decision == "approved"
        result = NormalizedResult(
            provider_code=self.code,
            normalized_outcome=outcome,
            reason_codes=reason_codes,
            provider_session_id=str(verification.get("id") or ""),
            provider_attempt_id=str(verification.get("attemptId") or ""),
            provider_reason_code=str(verification.get("reasonCode") or ""),
            provider_decision=decision if decision in _VERIFF_OUTCOME else "unknown",
            provider_status=decision,
            provider_reviewing=decision == "review",
            provider_decided_at=str(verification.get("decisionTime") or ""),
            document_metadata={
                "masked_document_number": mask_number(str(document.get("number") or "")),
                "issuer_country": str(document.get("country") or ""),
                "document_type": str(document.get("type") or ""),
                "expires_at": str(document.get("validUntil") or ""),
            },
            match_results={
                "document_authenticity": "PASS" if passed else "FAIL" if decision == "declined" else "NOT_CHECKED",
                "person_document_binding": "PASS" if passed else "FAIL" if decision == "declined" else "NOT_CHECKED",
                "liveness_or_alternative_binding": "PASS" if passed else "NOT_CHECKED",
            },
        )
        if passed:
            name = " ".join(p for p in (person.get("firstName"), person.get("lastName")) if p)
            result.verified_attributes = {"legal_name": name.strip()} if name.strip() else {}
            if person.get("dateOfBirth"):
                # Kept only if the country pack requires a date of birth (service decides).
                result.verified_attributes["date_of_birth"] = str(person["dateOfBirth"])
        return result


def _unwrap(value):
    """Full Auto payloads wrap values as {"value": ...}."""
    return value.get("value") if isinstance(value, dict) and "value" in value else value


def mask_number(number: str) -> str:
    """Last four characters only: "••••4567"."""
    cleaned = "".join(ch for ch in number if ch.isalnum())
    return f"••••{cleaned[-4:]}" if cleaned else ""


def veriff_configured() -> bool:
    return bool(settings.veriff_api_key and settings.veriff_shared_secret)


def get_provider(code: str) -> IdentityProvider | None:
    if code == VeriffProvider.code:
        if not veriff_configured():
            return None
        return VeriffProvider(
            settings.veriff_api_key, settings.veriff_shared_secret, settings.veriff_base_url,
            settings.veriff_timeout_seconds,
        )
    return None


PROVIDER_CODES = (VeriffProvider.code,)
