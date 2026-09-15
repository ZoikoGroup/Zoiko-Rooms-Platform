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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    environment: str = "development"
    database_url: str = "postgresql+psycopg://zoiko:zoiko@localhost:5432/zoiko_rooms"
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440
    cors_origins: str = "http://localhost:3000"
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
        if problems:
            raise ValueError(
                "Refusing to boot in production due to insecure configuration:\n- " + "\n- ".join(problems)
            )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
