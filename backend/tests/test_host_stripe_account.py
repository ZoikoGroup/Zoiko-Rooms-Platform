"""ZR-ENG-CLR-005 Section 4.4/9.1: Stripe Connect onboarding
(app/crud/host_stripe_account.py) -- the real-Stripe counterpart to
app/crud/payout_beneficiary.py, additive alongside it. Without real Stripe
credentials configured, create_connected_account/create_onboarding_link
fall back to generated placeholders (app/services/stripe_client.py), and
simulate_onboarding_complete drives an account to COMPLETE the same way
every other simulated provider in this codebase is exercised in tests."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.finance import HostStripeAccount
from app.models.party import Party
from tests.conftest import _make_admin, auth_admin_cookie


def _make_owner_party(db: Session) -> Party:
    party = Party(party_type="provider", status="active", jurisdiction="England")
    db.add(party)
    db.commit()
    return party


class TestCreateConnectedAccount:
    def test_creates_an_onboarding_account(self, client, db_session: Session):
        party = _make_owner_party(db_session)
        admin = _make_admin(db_session, email="hsa-create1@test.com", role="super_admin")

        r = client.post(
            "/api/finance/host-stripe-accounts",
            json={"partyId": party.id, "country": "GB", "email": "host@example.com"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "ONBOARDING"
        assert body["stripeAccountId"]
        assert body["payoutsEnabled"] is False

    def test_rejects_a_second_account_for_the_same_party(self, client, db_session: Session):
        party = _make_owner_party(db_session)
        admin = _make_admin(db_session, email="hsa-dup1@test.com", role="super_admin")
        payload = {"partyId": party.id, "country": "GB", "email": "host@example.com"}

        r1 = client.post("/api/finance/host-stripe-accounts", json=payload, cookies=auth_admin_cookie(admin))
        assert r1.status_code == 201, r1.text

        r2 = client.post("/api/finance/host-stripe-accounts", json=payload, cookies=auth_admin_cookie(admin))
        assert r2.status_code == 409, r2.text


class TestOnboardingLink:
    def test_returns_a_link_url(self, client, db_session: Session):
        party = _make_owner_party(db_session)
        admin = _make_admin(db_session, email="hsa-link1@test.com", role="super_admin")
        r = client.post(
            "/api/finance/host-stripe-accounts",
            json={"partyId": party.id, "country": "GB", "email": "host@example.com"},
            cookies=auth_admin_cookie(admin),
        )
        account_id = r.json()["id"]

        r = client.post(f"/api/finance/host-stripe-accounts/{account_id}/onboarding-link", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert r.json()["url"]


class TestSimulateOnboardingComplete:
    def test_marks_the_account_complete(self, client, db_session: Session):
        party = _make_owner_party(db_session)
        admin = _make_admin(db_session, email="hsa-sim1@test.com", role="super_admin")
        r = client.post(
            "/api/finance/host-stripe-accounts",
            json={"partyId": party.id, "country": "GB", "email": "host@example.com"},
            cookies=auth_admin_cookie(admin),
        )
        account_id = r.json()["id"]

        r = client.post(
            f"/api/finance/host-stripe-accounts/{account_id}/simulate-onboarding-complete",
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "COMPLETE"
        assert body["detailsSubmitted"] is True
        assert body["chargesEnabled"] is True
        assert body["payoutsEnabled"] is True

    def test_requires_super_admin(self, client, db_session: Session):
        party = _make_owner_party(db_session)
        super_admin = _make_admin(db_session, email="hsa-sim2-super@test.com", role="super_admin")
        plain_admin = _make_admin(db_session, email="hsa-sim2-plain@test.com", role="admin")
        r = client.post(
            "/api/finance/host-stripe-accounts",
            json={"partyId": party.id, "country": "GB", "email": "host@example.com"},
            cookies=auth_admin_cookie(super_admin),
        )
        account_id = r.json()["id"]

        r = client.post(
            f"/api/finance/host-stripe-accounts/{account_id}/simulate-onboarding-complete",
            cookies=auth_admin_cookie(plain_admin),
        )
        assert r.status_code == 403, r.text


class TestApplyAccountUpdatedEvent:
    """The webhook-driven counterpart to refresh_account_status -- see
    crud/host_stripe_account.py:apply_account_updated_event's own
    docstring."""

    def test_a_matching_account_is_updated_and_synced_to_complete(self, db_session: Session):
        from app.crud import host_stripe_account as hsa_crud

        party = _make_owner_party(db_session)
        account = hsa_crud.create_connected_account(
            db_session, party, admin=_make_admin(db_session, email="hsa-webhook1@test.com", role="super_admin"),
            country="GB", email="host@example.com",
        )
        assert account.status == "ONBOARDING"

        updated = hsa_crud.apply_account_updated_event(
            db_session, stripe_account_id=account.stripe_account_id,
            details_submitted=True, charges_enabled=True, payouts_enabled=True,
        )
        assert updated is not None
        assert updated.id == account.id
        assert updated.status == "COMPLETE"

    def test_an_unmatched_stripe_account_id_is_a_harmless_no_op(self, db_session: Session):
        from app.crud import host_stripe_account as hsa_crud

        result = hsa_crud.apply_account_updated_event(
            db_session, stripe_account_id="acct_does_not_exist",
            details_submitted=True, charges_enabled=True, payouts_enabled=True,
        )
        assert result is None


class TestRunPayoutUsesStripeTransferWhenConnected:
    def test_payout_records_a_transfer_id_once_the_host_is_stripe_connected(self, client, db_session: Session):
        from datetime import date, timezone
        from tests.test_ledger import _make_provider_rent_obligation

        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="hsa-payout1", amount=1000.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/host-stripe-accounts",
            json={"partyId": party_id, "country": "GB", "email": "host@example.com"},
            cookies=admin_cookies,
        )
        account_id = r.json()["id"]
        client.post(f"/api/finance/host-stripe-accounts/{account_id}/simulate-onboarding-complete", cookies=admin_cookies)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 1000.0, "currency": "INR", "idempotencyKey": "hsa-payout1"},
            cookies=admin_cookies,
        )
        payment_id = r.json()["id"]
        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 1000.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "PAID"
        assert body["stripeTransferId"]
        assert body["stripeTransferId"].startswith("TR-")
