"""SRCH-05 -- no raw bypass data in pre-unlock responses.

Output schemas for external cards contain no source URL/domain, phone, email,
social handle, exact address or booking links, and the anti-circumvention
sanitizer scrubs that class of data from any card text.
"""

from __future__ import annotations

from app.schemas.external_search import ExternalCard, ExternalCardResult
from app.services.anti_circumvention import sanitizer


class TestSanitizerText:
    def test_email_masked(self):
        assert sanitizer.sanitize_text("write to alice@example.com today") == "write to [email masked] today"

    def test_url_masked(self):
        assert sanitizer.sanitize_text("see https://beds.example.org/list?id=9") == "see [url masked]"

    def test_phone_masked(self):
        assert "7946 0958" not in sanitizer.sanitize_text("call 020 7946 0958")

    def test_address_masked(self):
        assert sanitizer.sanitize_text("at 221 Baker Street, London") == "at [address masked], London"

    def test_six_digit_postcode_masked(self):
        assert sanitizer.sanitize_text("PIN 400001 here") == "PIN [postcode masked] here"


class TestSanitizerCard:
    def test_masked_card_forces_flags_false(self):
        card = sanitizer.sanitize_card(
            {
                "title": "Cozy 1BHK near Health City",
                "availability_text": "call plus@telco.com or 040 4444 5555",
                "location_city": "Wards 7, Hyderabad",
                "has_exact_address": True,
                "has_phone": True,
                "has_email": True,
                "has_url": True,
                "is_unlocked": True,
                "amenities": ["furnished", "alice@rooms.example"],
            }
        )
        assert card["has_exact_address"] is False
        assert card["has_phone"] is False
        assert card["has_email"] is False
        assert card["has_url"] is False
        assert card["is_unlocked"] is False
        assert "plus@telco.com" not in card["availability_text"]
        assert "040 4444 5555" not in card["availability_text"]
        assert card["amenities"] == ["furnished", "[email masked]"]

    def test_sanitizized_card_validates_against_schema(self):
        raw = sanitizer.sanitize_card(
            {
                "source_id": "s1",
                "source_tier": "B",
                "title": "Cozy 1BHK",
                "availability_text": None,
                "location_city": "Pune",
                "location_region": None,
                "location_country": "IN",
                "room_type": None,
                "occupancy": None,
                "amenities": [],
                "has_exact_address": True,
                "has_phone": True,
                "has_email": True,
                "has_url": True,
                "is_unlocked": True,
            }
        )
        ExternalCard.model_validate(raw)


class TestNoProhibitedSchemaFields:
    def test_external_card_has_no_contact_or_source_url_fields(self):
        forbidden = {
            "source_url", "source_domain", "canonical_url", "phone", "email",
            "exact_address", "address", "social_handle", "direct_booking_url",
            "provider_phone", "provider_email", "contact",
        }
        assert forbidden.intersection(ExternalCard.model_fields) == set()

    def test_external_card_result_adds_only_opportunity_id(self):
        result_extra = set(ExternalCardResult.model_fields) - set(ExternalCard.model_fields)
        assert result_extra == {"opportunity_id"}

    def test_mask_flags_are_booleans_and_default_false(self):
        for name in ("has_exact_address", "has_phone", "has_email", "has_url", "is_unlocked"):
            assert ExternalCard.model_fields[name].default is False