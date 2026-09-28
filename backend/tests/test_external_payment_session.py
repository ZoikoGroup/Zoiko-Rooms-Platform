"""ZR-PAY-LINK-003 Section 6/10.1/Wireframe F: the provider-hosted payment
handoff -- crud/external_payment_session.py and its routes on
api/routes/rental_payments.py. No real Stripe credentials are configured in
tests, so services/stripe_client.py functions take their disclosed-simulation
fallback (create_session completes synchronously, same as the Listing Fee
flow already does)."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.crud import external_payment_session as eps_crud
from app.crud import rental_payment as rp_crud
from app.crud import rental_payment_provider_account as rpa_crud
from app.models.external_payment_session import ExternalPaymentSession
from app.models.market_policy import MarketPolicyPack
from app.models.rental_payment import RentalPaymentRecord
from tests.conftest import _make_admin, _make_user, auth_user_cookie
from tests.test_rental_payment_records import _make_guest, _make_party
from tests.test_rental_transaction_record import _make_rental



def _set_room_listing_authority(db: Session, room, party_id: int, status: str) -> None:
    """Who receives rent is decided by the room's listing authority -- revoke
    every existing one, then leave a single record in `status`."""
    from app.models.authority_record import AuthorityRecord

    for existing in db.query(AuthorityRecord).filter(AuthorityRecord.room_id == room.id):
        existing.status = "revoked"
    db.add(AuthorityRecord(party_id=party_id, room_id=room.id, authority_type="owner", relationship_type="OWNER", status=status))
    db.commit()

def _make_obligation_with_charge_ready_recipient(db: Session, *, guest_id: str = "G-EPS-1"):
    tenant = _make_guest(db, guest_id=guest_id)
    recipient = _make_party(db)
    account = rpa_crud.create_connected_account(db, recipient, country="GB", email="recipient@test.com")
    rpa_crud.simulate_onboarding_complete(db, account)
    obligation = rp_crud.create_obligation(
        db, obligation_type="RENT", tenant_guest_id=tenant.id, recipient_party_id=recipient.id,
        amount=850, currency="GBP", due_date=date.today(),
    )
    return obligation, tenant, recipient, account


class TestCreateSession:
    def test_requires_a_charge_ready_provider_account(self, db_session: Session):
        tenant = _make_guest(db_session, guest_id="G-EPS-NO-ACCT")
        recipient = _make_party(db_session)
        obligation = rp_crud.create_obligation(
            db_session, obligation_type="RENT", tenant_guest_id=tenant.id, recipient_party_id=recipient.id,
            amount=850, currency="GBP", due_date=date.today(),
        )
        with pytest.raises(HTTPException) as exc:
            eps_crud.create_session(
                db_session, tenant, obligation, success_url="https://app.test/return", cancel_url="https://app.test/return",
            )
        assert exc.value.status_code == 409

    def test_unconfigured_stripe_completes_synchronously(self, db_session: Session):
        obligation, tenant, _recipient, _account = _make_obligation_with_charge_ready_recipient(db_session)

        session, checkout_url = eps_crud.create_session(
            db_session, tenant, obligation, success_url="https://app.test/return", cancel_url="https://app.test/return",
        )
        assert session.status == "SUCCEEDED"
        assert checkout_url == ""

        record = db_session.query(RentalPaymentRecord).filter_by(obligation_id=obligation.id).one()
        assert record.status == "CONFIRMED"
        assert record.provenance == "PROVIDER_CONFIRMATION"
        assert record.declared_by_guest_id == tenant.id
        db_session.refresh(obligation)
        assert obligation.status == "CONFIRMED"

    def test_other_tenant_cannot_start_a_session_for_this_obligation(self, db_session: Session):
        obligation, _tenant, _recipient, _account = _make_obligation_with_charge_ready_recipient(db_session, guest_id="G-EPS-OWNER")
        other_tenant = _make_guest(db_session, guest_id="G-EPS-OTHER")

        with pytest.raises(HTTPException) as exc:
            eps_crud.create_session(
                db_session, other_tenant, obligation, success_url="https://app.test/return", cancel_url="https://app.test/return",
            )
        assert exc.value.status_code == 403


class _FakeStripeCheckout:
    """Stands in for real Stripe Checkout so a session can stay STARTED (the
    simulated fallback completes every session synchronously, which hides
    the double-click case entirely). `remote` is what Stripe would report
    for each checkout session id on retrieve."""

    def __init__(self, monkeypatch):
        self.created: list[str] = []  # idempotency keys, one per Stripe create call
        self.expired: list[str] = []
        self.remote: dict[str, dict] = {}
        monkeypatch.setattr(eps_crud.stripe_client, "is_configured", lambda: True)
        monkeypatch.setattr(eps_crud.stripe_client, "create_rent_payment_checkout_session", self._create)
        monkeypatch.setattr(eps_crud.stripe_client, "retrieve_rent_payment_checkout_session", self._retrieve)
        monkeypatch.setattr(eps_crud.stripe_client, "expire_rent_payment_checkout_session", self._expire)

    def _create(self, *, idempotency_key, **_kwargs):
        self.created.append(idempotency_key)
        session_id = f"cs_test_{len(self.created)}"
        self.remote[session_id] = {
            "payment_status": "unpaid", "payment_intent_id": None, "status": "open",
            "url": f"https://checkout.stripe.test/{session_id}",
        }
        return session_id, self.remote[session_id]["url"]

    def _retrieve(self, *, checkout_session_id, connected_account_id):
        return self.remote[checkout_session_id]

    def _expire(self, *, checkout_session_id, connected_account_id):
        self.expired.append(checkout_session_id)
        self.remote[checkout_session_id]["status"] = "expired"


def _start(db: Session, tenant, obligation):
    return eps_crud.create_session(
        db, tenant, obligation, success_url="https://app.test/return", cancel_url="https://app.test/return",
    )


class TestNoDoubleCharge:
    def test_double_click_reuses_the_open_checkout_instead_of_opening_a_second(self, db_session: Session, monkeypatch):
        obligation, tenant, _recipient, _account = _make_obligation_with_charge_ready_recipient(db_session, guest_id="G-EPS-DBL")
        stripe = _FakeStripeCheckout(monkeypatch)

        first, first_url = _start(db_session, tenant, obligation)
        second, second_url = _start(db_session, tenant, obligation)

        assert second.id == first.id
        assert second_url == first_url
        assert len(stripe.created) == 1
        assert db_session.query(ExternalPaymentSession).filter_by(obligation_id=obligation.id).count() == 1

    def test_an_already_paid_obligation_cannot_be_paid_again(self, db_session: Session):
        obligation, tenant, _recipient, _account = _make_obligation_with_charge_ready_recipient(db_session, guest_id="G-EPS-PAID")
        _start(db_session, tenant, obligation)  # simulated path -- completes, obligation CONFIRMED

        with pytest.raises(HTTPException) as exc:
            _start(db_session, tenant, obligation)
        assert exc.value.status_code == 409
        assert "already paid" in exc.value.detail
        assert db_session.query(RentalPaymentRecord).filter_by(obligation_id=obligation.id).count() == 1

    def test_paid_at_stripe_before_the_webhook_arrived_is_recorded_not_charged_again(self, db_session: Session, monkeypatch):
        obligation, tenant, _recipient, _account = _make_obligation_with_charge_ready_recipient(db_session, guest_id="G-EPS-LATE")
        stripe = _FakeStripeCheckout(monkeypatch)
        first, _url = _start(db_session, tenant, obligation)
        stripe.remote[first.provider_checkout_session_id].update(
            payment_status="paid", payment_intent_id="pi_test_late", status="complete",
        )

        with pytest.raises(HTTPException) as exc:
            _start(db_session, tenant, obligation)
        assert exc.value.status_code == 409
        assert len(stripe.created) == 1
        db_session.refresh(first)
        assert first.status == "SUCCEEDED"
        db_session.refresh(obligation)
        assert obligation.status == "CONFIRMED"

    def test_an_expired_checkout_is_replaced_by_a_fresh_attempt(self, db_session: Session, monkeypatch):
        obligation, tenant, _recipient, _account = _make_obligation_with_charge_ready_recipient(db_session, guest_id="G-EPS-EXP")
        stripe = _FakeStripeCheckout(monkeypatch)
        first, _url = _start(db_session, tenant, obligation)
        stripe.remote[first.provider_checkout_session_id]["status"] = "expired"

        second, second_url = _start(db_session, tenant, obligation)

        assert second.id != first.id
        assert second_url.endswith(second.provider_checkout_session_id)
        assert len(stripe.created) == 2
        assert stripe.created[0] != stripe.created[1]  # a new attempt gets a new Stripe idempotency key
        db_session.refresh(first)
        assert first.status == "FAILED"
        assert stripe.expired == []  # already expired at Stripe -- nothing to close

    def test_an_amended_amount_closes_the_old_checkout_at_stripe(self, db_session: Session, monkeypatch):
        obligation, tenant, _recipient, _account = _make_obligation_with_charge_ready_recipient(db_session, guest_id="G-EPS-AMD")
        stripe = _FakeStripeCheckout(monkeypatch)
        first, _url = _start(db_session, tenant, obligation)
        obligation.amount = 900
        db_session.commit()

        second, _url = _start(db_session, tenant, obligation)

        assert second.id != first.id
        assert float(second.amount) == 900.0
        # The old 850 checkout must not stay payable at Stripe.
        assert stripe.expired == [first.provider_checkout_session_id]


def _make_obligation_with_real_room(db: Session, *, suffix: str):
    """Unlike _make_obligation_with_charge_ready_recipient above (no
    agreement/occupancy, so RentalPaymentObligation.room is always None),
    this goes through the full rental fixture so .room actually resolves --
    needed to exercise the room-scoped SUSPENDED/jurisdiction checks in
    create_session, which are no-ops without a resolvable room."""
    rental = _make_rental(db, host_email=f"eps-room-host-{suffix}@test.com", renter_email=f"eps-room-renter-{suffix}@test.com")
    obligation = rp_crud.create_obligation(
        db, obligation_type="RENT", tenant_guest_id=rental["guest"].id, recipient_party_id=rental["host_party"].id,
        amount=850, currency="GBP", due_date=date.today(), occupancy_id=rental["occupancy"].id,
    )
    return obligation, rental


class TestSuspendedConnectionBlocksSession:
    def test_revoked_authority_blocks_a_new_session_even_with_a_charge_ready_account(self, db_session: Session):
        obligation, rental = _make_obligation_with_real_room(db_session, suffix="revoked")
        host_party, room, guest = rental["host_party"], rental["room"], rental["guest"]

        _set_room_listing_authority(db_session, room, host_party.id, "revoked")

        account = rpa_crud.create_connected_account(db_session, host_party, country="GB", email="recipient@test.com")
        rpa_crud.simulate_onboarding_complete(db_session, account)

        with pytest.raises(HTTPException) as exc:
            eps_crud.create_session(
                db_session, guest, obligation, success_url="https://app.test/return", cancel_url="https://app.test/return",
            )
        assert exc.value.status_code == 409
        assert "suspended" in exc.value.detail.lower()

    def test_a_healthy_connection_with_a_real_room_is_not_blocked(self, db_session: Session):
        obligation, rental = _make_obligation_with_real_room(db_session, suffix="healthy")
        host_party, guest = rental["host_party"], rental["guest"]

        account = rpa_crud.create_connected_account(db_session, host_party, country="GB", email="recipient@test.com")
        rpa_crud.simulate_onboarding_complete(db_session, account)

        session, _url = eps_crud.create_session(
            db_session, guest, obligation, success_url="https://app.test/return", cancel_url="https://app.test/return",
        )
        assert session.status == "SUCCEEDED"


class TestJurisdictionGating:
    def _block_card_for_england(self, db: Session) -> None:
        """Overrides the conftest-seeded England pack with a higher version
        that opts into BANK_TRANSFER only -- same override technique
        test_deposit_instrument_allowed.py:_set_deposit_policy uses."""
        db.add(MarketPolicyPack(
            jurisdiction_code="England", version=99, effective_from=date(2020, 1, 1),
            permitted_payment_method_classes=["BANK_TRANSFER"],
        ))
        db.commit()

    def test_session_creation_is_blocked_when_card_is_not_permitted(self, db_session: Session):
        obligation, rental = _make_obligation_with_real_room(db_session, suffix="jurisdiction")
        host_party, guest = rental["host_party"], rental["guest"]
        account = rpa_crud.create_connected_account(db_session, host_party, country="GB", email="recipient@test.com")
        rpa_crud.simulate_onboarding_complete(db_session, account)

        self._block_card_for_england(db_session)

        with pytest.raises(HTTPException) as exc:
            eps_crud.create_session(
                db_session, guest, obligation, success_url="https://app.test/return", cancel_url="https://app.test/return",
            )
        assert exc.value.status_code == 409

    def test_connection_view_does_not_offer_the_online_rail_when_not_permitted(self, db_session: Session):
        from app.crud.payment_connection import get_payment_connection_for_room

        obligation, rental = _make_obligation_with_real_room(db_session, suffix="jurisdiction-view")
        host_party, room = rental["host_party"], rental["room"]
        account = rpa_crud.create_connected_account(db_session, host_party, country="GB", email="recipient@test.com")
        rpa_crud.simulate_onboarding_complete(db_session, account)

        connection_before = get_payment_connection_for_room(db_session, room)
        assert connection_before.destination_method == "ONLINE_PROVIDER"

        self._block_card_for_england(db_session)

        connection_after = get_payment_connection_for_room(db_session, room)
        assert connection_after.destination_method != "ONLINE_PROVIDER"
        assert connection_after.state != "ACTIVE"


class TestRecordProviderPaymentSuccessIdempotency:
    def test_a_second_call_on_an_already_succeeded_session_is_a_no_op(self, db_session: Session):
        obligation, tenant, _recipient, _account = _make_obligation_with_charge_ready_recipient(db_session, guest_id="G-EPS-IDEM")
        session, _url = eps_crud.create_session(
            db_session, tenant, obligation, success_url="https://app.test/return", cancel_url="https://app.test/return",
        )
        assert session.status == "SUCCEEDED"
        record_count_before = db_session.query(RentalPaymentRecord).filter_by(obligation_id=obligation.id).count()

        result = eps_crud.record_provider_payment_success(db_session, session, payment_intent_id="pi_replay")
        assert result is None
        assert db_session.query(RentalPaymentRecord).filter_by(obligation_id=obligation.id).count() == record_count_before


class TestWebhookIngestion:
    def _make_started_session(self, db: Session, *, checkout_session_id: str, guest_id: str) -> ExternalPaymentSession:
        """A session deliberately left STARTED -- simulates the real-Stripe
        path where create_session's own synchronous-completion fallback
        does NOT fire (that only happens when Stripe is unconfigured;
        here the row is built directly to represent the configured-Stripe
        case, where only a webhook resolves it)."""
        obligation, tenant, _recipient, account = _make_obligation_with_charge_ready_recipient(db, guest_id=guest_id)
        session = ExternalPaymentSession(
            obligation_id=obligation.id, tenant_guest_id=tenant.id,
            recipient_stripe_account_id=account.stripe_account_id,
            provider_checkout_session_id=checkout_session_id, status="STARTED", amount=850, currency="GBP",
        )
        db.add(session)
        db.commit()
        return session

    def test_paid_session_creates_a_confirmed_record(self, db_session: Session):
        session = self._make_started_session(db_session, checkout_session_id="cs_eps_paid_1", guest_id="G-EPS-WH-1")
        event = {
            "id": "evt_eps_paid_1", "type": "checkout.session.completed", "account": session.recipient_stripe_account_id,
            "data": {"object": {"id": "cs_eps_paid_1", "payment_status": "paid", "payment_intent": "pi_eps_1"}},
        }
        eps_crud.ingest_stripe_webhook_event(db_session, event)
        db_session.refresh(session)
        assert session.status == "SUCCEEDED"
        assert session.provider_payment_intent_id == "pi_eps_1"

        record = db_session.query(RentalPaymentRecord).filter_by(obligation_id=session.obligation_id).one()
        assert record.status == "CONFIRMED"

    def test_replayed_event_id_is_a_no_op(self, db_session: Session):
        session = self._make_started_session(db_session, checkout_session_id="cs_eps_replay_1", guest_id="G-EPS-WH-2")
        event = {
            "id": "evt_eps_replay_1", "type": "checkout.session.completed", "account": session.recipient_stripe_account_id,
            "data": {"object": {"id": "cs_eps_replay_1", "payment_status": "paid", "payment_intent": "pi_eps_replay"}},
        }
        eps_crud.ingest_stripe_webhook_event(db_session, event)
        eps_crud.ingest_stripe_webhook_event(db_session, event)

        record_count = db_session.query(RentalPaymentRecord).filter_by(obligation_id=session.obligation_id).count()
        assert record_count == 1

    def test_event_for_an_unknown_connected_account_is_ignored(self, db_session: Session):
        session = self._make_started_session(db_session, checkout_session_id="cs_eps_unknown_acct", guest_id="G-EPS-WH-3")
        event = {
            "id": "evt_eps_unknown_acct", "type": "checkout.session.completed", "account": "acct_never_connected",
            "data": {"object": {"id": "cs_eps_unknown_acct", "payment_status": "paid", "payment_intent": "pi_x"}},
        }
        eps_crud.ingest_stripe_webhook_event(db_session, event)
        db_session.refresh(session)
        assert session.status == "STARTED"

    def test_event_with_no_account_at_all_is_ignored(self, db_session: Session):
        session = self._make_started_session(db_session, checkout_session_id="cs_eps_no_acct", guest_id="G-EPS-WH-4")
        event = {
            "id": "evt_eps_no_acct", "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_eps_no_acct", "payment_status": "paid", "payment_intent": "pi_x"}},
        }
        eps_crud.ingest_stripe_webhook_event(db_session, event)
        db_session.refresh(session)
        assert session.status == "STARTED"

    def test_async_payment_failed_marks_session_failed_with_no_record(self, db_session: Session):
        session = self._make_started_session(db_session, checkout_session_id="cs_eps_failed_1", guest_id="G-EPS-WH-5")
        event = {
            "id": "evt_eps_failed_1", "type": "checkout.session.async_payment_failed", "account": session.recipient_stripe_account_id,
            "data": {"object": {"id": "cs_eps_failed_1"}},
        }
        eps_crud.ingest_stripe_webhook_event(db_session, event)
        db_session.refresh(session)
        assert session.status == "FAILED"
        assert db_session.query(RentalPaymentRecord).filter_by(obligation_id=session.obligation_id).count() == 0

    def test_unknown_checkout_session_id_is_ignored_not_an_error(self, db_session: Session):
        _obligation, _tenant, recipient, account = _make_obligation_with_charge_ready_recipient(db_session, guest_id="G-EPS-WH-6")
        eps_crud.ingest_stripe_webhook_event(db_session, {
            "id": "evt_eps_never", "type": "checkout.session.completed", "account": account.stripe_account_id,
            "data": {"object": {"id": "cs_never_created", "payment_status": "paid", "payment_intent": "pi_never"}},
        })


class TestPaymentSessionRoutes:
    def test_start_and_get_session_route(self, client, db_session: Session):
        obligation, tenant, _recipient, _account = _make_obligation_with_charge_ready_recipient(db_session, guest_id="G-EPS-ROUTE-1")
        tenant_user = _make_user(db_session, email="eps-route-tenant@test.com")
        tenant.user_account_id = tenant_user.id
        db_session.commit()

        r = client.post(
            f"/api/users/rental-payments/obligations/{obligation.id}/payment-session", cookies=auth_user_cookie(tenant_user),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["session"]["status"] == "SUCCEEDED"
        session_id = body["session"]["id"]

        r_get = client.get(f"/api/users/rental-payments/payment-sessions/{session_id}", cookies=auth_user_cookie(tenant_user))
        assert r_get.status_code == 200, r_get.text
        assert r_get.json()["status"] == "SUCCEEDED"

    def test_other_tenant_cannot_view_the_session(self, client, db_session: Session):
        obligation, tenant, _recipient, _account = _make_obligation_with_charge_ready_recipient(db_session, guest_id="G-EPS-ROUTE-2")
        tenant_user = _make_user(db_session, email="eps-route-owner@test.com")
        tenant.user_account_id = tenant_user.id
        db_session.commit()

        r = client.post(
            f"/api/users/rental-payments/obligations/{obligation.id}/payment-session", cookies=auth_user_cookie(tenant_user),
        )
        session_id = r.json()["session"]["id"]

        outsider = _make_user(db_session, email="eps-route-outsider@test.com")
        db_session.commit()
        r_get = client.get(f"/api/users/rental-payments/payment-sessions/{session_id}", cookies=auth_user_cookie(outsider))
        assert r_get.status_code == 403, r_get.text

    def test_resolve_by_checkout_session_id_route(self, client, db_session: Session):
        obligation, tenant, _recipient, _account = _make_obligation_with_charge_ready_recipient(db_session, guest_id="G-EPS-ROUTE-3")
        tenant_user = _make_user(db_session, email="eps-route-resolve@test.com")
        tenant.user_account_id = tenant_user.id
        db_session.commit()

        r = client.post(
            f"/api/users/rental-payments/obligations/{obligation.id}/payment-session", cookies=auth_user_cookie(tenant_user),
        )
        assert r.status_code == 201, r.text
        session_row = db_session.query(ExternalPaymentSession).filter_by(obligation_id=obligation.id).one()

        r_resolve = client.get(
            f"/api/users/rental-payments/payment-sessions/by-checkout-session/{session_row.provider_checkout_session_id}",
            cookies=auth_user_cookie(tenant_user),
        )
        assert r_resolve.status_code == 200, r_resolve.text
        assert r_resolve.json()["status"] == "SUCCEEDED"

    def test_resolve_by_checkout_session_id_unknown_is_404(self, client, db_session: Session):
        _obligation, tenant, _recipient, _account = _make_obligation_with_charge_ready_recipient(db_session, guest_id="G-EPS-404")
        user = _make_user(db_session, email="eps-route-resolve-404@test.com")
        tenant.user_account_id = user.id
        db_session.commit()
        r = client.get(
            "/api/users/rental-payments/payment-sessions/by-checkout-session/cs_never_created",
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 404, r.text


class TestObligationConnectionRoute:
    """ZR-PAY-LINK-003 Section 3.1: the tenant-facing counterpart to the
    host-facing payment-connection route -- gap #4 from the re-audit
    ('tenant gets no SUSPENDED warning')."""

    def test_tenant_can_view_their_own_obligations_connection(self, client, db_session: Session):
        obligation, rental = _make_obligation_with_real_room(db_session, suffix="conn-route")
        r = client.get(
            f"/api/users/rental-payments/obligations/{obligation.id}/connection",
            cookies=auth_user_cookie(rental["renter_user"]),
        )
        assert r.status_code == 200, r.text
        assert r.json()["roomId"] == rental["room"].id

    def test_reflects_suspended_state(self, client, db_session: Session):
        obligation, rental = _make_obligation_with_real_room(db_session, suffix="conn-suspended")
        host_party, room = rental["host_party"], rental["room"]
        _set_room_listing_authority(db_session, room, host_party.id, "revoked")

        r = client.get(
            f"/api/users/rental-payments/obligations/{obligation.id}/connection",
            cookies=auth_user_cookie(rental["renter_user"]),
        )
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "SUSPENDED"

    def test_other_tenant_cannot_view_this_obligations_connection(self, client, db_session: Session):
        obligation, _rental = _make_obligation_with_real_room(db_session, suffix="conn-outsider")
        outsider = _make_user(db_session, email="eps-conn-route-outsider@test.com")
        db_session.commit()
        r = client.get(
            f"/api/users/rental-payments/obligations/{obligation.id}/connection", cookies=auth_user_cookie(outsider),
        )
        assert r.status_code == 403, r.text
