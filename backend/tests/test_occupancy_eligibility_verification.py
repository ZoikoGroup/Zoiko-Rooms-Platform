"""ZR-ENG-CLR-012 Section 9/AC-05: occupancy-eligibility as its own,
jurisdiction-scoped verification requirement -- 'There is no global "right
to rent" check... must not export England-specific immigration checking to
other markets.' Also covers Section 2's 'no raw document becomes a
permanent fact without a credential' doctrine (VerificationCredential) and
Section 24's source-of-truth rule (the eligibility gate reads the
credential, never re-derives PASS/FAIL from the check row itself), and the
automatic completion that replaced manual review: a verified identity
completes occupancy eligibility on its own."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.eligibility import check_agreement_eligibility
from app.crud.occupancy_eligibility import (
    get_valid_occupancy_eligibility_credential,
    open_occupancy_eligibility_check,
    record_occupancy_eligibility_result,
)
from app.models.market_policy import MarketPolicyPack
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_renter_offer_agreement_flow import _make_agreement_eligible
from tests.test_room_hold_atomicity import _apply_and_send_offer, _make_listing_with_room, _make_verified_renter


def _england_policy_pack(db: Session, *, occupancy_eligibility_required: bool) -> MarketPolicyPack:
    existing = db.scalar(select(MarketPolicyPack).where(MarketPolicyPack.jurisdiction_code == "England"))
    if existing:
        existing.occupancy_eligibility_required = occupancy_eligibility_required
        existing.occupancy_eligibility_follow_up_days = 365
        db.commit()
        db.refresh(existing)
        return existing
    pack = MarketPolicyPack(
        jurisdiction_code="England", version=1, effective_from=date.today() - timedelta(days=1),
        occupancy_eligibility_required=occupancy_eligibility_required,
        occupancy_eligibility_method_note="Digital share code or manual document check",
        occupancy_eligibility_follow_up_days=365,
    )
    db.add(pack)
    db.commit()
    db.refresh(pack)
    return pack


def _unverify_identity(db: Session, party_id: int) -> None:
    from app.models.identity_verification import IdentityVerification

    for record in db.scalars(select(IdentityVerification).where(IdentityVerification.party_id == party_id)):
        record.status, record.session_state = "rejected", "FAILED"
    db.commit()


def _england_offer(db_session: Session, client, *, email_suffix: str):
    listing_id, _room_id = _make_listing_with_room(db_session)
    _make_agreement_eligible(db_session, listing_id)
    super_admin = _make_admin(db_session, email=f"voe-admin-{email_suffix}@test.com", role="super_admin")
    admin_cookies = auth_admin_cookie(super_admin)
    renter = _make_verified_renter(db_session, email=f"voe-renter-{email_suffix}@test.com")
    _app_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)
    r = client.post(
        f"/api/leasing/offers/{offer_id}/terms",
        json={"monthlyRent": 500, "depositAmount": 500, "startDate": date.today().isoformat(), "termMonths": 6},
        cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(renter))
    assert r.status_code == 200, r.text
    return offer_id, admin_cookies, renter


class TestResolverFailsOpenWithoutAPolicyPack:
    def test_jurisdiction_with_no_policy_pack_has_no_extra_requirement(self, db_session: Session):
        from app.services.verification_requirements import resolve_verification_requirements
        requirements = resolve_verification_requirements(db_session, "ZZ-NOWHERE")
        assert requirements == []


class TestEnglandOccupancyEligibilityGate:
    def test_agreement_eligibility_blocks_while_identity_is_not_verified(self, client, db_session: Session):
        _england_policy_pack(db_session, occupancy_eligibility_required=True)
        offer_id, admin_cookies, renter = _england_offer(db_session, client, email_suffix="01")

        from app.models.leasing import Offer
        offer = db_session.get(Offer, offer_id)
        _unverify_identity(db_session, offer.guest.user_account.party_id)
        reasons = check_agreement_eligibility(db_session, offer)
        assert any("OCCUPANCY_ELIGIBILITY" in r for r in reasons), reasons

    def test_agreement_eligibility_passes_once_credential_issued(self, client, db_session: Session):
        _england_policy_pack(db_session, occupancy_eligibility_required=True)
        offer_id, admin_cookies, renter = _england_offer(db_session, client, email_suffix="02")

        from app.models.leasing import Offer
        offer = db_session.get(Offer, offer_id)
        party_id = offer.guest.user_account.party_id

        # Use the crud layer directly with a fresh admin (simpler than
        # decoding the cookie back into an AdminUser).
        from tests.conftest import _make_admin as make_admin
        deciding_admin = make_admin(db_session, email="voe-decider-02@test.com", role="super_admin")

        check = open_occupancy_eligibility_check(
            db_session, deciding_admin, party_id=party_id, jurisdiction_code="England", method="MANUAL_DOCUMENT_CHECK",
        )
        record_occupancy_eligibility_result(
            db_session, check, deciding_admin, result_status="PASS", reason_note="Verified passport + BRP",
            policy_pack_version=1, follow_up_days=365,
        )

        db_session.refresh(offer)
        reasons = check_agreement_eligibility(db_session, offer)
        assert not any("OCCUPANCY_ELIGIBILITY" in r for r in reasons), reasons

    def test_fail_ineligible_issues_no_credential_and_blocks_an_unverified_renter(self, client, db_session: Session):
        _england_policy_pack(db_session, occupancy_eligibility_required=True)
        offer_id, admin_cookies, renter = _england_offer(db_session, client, email_suffix="03")

        from app.models.leasing import Offer
        offer = db_session.get(Offer, offer_id)
        party_id = offer.guest.user_account.party_id

        from tests.conftest import _make_admin as make_admin
        deciding_admin = make_admin(db_session, email="voe-decider-03@test.com", role="super_admin")

        check = open_occupancy_eligibility_check(
            db_session, deciding_admin, party_id=party_id, jurisdiction_code="England", method="MANUAL_DOCUMENT_CHECK",
        )
        record_occupancy_eligibility_result(db_session, check, deciding_admin, result_status="FAIL_INELIGIBLE")

        credential = get_valid_occupancy_eligibility_credential(db_session, party_id, "England")
        assert credential is None
        _unverify_identity(db_session, party_id)

        reasons = check_agreement_eligibility(db_session, offer)
        assert any("OCCUPANCY_ELIGIBILITY" in r for r in reasons), reasons

    def test_jurisdiction_without_the_requirement_is_never_gated(self, client, db_session: Session):
        _england_policy_pack(db_session, occupancy_eligibility_required=False)
        offer_id, admin_cookies, renter = _england_offer(db_session, client, email_suffix="04")

        from app.models.leasing import Offer
        offer = db_session.get(Offer, offer_id)
        reasons = check_agreement_eligibility(db_session, offer)
        assert not any("OCCUPANCY_ELIGIBILITY" in r for r in reasons), reasons


class TestOccupancyEligibilityReCheckedAtMoveIn:
    """AC-05: 'Occupancy cannot activate while a mandatory occupancy-stage
    verification requirement is unresolved.' A credential valid at
    agreement-confirmation time (AC-04) could have since lapsed -- this
    proves check_move_in_eligibility re-checks independently rather than
    trusting the earlier pass at check_agreement_eligibility."""

    def test_move_in_blocked_when_credential_expires_after_agreement_signed(self, client, db_session: Session):
        from datetime import datetime, timedelta, timezone

        from app.crud.eligibility import check_move_in_eligibility
        from app.models.leasing import Agreement, Offer
        from app.models.verification_credential import VerificationCredential
        from tests.conftest import _make_admin as make_admin, auth_admin_cookie, auth_user_cookie, deliver_all_disclosures
        from tests.test_agreement_engine_extensions_2 import _apply_send_accept_add_terms
        from tests.test_agreement_engine_foundation import _pay_off_agreement_obligations
        from tests.test_room_hold_atomicity import _make_listing_with_room, _make_verified_renter

        _england_policy_pack(db_session, occupancy_eligibility_required=True)
        listing_id, _room_id = _make_listing_with_room(db_session)
        _make_agreement_eligible(db_session, listing_id)
        super_admin = make_admin(db_session, email="voe-mi-admin-01@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="voe-mi-renter-01@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today(),
        )
        offer = db_session.get(Offer, offer_id)
        party_id = offer.guest.user_account.party_id

        check = open_occupancy_eligibility_check(
            db_session, super_admin, party_id=party_id, jurisdiction_code="England", method="MANUAL_DOCUMENT_CHECK",
        )
        record_occupancy_eligibility_result(
            db_session, check, super_admin, result_status="PASS", reason_note="Verified passport",
            policy_pack_version=1, follow_up_days=365,
        )

        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)
        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)
        _pay_off_agreement_obligations(client, db_session, admin_cookies, agreement_id)

        db_session.expire_all()
        agreement = db_session.get(Agreement, agreement_id)
        assert agreement.status == "SIGNED"

        # Credential was valid through agreement confirmation -- now expire
        # it, simulating a lapse between confirmation and move-in.
        credential = db_session.scalar(select(VerificationCredential).where(
            VerificationCredential.party_id == party_id, VerificationCredential.requirement_code == "OCCUPANCY_ELIGIBILITY"))
        credential.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db_session.commit()
        # ...and identity no longer verified, so it isn't completed again automatically.
        _unverify_identity(db_session, party_id)

        reasons = check_move_in_eligibility(db_session, agreement)
        assert any("OCCUPANCY_ELIGIBILITY" in r for r in reasons), reasons


class TestAutomaticOccupancyEligibility:
    """Completed automatically -- a verified identity is enough; no admin
    opens or decides a check and the renter submits nothing else."""

    def test_verified_identity_completes_it_automatically(self, client, db_session: Session):
        _england_policy_pack(db_session, occupancy_eligibility_required=True)
        offer_id, _admin_cookies, _renter = _england_offer(db_session, client, email_suffix="a1")
        from app.models.leasing import Offer

        offer = db_session.get(Offer, offer_id)
        party_id = offer.guest.user_account.party_id
        assert get_valid_occupancy_eligibility_credential(db_session, party_id, "England") is None
        reasons = check_agreement_eligibility(db_session, offer)
        assert not any("OCCUPANCY_ELIGIBILITY" in r for r in reasons), reasons
        credential = get_valid_occupancy_eligibility_credential(db_session, party_id, "England")
        assert credential is not None and credential.method == "IDENTITY_DOCUMENT"
        assert credential.expires_at is not None  # the pack's follow-up period (365 days here)

    def test_admins_can_no_longer_open_or_decide_checks(self, client, db_session: Session):
        admin = auth_admin_cookie(_make_admin(db_session, email="voe-noadmin@test.com", role="super_admin"))
        r = client.post("/api/verification/occupancy-eligibility-checks", cookies=admin,
                        json={"partyId": 1, "jurisdictionCode": "England", "method": "MANUAL_DOCUMENT_CHECK"})
        assert r.status_code == 405
        assert client.post("/api/verification/occupancy-eligibility-checks/1/decide", cookies=admin,
                           json={"resultStatus": "PASS"}).status_code in (404, 405)


class TestOccupancyEligibilityExpandedStateModel:
    """ZR-ENG-CLR-012 Section 8's own 'Check state' table, in full --
    TECHNICAL_ERROR, FRAUD_REVIEW, WAIVED_POLICY and SUSPENDED, none of
    which need a live provider to be real, reachable manual-review outcomes."""

    def _party(self, db: Session):
        from app.models.party import Party

        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db.add(party)
        db.commit()
        return party

    def test_technical_error_is_not_terminal_and_issues_no_credential(self, db_session: Session):
        admin = _make_admin(db_session, email="voe-state-01@test.com", role="super_admin")
        party = self._party(db_session)
        check = open_occupancy_eligibility_check(
            db_session, admin, party_id=party.id, jurisdiction_code="England", method="DIGITAL_SHARE_CODE",
        )
        record_occupancy_eligibility_result(db_session, check, admin, result_status="TECHNICAL_ERROR", reason_note="Share code service unavailable")
        assert check.status == "TECHNICAL_ERROR"
        assert get_valid_occupancy_eligibility_credential(db_session, party.id, "England") is None

        # Never a dead end -- Section 8: "Retry/failover; never adverse decision."
        updated = record_occupancy_eligibility_result(
            db_session, check, admin, result_status="PASS", reason_note="Retried via manual document check",
        )
        assert updated.status == "PASS"
        assert get_valid_occupancy_eligibility_credential(db_session, party.id, "England") is not None

    def test_fraud_review_is_not_terminal_and_issues_no_credential(self, db_session: Session):
        admin = _make_admin(db_session, email="voe-state-02@test.com", role="super_admin")
        party = self._party(db_session)
        check = open_occupancy_eligibility_check(
            db_session, admin, party_id=party.id, jurisdiction_code="England", method="MANUAL_DOCUMENT_CHECK",
        )
        record_occupancy_eligibility_result(db_session, check, admin, result_status="FRAUD_REVIEW", reason_note="Document shows signs of tampering")
        assert check.status == "FRAUD_REVIEW"
        assert get_valid_occupancy_eligibility_credential(db_session, party.id, "England") is None
        # Still re-decidable -- a FRAUD_REVIEW flag is an interim triage
        # state, not an automatic permanent fraud finding.
        record_occupancy_eligibility_result(db_session, check, admin, result_status="FAIL_INELIGIBLE")
        assert check.status == "FAIL_INELIGIBLE"

    def test_suspended_is_not_terminal(self, db_session: Session):
        admin = _make_admin(db_session, email="voe-state-03@test.com", role="super_admin")
        party = self._party(db_session)
        check = open_occupancy_eligibility_check(
            db_session, admin, party_id=party.id, jurisdiction_code="England", method="MANUAL_DOCUMENT_CHECK",
        )
        record_occupancy_eligibility_result(db_session, check, admin, result_status="SUSPENDED", reason_note="Pending investigation")
        assert check.status == "SUSPENDED"
        updated = record_occupancy_eligibility_result(
            db_session, check, admin, result_status="PASS", reason_note="Investigation cleared",
        )
        assert updated.status == "PASS"

    def test_waived_policy_issues_a_credential_and_is_terminal(self, db_session: Session):
        admin = _make_admin(db_session, email="voe-state-04@test.com", role="super_admin")
        party = self._party(db_session)
        check = open_occupancy_eligibility_check(
            db_session, admin, party_id=party.id, jurisdiction_code="England", method="MANUAL_DOCUMENT_CHECK",
        )
        record_occupancy_eligibility_result(db_session, check, admin, result_status="WAIVED_POLICY", reason_note="Exempt under policy X")
        assert check.status == "WAIVED_POLICY"

        credential = get_valid_occupancy_eligibility_credential(db_session, party.id, "England")
        assert credential is not None
        assert credential.method == "WAIVED_POLICY"

        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            record_occupancy_eligibility_result(db_session, check, admin, result_status="PASS")

    def test_display_status_shows_expired_once_pass_credential_lapses(self, db_session: Session):
        """AC-31/Section 8: EXPIRED is a real check state, computed from the
        credential (source-of-truth) rather than a second stored copy."""
        from datetime import datetime, timedelta, timezone
        from app.crud.occupancy_eligibility import to_occupancy_eligibility_check_read

        admin = _make_admin(db_session, email="voe-state-05@test.com", role="super_admin")
        party = self._party(db_session)
        check = open_occupancy_eligibility_check(
            db_session, admin, party_id=party.id, jurisdiction_code="England", method="MANUAL_DOCUMENT_CHECK",
        )
        record_occupancy_eligibility_result(
            db_session, check, admin, result_status="PASS", reason_note="Manual document check passed", follow_up_days=1,
        )

        read_before = to_occupancy_eligibility_check_read(db_session, check)
        assert read_before.status == "PASS"

        credential = get_valid_occupancy_eligibility_credential(db_session, party.id, "England")
        credential.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db_session.commit()

        read_after = to_occupancy_eligibility_check_read(db_session, check)
        assert read_after.status == "EXPIRED"
