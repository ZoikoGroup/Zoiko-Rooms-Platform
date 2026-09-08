"""Coverage for the renter payment self-service gap found during a full
customer-journey audit: previously /api/users/payments was read-only history
only, with no way for a renter to see what they owe or pay it themselves --
every payment required an admin to record it on the renter's behalf."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.finance import OBLIGATION_TYPE_TO_PLANE, Obligation
from app.models.guest import Guest
from app.models.leasing import Agreement, Application, Offer, OfferTerms
from app.models.listing import Listing
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from tests.conftest import _make_user, auth_user_cookie


def _make_renter_with_agreement_obligations(db: Session):
    """A renter with a signed agreement carrying an unpaid RENT and DEPOSIT
    obligation (agreement-linked, exactly like create_agreement produces).
    Returns (renter_user, guest, rent_obligation, deposit_obligation)."""
    owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(owner_party)
    db.flush()
    prop = Property(owner_party_id=owner_party.id, address="1 Pay St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()

    renter_user = _make_user(db, email="payer@test.com")
    guest = Guest(id="G-PAYER", name="Payer", email="payer@test.com", joined_at=date.today(), user_account_id=renter_user.id)
    db.add(guest)
    db.flush()

    listing = Listing(
        id="L-PAYTEST", slug="paytest", name="Pay Test Listing", room_type="Private room",
        city="Bengaluru", location="Koramangala", price_per_night=500, guests=1,
        rating=0.0, review_count=0, party_id=owner_party.id, owner_id=None, room_id=room.id,
        state="PUBLISHED",
    )
    db.add(listing)
    db.flush()

    application = Application(listing_id=listing.id, guest_id=guest.id, status="DECIDED")
    db.add(application)
    db.flush()
    offer = Offer(application_id=application.id, listing_id=listing.id, guest_id=guest.id, status="ACCEPTED")
    db.add(offer)
    db.flush()
    db.add(OfferTerms(
        offer_id=offer.id, version=1, monthly_rent=15000, deposit_amount=30000,
        start_date=date.today(), term_months=11,
    ))
    agreement = Agreement(offer_id=offer.id, status="SIGNED")
    db.add(agreement)
    db.flush()

    rent = Obligation(
        obligation_type="RENT", money_plane=OBLIGATION_TYPE_TO_PLANE["RENT"], amount=15000,
        due_date=date.today() + timedelta(days=5), agreement_id=agreement.id,
    )
    deposit = Obligation(
        obligation_type="DEPOSIT", money_plane=OBLIGATION_TYPE_TO_PLANE["DEPOSIT"], amount=30000,
        due_date=date.today() + timedelta(days=5), agreement_id=agreement.id,
    )
    db.add_all([rent, deposit])
    db.commit()
    return renter_user, guest, rent, deposit


class TestListObligations:
    def test_renter_sees_their_own_obligations(self, client, db_session: Session):
        renter, _guest, rent, deposit = _make_renter_with_agreement_obligations(db_session)

        resp = client.get("/api/users/payments/obligations", cookies=auth_user_cookie(renter))
        assert resp.status_code == 200
        ids = {o["id"] for o in resp.json()}
        assert {rent.id, deposit.id}.issubset(ids)
        amounts = {o["id"]: o["amount"] for o in resp.json()}
        assert amounts[rent.id] == 15000.0
        assert amounts[deposit.id] == 30000.0

    def test_renter_with_no_guest_record_sees_empty_list(self, client, db_session: Session):
        renter = _make_user(db_session, email="no-guest@test.com")
        db_session.commit()

        resp = client.get("/api/users/payments/obligations", cookies=auth_user_cookie(renter))
        assert resp.status_code == 200
        assert resp.json() == []


class TestPayObligation:
    def test_renter_can_pay_their_own_rent(self, client, db_session: Session):
        renter, _guest, rent, _deposit = _make_renter_with_agreement_obligations(db_session)

        resp = client.post(f"/api/users/payments/obligations/{rent.id}/pay", cookies=auth_user_cookie(renter))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "SUCCEEDED"
        assert body["amount"] == 15000.0

        obligations = client.get("/api/users/payments/obligations", cookies=auth_user_cookie(renter)).json()
        rent_after = next(o for o in obligations if o["id"] == rent.id)
        assert rent_after["status"] == "PAID"

    def test_paying_twice_is_idempotent(self, client, db_session: Session):
        renter, _guest, rent, _deposit = _make_renter_with_agreement_obligations(db_session)

        first = client.post(f"/api/users/payments/obligations/{rent.id}/pay", cookies=auth_user_cookie(renter))
        assert first.status_code == 200

        second = client.post(f"/api/users/payments/obligations/{rent.id}/pay", cookies=auth_user_cookie(renter))
        assert second.status_code == 409

    def test_deposit_payment_creates_deposit_record(self, client, db_session: Session):
        renter, _guest, _rent, deposit = _make_renter_with_agreement_obligations(db_session)

        resp = client.post(f"/api/users/payments/obligations/{deposit.id}/pay", cookies=auth_user_cookie(renter))
        assert resp.status_code == 200

        db_session.refresh(deposit)
        assert deposit.deposit_record is not None
        assert float(deposit.deposit_record.held_amount) == 30000.0

    def test_cannot_pay_someone_elses_obligation(self, client, db_session: Session):
        _renter, _guest, rent, _deposit = _make_renter_with_agreement_obligations(db_session)
        stranger = _make_user(db_session, email="stranger-payer@test.com")
        stranger_guest = Guest(id="G-STRANGER", name="Stranger", email="stranger-payer@test.com", joined_at=date.today(), user_account_id=stranger.id)
        db_session.add(stranger_guest)
        db_session.commit()

        resp = client.post(f"/api/users/payments/obligations/{rent.id}/pay", cookies=auth_user_cookie(stranger))
        assert resp.status_code == 403

    def test_missing_obligation_is_404(self, client, db_session: Session):
        renter = _make_user(db_session, email="payer2@test.com")
        guest = Guest(id="G-PAYER2", name="Payer2", email="payer2@test.com", joined_at=date.today(), user_account_id=renter.id)
        db_session.add(guest)
        db_session.commit()

        resp = client.post("/api/users/payments/obligations/999999/pay", cookies=auth_user_cookie(renter))
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        resp = client.get("/api/users/payments/obligations")
        assert resp.status_code == 401
