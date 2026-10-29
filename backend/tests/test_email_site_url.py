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
    def test_local_development_still_links_to_the_live_site(self, site):
        assert site(environment="development", frontend_url="http://localhost:3000") == PRODUCTION_SITE_URL

    def test_frontend_url_is_never_used_for_emails(self, site):
        assert site(environment="production", frontend_url="https://staging.zoikorooms.com") == PRODUCTION_SITE_URL

    def test_nothing_set_uses_the_live_site(self, site):
        assert site(environment="production", frontend_url="") == PRODUCTION_SITE_URL

    def test_an_explicit_public_https_site_url_wins(self, site):
        assert site(environment="development", frontend_url="http://localhost:3000",
                    public_site_url="https://staging.zoikorooms.com/") == "https://staging.zoikorooms.com"

    @pytest.mark.parametrize("bad", ["http://localhost:3000", "https://localhost", "http://app.zoikorooms.com", "https://127.0.0.1:3000"])
    def test_a_localhost_or_plain_http_public_site_url_is_ignored(self, site, bad):
        assert site(environment="development", frontend_url="http://localhost:3000", public_site_url=bad) == PRODUCTION_SITE_URL


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
