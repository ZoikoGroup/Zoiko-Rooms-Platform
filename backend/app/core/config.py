from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor .env to the backend package root so settings load regardless of the
# working directory uvicorn (or an IDE run config) is started from.
BACKEND_DIR = Path(__file__).resolve().parents[2]

# Placeholder secrets that must never be used beyond local development. If
# ENVIRONMENT=production is set alongside any of these, the app refuses to boot.
PLACEHOLDER_JWT_SECRETS = ("dev-secret-change-me", "change-me", "changeme", "secret")
PLACEHOLDER_PASSWORDS = ("change-this-password", "change-me", "changeme", "password", "password123")
# A real, valid Fernet key (unlike JWT_SECRET/SEED_ADMIN_PASSWORD's plain
# placeholder strings, Fernet requires exactly 32 url-safe base64 bytes --
# an arbitrary human-readable placeholder string would just crash
# encrypt_json/decrypt_json in every dev/test run). Publicly known and
# committed to source control by design -- it must never be the key any
# real deployment actually uses, same as every other "change-this" default.
DEV_FIELD_ENCRYPTION_KEY = "r9v5IfBTi6JMliPvOaRnR34vW4O8OP5Gq6X9NDpMAgQ="
PLACEHOLDER_FIELD_ENCRYPTION_KEYS = (DEV_FIELD_ENCRYPTION_KEY,)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    environment: str = "development"
    database_url: str = "postgresql+psycopg://zoiko:zoiko@localhost:5432/zoiko_rooms"
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440
    # ZR-PAY-LINK-003 Section 8.1: "Sensitive financial fields are encrypted
    # and masked" -- the key for app/core/field_encryption.py's Fernet
    # helper (RentalPaymentInstruction.encrypted_bank_details, the first
    # at-rest-encrypted field in this codebase). A dev-only placeholder here
    # is fine (see _validate_production below); generate a real one with
    # `python -c "from cryptography.fernet import Fernet;
    # print(Fernet.generate_key().decode())"` for any real deployment.
    field_encryption_key: str = DEV_FIELD_ENCRYPTION_KEY
    # Comma-separated allow-list. Includes the authenticated platform frontend
    # and the public marketing site (local dev + deployed) so the anonymous
    # assistant widget can call /api/public/assistant cross-origin.
    cors_origins: str = (
        "http://localhost:3000,http://localhost:3001,"
        "https://zoikorooms.com,https://www.zoikorooms.com,https://app.zoikorooms.com"
    )
    cookie_secure: bool = False
    # None scopes the cookie to the exact request host (required for localhost, and
    # for cross-domain setups like Vercel + Render). Set to ".zoikorooms.com" in
    # production once frontend/backend share that domain.
    cookie_domain: str | None = None
    seed_admin_email: str = "admin@zoikorooms.com"
    seed_admin_password: str = "change-this-password"

    public_api_url: str = "http://localhost:8000"
    upload_dir: str = "uploads"
    max_upload_size_mb: int = 8

    # USER identity documents are never served through the public /uploads static
    # route -- they live in their own directory that main.py never mounts.
    identity_upload_dir: str = "secure_uploads/identity"
    identity_document_max_size_mb: int = 10

    # ZR-ENG-CLR-004 Section 13.1/AC-08: executed agreement PDFs, stored once
    # per AgreementVersion and never regenerated/overwritten -- same
    # never-publicly-mounted directory convention as identity_upload_dir.
    agreement_document_dir: str = "secure_uploads/agreements"
    # ZR-ENG-CLR-005 Section 13.1: one immutable PDF per successful
    # SimulatedPayment, same never-publicly-mounted convention.
    receipt_document_dir: str = "secure_uploads/receipts"
    # ZR-ENG-CLR-005 Section 6.3/13.1: one immutable PDF per PAID PayoutRecord.
    payout_statement_document_dir: str = "secure_uploads/payout_statements"
    # ZR-ENG-CLR-005 Section 13.1/AC-26: one immutable PDF per PAID PayoutRecord's fee line.
    service_fee_invoice_document_dir: str = "secure_uploads/service_fee_invoices"
    # ZR-ENG-CLR-005 Section 13.1: one immutable, host-issued PDF per RENT
    # Obligation (the request for payment; PaymentReceipt above is its
    # after-the-fact counterpart -- proof payment was actually made).
    rent_invoice_document_dir: str = "secure_uploads/rent_invoices"
    # ZR-ENG-CLR-010 Section 21: dispute evidence originals -- never publicly
    # mounted, same convention as identity_upload_dir. Disclosure class (not
    # the storage location) is what gates who can ever fetch one back out.
    evidence_upload_dir: str = "secure_uploads/dispute_evidence"
    evidence_document_max_size_mb: int = 10
    # ZR-ENG-CLR-010 Section 20: "Maker-checker: Mandatory above configured
    # thresholds or for safety/legal/manual override cases." A reasonable
    # MVP default, not a verified regulatory figure -- same REVIEW_REQUIRED
    # honesty every MarketPolicyPack field already carries. Flat
    # platform-wide value (Section 20 doesn't ask for this to vary by
    # jurisdiction the way deposit/termination policy does).
    dispute_financial_hold_maker_checker_threshold: float = 10000.0
    # ZR-ENG-CLR-005 Section 9.1: how long a dispatched payment attempt may sit
    # PENDING at the (simulated) provider before reconcile_stalled_payments is
    # allowed to fail it -- same on-demand-sweep shape as
    # signature_provider_dispatch's deadline handling.
    payment_provider_dispatch_timeout_minutes: int = 30

    # Real Stripe integration -- collection (Payment Intents) and payout
    # (Connect: Connected Accounts + Transfers). Left blank, every Stripe
    # call site in app/services/stripe_client.py falls back to exactly the
    # pre-existing simulated behavior (a generated id, no network call) --
    # same "test mode with no real adapter" posture SimulatedPayment already
    # had, just extended so setting real keys activates the real path
    # without any other code change.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # Both point at RecipientRentalPaymentsManager (the only page that
    # renders ProviderAccountManager / "Connect payment account" /
    # "Resume onboarding") -- there is no dedicated /host/payouts page.
    stripe_connect_refresh_url: str = "http://localhost:3001/account/host/payments"
    stripe_connect_return_url: str = "http://localhost:3001/account/host/payments"
    # ZR-PAY-002 Section 6/13: the Listing Fee is a separate Zoiko-own-account
    # checkout from the rent/payout domain above (see models/listing_fee.py's
    # own module docstring for why). Ops may register it as its own Stripe
    # webhook endpoint with its own signing secret; blank falls back to
    # stripe_webhook_secret so a single-endpoint Stripe setup keeps working
    # unchanged.
    stripe_listing_fee_webhook_secret: str = ""
    # ZR-PAY-LINK-003 Section 6: this domain's own Stripe Connect events
    # (direct charges on a recipient's connected account) -- same
    # separate-endpoint-with-its-own-secret, falls-back-to-shared-secret
    # pattern as stripe_listing_fee_webhook_secret above.
    stripe_rental_payment_webhook_secret: str = ""
    # ZR-PAY-002 Section 13.1: one immutable PDF per SUCCEEDED ListingFeePayment.
    # Same never-publicly-mounted secure_uploads/ convention as
    # receipt_document_dir above, own directory/module (core/listing_fee_
    # receipt_documents.py) since it's its own document series in its own domain.
    listing_fee_receipt_document_dir: str = "secure_uploads/listing_fee_receipts"
    # ZR-PAY-002 Section 10/13 'Retention: Minimum/maximum retention...
    # Follow jurisdiction policy.' A reasonable default for financial
    # evidence (7 years), not a verified legal figure for any specific
    # jurisdiction -- same REVIEW_REQUIRED honesty as every other numeric
    # default in this codebase (e.g. MarketPolicyPack.identity_evidence_
    # retention_days). Flat platform-wide value rather than a MarketPolicyPack
    # column: this domain (models/rental_payment.py) is deliberately kept
    # independent of that table's schema (Section 12.1's architecture rule).
    rental_payment_evidence_retention_days: int = 2555

    # ZR-ENG-CLR-010 Section 20/24, QA-Q19: the same "mandatory above
    # configured thresholds or for safety/legal/manual override cases" rule
    # Section 20 states for financial holds, applied to who may record an
    # external proceeding's decision -- below every trigger, the admin who
    # filed the proceeding may also record its outcome; at/above one, a
    # second, different admin must (crud/dispute_external_proceeding.py's
    # _requires_external_proceeding_dual_control). A reasonable MVP default,
    # not a verified regulatory figure, same as the financial-hold threshold above.
    dispute_external_proceeding_dual_control_threshold: float = 10000.0
    # ZR-ENG-CLR-010 Section 20/AC-10: "time/review bounded" -- a reasonable
    # MVP default review window for a financial hold that opens with no
    # case-officer-supplied review_at, not a verified legal/regulatory
    # figure (same honesty as every other fixed-window constant in this
    # codebase, e.g. the 7-day internal review window).
    dispute_financial_hold_default_review_days: int = 30

    frontend_url: str = "http://localhost:3000"
    password_reset_token_expire_minutes: int = 30

    llm_provider: str = "groq"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"

    # "file" (default) writes emails to dev_mail_outbox/ instead of sending them --
    # safe for every environment that hasn't explicitly opted in. Set to "smtp" in
    # production to actually send mail; see app/core/mailer.py.
    email_provider: str = "file"
    email_from: str = "Zoiko Rooms <no-reply@zoikorooms.com>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    # Implicit TLS (the connection is SSL-wrapped from the first byte, e.g. port
    # 465) rather than STARTTLS (plaintext connection upgraded mid-handshake,
    # e.g. port 587). Mutually exclusive with smtp_use_tls in practice -- set
    # this true for a 465-style provider and smtp_use_tls is then ignored.
    smtp_use_ssl: bool = False

    # Chat SSE rate limiting (requests per window, per authenticated actor).
    chat_rate_limit_max: int = 20
    chat_rate_limit_window_seconds: int = 60

    # Login brute-force throttling (attempts per window, per submitted email --
    # deliberately keyed pre-authentication, unlike chat's per-authenticated-actor
    # keying, since the whole point is to slow down guessing before a login ever
    # succeeds).
    login_rate_limit_max: int = 10
    login_rate_limit_window_seconds: int = 60

    # ZR-SUB-003 Section 10: "Rate-limit submission and document workflows."
    # Per-authenticated-actor, same keying discipline as chat above.
    sublet_submit_rate_limit_max: int = 5
    sublet_submit_rate_limit_window_seconds: int = 3600
    sublet_document_rate_limit_max: int = 20
    sublet_document_rate_limit_window_seconds: int = 3600

    # ZR-ENG-CLR-001 Rule 7 / policy key booking.acceptance_hold_duration:
    # once an offer is accepted, the room is held (see services/inventory.py)
    # but the renter must reach a confirmed move-in within this window or the
    # offer expires and the hold is released. Spec default is 24h.
    offer_acceptance_confirmation_hours: int = 24

    # ZR-ENG-CLR-001 Rule 7 / policy key payment.checkout_lock_duration: once
    # both signatures land on an agreement, the renter has this long to clear
    # the initial rent+deposit obligations before the checkout session expires
    # (see services/booking_expiry.py). Spec default is 30 minutes.
    payment_checkout_lock_minutes: int = 30
    # Anonymous public assistant. Shared Postgres-backed bucket keyed by hashed
    # client IP, fixed window (requests per IP per window).
    public_assistant_rate_limit_max: int = 10
    public_assistant_rate_limit_window_seconds: int = 60

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    @model_validator(mode="after")
    def _validate_production(self) -> "Settings":
        if not self.is_production:
            return self
        problems: list[str] = []
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true in production")
        if not self.jwt_secret or self.jwt_secret.strip().lower() in PLACEHOLDER_JWT_SECRETS:
            problems.append("JWT_SECRET is unset or is a known placeholder")
        if self.jwt_secret and len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET is shorter than 32 characters")
        if not self.seed_admin_password or self.seed_admin_password.strip().lower() in PLACEHOLDER_PASSWORDS:
            problems.append("SEED_ADMIN_PASSWORD is unset or is a known placeholder")
        if len(self.seed_admin_password) < 12:
            problems.append("SEED_ADMIN_PASSWORD is shorter than 12 characters")
        if not self.field_encryption_key or self.field_encryption_key.strip() in PLACEHOLDER_FIELD_ENCRYPTION_KEYS:
            problems.append("FIELD_ENCRYPTION_KEY is unset or is the known placeholder")
        else:
            from cryptography.fernet import Fernet

            try:
                Fernet(self.field_encryption_key.encode("utf-8"))
            except Exception:
                problems.append("FIELD_ENCRYPTION_KEY is not a valid Fernet key")
        if problems:
            raise ValueError(
                "Refusing to boot in production due to insecure configuration:\n- " + "\n- ".join(problems)
            )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
