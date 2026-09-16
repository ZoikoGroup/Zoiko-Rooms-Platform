"""ZR-ENG-CLR-012 Sections 10/11/AC-34..36: Application/Affordability
Evidence and Screening/Consumer-Report Controls. Covers: permissible
purpose is mandatory to open a check, prohibited check types are enforced
per jurisdiction, provider score vs. final decision stay separate, and an
adverse (FAIL) decision triggers the required notice."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.screening import (
    get_screening_check_or_404,
    list_screening_checks_for_party,
    open_screening_check,
    record_screening_decision,
)
from app.models.market_policy import MarketPolicyPack
from app.models.notification import Notification
from app.models.party import Party
from app.services.verification_requirements import is_screening_check_type_permitted
from tests.conftest import _make_admin, auth_admin_cookie


def _policy_pack(db: Session, *, jurisdiction: str, prohibited: list[str]) -> MarketPolicyPack:
    existing = db.scalar(select(MarketPolicyPack).where(MarketPolicyPack.jurisdiction_code == jurisdiction))
    if existing:
        existing.screening_prohibited_check_types = prohibited
        db.commit()
        db.refresh(existing)
        return existing
    pack = MarketPolicyPack(jurisdiction_code=jurisdiction, version=1, effective_from=date.today(), screening_prohibited_check_types=prohibited)
    db.add(pack)
    db.commit()
    db.refresh(pack)
    return pack


def _party(db: Session) -> Party:
    party = Party(party_type="renter", status="active", jurisdiction="IN")
    db.add(party)
    db.commit()
    return party


class TestPermittedResolver:
    def test_no_policy_pack_permits_everything(self, db_session: Session):
        assert is_screening_check_type_permitted(db_session, "ZZ-NOWHERE", "CREDIT_REPORT") is True

    def test_prohibited_check_type_is_blocked(self, db_session: Session):
        _policy_pack(db_session, jurisdiction="France", prohibited=["CRIMINAL_RECORD"])
        assert is_screening_check_type_permitted(db_session, "France", "CRIMINAL_RECORD") is False
        assert is_screening_check_type_permitted(db_session, "France", "AFFORDABILITY") is True


class TestOpenScreeningCheck:
    def test_requires_a_permissible_purpose(self, db_session: Session):
        admin = _make_admin(db_session, email="scr-admin-01@test.com", role="admin")
        party = _party(db_session)
        with __import__("pytest").raises(Exception):
            open_screening_check(
                db_session, admin, party_id=party.id, jurisdiction_code="England",
                check_type="CREDIT_REPORT", permissible_purpose="",
            )

    def test_rejects_a_prohibited_check_type(self, db_session: Session):
        _policy_pack(db_session, jurisdiction="France", prohibited=["CRIMINAL_RECORD"])
        admin = _make_admin(db_session, email="scr-admin-02@test.com", role="admin")
        party = _party(db_session)
        with __import__("pytest").raises(Exception):
            open_screening_check(
                db_session, admin, party_id=party.id, jurisdiction_code="France",
                check_type="CRIMINAL_RECORD", permissible_purpose="Tenancy screening under local law",
            )

    def test_permitted_check_opens_authorized(self, db_session: Session):
        admin = _make_admin(db_session, email="scr-admin-03@test.com", role="admin")
        party = _party(db_session)
        check = open_screening_check(
            db_session, admin, party_id=party.id, jurisdiction_code="England",
            check_type="AFFORDABILITY", permissible_purpose="Confirm income covers rent", provider_name="Open Banking API",
        )
        assert check.decision_status == "AUTHORIZED"
        assert check.permissible_purpose == "Confirm income covers rent"

    def test_policy_pack_version_recorded_when_a_pack_exists(self, db_session: Session):
        pack = _policy_pack(db_session, jurisdiction="France", prohibited=[])
        admin = _make_admin(db_session, email="scr-admin-08@test.com", role="admin")
        party = _party(db_session)
        check = open_screening_check(
            db_session, admin, party_id=party.id, jurisdiction_code="France",
            check_type="AFFORDABILITY", permissible_purpose="test",
        )
        assert check.policy_pack_version == pack.version

    def test_policy_pack_version_null_when_no_pack_exists(self, db_session: Session):
        admin = _make_admin(db_session, email="scr-admin-09@test.com", role="admin")
        party = _party(db_session)
        check = open_screening_check(
            db_session, admin, party_id=party.id, jurisdiction_code="ZZ-NOWHERE",
            check_type="AFFORDABILITY", permissible_purpose="test",
        )
        assert check.policy_pack_version is None


class TestScreeningDecision:
    def test_pass_decision_records_provider_result_separately_from_decision(self, db_session: Session):
        admin = _make_admin(db_session, email="scr-admin-04@test.com", role="admin")
        party = _party(db_session)
        check = open_screening_check(
            db_session, admin, party_id=party.id, jurisdiction_code="England",
            check_type="AFFORDABILITY", permissible_purpose="Confirm income covers rent",
        )
        updated = record_screening_decision(
            db_session, check, admin, decision_status="PASS",
            decision_reason="Income comfortably covers rent per Host policy", provider_result_summary="Score: 82/100",
        )
        assert updated.decision_status == "PASS"
        assert updated.provider_result_summary == "Score: 82/100"
        assert updated.adverse_action_notice_sent_at is None

    def test_fail_decision_triggers_adverse_action_notice(self, db_session: Session):
        admin = _make_admin(db_session, email="scr-admin-05@test.com", role="admin")
        party = _party(db_session)
        user_account = None
        check = open_screening_check(
            db_session, admin, party_id=party.id, jurisdiction_code="England",
            check_type="CREDIT_REPORT", permissible_purpose="Tenancy affordability check",
        )
        updated = record_screening_decision(
            db_session, check, admin, decision_status="FAIL", decision_reason="Insufficient income per Host policy",
            provider_result_summary="Score: 40/100",
        )
        assert updated.decision_status == "FAIL"
        assert updated.adverse_action_notice_sent_at is not None

    def test_cannot_decide_a_non_pending_check_twice(self, db_session: Session):
        admin = _make_admin(db_session, email="scr-admin-06@test.com", role="admin")
        party = _party(db_session)
        check = open_screening_check(
            db_session, admin, party_id=party.id, jurisdiction_code="England",
            check_type="AFFORDABILITY", permissible_purpose="Confirm income covers rent",
        )
        record_screening_decision(db_session, check, admin, decision_status="PASS")
        with __import__("pytest").raises(Exception):
            record_screening_decision(db_session, check, admin, decision_status="FAIL")

    def test_inconclusive_is_not_terminal_and_can_be_re_decided(self, db_session: Session):
        """AC-09/AC-10: INCONCLUSIVE routes to alternate/manual review --
        it must be possible to come back and decide it once more evidence
        arrives, unlike the genuinely terminal PASS/FAIL outcomes."""
        admin = _make_admin(db_session, email="scr-admin-10@test.com", role="admin")
        party = _party(db_session)
        check = open_screening_check(
            db_session, admin, party_id=party.id, jurisdiction_code="England",
            check_type="AFFORDABILITY", permissible_purpose="Confirm income covers rent",
        )
        record_screening_decision(db_session, check, admin, decision_status="INCONCLUSIVE", decision_reason="Need bank statement")
        assert check.decision_status == "INCONCLUSIVE"

        updated = record_screening_decision(db_session, check, admin, decision_status="PASS", decision_reason="Bank statement received")
        assert updated.decision_status == "PASS"

        with __import__("pytest").raises(Exception):
            record_screening_decision(db_session, check, admin, decision_status="FAIL")


class TestScreeningAdminRoutes:
    def test_full_open_and_decide_flow_via_http(self, client, db_session: Session):
        admin = _make_admin(db_session, email="scr-admin-07@test.com", role="super_admin")
        party = _party(db_session)
        cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/verification/screening-checks",
            json={
                "partyId": party.id, "jurisdictionCode": "England", "checkType": "AFFORDABILITY",
                "permissiblePurpose": "Confirm income covers rent", "providerName": "Open Banking API",
            },
            cookies=cookies,
        )
        assert r.status_code == 201, r.text
        check_id = r.json()["id"]

        r = client.get(f"/api/verification/screening-checks/party/{party.id}", cookies=cookies)
        assert r.status_code == 200, r.text
        assert any(c["id"] == check_id for c in r.json())

        r = client.post(
            f"/api/verification/screening-checks/{check_id}/decide",
            json={"decisionStatus": "PASS", "decisionReason": "Meets Host policy", "providerResultSummary": "Score: 90"},
            cookies=cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["decisionStatus"] == "PASS"


class TestScreeningDispute:
    def test_dispute_moves_a_decided_check_to_disputed_source(self, db_session: Session):
        """AC-48: 'User challenge corrects inaccurate evidence; credential
        is re-evaluated/versioned.'"""
        from app.crud.screening import dispute_screening_decision

        admin = _make_admin(db_session, email="scr-dispute-01@test.com", role="admin")
        party = _party(db_session)
        check = open_screening_check(
            db_session, admin, party_id=party.id, jurisdiction_code="England",
            check_type="CREDIT_REPORT", permissible_purpose="test",
        )
        record_screening_decision(db_session, check, admin, decision_status="FAIL", decision_reason="Low score")

        disputed = dispute_screening_decision(db_session, check, admin, dispute_reason="This report is not mine, wrong person")
        assert disputed.decision_status == "DISPUTED_SOURCE"
        assert disputed.dispute_reason == "This report is not mine, wrong person"
        assert disputed.disputed_at is not None

    def test_disputed_check_is_re_decidable(self, db_session: Session):
        from app.crud.screening import dispute_screening_decision

        admin = _make_admin(db_session, email="scr-dispute-02@test.com", role="admin")
        party = _party(db_session)
        check = open_screening_check(
            db_session, admin, party_id=party.id, jurisdiction_code="England",
            check_type="CREDIT_REPORT", permissible_purpose="test",
        )
        record_screening_decision(db_session, check, admin, decision_status="FAIL")
        dispute_screening_decision(db_session, check, admin, dispute_reason="Wrong person")

        updated = record_screening_decision(db_session, check, admin, decision_status="PASS", decision_reason="Confirmed identity mixup, correct report is clean")
        assert updated.decision_status == "PASS"

    def test_cannot_dispute_a_check_that_is_not_yet_decided(self, db_session: Session):
        from app.crud.screening import dispute_screening_decision

        admin = _make_admin(db_session, email="scr-dispute-03@test.com", role="admin")
        party = _party(db_session)
        check = open_screening_check(
            db_session, admin, party_id=party.id, jurisdiction_code="England",
            check_type="CREDIT_REPORT", permissible_purpose="test",
        )
        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            dispute_screening_decision(db_session, check, admin, dispute_reason="test")

    def test_dispute_requires_a_reason(self, db_session: Session):
        from app.crud.screening import dispute_screening_decision

        admin = _make_admin(db_session, email="scr-dispute-04@test.com", role="admin")
        party = _party(db_session)
        check = open_screening_check(
            db_session, admin, party_id=party.id, jurisdiction_code="England",
            check_type="CREDIT_REPORT", permissible_purpose="test",
        )
        record_screening_decision(db_session, check, admin, decision_status="FAIL")
        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            dispute_screening_decision(db_session, check, admin, dispute_reason="")

    def test_dispute_route_full_flow(self, client, db_session: Session):
        admin = _make_admin(db_session, email="scr-dispute-05@test.com", role="super_admin")
        party = _party(db_session)
        cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/verification/screening-checks",
            json={"partyId": party.id, "jurisdictionCode": "England", "checkType": "CREDIT_REPORT", "permissiblePurpose": "test"},
            cookies=cookies,
        )
        check_id = r.json()["id"]
        client.post(f"/api/verification/screening-checks/{check_id}/decide", json={"decisionStatus": "FAIL"}, cookies=cookies)

        r = client.post(
            f"/api/verification/screening-checks/{check_id}/dispute",
            json={"disputeReason": "This is not my report"}, cookies=cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["decisionStatus"] == "DISPUTED_SOURCE"
