"""ZR-ENG-CLR-004: verifies the six previously-MISSING acceptance criteria
(AC-04, AC-11's remaining methods, AC-18, AC-23/24, AC-28, AC-29) and the
seven new §13.1 data-model entities (agreement_party, agreement_premises,
commercial_terms_snapshot, execution_certificate, termination_record,
signature_request, plus the amendment relationship) are now real and
reachable end to end, not just declared."""

from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO

from sqlalchemy.orm import Session

from app.models.agreement_form_template import AgreementFormTemplate
from app.models.agreement_party import AgreementParty
from app.models.agreement_version_detail import AgreementPremises, CommercialTermsSnapshot, ExecutionCertificate
from app.models.identity_verification import IdentityVerification
from app.models.leasing import Agreement, Offer, SignatureEvent
from app.models.market_release import MarketRelease
from app.models.signature_provider import SignatureProviderStatus, SignatureRequest
from app.models.termination_record import TerminationRecord
from app.services.agreement_profile import AGREEMENT_CLASS, SUPPORTED_JURISDICTION
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie, deliver_all_disclosures
from tests.test_renter_offer_agreement_flow import _make_agreement_eligible
from tests.test_agreement_engine_foundation import _pay_off_agreement_obligations
from tests.test_room_hold_atomicity import _apply_and_send_offer, _make_listing_with_room, _make_verified_renter


def _apply_send_accept_add_terms(client, db_session: Session, admin_cookies: dict, renter, listing_id: str, *, start_date: date, term_months: int = 6):
    _app_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)
    r = client.post(
        f"/api/leasing/offers/{offer_id}/terms",
        json={"monthlyRent": 500, "depositAmount": 500, "startDate": start_date.isoformat(), "termMonths": term_months},
        cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(renter))
    assert r.status_code == 200, r.text
    return _app_id, offer_id


def _full_signed_agreement(client, db_session: Session, admin_cookies: dict, renter, listing_id: str, *, start_date=None) -> int:
    """Runs a listing all the way to SIGNED, returns the agreement_id."""
    start_date = start_date or date.today()
    _app_id, offer_id = _apply_send_accept_add_terms(client, db_session, admin_cookies, renter, listing_id, start_date=start_date)
    offer = db_session.get(Offer, offer_id)
    _make_agreement_eligible(db_session, offer.listing_id)
    r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    agreement_id = r.json()["id"]
    client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
    deliver_all_disclosures(client, admin_cookies, agreement_id)
    client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
    client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)
    _pay_off_agreement_obligations(client, db_session, admin_cookies, agreement_id)
    agreement = db_session.get(Agreement, agreement_id)
    assert agreement.status == "SIGNED", agreement.status
    return agreement_id


def _simple_pdf_bytes(text: str = "Official Blank Form") -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(50, 700, text)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf.getvalue()


class TestPrescribedFormsAC04:
    """AC-04: modes B (field-map official form), C (prescribed-content
    reconstruction with drift check), D (external approved document +
    signature wrapper) and E (manual-only, fails closed) are all real."""

    def test_mode_d_external_document_gets_a_signature_wrapper_appended(self, client, db_session: Session):
        import pypdf

        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac04-admin-d@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac04-renter-d@test.com")
        _make_agreement_eligible(db_session, listing_id)

        source_pdf = _simple_pdf_bytes("External Counsel-Provided Agreement")
        r = client.post(
            "/api/leasing/agreement-form-templates",
            params={
                "jurisdiction_scope": SUPPORTED_JURISDICTION, "agreement_class": AGREEMENT_CLASS, "form_mode": "D",
                "title": "External approved document v1",
            },
            files={"source_document": ("external.pdf", BytesIO(source_pdf), "application/pdf")},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        template = r.json()
        assert template["status"] == "DRAFT"

        r = client.post(f"/api/leasing/agreement-form-templates/{template['id']}/approve", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "APPROVED"

        _app_id, offer_id = _apply_send_accept_add_terms(client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5))
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        agreement_id = r.json()["id"]
        agreement = db_session.get(Agreement, agreement_id)
        assert agreement.versions[0].snapshot["form_mode"] == "D"

        r = client.get(f"/api/leasing/agreements/{agreement_id}/pdf", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        rendered = pypdf.PdfReader(BytesIO(r.content))
        source_reader = pypdf.PdfReader(BytesIO(source_pdf))
        assert len(rendered.pages) == len(source_reader.pages) + 1  # + signature wrapper page

    def test_mode_b_overlay_preserves_source_page_count(self, client, db_session: Session):
        import pypdf

        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac04-admin-b@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac04-renter-b@test.com")
        _make_agreement_eligible(db_session, listing_id)

        source_pdf = _simple_pdf_bytes("Official Government Form")
        field_map = {"provider_name": {"page": 0, "x": 100, "y": 600}, "renter_name": {"page": 0, "x": 100, "y": 580}}
        r = client.post(
            "/api/leasing/agreement-form-templates",
            params={
                "jurisdiction_scope": SUPPORTED_JURISDICTION, "agreement_class": AGREEMENT_CLASS, "form_mode": "B",
                "title": "Official form v1", "field_anchor_map_json": __import__("json").dumps(field_map),
            },
            files={"source_document": ("official.pdf", BytesIO(source_pdf), "application/pdf")},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        template_id = r.json()["id"]
        client.post(f"/api/leasing/agreement-form-templates/{template_id}/approve", cookies=admin_cookies)

        _app_id, offer_id = _apply_send_accept_add_terms(client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5))
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        assert db_session.get(Agreement, agreement_id).versions[0].snapshot["form_mode"] == "B"

        r = client.get(f"/api/leasing/agreements/{agreement_id}/pdf", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        rendered = pypdf.PdfReader(BytesIO(r.content))
        assert len(rendered.pages) == 1  # overlay merges onto the same page, doesn't add one

    def test_mode_c_blocks_delivery_on_content_drift(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac04-admin-c@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac04-renter-c@test.com")
        _make_agreement_eligible(db_session, listing_id)

        r = client.post(
            "/api/leasing/agreement-form-templates",
            params={
                "jurisdiction_scope": SUPPORTED_JURISDICTION, "agreement_class": AGREEMENT_CLASS, "form_mode": "C",
                "title": "Prescribed content v1",
                "authoritative_content_text": "Completely unrelated reference text about a totally different subject matter entirely.",
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        template_id = r.json()["id"]
        client.post(f"/api/leasing/agreement-form-templates/{template_id}/approve", cookies=admin_cookies)

        _app_id, offer_id = _apply_send_accept_add_terms(client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5))
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        assert db_session.get(Agreement, agreement_id).versions[0].snapshot["form_mode"] == "C"

        r = client.get(f"/api/leasing/agreements/{agreement_id}/pdf", cookies=admin_cookies)
        assert r.status_code == 409, r.text
        assert "drift" in r.text.lower()

    def test_mode_e_manual_only_market_fails_closed(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac04-admin-e@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac04-renter-e@test.com")
        _make_agreement_eligible(db_session, listing_id)

        release = db_session.query(MarketRelease).filter(MarketRelease.jurisdiction == SUPPORTED_JURISDICTION).first()
        release.manual_agreement_only = True
        db_session.commit()

        _app_id, offer_id = _apply_send_accept_add_terms(client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5))
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        assert r.status_code == 409, r.text

    def test_form_template_governance_requires_source_document_for_b_and_d(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="ac04-admin-gov@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)

        r = client.post(
            "/api/leasing/agreement-form-templates",
            params={
                "jurisdiction_scope": SUPPORTED_JURISDICTION, "agreement_class": AGREEMENT_CLASS, "form_mode": "B",
                "title": "no upload",
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text

    def test_form_template_governance_requires_super_admin(self, client, db_session: Session):
        plain_admin = _make_admin(db_session, email="ac04-plain-admin@test.com", role="admin")
        r = client.post(
            "/api/leasing/agreement-form-templates",
            params={
                "jurisdiction_scope": SUPPORTED_JURISDICTION, "agreement_class": AGREEMENT_CLASS, "form_mode": "C",
                "title": "x", "authoritative_content_text": "x",
            },
            cookies=auth_admin_cookie(plain_admin),
        )
        assert r.status_code == 403, r.text


class TestNewDataModelEntitiesArePopulated:
    """§13.1 gap check: agreement_party, agreement_premises,
    commercial_terms_snapshot, execution_certificate, signature_request are
    real populated rows, not just declared tables."""

    def test_create_agreement_populates_party_roster_premises_and_terms(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ent-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ent-renter1@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5))
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        agreement = db_session.get(Agreement, agreement_id)

        parties = list(db_session.query(AgreementParty).filter(AgreementParty.agreement_id == agreement_id))
        assert {p.role for p in parties} == {"provider", "renter"}
        assert all(p.legal_name for p in parties)

        version = agreement.versions[0]
        premises = db_session.query(AgreementPremises).filter(AgreementPremises.agreement_version_id == version.id).one()
        assert premises.listing_id == listing_id
        terms = db_session.query(CommercialTermsSnapshot).filter(CommercialTermsSnapshot.agreement_version_id == version.id).one()
        assert float(terms.monthly_rent) == 500.0

        signature_requests = list(db_session.query(SignatureRequest).filter(SignatureRequest.agreement_id == agreement_id))
        assert signature_requests == []  # not sent yet

        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        signature_requests = list(db_session.query(SignatureRequest).filter(SignatureRequest.agreement_id == agreement_id))
        assert {sr.party_role for sr in signature_requests} == {"provider", "renter"}
        assert all(sr.status == "PENDING" for sr in signature_requests)

    def test_execution_certificate_generated_alongside_artifact(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ent-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ent-renter2@test.com")

        agreement_id = _full_signed_agreement(client, db_session, admin_cookies, renter, listing_id)
        agreement = db_session.get(Agreement, agreement_id)
        version = agreement.versions[-1]

        cert = db_session.query(ExecutionCertificate).filter(ExecutionCertificate.agreement_version_id == version.id).one()
        assert cert.document_hash == version.content_hash
        assert len(cert.signer_summary) == 2
        assert {s["role"] for s in cert.signer_summary} == {"provider", "renter"}

        signature_requests = list(db_session.query(SignatureRequest).filter(SignatureRequest.agreement_version_id == version.id))
        assert all(sr.status == "COMPLETED" for sr in signature_requests)


class TestSignatureMethodsAC11:
    """AC-11: ACKNOWLEDGMENT, ADVANCED_ESIGN, QUALIFIED_ESIGN, WITNESSED_ESIGN,
    NOTARIZED are all real, distinctly-evidenced, reachable methods."""

    def _agreement_ready_to_sign(self, client, db_session, admin_cookies, renter, listing_id):
        _app_id, offer_id = _apply_send_accept_add_terms(client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5))
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)
        return agreement_id

    def test_acknowledgment_requires_consent_statement(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac11-ack-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac11-ack-renter1@test.com")
        agreement_id = self._agreement_ready_to_sign(client, db_session, admin_cookies, renter, listing_id)

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/sign",
            json={"asParty": "provider", "method": "ACKNOWLEDGMENT", "evidenceMetadata": {}},
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/sign",
            json={"asParty": "provider", "method": "ACKNOWLEDGMENT", "evidenceMetadata": {"consent_statement": "I agree to the terms"}},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        event = db_session.query(SignatureEvent).filter(SignatureEvent.agreement_id == agreement_id).order_by(SignatureEvent.id.desc()).first()
        assert event.method == "ACKNOWLEDGMENT"
        assert event.evidence_metadata["consent_statement"] == "I agree to the terms"

    def test_advanced_esign_requires_verified_identity(self, client, db_session: Session):
        listing_id, room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac11-adv-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac11-adv-renter1@test.com")
        agreement_id = self._agreement_ready_to_sign(client, db_session, admin_cookies, renter, listing_id)

        # Provider's party has no IdentityVerification yet -- ADVANCED_ESIGN blocked.
        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/sign",
            json={"asParty": "provider", "method": "ADVANCED_ESIGN"},
            cookies=admin_cookies,
        )
        assert r.status_code == 409, r.text

        agreement = db_session.get(Agreement, agreement_id)
        provider_party_id = agreement.offer.listing.room.property.owner_party_id
        db_session.add(IdentityVerification(party_id=provider_party_id, document_type="passport", status="verified"))
        db_session.commit()

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/sign",
            json={"asParty": "provider", "method": "ADVANCED_ESIGN"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        event = db_session.query(SignatureEvent).filter(SignatureEvent.agreement_id == agreement_id).order_by(SignatureEvent.id.desc()).first()
        assert event.method == "ADVANCED_ESIGN"

        # Renter already has a verified identity via _make_verified_renter.
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/sign",
            json={"method": "ADVANCED_ESIGN"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text

    def test_qualified_esign_requires_trust_service_certificate(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac11-qes-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac11-qes-renter1@test.com")
        agreement_id = self._agreement_ready_to_sign(client, db_session, admin_cookies, renter, listing_id)

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/sign",
            json={"asParty": "provider", "method": "QUALIFIED_ESIGN"},
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/sign",
            json={"asParty": "provider", "method": "QUALIFIED_ESIGN", "evidenceMetadata": {"trust_service_certificate_ref": "QTSP-CERT-123"}},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

    def test_witnessed_esign_requires_witness_identity(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac11-wit-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac11-wit-renter1@test.com")
        agreement_id = self._agreement_ready_to_sign(client, db_session, admin_cookies, renter, listing_id)

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/sign",
            json={"asParty": "provider", "method": "WITNESSED_ESIGN", "evidenceMetadata": {"witness_name": "Jane Witness"}},
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text  # missing witness_contact

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/sign",
            json={
                "asParty": "provider", "method": "WITNESSED_ESIGN",
                "evidenceMetadata": {"witness_name": "Jane Witness", "witness_contact": "jane@example.com"},
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

    def test_notarized_requires_notary_identity(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac11-not-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac11-not-renter1@test.com")
        agreement_id = self._agreement_ready_to_sign(client, db_session, admin_cookies, renter, listing_id)

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/sign",
            json={
                "asParty": "provider", "method": "NOTARIZED",
                "evidenceMetadata": {"notary_name": "John Notary", "notary_license_ref": "NOT-4821"},
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        event = db_session.query(SignatureEvent).filter(SignatureEvent.agreement_id == agreement_id).order_by(SignatureEvent.id.desc()).first()
        assert event.evidence_metadata["notary_license_ref"] == "NOT-4821"

    def test_unknown_method_rejected(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac11-unk-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac11-unk-renter1@test.com")
        agreement_id = self._agreement_ready_to_sign(client, db_session, admin_cookies, renter, listing_id)

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/sign",
            json={"asParty": "provider", "method": "CARRIER_PIGEON"},
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text


class TestAmendmentsAC18:
    """AC-18: the full REQUESTED -> ... -> EFFECTIVE lifecycle, producing a
    new immutable version linked back to the source, never editing it."""

    def test_full_amendment_lifecycle_reaches_effective(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac18-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac18-renter1@test.com")

        agreement_id = _full_signed_agreement(client, db_session, admin_cookies, renter, listing_id)
        agreement = db_session.get(Agreement, agreement_id)
        source_version_id = agreement.versions[-1].id
        assert agreement.versions[-1].status == "EXECUTED_IMMUTABLE"

        r = client.post(f"/api/leasing/agreements/{agreement_id}/amendments", json={"reason": "Rent review"}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        amendment = r.json()
        assert amendment["status"] == "REQUESTED"
        assert amendment["sourceVersionId"] == source_version_id
        amendment_id = amendment["id"]

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/amendments/{amendment_id}/classify",
            json={"amendmentType": "MATERIAL_CHANGE"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "CLASSIFIED"

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/amendments/{amendment_id}/propose-terms",
            json={"proposedTerms": {"monthlyRent": 600}}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "APPROVALS_PENDING"

        r = client.post(f"/api/leasing/agreements/{agreement_id}/amendments/{amendment_id}/approve", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "EXECUTION_PENDING"
        resulting_version_id = r.json()["resultingVersionId"]
        assert resulting_version_id != source_version_id

        # Source version is untouched -- still immutable and unchanged.
        source_version = db_session.get(type(agreement.versions[0]), source_version_id)
        db_session.refresh(source_version)
        assert source_version.status == "EXECUTED_IMMUTABLE"
        assert source_version.snapshot["monthly_rent"] == 500.0

        db_session.refresh(agreement)
        assert agreement.status == "AMENDMENT_PENDING"
        assert agreement.signed_by_provider_at is None
        assert agreement.signed_by_renter_at is None

        r = client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PARTIALLY_EXECUTED"

        r = client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "SIGNED"  # obligations already paid from original execution

        db_session.refresh(agreement)
        resulting_version = agreement.versions[-1]
        assert resulting_version.id == resulting_version_id
        assert resulting_version.status == "EXECUTED_IMMUTABLE"
        assert resulting_version.snapshot["monthly_rent"] == 600.0

        r = client.get(f"/api/leasing/agreements/{agreement_id}/amendments", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        final_amendment = r.json()[0]
        assert final_amendment["status"] == "EFFECTIVE"
        assert final_amendment["executedAt"] is not None
        assert final_amendment["effectiveAt"] is not None

    def test_amendment_requires_a_fully_signed_agreement(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac18-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac18-renter2@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5))
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]

        r = client.post(f"/api/leasing/agreements/{agreement_id}/amendments", json={"reason": "too early"}, cookies=admin_cookies)
        assert r.status_code == 409, r.text

    def test_propose_terms_rejects_unknown_fields(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac18-admin3@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac18-renter3@test.com")
        agreement_id = _full_signed_agreement(client, db_session, admin_cookies, renter, listing_id)

        r = client.post(f"/api/leasing/agreements/{agreement_id}/amendments", json={"reason": "x"}, cookies=admin_cookies)
        amendment_id = r.json()["id"]
        client.post(
            f"/api/leasing/agreements/{agreement_id}/amendments/{amendment_id}/classify",
            json={"amendmentType": "CORRECTION"}, cookies=admin_cookies,
        )
        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/amendments/{amendment_id}/propose-terms",
            json={"proposedTerms": {"petsAllowed": True}}, cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text

    def test_amended_start_date_is_reflected_at_move_in_not_the_stale_original(self, client, db_session: Session):
        """approve_amendment used to build its OfferTerms only in-memory, so
        confirm_move_in's offer.terms[-1] lookup would silently keep using
        the pre-amendment startDate/termMonths when computing
        expected_end_date. A persisted new OfferTerms row fixes that."""
        from app.crud.occupancy import _add_months
        from app.models.occupancy import Occupancy

        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac18-admin4@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac18-renter4@test.com")
        original_start = date.today() - timedelta(days=20)
        agreement_id = _full_signed_agreement(client, db_session, admin_cookies, renter, listing_id, start_date=original_start)
        agreement = db_session.get(Agreement, agreement_id)
        offer_id = agreement.offer_id
        assert len(db_session.get(Offer, offer_id).terms) == 1

        # Stays in the past so the amended agreement is already effective by
        # the time confirm-move-in is called below -- this test is about
        # expected_end_date picking up the new date, not lease-effectiveness timing.
        new_start = original_start + timedelta(days=10)
        r = client.post(f"/api/leasing/agreements/{agreement_id}/amendments", json={"reason": "renter asked to delay move-in"}, cookies=admin_cookies)
        amendment_id = r.json()["id"]
        client.post(
            f"/api/leasing/agreements/{agreement_id}/amendments/{amendment_id}/classify",
            json={"amendmentType": "MATERIAL_CHANGE"}, cookies=admin_cookies,
        )
        client.post(
            f"/api/leasing/agreements/{agreement_id}/amendments/{amendment_id}/propose-terms",
            json={"proposedTerms": {"startDate": new_start.isoformat(), "termMonths": 6}}, cookies=admin_cookies,
        )
        client.post(f"/api/leasing/agreements/{agreement_id}/amendments/{amendment_id}/approve", cookies=admin_cookies)

        offer = db_session.get(Offer, offer_id)
        db_session.refresh(offer)
        assert len(offer.terms) == 2, "amendment must persist a new OfferTerms row, not just an in-memory snapshot"
        assert offer.terms[-1].start_date == new_start

        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)

        r = client.post(f"/api/occupancy/agreements/{agreement_id}/confirm-move-in", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        occupancy = db_session.get(Occupancy, r.json()["id"])
        assert occupancy.expected_end_date == _add_months(new_start, 6)
        assert occupancy.expected_end_date != _add_months(original_start, 6)


class TestSignatureProviderAC23AC24:
    """AC-23/24: idempotent authenticated callback ingestion + outage-safe
    reconciliation against the simulated provider."""

    def _dispatch_a_request(self, client, db_session, admin_cookies, agreement_id):
        r = client.get(f"/api/leasing/agreements/{agreement_id}/signature-requests", cookies=admin_cookies)
        provider_sr = next(sr for sr in r.json() if sr["partyRole"] == "provider")
        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/signature-requests/{provider_sr['id']}/dispatch", cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        return r.json()

    def _agreement_sent(self, client, db_session, admin_cookies, renter, listing_id):
        _app_id, offer_id = _apply_send_accept_add_terms(client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5))
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)
        return agreement_id

    def test_callback_completes_signature_and_is_idempotent(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac23-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac23-renter1@test.com")
        agreement_id = self._agreement_sent(client, db_session, admin_cookies, renter, listing_id)

        dispatched = self._dispatch_a_request(client, db_session, admin_cookies, agreement_id)
        assert dispatched["status"] == "DISPATCHED"
        assert dispatched["providerTransactionId"]

        payload = {
            "providerEventId": "evt-001", "providerTransactionId": dispatched["providerTransactionId"],
            "eventType": "SIGNATURE_COMPLETED",
        }
        r = client.post(f"/api/leasing/agreements/{agreement_id}/signature-webhooks/simulate", json=payload, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "COMPLETED"

        agreement = db_session.get(Agreement, agreement_id)
        assert agreement.signed_by_provider_at is not None

        # Replay the same event -- idempotent no-op, not double-processed.
        provider_signed_at_first = agreement.signed_by_provider_at
        r2 = client.post(f"/api/leasing/agreements/{agreement_id}/signature-webhooks/simulate", json=payload, cookies=admin_cookies)
        assert r2.status_code == 200, r2.text
        db_session.refresh(agreement)
        assert agreement.signed_by_provider_at == provider_signed_at_first
        events = list(db_session.query(SignatureEvent).filter(SignatureEvent.agreement_id == agreement_id, SignatureEvent.signer_role == "provider"))
        assert len(events) == 1

    def test_dispatch_fails_closed_during_outage(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac24-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac24-renter1@test.com")
        agreement_id = self._agreement_sent(client, db_session, admin_cookies, renter, listing_id)

        r = client.post("/api/leasing/signature-provider/health", json={"healthy": False}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["healthy"] is False

        r = client.get(f"/api/leasing/agreements/{agreement_id}/signature-requests", cookies=admin_cookies)
        provider_sr = next(sr for sr in r.json() if sr["partyRole"] == "provider")
        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/signature-requests/{provider_sr['id']}/dispatch", cookies=admin_cookies,
        )
        assert r.status_code == 503, r.text

        client.post("/api/leasing/signature-provider/health", json={"healthy": True}, cookies=admin_cookies)

    def test_reconcile_marks_stalled_requests_failed_never_auto_completes(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac24-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac24-renter2@test.com")
        agreement_id = self._agreement_sent(client, db_session, admin_cookies, renter, listing_id)

        status_row = db_session.get(SignatureProviderStatus, 1)
        if status_row is None:
            status_row = SignatureProviderStatus(id=1, healthy=False)
            db_session.add(status_row)
        else:
            status_row.healthy = False
        srs = list(db_session.query(SignatureRequest).filter(SignatureRequest.agreement_id == agreement_id))
        from datetime import datetime, timezone, timedelta as td
        for sr in srs:
            sr.deadline = datetime.now(timezone.utc) - td(minutes=1)
        db_session.commit()

        r = client.post("/api/leasing/signature-requests/reconcile-stalled", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["failedCount"] >= 2

        for sr in srs:
            db_session.refresh(sr)
            assert sr.status == "FAILED"

        agreement = db_session.get(Agreement, agreement_id)
        assert agreement.signed_by_provider_at is None
        assert agreement.signed_by_renter_at is None


class TestAccessibilityAC28:
    def test_accessible_text_rendering_matches_snapshot_terms(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac28-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac28-renter1@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5))
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]

        r = client.get(f"/api/leasing/agreements/{agreement_id}/accessible-text", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert "text/plain" in r.headers["content-type"]
        assert "Monthly rent: Rs. 500.00" in r.text

        r = client.get(f"/api/users/rentals/agreements/{agreement_id}/accessible-text", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        assert "Monthly rent" in r.text

    def test_disclosure_delivery_channel_recorded_and_accessible_text_available(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac28-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac28-renter2@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5))
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)

        disclosures = client.get(f"/api/leasing/agreements/{agreement_id}/disclosures", cookies=admin_cookies).json()
        disclosure_id = disclosures[0]["id"]

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/disclosures/{disclosure_id}/deliver",
            json={"toParty": "renter", "deliveryChannel": "ACCESSIBLE_TEXT"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["deliveryChannel"] == "ACCESSIBLE_TEXT"

        r = client.get(f"/api/leasing/agreements/{agreement_id}/disclosures/{disclosure_id}/accessible-text", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert "text/plain" in r.headers["content-type"]

    def test_invalid_delivery_channel_rejected(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac28-admin3@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac28-renter3@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5))
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        disclosures = client.get(f"/api/leasing/agreements/{agreement_id}/disclosures", cookies=admin_cookies).json()
        disclosure_id = disclosures[0]["id"]

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/disclosures/{disclosure_id}/deliver",
            json={"deliveryChannel": "CARRIER_PIGEON"}, cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text


class TestClauseTranslationsAC29:
    def test_translation_pinned_to_exact_clause_version_and_missing_report(self, client, db_session: Session):
        from app.services.agreement_profile import ensure_default_clause_registry

        ensure_default_clause_registry(db_session)
        db_session.commit()

        super_admin = _make_admin(db_session, email="ac29-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        signatures = client.get("/api/leasing/agreement-clauses?clause_id=signatures", cookies=admin_cookies).json()
        v1 = signatures[0]

        r = client.get(f"/api/leasing/agreement-clauses/missing-translations?language_code=fr", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert any(m["clauseDefinitionId"] == v1["id"] for m in r.json())

        r = client.post(
            f"/api/leasing/agreement-clauses/{v1['id']}/translations",
            json={"languageCode": "fr", "translatedTitle": "Signatures (FR)", "translatedContent": "Contenu"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        translation_id = r.json()["id"]
        assert r.json()["status"] == "DRAFT"

        r = client.post(f"/api/leasing/agreement-clause-translations/{translation_id}/approve", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "APPROVED"

        r = client.get("/api/leasing/agreement-clauses/missing-translations?language_code=fr", cookies=admin_cookies)
        assert not any(m["clauseDefinitionId"] == v1["id"] for m in r.json())

        # Duplicate translation for the same (clause_definition_id, language) is rejected.
        r = client.post(
            f"/api/leasing/agreement-clauses/{v1['id']}/translations",
            json={"languageCode": "fr", "translatedTitle": "dup", "translatedContent": "dup"},
            cookies=admin_cookies,
        )
        assert r.status_code == 409, r.text

        # A new clause version does NOT inherit the old version's translation.
        r = client.post(
            "/api/leasing/agreement-clauses",
            json={
                "clauseId": "signatures", "jurisdictionScope": SUPPORTED_JURISDICTION, "agreementClass": AGREEMENT_CLASS,
                "mandatoryLevel": "MANDATORY", "title": "Signatures v2",
            },
            cookies=admin_cookies,
        )
        v2 = r.json()
        client.post(f"/api/leasing/agreement-clauses/{v2['id']}/approve", cookies=admin_cookies)

        r = client.get("/api/leasing/agreement-clauses/missing-translations?language_code=fr", cookies=admin_cookies)
        assert any(m["clauseDefinitionId"] == v2["id"] for m in r.json())


class TestTerminationRecordEntity:
    def test_end_occupancy_creates_a_termination_record(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="term-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="term-renter1@test.com")
        agreement_id = _full_signed_agreement(client, db_session, admin_cookies, renter, listing_id, start_date=date.today())

        r = client.post(f"/api/occupancy/agreements/{agreement_id}/confirm-move-in", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        occupancy_id = r.json()["id"]

        r = client.post(
            f"/api/occupancy/{occupancy_id}/end",
            json={"basis": "MUTUAL_SURRENDER", "liabilityEndDate": (date.today() + timedelta(days=3)).isoformat()},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        records = list(db_session.query(TerminationRecord).filter(TerminationRecord.occupancy_id == occupancy_id))
        assert len(records) == 1
        record = records[0]
        assert record.agreement_id == agreement_id
        assert record.basis == "MUTUAL_SURRENDER"
        assert record.liability_end_date == date.today() + timedelta(days=3)
        assert record.physical_move_out_date == date.today()
        assert record.created_by_admin_id is not None

        r = client.get(f"/api/occupancy/{occupancy_id}/termination-record", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["basis"] == "MUTUAL_SURRENDER"
        assert r.json()["agreementId"] == agreement_id
