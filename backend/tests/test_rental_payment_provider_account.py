"""ZR-PAY-LINK-003 Section 6/Wireframe C: self-service Stripe Connect
onboarding for a rent-collection recipient. Covers
crud/rental_payment_provider_account.py and the recipient_router routes in
api/routes/rental_payments.py. No real Stripe credentials are configured in
tests, so every stripe_client call takes its disclosed-simulation fallback
(see services/stripe_client.py:is_configured)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.crud import rental_payment_provider_account as crud
from app.models.party import Party
from app.models.user_account import UserAccount
from tests.conftest import _make_user, auth_user_cookie


def _make_recipient(db: Session, *, email: str = "provider-account@test.com") -> tuple[UserAccount, Party]:
    party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()
    user = _make_user(db, email=email)
    user.party_id = party.id
    db.commit()
    return user, party


class TestCreateConnectedAccountCrud:
    def test_connect_creates_an_onboarding_account(self, db_session: Session):
        _user, party = _make_recipient(db_session)
        account = crud.create_connected_account(db_session, party, country="GB", email="recipient@test.com")
        assert account.status == "ONBOARDING"
        assert account.stripe_account_id
        assert account.charges_enabled is False

    def test_cannot_connect_twice(self, db_session: Session):
        _user, party = _make_recipient(db_session, email="twice@test.com")
        crud.create_connected_account(db_session, party, country="GB", email="recipient@test.com")
        with pytest.raises(HTTPException) as exc:
            crud.create_connected_account(db_session, party, country="GB", email="recipient@test.com")
        assert exc.value.status_code == 409

    def test_onboarding_link_is_returned(self, db_session: Session):
        _user, party = _make_recipient(db_session, email="link@test.com")
        account = crud.create_connected_account(db_session, party, country="GB", email="recipient@test.com")
        url = crud.create_onboarding_link(account)
        assert url


class TestSimulateOnboardingComplete:
    def test_simulate_marks_account_complete(self, db_session: Session):
        _user, party = _make_recipient(db_session, email="simulate@test.com")
        account = crud.create_connected_account(db_session, party, country="GB", email="recipient@test.com")
        updated = crud.simulate_onboarding_complete(db_session, account)
        assert updated.status == "COMPLETE"
        assert updated.charges_enabled is True
        assert crud.get_charge_ready_provider_account_for_party(db_session, party.id) is not None

    def test_simulate_refuses_once_stripe_is_configured(self, db_session: Session, monkeypatch):
        _user, party = _make_recipient(db_session, email="configured@test.com")
        account = crud.create_connected_account(db_session, party, country="GB", email="recipient@test.com")

        monkeypatch.setattr("app.services.stripe_client.is_configured", lambda: True)
        with pytest.raises(HTTPException) as exc:
            crud.simulate_onboarding_complete(db_session, account)
        assert exc.value.status_code == 409


class TestChargeReadyResolver:
    def test_onboarding_account_is_not_charge_ready(self, db_session: Session):
        _user, party = _make_recipient(db_session, email="not-ready@test.com")
        crud.create_connected_account(db_session, party, country="GB", email="recipient@test.com")
        assert crud.get_charge_ready_provider_account_for_party(db_session, party.id) is None

    def test_no_account_is_not_charge_ready(self, db_session: Session):
        _user, party = _make_recipient(db_session, email="no-account@test.com")
        assert crud.get_charge_ready_provider_account_for_party(db_session, party.id) is None


class TestProviderAccountRoutes:
    def test_connect_refresh_and_simulate_flow(self, client, db_session: Session):
        user, _party = _make_recipient(db_session, email="route-flow@test.com")

        r_connect = client.post(
            "/api/users/rental-payments/recipient/provider-account",
            json={"country": "GB", "email": "recipient@test.com"},
            cookies=auth_user_cookie(user),
        )
        assert r_connect.status_code == 201, r_connect.text
        body = r_connect.json()
        assert body["account"]["status"] == "ONBOARDING"
        assert body["onboardingUrl"]
        assert "stripeAccountId" not in body["account"]

        r_get = client.get("/api/users/rental-payments/recipient/provider-account", cookies=auth_user_cookie(user))
        assert r_get.status_code == 200, r_get.text
        assert r_get.json()["status"] == "ONBOARDING"

        r_simulate = client.post(
            "/api/users/rental-payments/recipient/provider-account/simulate-onboarding-complete",
            cookies=auth_user_cookie(user),
        )
        assert r_simulate.status_code == 200, r_simulate.text
        assert r_simulate.json()["status"] == "COMPLETE"

    def test_get_with_no_account_is_404(self, client, db_session: Session):
        user, _party = _make_recipient(db_session, email="route-none@test.com")
        r = client.get("/api/users/rental-payments/recipient/provider-account", cookies=auth_user_cookie(user))
        assert r.status_code == 404, r.text

    def test_connecting_twice_via_route_is_409(self, client, db_session: Session):
        user, _party = _make_recipient(db_session, email="route-twice@test.com")
        payload = {"country": "GB", "email": "recipient@test.com"}
        r1 = client.post(
            "/api/users/rental-payments/recipient/provider-account", json=payload, cookies=auth_user_cookie(user),
        )
        assert r1.status_code == 201, r1.text
        r2 = client.post(
            "/api/users/rental-payments/recipient/provider-account", json=payload, cookies=auth_user_cookie(user),
        )
        assert r2.status_code == 409, r2.text


class TestAccountChangeGovernance:
    """ZR-PAY-LINK-003 Section 14.1: request_account_change/confirm_account_change
    -- the governed flow that lets a recipient replace their connected
    account, mirroring RentalPaymentInstruction's own step-up-then-supersede
    shape."""

    def _make_complete_account(self, db: Session, *, email: str):
        user, party = _make_recipient(db, email=email)
        account = crud.create_connected_account(db, party, country="GB", email="recipient@test.com")
        crud.simulate_onboarding_complete(db, account)
        return user, party, account

    def test_request_change_does_not_disturb_the_current_account(self, db_session: Session):
        user, party, account = self._make_complete_account(db_session, email="change-request@test.com")
        raw_code = crud.request_account_change(db_session, account, user)

        assert len(raw_code) == 6
        assert account.status == "COMPLETE"
        assert account.charges_enabled is True
        assert crud.get_charge_ready_provider_account_for_party(db_session, party.id).id == account.id

    def test_wrong_code_increments_attempts_and_fails(self, db_session: Session):
        user, _party, account = self._make_complete_account(db_session, email="change-wrong-code@test.com")
        crud.request_account_change(db_session, account, user)

        with pytest.raises(HTTPException) as exc:
            crud.confirm_account_change(db_session, account, user, "000000", country="GB", email="new@test.com")
        assert exc.value.status_code == 400
        assert account.verification_attempts == 1
        assert account.status == "COMPLETE"

    def test_too_many_attempts_is_rejected(self, db_session: Session):
        user, _party, account = self._make_complete_account(db_session, email="change-too-many@test.com")
        crud.request_account_change(db_session, account, user)

        for _ in range(crud.ACCOUNT_CHANGE_MAX_VERIFICATION_ATTEMPTS):
            with pytest.raises(HTTPException):
                crud.confirm_account_change(db_session, account, user, "000000", country="GB", email="new@test.com")
        with pytest.raises(HTTPException) as exc:
            crud.confirm_account_change(db_session, account, user, "000000", country="GB", email="new@test.com")
        assert exc.value.status_code == 429

    def test_expired_code_is_rejected(self, db_session: Session):
        from datetime import datetime, timedelta, timezone

        user, _party, account = self._make_complete_account(db_session, email="change-expired@test.com")
        crud.request_account_change(db_session, account, user)
        account.verification_code_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()

        with pytest.raises(HTTPException) as exc:
            crud.confirm_account_change(db_session, account, user, "000000", country="GB", email="new@test.com")
        assert exc.value.status_code == 400

    def test_correct_code_supersedes_old_account_and_creates_a_new_one(self, db_session: Session):
        user, party, account = self._make_complete_account(db_session, email="change-correct@test.com")
        old_id, old_stripe_id = account.id, account.stripe_account_id
        raw_code = crud.request_account_change(db_session, account, user)

        new_account = crud.confirm_account_change(
            db_session, account, user, raw_code, country="GB", email="new-recipient@test.com",
        )

        assert new_account.id != old_id
        assert new_account.stripe_account_id != old_stripe_id
        assert new_account.status == "ONBOARDING"

        db_session.refresh(account)
        assert account.status == "SUPERSEDED"
        assert account.verification_code_hash is None

        # The new row is what's now resolved as current -- a second
        # create_connected_account for the same party must not conflict
        # with the SUPERSEDED old one.
        current = crud.get_for_party(db_session, party.id)
        assert current.id == new_account.id
        assert crud.get_charge_ready_provider_account_for_party(db_session, party.id) is None  # not onboarded yet

    def test_high_risk_change_is_flagged_on_the_new_account(self, db_session: Session):
        from datetime import datetime, timezone

        user, _party, account = self._make_complete_account(db_session, email="change-risk@test.com")
        user.password_changed_at = datetime.now(timezone.utc)
        db_session.commit()
        raw_code = crud.request_account_change(db_session, account, user)

        new_account = crud.confirm_account_change(
            db_session, account, user, raw_code, country="GB", email="new-recipient@test.com",
        )
        assert new_account.is_high_risk is True
        assert new_account.high_risk_reason

    def test_confirm_without_a_pending_request_is_rejected(self, db_session: Session):
        user, _party, account = self._make_complete_account(db_session, email="change-no-request@test.com")
        with pytest.raises(HTTPException) as exc:
            crud.confirm_account_change(db_session, account, user, "000000", country="GB", email="new@test.com")
        assert exc.value.status_code == 409

    def test_tenants_with_open_obligations_are_notified(self, db_session: Session):
        from datetime import date

        from app.crud import rental_payment as rp_crud
        from app.models.notification import Notification
        from tests.test_rental_payment_records import _make_guest

        user, party, account = self._make_complete_account(db_session, email="change-notify@test.com")
        tenant_user = _make_user(db_session, email="change-notify-tenant@test.com")
        guest = _make_guest(db_session, guest_id="G-PROV-CHANGE-NOTIFY")
        guest.user_account_id = tenant_user.id
        db_session.commit()
        rp_crud.create_obligation(
            db_session, obligation_type="RENT", tenant_guest_id=guest.id, recipient_party_id=party.id,
            amount=850, currency="GBP", due_date=date.today(),
        )

        raw_code = crud.request_account_change(db_session, account, user)
        crud.confirm_account_change(db_session, account, user, raw_code, country="GB", email="new@test.com")

        notifications = db_session.query(Notification).filter_by(
            recipient_user_id=tenant_user.id, notification_type="rental_payment_provider_account.changed",
        ).all()
        assert len(notifications) == 1


class TestAccountChangeRoutes:
    def _connect_and_complete(self, client, user):
        client.post(
            "/api/users/rental-payments/recipient/provider-account",
            json={"country": "GB", "email": "recipient@test.com"}, cookies=auth_user_cookie(user),
        )
        client.post(
            "/api/users/rental-payments/recipient/provider-account/simulate-onboarding-complete",
            cookies=auth_user_cookie(user),
        )

    def test_request_and_confirm_change_via_routes(self, client, db_session: Session):
        user, party = _make_recipient(db_session, email="route-change@test.com")
        self._connect_and_complete(client, user)
        old_account = crud.get_for_party(db_session, party.id)

        r_request = client.post(
            "/api/users/rental-payments/recipient/provider-account/request-change", cookies=auth_user_cookie(user),
        )
        assert r_request.status_code == 200, r_request.text

        db_session.refresh(old_account)
        assert old_account.verification_code_hash is not None
        # The route never returns the raw code (mailed only), so this
        # route-level test only exercises the wrong-code path -- the happy
        # path (obtaining raw_code via crud directly) is covered by
        # test_confirm_change_succeeds_with_the_correct_code_via_routes below.
        r_confirm_wrong = client.post(
            "/api/users/rental-payments/recipient/provider-account/confirm-change",
            json={"code": "000000", "country": "GB", "email": "new@test.com"}, cookies=auth_user_cookie(user),
        )
        assert r_confirm_wrong.status_code == 400, r_confirm_wrong.text

    def test_confirm_change_succeeds_with_the_correct_code_via_routes(self, client, db_session: Session):
        user, party = _make_recipient(db_session, email="route-change-ok@test.com")
        self._connect_and_complete(client, user)
        account = crud.get_for_party(db_session, party.id)

        raw_code = crud.request_account_change(db_session, account, user)

        r_confirm = client.post(
            "/api/users/rental-payments/recipient/provider-account/confirm-change",
            json={"code": raw_code, "country": "GB", "email": "new@test.com"}, cookies=auth_user_cookie(user),
        )
        assert r_confirm.status_code == 200, r_confirm.text
        assert r_confirm.json()["account"]["status"] == "ONBOARDING"

    def test_request_change_with_no_account_is_404(self, client, db_session: Session):
        user, _party = _make_recipient(db_session, email="route-change-none@test.com")
        r = client.post(
            "/api/users/rental-payments/recipient/provider-account/request-change", cookies=auth_user_cookie(user),
        )
        assert r.status_code == 404, r.text
