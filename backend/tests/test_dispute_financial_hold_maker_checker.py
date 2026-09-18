"""Integration tests for the ZR-ENG-CLR-010 Section 20 financial-hold
maker-checker control: app/crud/disputes.py's new
_requires_maker_checker/approve_financial_hold/confirm_release_financial_hold,
and the new /approve and /confirm-release routes in
app/api/routes/disputes.py.

Covers: a hold below every trigger opens/releases exactly as before
(single admin, immediately ACTIVE/RELEASED -- the untouched common path);
a hold at/above the amount threshold requires a second, different admin to
approve it before it's ACTIVE, and a second, different admin to confirm
its release before it's RELEASED; a SEV-0 case and a LEGAL_REVIEW_REQUIRED
claim each trigger the same control regardless of amount; close_case still
refuses while a hold sits at RELEASE_PENDING; AC-10's review_at default/
override/overdue-flag behavior (never an auto-release)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import settings
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


def _open_deposit_case(client, renter_cookies, occupancy_id: int, *, amount: float = 600) -> tuple[int, int]:
    r = client.post(
        "/api/users/rentals/disputes",
        json={"occupancyId": occupancy_id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": amount}},
        cookies=renter_cookies,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["id"], body["claims"][0]["id"]


def _open_safety_flagged_case(client, renter_cookies, occupancy_id: int) -> tuple[int, int]:
    r = client.post(
        "/api/users/rentals/disputes",
        json={"occupancyId": occupancy_id, "claim": {"claimCode": "ILLEGAL_LOCKOUT", "claimFamily": "SUBLET_OCCUPANCY", "safetyFlag": True}},
        cookies=renter_cookies,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["id"], body["claims"][0]["id"]


class TestBelowThreshold:
    def test_a_hold_below_every_trigger_opens_and_releases_in_one_step(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="mchost1@test.com", renter_email="mcrenter1@test.com")
        _case_id, claim_id = _open_deposit_case(client, auth_user_cookie(renter), occ.id, amount=100)

        admin = _make_admin(db_session, email="mc-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/financial-holds",
            json={"amount": 50, "authorityBasis": "test"}, cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        hold = r.json()
        assert hold["status"] == "ACTIVE"

        r = client.post(f"/api/admin/disputes/financial-holds/{hold['id']}/release", json={}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "RELEASED"


class TestAboveAmountThreshold:
    def test_open_requires_a_different_admin_to_approve(self, client, db_session: Session, monkeypatch):
        monkeypatch.setattr(settings, "dispute_financial_hold_maker_checker_threshold", 1000.0)
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="mchost2@test.com", renter_email="mcrenter2@test.com")
        _case_id, claim_id = _open_deposit_case(client, auth_user_cookie(renter), occ.id, amount=5000)

        maker = _make_admin(db_session, email="mc-maker2@test.com", role="super_admin")
        maker_cookies = auth_admin_cookie(maker)
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/financial-holds",
            json={"amount": 5000, "authorityBasis": "large deposit hold"}, cookies=maker_cookies,
        )
        assert r.status_code == 201, r.text
        hold = r.json()
        assert hold["status"] == "PROPOSED"

        r = client.post(f"/api/admin/disputes/financial-holds/{hold['id']}/approve", cookies=maker_cookies)
        assert r.status_code == 409, r.text

        checker = _make_admin(db_session, email="mc-checker2@test.com", role="super_admin")
        r = client.post(f"/api/admin/disputes/financial-holds/{hold['id']}/approve", cookies=auth_admin_cookie(checker))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ACTIVE"

    def test_release_requires_a_different_admin_to_confirm(self, client, db_session: Session, monkeypatch):
        monkeypatch.setattr(settings, "dispute_financial_hold_maker_checker_threshold", 1000.0)
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="mchost3@test.com", renter_email="mcrenter3@test.com")
        _case_id, claim_id = _open_deposit_case(client, auth_user_cookie(renter), occ.id, amount=5000)

        admin1 = _make_admin(db_session, email="mc-admin3a@test.com", role="super_admin")
        admin2 = _make_admin(db_session, email="mc-admin3b@test.com", role="super_admin")
        admin1_cookies, admin2_cookies = auth_admin_cookie(admin1), auth_admin_cookie(admin2)

        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/financial-holds",
            json={"amount": 5000, "authorityBasis": "large deposit hold"}, cookies=admin1_cookies,
        )
        hold_id = r.json()["id"]
        r = client.post(f"/api/admin/disputes/financial-holds/{hold_id}/approve", cookies=admin2_cookies)
        assert r.status_code == 200, r.text

        r = client.post(f"/api/admin/disputes/financial-holds/{hold_id}/release", json={}, cookies=admin1_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "RELEASE_PENDING"

        r = client.post(f"/api/admin/disputes/financial-holds/{hold_id}/confirm-release", cookies=admin1_cookies)
        assert r.status_code == 409, r.text

        r = client.post(f"/api/admin/disputes/financial-holds/{hold_id}/confirm-release", cookies=admin2_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "RELEASED"


class TestSafetyAndLegalTriggers:
    def test_sev0_case_requires_maker_checker_regardless_of_amount(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="mchost4@test.com", renter_email="mcrenter4@test.com")
        _case_id, claim_id = _open_safety_flagged_case(client, auth_user_cookie(renter), occ.id)

        admin = _make_admin(db_session, email="mc-admin4@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/financial-holds",
            json={"amount": 1, "authorityBasis": "tiny amount but SEV-0"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "PROPOSED"

    def test_legal_review_required_claim_requires_maker_checker_regardless_of_amount(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="mchost5@test.com", renter_email="mcrenter5@test.com")
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "OTHER", "claimFamily": "MARKETPLACE_CONDUCT"}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        claim_id = body["claims"][0]["id"]
        assert body["claims"][0]["resolverConfidence"] == "LEGAL_REVIEW_REQUIRED"

        admin = _make_admin(db_session, email="mc-admin5@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/financial-holds",
            json={"amount": 1, "authorityBasis": "tiny amount but unresolved forum"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "PROPOSED"


class TestCurrencyValidation:
    def test_a_hold_currency_mismatched_with_its_claim_is_refused(self, client, db_session: Session):
        """QA-Q43: a hold denominated in a different currency than the claim
        it secures is rejected at creation, rather than persisting a
        silently-inconsistent pair."""
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="mchost7@test.com", renter_email="mcrenter7@test.com")
        _case_id, claim_id = _open_deposit_case(client, auth_user_cookie(renter), occ.id, amount=100)

        admin = _make_admin(db_session, email="mc-admin7@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/financial-holds",
            json={"amount": 50, "currency": "USD", "authorityBasis": "test"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 400, r.text


class TestCloseCaseGuard:
    def test_close_case_still_refused_while_a_hold_is_release_pending(self, client, db_session: Session, monkeypatch):
        monkeypatch.setattr(settings, "dispute_financial_hold_maker_checker_threshold", 1000.0)
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="mchost6@test.com", renter_email="mcrenter6@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 5000}},
            cookies=renter_cookies,
        )
        case_id, claim_id = r.json()["id"], r.json()["claims"][0]["id"]

        admin1 = _make_admin(db_session, email="mc-admin6a@test.com", role="super_admin")
        admin2 = _make_admin(db_session, email="mc-admin6b@test.com", role="super_admin")
        admin1_cookies, admin2_cookies = auth_admin_cookie(admin1), auth_admin_cookie(admin2)

        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/financial-holds",
            json={"amount": 5000, "authorityBasis": "fee hold"}, cookies=admin1_cookies,
        )
        hold_id = r.json()["id"]
        client.post(f"/api/admin/disputes/financial-holds/{hold_id}/approve", cookies=admin2_cookies)
        client.post(f"/api/admin/disputes/financial-holds/{hold_id}/release", json={}, cookies=admin1_cookies)

        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/decision",
            json={"outcome": "UPHELD", "reasonCode": "fee_waived"}, cookies=admin1_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(f"/api/admin/disputes/{case_id}/close", cookies=admin1_cookies)
        assert r.status_code == 409, r.text


class TestReviewDate:
    """AC-10: "Financial holds are... time/review bounded." A hold with no
    explicit review_at gets a default review window; one past its
    review_at is flagged (never auto-released)."""

    def test_a_hold_with_no_explicit_review_at_gets_the_default_window(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="mchost7@test.com", renter_email="mcrenter7@test.com")
        _case_id, claim_id = _open_deposit_case(client, auth_user_cookie(renter), occ.id, amount=100)

        admin = _make_admin(db_session, email="mc-admin7@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/financial-holds",
            json={"amount": 50, "authorityBasis": "test"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["reviewAt"] is not None
        assert body["isOverdueForReview"] is False

    def test_an_explicit_review_at_is_honored(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="mchost8@test.com", renter_email="mcrenter8@test.com")
        _case_id, claim_id = _open_deposit_case(client, auth_user_cookie(renter), occ.id, amount=100)

        admin = _make_admin(db_session, email="mc-admin8@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/financial-holds",
            json={"amount": 50, "authorityBasis": "test", "reviewAt": "2026-09-16T00:00:00Z"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        assert r.json()["reviewAt"].startswith("2026-09-16")

    def test_a_hold_past_its_review_date_is_flagged_but_not_auto_released(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="mchost9@test.com", renter_email="mcrenter9@test.com")
        _case_id, claim_id = _open_deposit_case(client, auth_user_cookie(renter), occ.id, amount=100)

        admin = _make_admin(db_session, email="mc-admin9@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/financial-holds",
            json={"amount": 50, "authorityBasis": "test", "reviewAt": "2020-01-01T00:00:00Z"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["isOverdueForReview"] is True
        assert body["status"] == "ACTIVE"  # flagged for review, never auto-released
