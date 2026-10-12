"""Stripe webhook production readiness: an endpoint with no signing secret
rejects every delivery, and production refuses to boot with real Stripe on
but the Listing Fee webhook secret missing. Rent is paid directly (bank
transfer / UPI / cash) -- there is no rent webhook, and the rent-era Stripe
settings are never required."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.core.config import settings

BACKEND_DIR = Path(__file__).resolve().parents[1]

WEBHOOK_PATHS = (
    "/api/finance/payments/stripe/webhook",
    "/api/finance/listing-fees/stripe/webhook",
)


@pytest.mark.parametrize("path", WEBHOOK_PATHS)
def test_webhook_without_any_signing_secret_is_rejected(client, monkeypatch, path):
    monkeypatch.setattr(settings, "stripe_webhook_secret", "")
    monkeypatch.setattr(settings, "stripe_listing_fee_webhook_secret", "")

    r = client.post(path, headers={"stripe-signature": "t=1,v1=forged"}, content=b'{"type": "account.updated"}')
    assert r.status_code == 400, r.text


PRODUCTION_BASE_ENV = {
    "ENVIRONMENT": "production",
    "COOKIE_SECURE": "true",
    "JWT_SECRET": "x" * 48,
    "SEED_ADMIN_PASSWORD": "y" * 16,
    "STRIPE_SECRET_KEY": "sk_live_example",
    "FRONTEND_URL": "https://zoikorooms.example",
    "STRIPE_WEBHOOK_SECRET": "",
    "STRIPE_LISTING_FEE_WEBHOOK_SECRET": "whsec_listing",
    "STRIPE_RENTAL_PAYMENT_WEBHOOK_SECRET": "whsec_rental",
    "STRIPE_CONNECT_REFRESH_URL": "https://app.zoikorooms.com/account/host/payments",
    "STRIPE_CONNECT_RETURN_URL": "https://app.zoikorooms.com/account/host/payments",
}


def _boot_production(**overrides: str) -> subprocess.CompletedProcess:
    env = {**PRODUCTION_BASE_ENV, **overrides}
    code = textwrap.dedent(f"""
        import os
        os.environ.update({env!r})
        from cryptography.fernet import Fernet
        os.environ['FIELD_ENCRYPTION_KEY'] = Fernet.generate_key().decode()
        from app.core.config import Settings
        Settings()
        print('booted')
    """)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=BACKEND_DIR)


def test_production_boots_with_complete_stripe_config():
    p = _boot_production()
    assert "booted" in p.stdout, p.stderr


def test_production_refuses_to_boot_without_the_listing_fee_webhook_secret():
    p = _boot_production(STRIPE_LISTING_FEE_WEBHOOK_SECRET="")
    assert p.returncode != 0
    assert "Refusing to boot in production" in p.stderr
    assert "STRIPE_LISTING_FEE_WEBHOOK_SECRET" in p.stderr


RENT_ONLY_GAPS = [
    ({"STRIPE_RENTAL_PAYMENT_WEBHOOK_SECRET": ""}, "STRIPE_RENTAL_PAYMENT_WEBHOOK_SECRET"),
    ({"STRIPE_CONNECT_RETURN_URL": "http://localhost:3000/account/host/payments"}, "STRIPE_CONNECT_RETURN_URL"),
    ({"STRIPE_CONNECT_REFRESH_URL": "http://app.zoikorooms.com/account/host/payments"}, "STRIPE_CONNECT_REFRESH_URL"),
]


@pytest.mark.parametrize("overrides, expected", RENT_ONLY_GAPS)
def test_rent_only_stripe_settings_are_not_required_while_rent_is_paid_directly(overrides, expected):
    p = _boot_production(**overrides)
    assert "booted" in p.stdout, p.stderr


def test_shared_webhook_secret_satisfies_both_endpoints():
    p = _boot_production(
        STRIPE_WEBHOOK_SECRET="whsec_shared",
        STRIPE_LISTING_FEE_WEBHOOK_SECRET="",
        STRIPE_RENTAL_PAYMENT_WEBHOOK_SECRET="",
    )
    assert "booted" in p.stdout, p.stderr


def test_production_refuses_to_boot_without_any_stripe_key():
    """No key means every Listing Fee checkout completes as paid without a
    charge -- production must not start that way."""
    p = _boot_production(
        STRIPE_SECRET_KEY="",
        STRIPE_LISTING_FEE_WEBHOOK_SECRET="",
        STRIPE_RENTAL_PAYMENT_WEBHOOK_SECRET="",
    )
    assert p.returncode != 0
    assert "STRIPE_SECRET_KEY must be set in production" in p.stderr


def test_test_mode_key_boots_with_gaps_logged_not_enforced():
    """Production currently runs on a Stripe test-mode key -- missing webhook
    secrets must not block that deployment, only be logged."""
    p = _boot_production(
        STRIPE_SECRET_KEY="sk_test_example",
        STRIPE_LISTING_FEE_WEBHOOK_SECRET="",
        STRIPE_RENTAL_PAYMENT_WEBHOOK_SECRET="",
        STRIPE_CONNECT_RETURN_URL="http://localhost:3000/account/host/payments",
    )
    assert "booted" in p.stdout, p.stderr
    assert "test-mode key" in p.stderr
    assert "STRIPE_LISTING_FEE_WEBHOOK_SECRET" in p.stderr
