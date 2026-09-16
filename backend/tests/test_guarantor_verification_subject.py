"""ZR-ENG-CLR-012 Section 15/AC-21/QA-33: 'Each person is its own
Verification Subject... Guarantor verification is a separate case with
separate permissions,' including consent. Adding a guarantor to an
already-SIGNED agreement goes through the real ADDENDUM amendment pipeline
(crud/agreement_amendments.py) -- new immutable version, audit trail -- and
their consent is recorded independently (wet-ink evidence, since a
guarantor has no login to self-serve-sign with), never by reusing
Agreement.signed_by_provider_at/signed_by_renter_at."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.crud.agreement_amendments import approve_amendment, classify_amendment, propose_guarantor_addition, request_amendment
from app.crud.agreement_party import get_agreement_party_or_404, record_guarantor_consent
from app.crud.identity_verification import get_valid_identity_credential, verify_identity_verification
from app.crud.screening import list_screening_checks_for_party, open_screening_check
from app.models.identity_verification import IdentityVerification
from app.models.leasing import Agreement
from tests.conftest import _make_admin, auth_admin_cookie
from tests.test_agreement_engine_extensions_2 import _full_signed_agreement
from tests.test_room_hold_atomicity import _make_listing_with_room, _make_verified_renter


def _signed_agreement(client, db_session: Session, email_suffix: str) -> tuple[int, dict]:
    listing_id, _room_id = _make_listing_with_room(db_session)
    super_admin = _make_admin(db_session, email=f"gvs-admin-{email_suffix}@test.com", role="super_admin")
    admin_cookies = auth_admin_cookie(super_admin)
    renter = _make_verified_renter(db_session, email=f"gvs-renter-{email_suffix}@test.com")
    agreement_id = _full_signed_agreement(client, db_session, admin_cookies, renter, listing_id)
    return agreement_id, admin_cookies


def _add_guarantor_via_amendment(db_session: Session, admin, agreement_id: int, *, legal_name: str, contact_email: str):
    agreement = db_session.get(Agreement, agreement_id)
    amendment = request_amendment(db_session, agreement, admin, "Landlord requires a guarantor")
    classify_amendment(db_session, amendment, admin, "ADDENDUM")
    propose_guarantor_addition(db_session, amendment, admin, legal_name=legal_name, contact_email=contact_email)
    approve_amendment(db_session, amendment, admin)
    db_session.refresh(agreement)
    guarantor_party = next(p for p in agreement.parties if p.role == "guarantor")
    return guarantor_party


class TestGuarantorAdditionGoesThroughAmendmentPipeline:
    def test_guarantor_party_is_distinct_from_renter_party(self, client, db_session: Session):
        agreement_id, admin_cookies = _signed_agreement(client, db_session, "01")
        admin = _make_admin(db_session, email="gvs-adder-01@test.com", role="super_admin")
        agreement = db_session.get(Agreement, agreement_id)
        renter_party_id = agreement.offer.guest.user_account.party_id

        guarantor_party = _add_guarantor_via_amendment(
            db_session, admin, agreement_id, legal_name="Jane Guarantor", contact_email="jane.guarantor@test.com",
        )

        assert guarantor_party.role == "guarantor"
        assert guarantor_party.party_id is not None
        assert guarantor_party.party_id != renter_party_id
        assert guarantor_party.consented_at is None

    def test_amendment_creates_a_new_immutable_version(self, client, db_session: Session):
        agreement_id, admin_cookies = _signed_agreement(client, db_session, "02")
        admin = _make_admin(db_session, email="gvs-adder-02@test.com", role="super_admin")
        agreement = db_session.get(Agreement, agreement_id)
        version_count_before = len(agreement.versions)

        _add_guarantor_via_amendment(
            db_session, admin, agreement_id, legal_name="Jane Guarantor", contact_email="jane.guarantor@test.com",
        )

        db_session.refresh(agreement)
        assert len(agreement.versions) == version_count_before + 1

    def test_guarantor_cannot_sign_via_the_two_party_signature_endpoint(self, client, db_session: Session):
        """Guardrail: adding 'guarantor' to required_signers would silently
        overwrite signed_by_renter_at inside _apply_signature (it only knows
        'provider' and the else-branch). This proves that never happens --
        the amendment must NOT add guarantor to required_signers."""
        agreement_id, admin_cookies = _signed_agreement(client, db_session, "03")
        admin = _make_admin(db_session, email="gvs-adder-03@test.com", role="super_admin")
        _add_guarantor_via_amendment(
            db_session, admin, agreement_id, legal_name="Jane Guarantor", contact_email="jane.guarantor@test.com",
        )
        agreement = db_session.get(Agreement, agreement_id)
        required_signers = agreement.versions[-1].snapshot.get("required_signers")
        assert "guarantor" not in required_signers


class TestGuarantorConsent:
    def test_record_consent_requires_evidence(self, client, db_session: Session):
        agreement_id, admin_cookies = _signed_agreement(client, db_session, "04")
        admin = _make_admin(db_session, email="gvs-adder-04@test.com", role="super_admin")
        guarantor_party = _add_guarantor_via_amendment(
            db_session, admin, agreement_id, legal_name="Jane Guarantor", contact_email="jane.guarantor@test.com",
        )
        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            record_guarantor_consent(db_session, guarantor_party, admin, evidence_ref="")

    def test_record_consent_succeeds_with_evidence(self, client, db_session: Session):
        agreement_id, admin_cookies = _signed_agreement(client, db_session, "05")
        admin = _make_admin(db_session, email="gvs-adder-05@test.com", role="super_admin")
        guarantor_party = _add_guarantor_via_amendment(
            db_session, admin, agreement_id, legal_name="Jane Guarantor", contact_email="jane.guarantor@test.com",
        )
        updated = record_guarantor_consent(db_session, guarantor_party, admin, evidence_ref="scan-of-signed-form.pdf")
        assert updated.consented_at is not None
        assert updated.consent_method == "WET_INK"
        assert updated.consent_evidence_ref == "scan-of-signed-form.pdf"

    def test_cannot_record_consent_twice(self, client, db_session: Session):
        agreement_id, admin_cookies = _signed_agreement(client, db_session, "06")
        admin = _make_admin(db_session, email="gvs-adder-06@test.com", role="super_admin")
        guarantor_party = _add_guarantor_via_amendment(
            db_session, admin, agreement_id, legal_name="Jane Guarantor", contact_email="jane.guarantor@test.com",
        )
        record_guarantor_consent(db_session, guarantor_party, admin, evidence_ref="first.pdf")
        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            record_guarantor_consent(db_session, guarantor_party, admin, evidence_ref="second.pdf")


class TestGuarantorVerificationSubjectIndependence:
    def test_guarantor_identity_credential_never_leaks_to_renter(self, client, db_session: Session):
        agreement_id, admin_cookies = _signed_agreement(client, db_session, "07")
        admin = _make_admin(db_session, email="gvs-adder-07@test.com", role="super_admin")
        agreement = db_session.get(Agreement, agreement_id)
        renter_party_id = agreement.offer.guest.user_account.party_id

        guarantor_party = _add_guarantor_via_amendment(
            db_session, admin, agreement_id, legal_name="Jane Guarantor", contact_email="jane.guarantor@test.com",
        )
        guarantor_party_id = guarantor_party.party_id

        record = IdentityVerification(party_id=guarantor_party_id, document_type="passport", status="pending")
        db_session.add(record)
        db_session.commit()
        verify_identity_verification(db_session, record, admin)

        assert get_valid_identity_credential(db_session, guarantor_party_id) is not None
        assert get_valid_identity_credential(db_session, renter_party_id) is None

    def test_guarantor_screening_check_is_independent_of_renter_screening(self, client, db_session: Session):
        agreement_id, admin_cookies = _signed_agreement(client, db_session, "08")
        admin = _make_admin(db_session, email="gvs-adder-08@test.com", role="super_admin")
        agreement = db_session.get(Agreement, agreement_id)
        renter_party_id = agreement.offer.guest.user_account.party_id

        guarantor_party = _add_guarantor_via_amendment(
            db_session, admin, agreement_id, legal_name="Jane Guarantor", contact_email="jane.guarantor@test.com",
        )
        guarantor_party_id = guarantor_party.party_id

        open_screening_check(
            db_session, admin, party_id=guarantor_party_id, jurisdiction_code="England",
            check_type="AFFORDABILITY", permissible_purpose="Guarantor affordability check",
        )

        assert len(list_screening_checks_for_party(db_session, guarantor_party_id)) == 1
        assert list_screening_checks_for_party(db_session, renter_party_id) == []


class TestGuarantorAmendmentRoutes:
    def test_full_http_flow_request_classify_propose_approve_consent(self, client, db_session: Session):
        agreement_id, admin_cookies = _signed_agreement(client, db_session, "09")

        r = client.post(f"/api/leasing/agreements/{agreement_id}/amendments", json={"reason": "Guarantor required"}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        amendment_id = r.json()["id"]

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/amendments/{amendment_id}/classify",
            json={"amendmentType": "ADDENDUM"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/amendments/{amendment_id}/propose-guarantor",
            json={"legalName": "Jane Guarantor", "contactEmail": "jane.guarantor@test.com"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["proposedGuarantor"]["legalName"] == "Jane Guarantor"

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/amendments/{amendment_id}/approve", cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.get(f"/api/leasing/agreements/{agreement_id}/parties", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        guarantor_row = next(p for p in r.json() if p["role"] == "guarantor")
        assert guarantor_row["consentedAt"] is None

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/parties/{guarantor_row['id']}/guarantor-consent",
            json={"evidenceRef": "scan-of-signed-form.pdf"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["consentedAt"] is not None
        assert r.json()["consentMethod"] == "WET_INK"

    def test_propose_guarantor_requires_addendum_classification(self, client, db_session: Session):
        agreement_id, admin_cookies = _signed_agreement(client, db_session, "10")

        r = client.post(f"/api/leasing/agreements/{agreement_id}/amendments", json={"reason": "Guarantor required"}, cookies=admin_cookies)
        amendment_id = r.json()["id"]
        client.post(
            f"/api/leasing/agreements/{agreement_id}/amendments/{amendment_id}/classify",
            json={"amendmentType": "MATERIAL_CHANGE"}, cookies=admin_cookies,
        )
        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/amendments/{amendment_id}/propose-guarantor",
            json={"legalName": "Jane Guarantor"}, cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text
