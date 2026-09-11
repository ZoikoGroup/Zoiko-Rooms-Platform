"""ZR-ENG-CLR-005 AC-02/AC-07: crud/leasing.py::create_agreement creates a
versioned PaymentSchedule alongside the agreement's first RENT obligation,
and crud/occupancy.py::generate_next_rent_obligation sources subsequent
obligations from that same schedule (falling back to the pre-existing
"last obligation's amount" logic when no schedule exists, e.g. an agreement
built outside create_agreement -- see test_occupancy_crud.py's own bare-
Agreement fixtures, which exercise exactly that fallback path unmodified)."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import occupancy as occupancy_crud
from app.crud.party import get_or_create_default_party
from app.models.authority_record import AuthorityRecord
from app.models.finance import Obligation, PaymentSchedule
from app.models.guest import Guest
from app.models.leasing import Agreement, Application, Offer, OfferTerms
from app.models.listing import Listing
from app.models.market_release import MarketRelease
from app.models.occupancy_classification import OccupancyClassification
from app.models.property import Property
from app.models.room import Room
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_application_workflow import _make_verified_renter_with_published_listing
from tests.test_deposit_claims import _make_agreement_eligible


class TestCreateAgreementCreatesAPaymentSchedule:
    def test_agreement_creation_creates_a_linked_schedule(self, client, db_session: Session):
        user, listing_id = _make_verified_renter_with_published_listing(db_session, email="schedule-renter@test.com")
        user_cookies = auth_user_cookie(user)
        admin = _make_admin(db_session, email="schedule-admin@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/users/rentals/applications",
            json={"listingId": listing_id, "message": "hi", "desiredMoveIn": None},
            cookies=user_cookies,
        )
        assert r.status_code == 201, r.text
        application_id = r.json()["id"]
        assert client.post(
            f"/api/leasing/applications/{application_id}/decide", json={"decision": "APPROVED"}, cookies=admin_cookies,
        ).status_code == 200
        r = client.post(f"/api/leasing/applications/{application_id}/offers", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        offer_id = r.json()["id"]

        r = client.post(
            f"/api/leasing/offers/{offer_id}/terms",
            json={
                "monthlyRent": 3000.0, "depositAmount": 3000.0,
                "startDate": (date.today() + timedelta(days=5)).isoformat(), "termMonths": 6,
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert client.post(f"/api/leasing/offers/{offer_id}/send", cookies=admin_cookies).status_code == 200
        assert client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=user_cookies).status_code == 200

        _make_agreement_eligible(db_session, listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        agreement_id = r.json()["id"]

        schedule = db_session.scalar(select(PaymentSchedule).where(PaymentSchedule.agreement_id == agreement_id))
        assert schedule is not None
        assert float(schedule.amount) == 3000.0
        assert schedule.first_due == date.today() + timedelta(days=5)
        assert schedule.cadence == "MONTHLY"
        assert schedule.status == "ACTIVE"

        rent_obligation = db_session.scalar(
            select(Obligation).where(Obligation.agreement_id == agreement_id, Obligation.obligation_type == "RENT")
        )
        deposit_obligation = db_session.scalar(
            select(Obligation).where(Obligation.agreement_id == agreement_id, Obligation.obligation_type == "DEPOSIT")
        )
        assert rent_obligation.schedule_id == schedule.id
        assert deposit_obligation.schedule_id is None  # only the recurring RENT series is scheduled


def _make_signed_agreement_with_schedule(db: Session, *, admin, monthly_rent: float = 1000.0, term_months: int = 12):
    """Same shape as test_occupancy_crud.py's _make_signed_agreement, but also
    creates and links a PaymentSchedule -- proving generate_next_rent_obligation
    actually reads from it, rather than only ever exercising the no-schedule
    fallback path the other file's bare fixtures hit."""
    owner_party = get_or_create_default_party(db, admin)
    prop = Property(owner_party_id=owner_party.id, address="1 Schedule St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()

    # check_move_in_eligibility (shared with listing publish eligibility)
    # requires all three of these in good standing -- mirrors
    # test_occupancy_crud.py:_make_signed_agreement exactly.
    market_release = MarketRelease(jurisdiction=owner_party.jurisdiction, status="active", min_stay_nights=30)
    db.add(market_release)
    db.add(AuthorityRecord(party_id=owner_party.id, room_id=room.id, authority_type="lease", status="verified"))
    db.add(OccupancyClassification(room_id=room.id, classification="long_term_residential", review_state="APPROVED"))
    db.flush()

    listing = Listing(
        id="L-SCHEDTEST", slug="schedtest", name="Schedule Test Listing", room_type="Private room",
        city="Bengaluru", location="Koramangala", price_per_night=500, guests=1,
        rating=4.5, review_count=0, party_id=owner_party.id, owner_id=None, room_id=room.id, state="PUBLISHED",
        market_release_id=market_release.id,
    )
    db.add(listing)
    db.flush()

    guest = Guest(id="G-SCHEDTEST", name="Renter", email="schedtest-renter@test.com", joined_at=date.today())
    db.add(guest)
    db.flush()

    application = Application(listing_id=listing.id, guest_id=guest.id, status="DECIDED")
    db.add(application)
    db.flush()
    offer = Offer(application_id=application.id, listing_id=listing.id, guest_id=guest.id, status="ACCEPTED")
    db.add(offer)
    db.flush()
    db.add(OfferTerms(
        offer_id=offer.id, version=1, monthly_rent=monthly_rent, deposit_amount=monthly_rent,
        start_date=date.today(), term_months=term_months,
    ))
    db.flush()
    agreement = Agreement(offer_id=offer.id, status="SIGNED")
    db.add(agreement)
    db.flush()

    schedule = PaymentSchedule(agreement_id=agreement.id, amount=monthly_rent, first_due=date.today(), anchor_day=date.today().day)
    db.add(schedule)
    db.flush()

    db.add(Obligation(
        obligation_type="RENT", money_plane="OCCUPANCY", amount=monthly_rent, currency="INR",
        due_date=date.today(), status="PAID", agreement_id=agreement.id, schedule_id=schedule.id,
    ))
    db.commit()
    return agreement, offer, listing, room, guest, schedule


class TestGenerateNextRentObligationUsesSchedule:
    def test_next_obligation_is_linked_to_and_sourced_from_the_schedule(self, db_session: Session):
        admin = _make_admin(db_session, email="sched-rent-admin@test.com", role="admin")
        agreement, offer, listing, room, guest, schedule = _make_signed_agreement_with_schedule(
            db_session, admin=admin, monthly_rent=1750.0,
        )
        occupancy = occupancy_crud.confirm_move_in(db_session, agreement, admin)

        obligation = occupancy_crud.generate_next_rent_obligation(db_session, occupancy, admin)
        assert obligation is not None
        assert obligation.schedule_id == schedule.id
        assert float(obligation.amount) == 1750.0
