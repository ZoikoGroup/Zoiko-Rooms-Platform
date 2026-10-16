"""75% match confidence for names, address lines and postal codes on
property / authority documents -- tolerant of OCR and typing slips, never of
a different person, house number or postcode."""

import pytest

from app.services import match_confidence as mc


@pytest.mark.parametrize("text,name", [
    ("NAKULUR1 VENKATSWAMY", "Nakuluri Venkatswamy"),     # OCR read 1 for I
    ("ANILKUMAR NAKULURI", "Nakuluri Anil Kumar"),        # joined, other order
    ("N. ANILKUMAR", "Nakuluri Anil Kumar"),              # surname as an initial
])
def test_small_name_differences_are_accepted(text, name):
    assert mc.accepted(mc.name_confidence(text, name))


@pytest.mark.parametrize("text,name", [
    ("ASHA ROY", "Asha Rao"), ("PRIYA MENON", "Asha Rao"),
    ("SUNIL KUMAR NAKULURI", "Nakuluri Anil Kumar"),       # a relative: one letter-pair away
    ("NAKULURI KUMAR", "Nakuluri Anil Kumar"),             # a name missing altogether
])
def test_a_different_person_is_not(text, name):
    assert not mc.accepted(mc.name_confidence(text, name))


def test_address_slips_are_accepted_but_not_another_house_number():
    assert mc.accepted(mc.phrase_confidence("12 LAKE VEIW RD", "12 Lake View Road"))
    assert mc.phrase_confidence("14 LAKE VIEW ROAD", "12 Lake View Road") == 0.0


def test_one_misread_postcode_character_is_accepted_two_are_not():
    assert mc.accepted(mc.postal_confidence("LONDON EH1 1AB", "EH1 1AA"))
    assert not mc.accepted(mc.postal_confidence("LONDON EH2 7XZ", "EH1 1AA"))


def test_threshold_is_a_setting(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "document_match_threshold", 0.95)
    assert not mc.accepted(mc.name_confidence("NAKULUR1 VENKATSWAMY", "Nakuluri Venkatswamy"))
