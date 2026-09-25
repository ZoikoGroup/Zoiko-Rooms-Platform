"""An Aadhaar card often prints another 4-digit group (a year, the VID)
right before the number, so the first 12-digit window in the OCR text can
straddle it -- extraction must pick the real number, not that window."""

from __future__ import annotations

from app.services import document_ocr


def _with_check_digit(first_11: str) -> str:
    for d in "0123456789":
        if document_ocr.is_valid_aadhaar_checksum(first_11 + d):
            return first_11 + d
    raise AssertionError("no check digit found")


REAL_NUMBER = _with_check_digit("48213579024")
REAL_PRINTED = f"{REAL_NUMBER[:4]} {REAL_NUMBER[4:8]} {REAL_NUMBER[8:]}"


def _ocr_returns(monkeypatch, text: str, confidence: float = 72.0) -> None:
    monkeypatch.setattr(document_ocr, "_ocr_text_and_confidence", lambda _bytes: (text.upper(), confidence))


def test_a_year_printed_before_the_number_is_not_mistaken_for_part_of_it(monkeypatch):
    straddling = "2011" + REAL_NUMBER[:8]
    assert not document_ocr.is_valid_aadhaar_checksum(straddling)
    _ocr_returns(monkeypatch, f"GOVERNMENT OF INDIA DOB 12/05/1990 2011 {REAL_PRINTED} MALE")

    number, confidence = document_ocr.extract_and_score(b"img", "aadhaar")

    assert number == REAL_NUMBER
    assert confidence == 72.0


def test_the_typed_number_is_preferred_when_it_is_on_the_card(monkeypatch):
    other_valid = _with_check_digit("91827364500")
    _ocr_returns(monkeypatch, f"{other_valid[:4]} {other_valid[4:8]} {other_valid[8:]} VID {REAL_PRINTED}")

    number, _ = document_ocr.extract_and_score(b"img", "aadhaar", expected_number=REAL_PRINTED)

    assert number == REAL_NUMBER


def test_a_typed_number_that_is_not_on_the_card_is_never_returned(monkeypatch):
    _ocr_returns(monkeypatch, f"DOB 2011 {REAL_PRINTED}")

    number, _ = document_ocr.extract_and_score(b"img", "aadhaar", expected_number="999999999999")

    assert number == REAL_NUMBER


def test_no_valid_candidate_still_reports_the_first_match(monkeypatch):
    invalid = REAL_NUMBER[:11] + str((int(REAL_NUMBER[11]) + 1) % 10)
    assert not document_ocr.is_valid_aadhaar_checksum(invalid)
    _ocr_returns(monkeypatch, f"{invalid[:4]} {invalid[4:8]} {invalid[8:]}")

    number, _ = document_ocr.extract_and_score(b"img", "aadhaar")

    assert number == invalid


def test_other_document_types_keep_the_first_match(monkeypatch):
    _ocr_returns(monkeypatch, "INCOME TAX DEPARTMENT ABCDE1234F")

    number, _ = document_ocr.extract_and_score(b"img", "pan_card")

    assert number == "ABCDE1234F"
