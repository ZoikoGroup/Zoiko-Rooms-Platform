"""SRCH-12 -- payment safety for unverified external leads.

An unverified external opportunity can never produce payment instructions.
Payment workflows are only reachable after the opportunity is internalized AND
verified by a recognised authority AND holds the SEPARATE payment-receipt
authority (Section 11.1); until then every payment gate must fail closed.
"""

from __future__ import annotations

from app.core.field_encryption import encrypt_text
from app.models.external_search import ExternalOpportunity
from app.services.commercial_policy import commercial_policy_service


def _opp(db, *, status, verification_status, payment_receipt=False, sublet_evidence=None,
         sublet_ok=False):
    opp = ExternalOpportunity(
        external_opportunity_id=f"opp-{status}-{verification_status}",
        source_id="s1",
        status=status,
        verification_status=verification_status,
        payment_receipt_authority_verified=payment_receipt,
        sublet_evidence_encrypted=encrypt_text(sublet_evidence) if sublet_evidence else None,
        sublet_permission_verified=sublet_ok,
    )
    db.add(opp)
    db.flush()
    return opp


class TestPaymentSafeGates:
    def test_unverified_discovered_not_eligible(self, db_session):
        opp = _opp(db_session, status="EXTERNAL_DISCOVERED", verification_status="NOT_VERIFIED_BY_ZOIKO_ROOMS")
        assert commercial_policy_service.external_payment_eligible(opp) is False

    def test_unverified_accepted_not_eligible(self, db_session):
        # Even after acceptance and a completed introduction the lead is NOT
        # payment-eligible: acceptance is not verification (SRCH-11).
        opp = _opp(db_session, status="PROVIDER_ACCEPTED", verification_status="VERIFICATION_IN_PROGRESS")
        assert commercial_policy_service.external_payment_eligible(opp) is False

    def test_verified_but_not_internalized_not_eligible(self, db_session):
        opp = _opp(db_session, status="PROVIDER_ACCEPTED", verification_status="VERIFIED_AUTHORITY")
        assert commercial_policy_service.external_payment_eligible(opp) is False

    def test_internalized_but_unverified_not_eligible(self, db_session):
        opp = _opp(db_session, status="INTERNALIZED_VERIFIED", verification_status="NOT_VERIFIED_BY_ZOIKO_ROOMS")
        assert commercial_policy_service.external_payment_eligible(opp) is False

    def test_internalized_and_verified_but_no_payment_receipt_authority_not_eligible(self, db_session):
        # Section 11.1: payment-receipt authority is separate from VERIFIED_AUTHORITY
        # and is never implied by it.
        opp = _opp(db_session, status="INTERNALIZED_VERIFIED", verification_status="VERIFIED_AUTHORITY")
        assert commercial_policy_service.external_payment_eligible(opp) is False

    def test_internalized_verified_and_payment_receipt_authority_eligible(self, db_session):
        opp = _opp(
            db_session,
            status="INTERNALIZED_VERIFIED",
            verification_status="VERIFIED_AUTHORITY",
            payment_receipt=True,
        )
        assert commercial_policy_service.external_payment_eligible(opp) is True

    def test_payment_receipt_authority_never_carries_unverified_lead(self, db_session):
        # A sublet lead whose permission evidence is still unresolved fails
        # closed even with the receipt flag set.
        opp = _opp(
            db_session,
            status="INTERNALIZED_VERIFIED",
            verification_status="VERIFIED_AUTHORITY",
            payment_receipt=True,
            sublet_evidence="landlord consent letter",
            sublet_ok=False,
        )
        assert commercial_policy_service.external_payment_eligible(opp) is False