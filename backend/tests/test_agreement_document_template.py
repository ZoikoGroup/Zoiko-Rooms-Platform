"""The Residential Occupancy Agreement global master template
(services/agreement_document): facts frozen into the snapshot, verbatim
clause text, host-supplied agreement details, status vocabulary (template
clause 40) and the rendered PDF/accessible text."""

from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO
from types import SimpleNamespace

from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.models.leasing import Agreement
from app.models.listing import Listing
from app.services.agreement_document import build_document_model, render_agreement_pdf, render_agreement_text
from app.services.agreement_document import template_text as T
from app.services.agreement_document.facts import fixed_term_end_date
from tests.conftest import auth_user_cookie
from tests.test_host_offer_agreement_decisions import _make_agreement_eligible, _submit_and_approve_application
from tests.test_self_listing_restriction import _make_host_and_listing, _make_verified_renter

START = date.today() + timedelta(days=5)

AGREEMENT_DETAILS = {
    "exclusiveUseAreas": "Bedroom 2 and ensuite",
    "sharedUseAreas": "Kitchen, living room, garden",
    "hostServiceAddress": "Flat 1, 10 Notice Street, London",
    "rentDueRule": "1st of each month",
    "renewalRule": "Rolls onto a monthly periodic agreement unless either party gives notice",
    "utilities": {
        "electricity": {"payer": "HOST"},
        "internet": {"payer": "INCLUDED", "notes": "Fibre 500Mb"},
        "local_taxes": {"payer": "RENTER"},
    },
    "houseRules": {"pets": "No pets without written consent", "noise": "Quiet hours 10pm-7am"},
}


def _agreement(client, db: Session, suffix: str, *, details: dict | None = AGREEMENT_DETAILS):
    host, listing_id = _make_host_and_listing(db, email=f"tpl-host-{suffix}@test.com")
    renter = _make_verified_renter(db, email=f"tpl-renter-{suffix}@test.com")
    host_cookies = auth_user_cookie(host)
    if details is not None:
        r = client.put(f"/api/users/hosting/listings/{listing_id}", json={"agreementDetails": details}, cookies=host_cookies)
        assert r.status_code == 200, r.text

    application_id = _submit_and_approve_application(client, host, renter, listing_id)
    offer_id = client.post(f"/api/users/hosting/applications/{application_id}/offers", cookies=host_cookies).json()["id"]
    r = client.post(
        f"/api/users/hosting/offers/{offer_id}/terms",
        json={"monthlyRent": 750, "depositAmount": 750, "startDate": START.isoformat(), "termMonths": 12},
        cookies=host_cookies,
    )
    assert r.status_code == 200, r.text
    client.post(f"/api/users/hosting/offers/{offer_id}/send", cookies=host_cookies)
    assert client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(renter)).status_code == 200
    _make_agreement_eligible(db, listing_id)
    r = client.post(f"/api/users/hosting/offers/{offer_id}/agreement", cookies=host_cookies)
    assert r.status_code == 201, r.text
    agreement = db.get(Agreement, r.json()["id"])
    return host, renter, listing_id, agreement


def _pdf_text(pdf_bytes: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf_bytes)).pages)


class TestTemplateText:
    def test_has_all_42_clauses_in_order(self):
        numbers = [n for _, _, clauses in T.SECTIONS for n, _, _ in clauses]
        assert numbers == list(range(1, 43))
        assert [s[0] for s in T.SECTIONS] == ["02", "03", "04", "05", "06"]


class TestSnapshotFacts:
    def test_document_facts_are_frozen_into_the_snapshot(self, client, db_session: Session):
        _, _, listing_id, agreement = _agreement(client, db_session, "a")
        doc = agreement.versions[-1].snapshot["document"]
        listing = db_session.get(Listing, listing_id)

        assert doc["template_id"] == T.TEMPLATE_ID and doc["template_version"] == T.TEMPLATE_VERSION
        assert doc["currency"] == listing.currency
        assert doc["rent"]["amount"] == 750 and doc["rent"]["frequency_label"] == "month"
        assert doc["term"]["start_date"] == START.isoformat()
        assert doc["term"]["end_date"] == fixed_term_end_date(START, 12).isoformat()
        assert doc["property"]["exclusive_use_areas"] == "Bedroom 2 and ensuite"
        assert doc["host"]["service_address"] == "Flat 1, 10 Notice Street, London"
        assert doc["utilities"]["electricity"]["payer"] == "HOST"
        assert doc["house_rules"]["pets"] == "No pets without written consent"
        assert agreement.versions[-1].commercial_terms.currency == listing.currency

    def test_later_listing_edits_do_not_change_a_generated_version(self, client, db_session: Session):
        host, _, listing_id, agreement = _agreement(client, db_session, "b")
        before = render_agreement_text(build_document_model(agreement, agreement.versions[-1]))

        r = client.put(
            f"/api/users/hosting/listings/{listing_id}",
            json={"agreementDetails": {"hostServiceAddress": "Somewhere else entirely"}},
            cookies=auth_user_cookie(host),
        )
        assert r.status_code == 200, r.text
        db_session.refresh(agreement)
        after = render_agreement_text(build_document_model(agreement, agreement.versions[-1]))
        assert after == before
        assert "Somewhere else entirely" not in after


class TestRenderedDocument:
    def test_pdf_contains_template_sections_and_real_values(self, client, db_session: Session):
        _, renter, listing_id, agreement = _agreement(client, db_session, "c")
        currency = db_session.get(Listing, listing_id).currency

        r = client.get(f"/api/users/rentals/agreements/{agreement.id}/pdf", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        text = _pdf_text(r.content)

        for heading in ("Residential Occupancy Agreement", "Agreement Summary", "Core Agreement Terms",
                        "Schedule A", "Schedule B", "Schedule C", "Schedule D", "Execution certificate"):
            assert heading in text, heading
        for _, _, clauses in T.SECTIONS:
            for number, title, _ in clauses:
                assert f"{number}. {title}" in text, title
        assert f"ZR-AGR-{agreement.id:08d}" in text
        assert f"{currency} 750.00 per month" in text
        assert "Rs." not in text
        assert "Quiet hours 10pm-7am" in text
        # Still a DRAFT until the host sends it (certificate's final status row).
        assert f"AUD-{agreement.id:08d}-V1 | DRAFT" in text

    def test_missing_host_details_render_as_not_specified(self, client, db_session: Session):
        _, _, _, agreement = _agreement(client, db_session, "d", details=None)
        text = render_agreement_text(build_document_model(agreement, agreement.versions[-1]))
        assert "Host service address: Not specified" in text
        assert "- Pets: Not specified (Subject to mandatory law)" in text

    def test_status_follows_template_clause_40_vocabulary(self, client, db_session: Session):
        host, _, _, agreement = _agreement(client, db_session, "e")
        assert build_document_model(agreement, agreement.versions[-1]).status_label == "Draft"

        r = client.post(f"/api/users/hosting/agreements/{agreement.id}/send", cookies=auth_user_cookie(host))
        assert r.status_code == 200, r.text
        db_session.refresh(agreement)
        assert build_document_model(agreement, agreement.versions[-1]).status_label == "Ready to Sign"


class TestLegacySnapshots:
    def test_version_generated_before_document_facts_still_renders(self):
        """Versions created before snapshot["document"] existed only have the
        legacy top-level keys -- they must still render, without crashing or
        inventing values."""
        version = SimpleNamespace(
            version_no=1, content_hash=None, signature_events=[],
            snapshot={
                "listing_name": "Old listing", "listing_location": "1 Old Road", "listing_city": "London",
                "provider_name": "Old Host", "renter_name": "Old Renter",
                "monthly_rent": 500.0, "deposit_amount": 500.0, "start_date": "2026-01-01", "term_months": 6,
                "agreement_class": "room_share_agreement",
            },
        )
        agreement = SimpleNamespace(id=7, status="SIGNED", parties=[], signed_by_provider_at=None, signed_by_renter_at=None)
        model = build_document_model(agreement, version)
        assert model.status_label == "Executed"
        text = render_agreement_text(model)
        assert "Old Host" in text and "1 Old Road, London" in text
        assert "30 June 2026" in text  # fixed-term end date derived from start + term
        # Executed: the PDF can't embed its own hash, so it points at the record.
        assert "SHA-256 recorded with the immutable executed copy in the platform record (AUD-00000007-V1)" in text
        assert render_agreement_pdf(model).startswith(b"%PDF")


class TestCertificateLabels:
    def test_consent_row_uses_template_party_names(self):
        events = [
            SimpleNamespace(id=3, signer_role="provider", method="SIMPLE_ESIGN", document_hash=""),
            SimpleNamespace(id=4, signer_role="renter", method="SIMPLE_ESIGN", document_hash=""),
        ]
        version = SimpleNamespace(version_no=1, content_hash=None, signature_events=events, snapshot={"monthly_rent": 1.0})
        agreement = SimpleNamespace(id=2, status="PAYMENT_IN_PROGRESS", parties=[], signed_by_provider_at=None, signed_by_renter_at=None)
        rows = dict(build_document_model(agreement, version).certificate_rows)
        assert rows["Electronic-record consent"] == "Host SIG-00000003; Renter SIG-00000004"
        assert "Provider" not in rows["Electronic-record consent"]

    def test_verified_identity_name_is_the_legal_name(self, client, db_session: Session):
        from app.models.identity_verification import IdentityVerification

        _, renter, _, _ = _agreement(client, db_session, "names-seed")  # warm-up for helpers
        identity = db_session.query(IdentityVerification).filter(IdentityVerification.party_id == renter.party_id).first()
        assert identity is not None
        identity.extracted_name = "RAVI KUMAR SHARMA"
        identity.document_category = "identity"
        identity.status = "verified"
        db_session.commit()

        from app.services.agreement_document.facts import _legal_name, _verified_identity

        record = _verified_identity(db_session, renter.party_id)
        assert _legal_name(record, "user2") == "Ravi Kumar Sharma"
        assert _legal_name(None, "user2") == "user2"


class TestAgreementDetailsValidation:
    def test_unknown_utility_payer_is_rejected(self, client, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="tpl-host-v@test.com")
        r = client.put(
            f"/api/users/hosting/listings/{listing_id}",
            json={"agreementDetails": {"utilities": {"electricity": {"payer": "LANDLORD_MAYBE"}}}},
            cookies=auth_user_cookie(host),
        )
        assert r.status_code == 422, r.text

    def test_details_round_trip_on_the_hosted_listing(self, client, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="tpl-host-w@test.com")
        r = client.put(
            f"/api/users/hosting/listings/{listing_id}", json={"agreementDetails": AGREEMENT_DETAILS},
            cookies=auth_user_cookie(host),
        )
        assert r.status_code == 200, r.text
        details = r.json()["agreementDetails"]
        assert details["hostServiceAddress"] == AGREEMENT_DETAILS["hostServiceAddress"]
        assert details["utilities"]["internet"] == {"payer": "INCLUDED", "notes": "Fibre 500Mb"}
        assert details["houseRules"]["smoking"] == ""
