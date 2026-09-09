"""Validation that insecure production config refuses to boot.

ZR-AI-SEC/PRIV: secrets must never run as placeholders, and cookies must be
Secure in production.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _prod_settings(**overrides) -> Settings:
    base = dict(
        environment="production",
        jwt_secret="a" * 48,
        seed_admin_password="a-secure-password-123",
        cookie_secure=True,
        database_url="sqlite://",
    )
    base.update(overrides)
    return Settings(**base)


def test_production_with_secure_config_loads():
    s = _prod_settings()
    assert s.is_production is True
    assert s.cookie_secure is True


def test_production_refuses_cookie_secure_false():
    with pytest.raises(ValidationError) as exc:
        _prod_settings(cookie_secure=False)
    assert "COOKIE_SECURE must be true in production" in str(exc.value)


def test_production_refuses_placeholder_jwt_secret():
    with pytest.raises(ValidationError) as exc:
        _prod_settings(jwt_secret="dev-secret-change-me")
    assert "JWT_SECRET" in str(exc.value)


def test_production_refuses_short_jwt_secret():
    with pytest.raises(ValidationError) as exc:
        _prod_settings(jwt_secret="short")
    assert "JWT_SECRET" in str(exc.value)


def test_production_refuses_placeholder_admin_password():
    with pytest.raises(ValidationError) as exc:
        _prod_settings(seed_admin_password="change-this-password")
    assert "SEED_ADMIN_PASSWORD" in str(exc.value)


def test_development_allows_placeholders():
    # The dev default must keep working so local boot is not blocked.
    s = Settings(environment="development")
    assert s.is_production is False
