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

Providers:
- VeriffProvider ("veriff") -- the P0 primary provider: Document + Selfie
  IDV, hosted capture, decision webhook (ZR-IDV-ADR-001).
- DocumentCheckProvider ("zoiko_document_check") -- the built-in upload check
  used where Veriff isn't configured (local development / sandbox-less).
- ManualReviewProvider ("zoiko_manual_review") -- the accessible manual route.
- SignedWebhookProvider ("signed_webhook") -- a generic signed-webhook
  provider, kept for a future secondary vendor.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from app.core.config import settings

logger = logging.getLogger("uvicorn.error")

UPLOAD = "UPLOAD"  # the person uploads to Zoiko; the provider evaluates it
PROVIDER_HOSTED = "PROVIDER_HOSTED"  # the person captures inside the provider's flow


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
class Evidence:
    """What an UPLOAD provider evaluates for one session."""

    document_type: str
    document_bytes: bytes
    content_type: str
    typed_number: str
    legal_name: str
    country_code: str
    duplicate_of_verification_id: int | None = None
    number_used_by_other_account: bool = False


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
    capture_mode = UPLOAD
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

    # UPLOAD providers
    def evaluate(self, evidence: Evidence) -> NormalizedResult:
        raise NotImplementedError

    # PROVIDER_HOSTED providers
    def create_session(self, *, vendor_data: str, end_user_id: str, callback_url: str) -> ProviderSession:
        raise NotImplementedError

    def get_decision(self, provider_session_id: str) -> NormalizedResult | None:
        return None

    def delete_session(self, provider_session_id: str) -> bool:
        return False

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


# -- built-in providers ------------------------------------------------------------

class DocumentCheckProvider(IdentityProvider):
    """Built-in upload check (services/document_regex.py -- no OCR, no
    vendor) for environments without Veriff. It can confirm the number's
    format and that the confirmed legal name appears in a PDF's text layer;
    it cannot judge authenticity or that the person holds the document, so
    anything it can't confirm goes to a reviewer."""

    code = "zoiko_document_check"
    display_name = "Zoiko document check"

    def evaluate(self, evidence: Evidence) -> NormalizedResult:
        from app.services import document_regex

        text = document_regex.pdf_text(evidence.document_bytes) if evidence.content_type == "application/pdf" else ""
        typed = document_regex.extract_number(evidence.document_type, evidence.typed_number)
        in_document = document_regex.extract_number(evidence.document_type, text) if text else None
        number = typed or in_document
        result = NormalizedResult(
            provider_code=self.code,
            normalized_outcome="PASS",
            provider_status="completed",
            document_metadata={"masked_document_number": mask_number(number or ""), "issuer_country": evidence.country_code},
            match_results={
                "document_authenticity": "NOT_CHECKED",
                "person_document_binding": "NOT_CHECKED",
                "liveness_or_alternative_binding": "NOT_AVAILABLE",
            },
        )
        # A document already used by another account always goes to a
        # person, whatever else is wrong with it (Section 9.1).
        if evidence.duplicate_of_verification_id is not None or evidence.number_used_by_other_account:
            result.risk_signals.append("DUPLICATE_EVIDENCE")
            return _outcome(result, "REVIEW", "DUPLICATE_EVIDENCE")
        if not number:
            return _outcome(result, "ACTION_REQUIRED", "DOCUMENT_NUMBER_INVALID")
        if typed and in_document and typed != in_document:
            return _outcome(result, "ACTION_REQUIRED", "DOCUMENT_NUMBER_INVALID")

        name_found = document_regex.name_matches(evidence.legal_name, text) if text else None
        result.match_results["extracted_name_match"] = (
            "PASS" if name_found else "FAIL" if name_found is False else "NOT_CHECKED"
        )
        if name_found is False:
            return _outcome(result, "ACTION_REQUIRED", "NAME_MISMATCH")
        if name_found is None:
            return _outcome(result, "REVIEW", "NAME_NOT_CHECKABLE")

        result.verified_attributes = {"legal_name": evidence.legal_name}
        return result


class ManualReviewProvider(IdentityProvider):
    """The alternative / manual route: a trained Zoiko reviewer decides."""

    code = "zoiko_manual_review"
    display_name = "Manual review"

    def evaluate(self, evidence: Evidence) -> NormalizedResult:
        return NormalizedResult(
            provider_code=self.code, normalized_outcome="REVIEW", reason_codes=["ALTERNATIVE_REQUESTED"],
            method_type="MANUAL", provider_status="queued",
            match_results={"document_authenticity": "NOT_CHECKED", "person_document_binding": "NOT_CHECKED",
                           "liveness_or_alternative_binding": "NOT_CHECKED"},
        )


class SignedWebhookProvider(IdentityProvider):
    """Generic provider reporting results to POST /api/webhooks/identity/{code},
    signed as `X-Identity-Timestamp` + `X-Identity-Signature: sha256=<hex HMAC
    of "{timestamp}.{body}">`, body already normalized:
    {"events": [{"id", "type", "provider_session_id", "outcome", ...}]}.
    Enabled only when settings.identity_webhook_secret is set."""

    code = "signed_webhook"
    display_name = "External identity provider"
    supports_webhooks = True
    checks_document_authenticity = True
    checks_person_binding = True
    tolerance_seconds = 300

    def __init__(self, secret: str):
        self._secret = secret

    def evaluate(self, evidence: Evidence) -> NormalizedResult:
        return NormalizedResult(provider_code=self.code, normalized_outcome="", provider_status="pending")

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        lowered = {k.lower(): v for k, v in headers.items()}
        timestamp = lowered.get("x-identity-timestamp", "")
        signature = lowered.get("x-identity-signature", "").removeprefix("sha256=")
        if not timestamp.isdigit() or not signature:
            return False
        if abs(time.time() - int(timestamp)) > self.tolerance_seconds:
            return False  # replay window
        return hmac.compare_digest(sign(self._secret, timestamp, body), signature)

    def parse_webhook(self, body: bytes, kind: str = "decision") -> list[WebhookEvent]:
        data = json.loads(body or b"{}")
        events = []
        for raw in data.get("events", []):
            outcome = str(raw.get("outcome", "")).upper()
            result = None
            if outcome in ("PASS", "REVIEW", "ACTION_REQUIRED", "FAIL"):
                result = NormalizedResult(
                    provider_code=self.code, normalized_outcome=outcome,
                    reason_codes=[str(c) for c in raw.get("reason_codes", [])],
                    provider_session_id=str(raw.get("provider_session_id", "")), provider_status="completed",
                    match_results=dict(raw.get("match_results") or {}),
                    document_metadata=dict(raw.get("document_metadata") or {}),
                    verified_attributes=dict(raw.get("verified_attributes") or {}),
                )
            events.append(WebhookEvent(
                provider_event_id=str(raw.get("id", "")), event_type=str(raw.get("type", "")) or "decision",
                provider_session_id=str(raw.get("provider_session_id", "")), result=result,
            ))
        return events


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode("utf-8") + body, hashlib.sha256).hexdigest()


def _outcome(result: NormalizedResult, outcome: str, code: str) -> NormalizedResult:
    result.normalized_outcome = outcome
    result.reason_codes = [code]
    return result


def mask_number(number: str) -> str:
    """Last four characters only: "••••4567"."""
    cleaned = "".join(ch for ch in number if ch.isalnum())
    return f"••••{cleaned[-4:]}" if cleaned else ""


def number_hash(document_type: str, number: str) -> str:
    cleaned = "".join(ch for ch in number.upper() if ch.isalnum())
    return hashlib.sha256(f"{document_type}:{cleaned}".encode("utf-8")).hexdigest() if cleaned else ""


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
    if code == DocumentCheckProvider.code:
        return DocumentCheckProvider()
    if code == ManualReviewProvider.code:
        return ManualReviewProvider()
    if code == SignedWebhookProvider.code and settings.identity_webhook_secret:
        return SignedWebhookProvider(settings.identity_webhook_secret)
    return None


PROVIDER_CODES = (VeriffProvider.code, DocumentCheckProvider.code, SignedWebhookProvider.code)
