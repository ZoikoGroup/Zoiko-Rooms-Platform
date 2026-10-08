"""ZR-AI-SEARCH-001 Section 11.1 -- separate verification domains.

Payment-receipt authority and landlord/agent sublet permission are distinct
from identity/property/listing-authority and are never implied by provider
acceptance. Records carrying sublet evidence WITHOUT confirmed permission fail
closed at internalization and at the payment gate.
"""

from __future__ import annotations

import pytest

from app.services.external_outreach import outreach_service
from app.services.external_search_crypto import decrypt_contact


def _opp(db, *, status="PROVIDER_ACCEPTED", verification="VERIFICATION_IN_PROGRESS",
         idx="vr-1", advertised_price_minor=65000):
    from app.models.external_search import ExternalOpportunity

    opp = ExternalOpportunity(
        external_opportunity_id=idx,
        source_id="s1",
        status=status,
        verification_status=verification,
        provider_name="Provider Ltd",
        approx_location="Glasgow central",
        advertised_price_minor=advertised_price_minor,
        advertised_price_currency="GBP",
        price_period="MONTH",
        room_type="ensuite",
    )
    db.add(opp)
    db.flush()
    return opp


class TestRecordSubletPermission:
    def test_evidence_encrypted_at_rest_and_flag_set_together(self, db_session):
        opp = _opp(db_session)
        outreach_service.record_sublet_permission(db_session, opp, evidence="landlord consent letter ref L-42")
        assert opp.sublet_permission_verified is True
        assert opp.sublet_permission_verified_at is not None
        assert opp.sublet_evidence_encrypted != "landlord consent letter ref L-42"
        assert decrypt_contact(opp.sublet_evidence_encrypted) == "landlord consent letter ref L-42"

    def test_requires_provider_acceptance(self, db_session):
        opp = _opp(db_session, status="EXTERNAL_DISCOVERED")
        with pytest.raises(PermissionError):
            outreach_service.record_sublet_permission(db_session, opp, evidence="x")


class TestRecordPaymentReceiptAuthority:
    def test_separate_flag_independent_of_acceptance(self, db_session):
        opp = _opp(db_session, verification="NOT_VERIFIED_BY_ZOIKO_ROOMS")
        # Before the journey starts the authority cannot be recorded.
        with pytest.raises(PermissionError):
            outreach_service.record_payment_receipt_authority(db_session, opp)
        # Within an in-progress journey the authority is recorded separately.
        outreach_service.start_verification(db_session, opp)
        outreach_service.record_payment_receipt_authority(db_session, opp)
        assert opp.payment_receipt_authority_verified is True
        assert opp.payment_receipt_authority_verified_at is not None

    def test_acceptance_alone_never_sets_it(self, db_session):
        opp = _opp(db_session)
        assert opp.payment_receipt_authority_verified is False


class TestInternalizeSubletGate:
    def test_internalize_fails_closed_on_unresolved_sublet(self, db_session):
        opp = _opp(db_session)
        # VERIFIED_AUTHORITY but sublet evidence without confirmed permission.
        opp.verification_status = "VERIFIED_AUTHORITY"
        from app.services.external_search_crypto import encrypt_optional

        opp.sublet_evidence_encrypted = encrypt_optional("lease agreement mentions sublet permitted")
        db_session.flush()
        with pytest.raises(PermissionError):
            outreach_service.internalize_listing(db_session, opp)
        assert opp.status == "PROVIDER_ACCEPTED"

    def test_internalize_allows_sublet_when_permission_confirmed(self, db_session):
        opp = _opp(db_session)
        opp.verification_status = "VERIFIED_AUTHORITY"
        db_session.flush()
        outreach_service.record_sublet_permission(db_session, opp, evidence="landlord email consent")
        listing = outreach_service.internalize_listing(db_session, opp)
        assert listing is not None and listing.id == opp.internal_listing_id
        assert opp.status == "INTERNALIZED_VERIFIED"

    def test_internalize_still_requires_verified_authority(self, db_session):
        opp = _opp(db_session)
        with pytest.raises(PermissionError):
            outreach_service.internalize_listing(db_session, opp)