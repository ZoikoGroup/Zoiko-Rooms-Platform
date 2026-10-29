"""Every button/link in an email is built from settings.email_site_url, so
production emails must open the live site (app.zoikorooms.com), never localhost."""

from __future__ import annotations

import pytest

from app.core import mailer
from app.core.config import PRODUCTION_SITE_URL, settings


@pytest.fixture
def site(monkeypatch):
    def configure(*, environment: str, frontend_url: str, public_site_url: str = ""):
        monkeypatch.setattr(settings, "environment", environment)
        monkeypatch.setattr(settings, "frontend_url", frontend_url)
        monkeypatch.setattr(settings, "public_site_url", public_site_url)
        return settings.email_site_url
    return configure


class TestEmailSiteUrl:
    def test_local_development_uses_the_local_site(self, site):
        assert site(environment="development", frontend_url="http://localhost:3000") == "http://localhost:3000"

    def test_public_site_url_wins_when_set(self, site):
        assert site(environment="development", frontend_url="http://localhost:3000",
                    public_site_url="https://app.zoikorooms.com/") == "https://app.zoikorooms.com"

    def test_production_never_links_to_localhost(self, site):
        assert site(environment="production", frontend_url="http://localhost:3000") == PRODUCTION_SITE_URL

    def test_production_with_nothing_set_falls_back_to_the_live_site(self, site):
        assert site(environment="production", frontend_url="") == PRODUCTION_SITE_URL

    def test_production_with_a_plain_http_address_falls_back_to_the_live_site(self, site):
        assert site(environment="production", frontend_url="http://app.zoikorooms.com") == PRODUCTION_SITE_URL

    def test_production_keeps_a_configured_https_address(self, site):
        assert site(environment="production", frontend_url="https://staging.zoikorooms.com") == "https://staging.zoikorooms.com"


class TestEmailButtons:
    def test_buttons_use_the_production_site_and_listing_live_opens_the_host_dashboard(self, site, monkeypatch, tmp_path):
        site(environment="production", frontend_url="http://localhost:3000")
        monkeypatch.setattr(settings, "email_provider", "file")
        monkeypatch.setattr(mailer, "DEV_MAIL_OUTBOX_DIR", tmp_path)

        mailer.send_listing_published_email("host@x.com", "Ravi", "Sunny room", listing_id="L-1", market_name="London", min_stay_nights=30)
        html = (tmp_path / "host@x.com.html").read_text(encoding="utf-8")
        assert 'href="https://app.zoikorooms.com/account/host/listings"' in html
        assert "localhost" not in html
        # A host can't open their own public listing page ("Listing not found"), so it isn't linked.
        assert "/listings/L-1" not in html

        mailer.send_welcome_email("new@x.com", "Ravi Kumar")
        welcome = (tmp_path / "new@x.com.html").read_text(encoding="utf-8")
        assert 'href="https://app.zoikorooms.com/account"' in welcome
