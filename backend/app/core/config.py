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

    # Chat SSE rate limiting (requests per window, per authenticated actor).
    chat_rate_limit_max: int = 20
    chat_rate_limit_window_seconds: int = 60

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
