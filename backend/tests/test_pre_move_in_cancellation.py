"""Section 7 gap: cancelling a signed-but-not-moved-in booking previously
had only one escape hatch anywhere -- the generic admin end_occupancy
action, which never calculated or refunded any money and had no dedicated
entry point for a renter or a Host to use themselves. crud/occupancy.py:
cancel_before_move_in is the real feature this file tests: a free-
cancellation window, a configurable fee outside it, a real refund issued
through the same request_refund/decide_refund pipeline every other refund
in this codebase uses, and three separate entry points (renter, Host,
admin)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import Obligation
from app.models.leasing import Agreement
from app.models.listing import Listing
from app.models.market_policy import MarketPolicyPack
from app.models.occupancy import Occupancy
from app.models.user_account import UserAccount
from app.models.party import Party
from app.core.config import settings
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie
from tests.test_booking_change_requests import _signed_agreement_before_move_in


def _seed_system_admin(db: Session) -> None:
    """cancel_before_move_in's renter/Host self-service path attributes the
    resulting RefundRequest to the platform's seeded system admin
    (get_system_admin) when no human admin session triggered it -- same
    seed tests/test_ledger.py's own PSP-dispatch tests already need."""
    _make_admin(db, email=settings.seed_admin_email, role="super_admin")


def _occupancy_for_agreement(db: Session, agreement_id: int) -> Occupancy:
    agreement = db.get(Agreement, agreement_id)
    return db.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id))


def _make_host_user_for_listing(db: Session, agreement_id: int, *, email: str) -> UserAccount:
    agreement = db.get(Agreement, agreement_id)
    listing = db.get(Listing, agreement.offer.listing_id)
    host = _make_user(db, email=email)
    host.party_id = listing.party_id
    db.commit()
    return host


class TestRenterCancelsBeforeMoveIn:
    def test_within_free_window_is_a_full_refund(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="pmc1", start_date=date.today() + timedelta(days=10),
        )
        occupancy = _occupancy_for_agreement(db_session, agreement_id)
        assert occupancy.status == "PENDING_MOVE_IN"
        _seed_system_admin(db_session)

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/cancel-before-move-in",
            json={"reason": "changed my mind"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["occupancy"]["status"] == "CANCELLED"
        assert body["feeAmount"] == 0.0
        # 500 rent + 500 deposit paid by _full_signed_agreement's own fixture
        assert body["refundedAmount"] == 1000.0

    def test_outside_free_window_with_a_configured_fee_deducts_it(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="pmc2", start_date=date.today() + timedelta(days=10),
        )
        occupancy = _occupancy_for_agreement(db_session, agreement_id)
        occupancy.created_at = datetime.now(timezone.utc) - timedelta(hours=48)

        # _make_listing_with_room's own Property row never sets
        # jurisdiction_code, which defaults to "England" (not "IN") --
        # that's what jurisdiction_code_for_occupancy actually resolves here.
        policy = db_session.query(MarketPolicyPack).filter_by(jurisdiction_code="England").one()
        policy.pre_move_in_free_cancellation_hours = 24
        policy.pre_move_in_cancellation_fee_rent_multiple = 0.5  # 0.5 x 500 rent = 250 fee
        db_session.commit()
        _seed_system_admin(db_session)

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/cancel-before-move-in",
            json={"reason": "no longer needed"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["feeAmount"] == 250.0
        assert body["refundedAmount"] == 750.0  # 1000 paid - 250 fee

        deposit_obligation = db_session.scalar(
            select(Obligation).where(Obligation.agreement_id == agreement_id, Obligation.obligation_type == "DEPOSIT")
        )
        # Fee is deducted from deposit first.
        remaining_paid = sum(a.amount_allocated for a in deposit_obligation.allocations)
        assert float(remaining_paid) == 250.0  # 500 paid - 250 refunded

    def test_a_different_renter_cannot_cancel_someone_elses_booking(self, client, db_session: Session):
        agreement_id, _admin_cookies, _renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="pmc3", start_date=date.today() + timedelta(days=10),
        )
        occupancy = _occupancy_for_agreement(db_session, agreement_id)
        intruder = _make_user(db_session, email="pmc3-intruder@test.com")

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/cancel-before-move-in",
            json={}, cookies=auth_user_cookie(intruder),
        )
        assert r.status_code == 403, r.text

    def test_cannot_cancel_an_active_occupancy_this_way(self, client, db_session: Session):
        agreement_id, _admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="pmc4", start_date=date.today() + timedelta(days=10),
        )
        occupancy = _occupancy_for_agreement(db_session, agreement_id)
        occupancy.status = "ACTIVE"
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/cancel-before-move-in",
            json={}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 409, r.text


class TestHostCancelsBeforeMoveIn:
    def test_host_can_cancel_their_own_property_booking(self, client, db_session: Session):
        agreement_id, _admin_cookies, _renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="pmc5", start_date=date.today() + timedelta(days=10),
        )
        occupancy = _occupancy_for_agreement(db_session, agreement_id)
        host = _make_host_user_for_listing(db_session, agreement_id, email="pmc5-host@test.com")
        _seed_system_admin(db_session)

        r = client.post(
            f"/api/users/hosting/occupancies/{occupancy.id}/cancel-before-move-in",
            json={"reason": "property needs urgent repairs"}, cookies=auth_user_cookie(host),
        )
        assert r.status_code == 200, r.text
        assert r.json()["occupancy"]["status"] == "CANCELLED"

    def test_a_host_cannot_cancel_a_different_propertys_booking(self, client, db_session: Session):
        agreement_id, _admin_cookies, _renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="pmc6", start_date=date.today() + timedelta(days=10),
        )
        occupancy = _occupancy_for_agreement(db_session, agreement_id)

        other_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(other_party)
        db_session.flush()
        intruder_host = _make_user(db_session, email="pmc6-intruder-host@test.com")
        intruder_host.party_id = other_party.id
        db_session.commit()

        r = client.post(
            f"/api/users/hosting/occupancies/{occupancy.id}/cancel-before-move-in",
            json={}, cookies=auth_user_cookie(intruder_host),
        )
        assert r.status_code == 403, r.text


class TestAdminCancelsBeforeMoveIn:
    def test_admin_can_cancel_any_booking(self, client, db_session: Session):
        agreement_id, admin_cookies, _renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="pmc7", start_date=date.today() + timedelta(days=10),
        )
        occupancy = _occupancy_for_agreement(db_session, agreement_id)

        r = client.post(
            f"/api/occupancy/{occupancy.id}/cancel-before-move-in",
            json={"reason": "duplicate booking"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["occupancy"]["status"] == "CANCELLED"
