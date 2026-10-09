"""ZR-LF-001 One-Time Listing Fee: checkout only once every other publish
requirement is met, one status call for the fee screens (receipt number,
refunds), the funnel events, the market in Stripe's metadata, and the
guards against one fee covering a second room."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import listing as listing_crud
from app.crud import listing_fee as lf_crud
from app.models.domain_event import DomainEvent
from app.models.listing_fee import ListingFeeRefund
from app.models.notification import Notification
from app.services import stripe_client
from tests.conftest import _make_admin, _make_room_owned_by, _make_user, auth_admin_cookie, auth_user_cookie
from tests.test_rental_payment_records import _make_party
from tests.test_zr_pay_002_acceptance_gates import _make_listing, _make_listing_fee_policy


def _host_listing(db: Session, key: str):
    user = _make_user(db, email=f"lf001-{key}@test.com")
    party = _make_party(db, party_type="provider")
    user.party_id = party.id
    listing = _make_listing(db, listing_id=f"L-LF001-{key}", party_id=party.id)
    _make_listing_fee_policy(db)
    db.commit()
    return user, party, listing


def _only_fee_outstanding(monkeypatch):
    monkeypatch.setattr(listing_crud, "check_publish_eligibility", lambda db, listing: ["Listing Fee has not been paid"])


def _funnel(db: Session, listing_id: str) -> list[str]:
    return list(db.scalars(
        select(DomainEvent.event_type)
        .where(DomainEvent.resource_id == listing_id, DomainEvent.event_type.like("listing_fee.funnel.%"))
        .order_by(DomainEvent.id)
    ))


class TestCheckoutRequiresEveryOtherRequirement:
    def test_checkout_is_refused_while_other_requirements_are_open(self, client, db_session: Session):
        user, party, listing = _host_listing(db_session, "blocked")
        quote = lf_crud.create_quote(db_session, listing, party)
        r = client.post(
            "/api/users/listing-fees/checkout-sessions",
            json={"quoteId": quote.id, "idempotencyKey": "lf001-blocked", "billingCountry": "GB"},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 409, r.text
        assert "not linked to a room" in r.json()["detail"]
        assert not lf_crud.listing_fee_is_paid(db_session, listing.id)

    def test_checkout_is_allowed_once_only_the_fee_is_left(self, client, db_session: Session, monkeypatch):
        _only_fee_outstanding(monkeypatch)
        user, party, listing = _host_listing(db_session, "ready")
        listing.state = "APPROVED"
        db_session.commit()
        quote = lf_crud.create_quote(db_session, listing, party)
        r = client.post(
            "/api/users/listing-fees/checkout-sessions",
            json={"quoteId": quote.id, "idempotencyKey": "lf001-ready", "billingCountry": "GB"},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 201, r.text

    def test_checkout_is_refused_before_admin_approval(self, client, db_session: Session, monkeypatch):
        _only_fee_outstanding(monkeypatch)
        user, party, listing = _host_listing(db_session, "unapproved")
        quote = lf_crud.create_quote(db_session, listing, party)
        r = client.post(
            "/api/users/listing-fees/checkout-sessions",
            json={"quoteId": quote.id, "idempotencyKey": "lf001-unapproved", "billingCountry": "GB"},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 409, r.text
        assert "approved" in r.json()["detail"]
        assert not lf_crud.listing_fee_is_paid(db_session, listing.id)

    def test_paying_the_fee_publishes_the_approved_listing(self, client, db_session: Session, monkeypatch):
        """No Stripe key in tests -> checkout completes synchronously, which runs
        the same _complete_payment_success path the webhook/return uses."""
        from app.core.config import settings
        from tests.conftest import _make_admin

        from app.models.property import Property
        from app.models.room import Room

        _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        _only_fee_outstanding(monkeypatch)
        user, party, listing = _host_listing(db_session, "autopublish")
        prop = Property(owner_party_id=party.id, address="1 Fee Way", city="London", status="active")
        db_session.add(prop)
        db_session.flush()
        room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
        db_session.add(room)
        db_session.flush()
        listing.room_id = room.id
        listing.state = "APPROVED"
        db_session.commit()
        # The fee publishes only once identity / property / authority also pass
        # (ZR-AUTHORITY-002 Section 2.4 -- paying never stands in for them).
        from tests.conftest import make_room_publishable

        make_room_publishable(db_session, room)
        quote = lf_crud.create_quote(db_session, listing, party)
        r = client.post(
            "/api/users/listing-fees/checkout-sessions",
            json={"quoteId": quote.id, "idempotencyKey": "lf001-autopublish", "billingCountry": "GB"},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 201, r.text
        db_session.refresh(listing)
        assert lf_crud.listing_fee_is_paid(db_session, listing.id)
        assert listing.state == "PUBLISHED"

    def test_blockers_never_include_the_fee_itself(self, db_session: Session, monkeypatch):
        monkeypatch.setattr(
            listing_crud, "check_publish_eligibility",
            lambda db, listing: ["Property verification is not approved", "Listing Fee has not been paid",
                                 lf_crud.LISTING_FEE_UNAVAILABLE_MESSAGE],
        )
        _user, _party, listing = _host_listing(db_session, "reasons")
        assert lf_crud.listing_fee_checkout_blockers(db_session, listing) == ["Property verification is not approved"]


class TestStripeMetadata:
    def test_market_and_currency_are_sent_to_stripe(self, db_session: Session, monkeypatch):
        seen = {}

        def fake_create(**kwargs):
            seen.update(kwargs["metadata"])
            return "cs_lf001_meta", "https://checkout.stripe.test/cs_lf001_meta"

        monkeypatch.setattr(stripe_client, "create_checkout_session", fake_create)
        monkeypatch.setattr(stripe_client, "is_configured", lambda: True)
        _user, party, listing = _host_listing(db_session, "meta")
        quote = lf_crud.create_quote(db_session, listing, party)
        lf_crud.create_checkout(db_session, quote, party, idempotency_key="lf001-meta", billing_country="GB")
        assert seen["domain"] == "listing_fee"
        assert seen["listing_id"] == listing.id
        assert seen["market"] == "England"
        assert seen["currency"] == "GBP"


class TestStatusEndpoint:
    def test_status_shows_blockers_and_the_paid_payment_with_its_receipt(self, client, db_session: Session):
        user, party, listing = _host_listing(db_session, "status")
        r = client.get(f"/api/users/listing-fees/listings/{listing.id}/status", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["listingName"] == listing.name
        assert body["listingAddress"] == "Koramangala"
        assert body["feePaid"] is False
        assert body["latestPayment"] is None
        assert body["checkoutBlockers"]

        quote = lf_crud.create_quote(db_session, listing, party)
        lf_crud.create_checkout(db_session, quote, party, idempotency_key="lf001-status", billing_country="GB")
        body = client.get(f"/api/users/listing-fees/listings/{listing.id}/status", cookies=auth_user_cookie(user)).json()
        assert body["feePaid"] is True
        payment = body["latestPayment"]
        assert payment["status"] == "SUCCEEDED"
        assert payment["receiptNumber"].startswith("ZR-LF-")
        assert payment["feeAmount"] == 25.0
        assert payment["taxAmount"] == 5.0
        assert payment["amount"] == 30.0
        assert payment["fullyRefunded"] is False

    def test_a_fully_refunded_fee_is_shown_as_refunded(self, client, db_session: Session):
        user, party, listing = _host_listing(db_session, "refunded")
        quote = lf_crud.create_quote(db_session, listing, party)
        payment, _ = lf_crud.create_checkout(db_session, quote, party, idempotency_key="lf001-refunded", billing_country="GB")
        admin = _make_admin(db_session, email="lf001-refund-admin@test.com", role="super_admin")
        db_session.add(ListingFeeRefund(
            payment_id=payment.id, amount=float(payment.amount), currency=payment.currency, reason="test",
            idempotency_key="lf001-refund", status="REFUNDED", requested_by_admin_id=admin.id,
            completed_at=datetime.now(timezone.utc),
        ))
        db_session.commit()

        body = client.get(f"/api/users/listing-fees/listings/{listing.id}/status", cookies=auth_user_cookie(user)).json()
        assert body["feePaid"] is False
        latest = body["latestPayment"]
        assert latest["id"] == payment.id
        assert latest["fullyRefunded"] is True
        assert latest["refundStatus"] == "REFUNDED"
        assert latest["refundedAmount"] == 30.0

    def test_another_host_cannot_read_the_status(self, client, db_session: Session):
        _user, _party, listing = _host_listing(db_session, "owner")
        stranger, _p, _l = _host_listing(db_session, "stranger")
        r = client.get(f"/api/users/listing-fees/listings/{listing.id}/status", cookies=auth_user_cookie(stranger))
        assert r.status_code == 403


class TestFunnelEvents:
    def test_viewed_started_completed(self, db_session: Session):
        _user, party, listing = _host_listing(db_session, "funnel")
        quote = lf_crud.create_quote(db_session, listing, party)
        lf_crud.create_checkout(db_session, quote, party, idempotency_key="lf001-funnel", billing_country="GB")
        assert _funnel(db_session, listing.id) == [
            "listing_fee.funnel.fee_viewed", "listing_fee.funnel.checkout_started", "listing_fee.funnel.checkout_completed",
        ]

    def test_cancel_return_records_checkout_cancelled_and_charges_nothing(self, client, db_session: Session):
        from tests.test_listing_fee_checkout_session_webhook import _make_pending_checkout_session_payment

        payment, listing = _make_pending_checkout_session_payment(db_session, checkout_session_id="cs_lf001_cancel")
        user = _make_user(db_session, email="lf001-cancel@test.com")
        user.party_id = payment.party_id
        db_session.commit()
        r = client.post("/api/users/listing-fees/checkout-sessions/cs_lf001_cancel/cancelled", cookies=auth_user_cookie(user))
        assert r.status_code == 204, r.text
        db_session.refresh(payment)
        assert payment.status == "PENDING"
        assert "listing_fee.funnel.checkout_cancelled" in _funnel(db_session, listing.id)

    def test_failure_records_checkout_failed(self, db_session: Session):
        from tests.test_listing_fee_checkout_session_webhook import _make_pending_checkout_session_payment

        payment, listing = _make_pending_checkout_session_payment(db_session, checkout_session_id="cs_lf001_fail")
        lf_crud._complete_payment_failure(db_session, payment, "Card declined", notify=False)
        assert "listing_fee.funnel.checkout_failed" in _funnel(db_session, listing.id)


class TestOneFeeOneRoom:
    def test_a_paid_listing_cannot_be_moved_to_another_room(self, db_session: Session):
        _user, party, listing = _host_listing(db_session, "room")
        room_a, room_b = _make_room_owned_by(db_session, party), _make_room_owned_by(db_session, party)
        listing.room_id = room_a.id
        quote = lf_crud.create_quote(db_session, listing, party)
        lf_crud.create_checkout(db_session, quote, party, idempotency_key="lf001-room", billing_country="GB")
        with pytest.raises(HTTPException) as exc:
            lf_crud.assert_room_change_allowed(db_session, listing, room_b.id)
        assert exc.value.status_code == 409
        lf_crud.assert_room_change_allowed(db_session, listing, room_a.id)  # same room is fine

    def test_an_unpaid_listing_can_change_room(self, db_session: Session):
        _user, party, listing = _host_listing(db_session, "room-unpaid")
        room_a, room_b = _make_room_owned_by(db_session, party), _make_room_owned_by(db_session, party)
        listing.room_id = room_a.id
        lf_crud.assert_room_change_allowed(db_session, listing, room_b.id)

    def test_second_listing_for_a_paid_room_is_flagged_to_super_admins(self, db_session: Session):
        _make_admin(db_session, email="lf001-dup-sa@test.com", role="super_admin")
        _user, party, first = _host_listing(db_session, "dup1")
        room = _make_room_owned_by(db_session, party)
        first.room_id = room.id
        quote = lf_crud.create_quote(db_session, first, party)
        lf_crud.create_checkout(db_session, quote, party, idempotency_key="lf001-dup", billing_country="GB")
        second = _make_listing(db_session, listing_id="L-LF001-dup2", party_id=party.id)
        second.room_id = room.id
        db_session.commit()

        assert lf_crud.flag_possible_duplicate_listing(db_session, second, party) is True
        assert lf_crud.flag_possible_duplicate_listing(db_session, second, party) is True  # flagged once
        alerts = db_session.scalars(
            select(Notification).where(Notification.notification_type == "listing_fee.duplicate_listing_suspected")
        ).all()
        assert len(alerts) == 1


class TestAdminReports:
    def test_funnel_counts_distinct_listings_per_stage(self, client, db_session: Session):
        _user, party, listing = _host_listing(db_session, "rep-funnel")
        lf_crud.create_quote(db_session, listing, party)
        quote = lf_crud.create_quote(db_session, listing, party)  # viewed twice, counted once
        lf_crud.create_checkout(db_session, quote, party, idempotency_key="lf001-rep", billing_country="GB")
        admin = _make_admin(db_session, email="lf001-rep-sa@test.com", role="super_admin")
        r = client.get("/api/finance/listing-fees/funnel?days=7", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        stages = {s["name"]: s["listings"] for s in r.json()["stages"]}
        assert stages["fee_viewed"] == 1
        assert stages["checkout_started"] == 1
        assert stages["checkout_completed"] == 1
        assert stages["checkout_cancelled"] == 0
        assert r.json()["conversionRate"] == 1.0

    def test_duplicate_flags_are_listed_for_super_admins_only(self, client, db_session: Session):
        _user, party, first = _host_listing(db_session, "rep-dup1")
        room = _make_room_owned_by(db_session, party)
        first.room_id = room.id
        quote = lf_crud.create_quote(db_session, first, party)
        lf_crud.create_checkout(db_session, quote, party, idempotency_key="lf001-rep-dup", billing_country="GB")
        second = _make_listing(db_session, listing_id="L-LF001-rep-dup2", party_id=party.id)
        second.room_id = room.id
        db_session.commit()
        lf_crud.flag_possible_duplicate_listing(db_session, second, party)

        admin = _make_admin(db_session, email="lf001-rep-dup-sa@test.com", role="super_admin")
        r = client.get("/api/finance/listing-fees/duplicate-flags", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        flag = r.json()[0]
        assert flag["listingId"] == second.id
        assert flag["paidListingId"] == first.id
        assert flag["roomId"] == room.id

        staff = _make_admin(db_session, email="lf001-rep-dup-staff@test.com", role="admin")
        assert client.get("/api/finance/listing-fees/duplicate-flags", cookies=auth_admin_cookie(staff)).status_code == 403
