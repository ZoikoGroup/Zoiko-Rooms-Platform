"""Reporting conversion to US dollars (app/services/fx.py)."""

from app.core.config import Settings
from app.models.listing import SUPPORTED_CURRENCIES
from app.services import fx


def test_every_supported_currency_has_a_default_rate():
    """A listing currency without a rate would silently drop out of the
    dashboard's revenue -- add its rate when adding the currency."""
    defaults = Settings.model_fields["fx_rates_to_usd"].default
    missing = [c for c in SUPPORTED_CURRENCIES if c not in defaults]
    assert missing == []


def test_conversion_and_unknown_currency(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "fx_rates_to_usd", {"USD": 1.0, "NZD": 0.6})
    assert fx.to_usd(100, "nzd") == 60
    assert fx.to_usd(5, "USD") == 5
    assert fx.to_usd(5, "JPY") is None
