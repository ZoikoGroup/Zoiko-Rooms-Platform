"""Property document reading (services/property_document_analysis.py): PDF
text layer first, OCR for photos/scans, then address / postal / unit / owner
/ type / year / reference extraction, and how the decision uses it."""

from __future__ import annotations

import io
from datetime import date

import pytest

from app.models.property_location import PropertyLocationEvidence, PropertyLocationVerification
from app.services import document_ocr
from app.services import property_document_analysis as pda
from app.services import property_location_service as svc

ADDRESS = {"address_line_1": "2-599 Madupally", "locality": "Madhira", "postal_code": "507116", "country_code": "IN"}
YEAR = date.today().year


def _pdf(*lines: str) -> bytes:
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer)
    y = 800
    for line in lines:
        page.drawString(72, y, line)
        y -= 20
    page.save()
    return buffer.getvalue()


def _fake_ocr(monkeypatch, text, confidence=85.0):
    monkeypatch.setattr(pda, "_ocr", lambda content: (text.upper(), confidence))


class TestTextSources:
    def test_digital_pdf_uses_its_text_layer(self, monkeypatch):
        monkeypatch.setattr(pda, "_ocr", lambda c: pytest.fail("OCR must not run for a text PDF"))
        a = pda.analyze(_pdf("Gram Panchayat Madhira - Property Tax Demand Notice", f"Assessment No: GP/1234/{YEAR}",
                             "Owner: Asha Rao", "2-599 Madupally, Madhira 507116"),
                        "application/pdf", evidence_type="PROPERTY_TAX_RECORD", canonical_address=ADDRESS,
                        owner_name="Asha Rao")
        assert a.text_source == "PDF_TEXT" and a.quality == "GOOD" and a.readable
        assert a.address_matched and a.postal_matched and a.owner_name_matched and a.document_type_matched
        assert a.document_year == YEAR and a.reference_number.startswith("GP/1234")
        assert a.signals == []

    def test_photo_is_read_with_ocr_and_tolerates_ocr_noise(self, monkeypatch):
        _fake_ocr(monkeypatch, f"PROPERTY TAX RECEIPT {YEAR} 2-599 MADUPALY MADHIRA 507116")  # one letter dropped
        a = pda.analyze(b"\x89PNG....", "image/png", evidence_type="PROPERTY_TAX_RECORD", canonical_address=ADDRESS)
        assert a.text_source == "OCR" and a.quality == "GOOD" and a.address_matched

    def test_blurred_photo_is_poor_quality(self, monkeypatch):
        _fake_ocr(monkeypatch, "PR0PERTY T4X 2 599", confidence=30.0)
        a = pda.analyze(b"\xff\xd8...", "image/jpeg", evidence_type="PROPERTY_TAX_RECORD", canonical_address=ADDRESS)
        assert a.quality == "POOR" and not a.readable and "EVIDENCE_POOR_QUALITY" in a.signals

    def test_scanned_pdf_falls_back_to_ocr(self, monkeypatch):
        _fake_ocr(monkeypatch, f"HOUSE TAX {YEAR} 2-599 MADUPALLY 507116")
        monkeypatch.setattr(pda, "_pdf_text", lambda c: "")
        a = pda.analyze(b"%PDF-1.4 scanned", "application/pdf", evidence_type="PROPERTY_TAX_RECORD",
                        canonical_address=ADDRESS)
        assert a.text_source == "OCR" and a.address_matched

    def test_no_ocr_installed_means_review_not_a_guess(self, monkeypatch):
        monkeypatch.setattr(pda, "_ocr", lambda c: None)
        a = pda.analyze(b"\x89PNG", "image/png", evidence_type="PROPERTY_TAX_RECORD", canonical_address=ADDRESS)
        assert a.quality == "OCR_UNAVAILABLE" and a.address_matched is None


class TestSignals:
    def test_wrong_postal_code_is_not_a_match(self, monkeypatch):
        _fake_ocr(monkeypatch, f"PROPERTY TAX {YEAR} 2-599 MADUPALLY 500001")
        a = pda.analyze(b"x", "image/png", evidence_type="PROPERTY_TAX_RECORD", canonical_address=ADDRESS)
        assert a.postal_matched is False and a.address_matched is False

    def test_old_bill_is_outdated(self, monkeypatch):
        _fake_ocr(monkeypatch, f"PROPERTY TAX {YEAR - 6} 2-599 MADUPALLY 507116")
        a = pda.analyze(b"x", "image/png", evidence_type="PROPERTY_TAX_RECORD", canonical_address=ADDRESS)
        assert "EVIDENCE_OUTDATED" in a.signals

    def test_document_not_looking_like_chosen_type(self, monkeypatch):
        _fake_ocr(monkeypatch, f"RESTAURANT MENU {YEAR} 2-599 MADUPALLY 507116")
        a = pda.analyze(b"x", "image/png", evidence_type="PROPERTY_TAX_RECORD", canonical_address=ADDRESS)
        assert a.document_type_matched is False and "EVIDENCE_TYPE_UNCLEAR" in a.signals

    def test_owner_name_mismatch_is_only_a_signal(self, monkeypatch):
        _fake_ocr(monkeypatch, f"PROPERTY TAX {YEAR} OWNER RAVI KUMAR 2-599 MADUPALLY 507116")
        a = pda.analyze(b"x", "image/png", evidence_type="PROPERTY_TAX_RECORD", canonical_address=ADDRESS,
                        owner_name="Asha Rao")
        assert a.owner_name_matched is False and a.address_matched  # still confirms existence


class TestDecision:
    def _v(self, **evidence):
        v = PropertyLocationVerification(property_id=1, party_id=1, country_code="IN", pack_version=1,
                                         canonical_address=ADDRESS, geocode_status="RESOLVED",
                                         location_precision="ROOFTOP", address_status="VALIDATED", pin_status="AUTO_CONFIRMED")
        v.evidence = [PropertyLocationEvidence(evidence_type="PROPERTY_TAX_RECORD", reused_elsewhere=False, **evidence)]
        return v

    def test_good_matching_bill_verifies(self, db_session):
        v = self._v(readable=True, address_matched=True, quality="GOOD", document_type_matched=True, signals=[])
        assert svc.decide(v, svc.get_pack(db_session, "IN"))[0] == "VERIFIED"

    def test_only_blurred_photos_ask_for_a_clearer_copy(self, db_session):
        v = self._v(readable=False, address_matched=True, quality="POOR", signals=["EVIDENCE_POOR_QUALITY"])
        state, _e, codes = svc.decide(v, svc.get_pack(db_session, "IN"))
        assert state == "ACTION_REQUIRED" and "EVIDENCE_POOR_QUALITY" in codes

    def test_outdated_bill_needs_a_recent_one(self, db_session):
        v = self._v(readable=True, address_matched=True, quality="GOOD", document_type_matched=True,
                    signals=["EVIDENCE_OUTDATED"])
        state, _e, codes = svc.decide(v, svc.get_pack(db_session, "IN"))
        assert state == "ACTION_REQUIRED" and "EVIDENCE_OUTDATED" in codes

    def test_unclear_document_type_asks_for_the_right_document(self, db_session):
        v = self._v(readable=True, address_matched=True, quality="GOOD", document_type_matched=False,
                    signals=["EVIDENCE_TYPE_UNCLEAR"])
        state, _e, codes = svc.decide(v, svc.get_pack(db_session, "IN"))
        assert state == "ACTION_REQUIRED" and "EVIDENCE_TYPE_UNCLEAR" in codes


@pytest.mark.skipif(not document_ocr.is_available(), reason="Tesseract not installed")
def test_real_ocr_reads_a_photo_of_a_bill():
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1400, 500), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 40)
    except OSError:
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 40)
        except OSError:
            font = ImageFont.load_default()
    for i, line in enumerate(["PROPERTY TAX RECEIPT", f"YEAR {YEAR}", "2-599 MADUPALLY MADHIRA", "PIN 507116"]):
        draw.text((60, 40 + i * 100), line, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    a = pda.analyze(buffer.getvalue(), "image/png", evidence_type="PROPERTY_TAX_RECORD", canonical_address=ADDRESS)
    assert a.text_source == "OCR"
    assert a.address_matched and a.postal_matched and a.document_type_matched


GB_ADDRESS = {"address_line_1": "10 Downing Street", "locality": "London", "postal_code": "SW1A 2AA", "country_code": "GB"}
US_ADDRESS = {"address_line_1": "1600 Pennsylvania Avenue NW", "locality": "Washington", "postal_code": "20500",
              "country_code": "US"}


class TestUkAndUsDocuments:
    def test_uk_council_tax_bill(self, monkeypatch):
        _fake_ocr(monkeypatch, f"WESTMINSTER CITY COUNCIL COUNCIL TAX BILL {YEAR}/{YEAR + 1} "
                               "ACCOUNT NUMBER 60012345 BAND D 10 DOWNING STREET LONDON SW1A 2AA")
        a = pda.analyze(b"x", "image/png", evidence_type="PROPERTY_TAX_RECORD", canonical_address=GB_ADDRESS)
        assert a.document_type_matched and a.address_matched and a.postal_matched
        assert a.reference_number == "60012345" and a.council_tax_band == "D"

    def test_uk_land_registry_title_register(self, monkeypatch):
        _fake_ocr(monkeypatch, f"HM LAND REGISTRY OFFICIAL COPY OF REGISTER OF TITLE TITLE NUMBER: LN123456 "
                               f"EDITION DATE {YEAR} A: PROPERTY REGISTER 10 DOWNING STREET LONDON SW1A 2AA")
        a = pda.analyze(b"x", "image/png", evidence_type="LAND_REGISTRY_RECORD", canonical_address=GB_ADDRESS)
        assert a.document_type_matched and a.address_matched and a.reference_number == "LN123456"

    def test_us_county_property_tax_statement(self, monkeypatch):
        _fake_ocr(monkeypatch, f"DISTRICT OF COLUMBIA OFFICE OF TAX AND REVENUE REAL ESTATE TAX {YEAR} "
                               "ASSESSOR'S PARCEL NUMBER 0187-0001-0800 1600 PENNSYLVANIA AVENUE NW WASHINGTON DC 20500")
        a = pda.analyze(b"x", "image/png", evidence_type="PROPERTY_TAX_RECORD", canonical_address=US_ADDRESS)
        assert a.document_type_matched and a.address_matched and a.reference_number == "0187-0001-0800"

    def test_us_grant_deed(self, monkeypatch):
        _fake_ocr(monkeypatch, f"RECORDING REQUESTED BY GRANT DEED {YEAR} GRANTOR JOHN SMITH GRANTEE ASHA RAO "
                               "APN: 123-456-789 1600 PENNSYLVANIA AVENUE NW WASHINGTON 20500")
        a = pda.analyze(b"x", "image/png", evidence_type="TITLE_DEED", canonical_address=US_ADDRESS, owner_name="Asha Rao")
        assert a.document_type_matched and a.address_matched and a.owner_name_matched
        assert a.reference_number == "123-456-789"

    def test_us_zip_plus_four_still_matches(self, monkeypatch):
        _fake_ocr(monkeypatch, f"COUNTY ASSESSOR TAX YEAR {YEAR} 1600 PENNSYLVANIA AVENUE NW WASHINGTON DC 20500-0003")
        a = pda.analyze(b"x", "image/png", evidence_type="PROPERTY_TAX_RECORD", canonical_address=US_ADDRESS)
        assert a.postal_matched and a.address_matched


class TestIdentityAddressMatch:
    """A document identifies the property by house number + place name (+ the
    postal code, checked separately) -- landmarks the host added aren't on
    official documents and aren't required."""
    DOC = "PROPERTY TAX RECEIPT PROPERTY ADDRESS: 2-599 MADUPALLY, MADHIRA, TELANGANA, 507203, IN"

    def _address(self, **changes):
        return {"address_line_1": "2-599 Muthyalamma temple", "address_line_2": "Madupalli", "locality": "Madupalli",
                "administrative_area": "Telangana", "postal_code": "507203", "country_code": "IN", **changes}

    def test_landmark_words_are_not_required(self):
        from app.services.property_document_analysis import identity_matches
        assert identity_matches(self.DOC, self._address(), tolerant=True)

    def test_house_number_must_match(self):
        from app.services.property_document_analysis import identity_matches
        assert not identity_matches(self.DOC, self._address(address_line_1="9-999 Muthyalamma temple"), True)

    def test_a_place_name_must_match(self):
        from app.services.property_document_analysis import identity_matches
        assert not identity_matches(self.DOC, self._address(address_line_2="Kukatpally", locality="Hyderabad",
                                                            address_line_1="2-599 Gandhi Road"), True)
