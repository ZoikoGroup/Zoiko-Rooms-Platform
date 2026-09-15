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
from app.models.occupancy import Occupancy
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


def _make_signed_agreement_with_schedule(
    db: Session, *, admin, monthly_rent: float = 1000.0, term_months: int = 12, cadence: str = "MONTHLY",
    custom_interval_days: int | None = None, listing_id: str = "L-SCHEDTEST", guest_id: str = "G-SCHEDTEST",
):
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
        id=listing_id, slug=listing_id.lower(), name="Schedule Test Listing", room_type="Private room",
        city="Bengaluru", location="Koramangala", price_per_night=500, guests=1,
        rating=4.5, review_count=0, party_id=owner_party.id, owner_id=None, room_id=room.id, state="PUBLISHED",
        market_release_id=market_release.id,
    )
    db.add(listing)
    db.flush()

    guest = Guest(id=guest_id, name="Renter", email=f"{guest_id.lower()}@test.com", joined_at=date.today())
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
        start_date=date.today(), term_months=term_months, cadence=cadence, custom_interval_days=custom_interval_days,
    ))
    db.flush()
    agreement = Agreement(offer_id=offer.id, status="SIGNED")
    db.add(agreement)
    db.flush()

    schedule = PaymentSchedule(
        agreement_id=agreement.id, cadence=cadence, custom_interval_days=custom_interval_days,
        amount=monthly_rent, first_due=date.today(), anchor_day=date.today().day,
    )
    db.add(schedule)
    db.flush()

    db.add(Obligation(
        obligation_type="RENT", money_plane="OCCUPANCY", amount=monthly_rent, currency="INR",
        due_date=date.today(), status="PAID", agreement_id=agreement.id, schedule_id=schedule.id,
    ))
    # dev's occupancy-lifecycle change: the occupancy row is now created at
    # PARTIALLY_EXECUTED (PENDING_MOVE_IN), not by confirm_move_in -- mirrors
    # test_occupancy_crud.py:_make_signed_agreement's own fix.
    db.add(Occupancy(
        offer_id=offer.id, listing_id=listing.id, room_id=room.id, guest_id=guest.id,
        status="PENDING_MOVE_IN", expected_end_date=occupancy_crud._add_months(date.today(), term_months),
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


class TestMultiCadenceSchedules:
    """ZR-ENG-CLR-005 AC-06: a schedule's cadence is no longer implicitly
    MONTHLY -- WEEKLY/FORTNIGHTLY are real, selectable options that drive
    generate_next_rent_obligation's own due-date math (crud/occupancy.py:
    _next_due_date), not just a stored label."""

    def test_weekly_cadence_advances_the_next_due_date_by_seven_days(self, db_session: Session):
        admin = _make_admin(db_session, email="sched-weekly-admin@test.com", role="admin")
        agreement, offer, listing, room, guest, schedule = _make_signed_agreement_with_schedule(
            db_session, admin=admin, monthly_rent=500.0, cadence="WEEKLY",
            listing_id="L-SCHEDWEEKLY", guest_id="G-SCHEDWEEKLY",
        )
        assert schedule.cadence == "WEEKLY"
        occupancy = occupancy_crud.confirm_move_in(db_session, agreement, admin)

        obligation = occupancy_crud.generate_next_rent_obligation(db_session, occupancy, admin)
        assert obligation is not None
        assert obligation.due_date == date.today() + timedelta(days=7)
        assert float(obligation.amount) == 500.0

    def test_fortnightly_cadence_advances_the_next_due_date_by_fourteen_days(self, db_session: Session):
        admin = _make_admin(db_session, email="sched-fortnightly-admin@test.com", role="admin")
        agreement, offer, listing, room, guest, schedule = _make_signed_agreement_with_schedule(
            db_session, admin=admin, monthly_rent=800.0, cadence="FORTNIGHTLY",
            listing_id="L-SCHEDFORTNIGHT", guest_id="G-SCHEDFORTNIGHT",
        )
        assert schedule.cadence == "FORTNIGHTLY"
        occupancy = occupancy_crud.confirm_move_in(db_session, agreement, admin)

        obligation = occupancy_crud.generate_next_rent_obligation(db_session, occupancy, admin)
        assert obligation is not None
        assert obligation.due_date == date.today() + timedelta(days=14)
        assert float(obligation.amount) == 800.0

    def test_add_offer_terms_rejects_an_unsupported_cadence(self, client, db_session: Session):
        user, listing_id = _make_verified_renter_with_published_listing(db_session, email="cadence-reject-renter@test.com")
        user_cookies = auth_user_cookie(user)
        admin = _make_admin(db_session, email="cadence-reject-admin@test.com", role="super_admin")
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
                "cadence": "DAILY",
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text

    def test_create_agreement_carries_the_terms_cadence_into_the_schedule(self, client, db_session: Session):
        user, listing_id = _make_verified_renter_with_published_listing(db_session, email="cadence-carry-renter@test.com")
        user_cookies = auth_user_cookie(user)
        admin = _make_admin(db_session, email="cadence-carry-admin@test.com", role="super_admin")
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
                "cadence": "FORTNIGHTLY",
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["cadence"] == "FORTNIGHTLY"
        assert client.post(f"/api/leasing/offers/{offer_id}/send", cookies=admin_cookies).status_code == 200
        assert client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=user_cookies).status_code == 200

        _make_agreement_eligible(db_session, listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        agreement_id = r.json()["id"]

        schedule = db_session.scalar(select(PaymentSchedule).where(PaymentSchedule.agreement_id == agreement_id))
        assert schedule.cadence == "FORTNIGHTLY"

    def test_custom_cadence_requires_a_positive_custom_interval_days(self, client, db_session: Session):
        user, listing_id = _make_verified_renter_with_published_listing(db_session, email="custom-missing-renter@test.com")
        user_cookies = auth_user_cookie(user)
        admin = _make_admin(db_session, email="custom-missing-admin@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/users/rentals/applications",
            json={"listingId": listing_id, "message": "hi", "desiredMoveIn": None},
            cookies=user_cookies,
        )
        application_id = r.json()["id"]
        assert client.post(
            f"/api/leasing/applications/{application_id}/decide", json={"decision": "APPROVED"}, cookies=admin_cookies,
        ).status_code == 200
        r = client.post(f"/api/leasing/applications/{application_id}/offers", cookies=admin_cookies)
        offer_id = r.json()["id"]

        r = client.post(
            f"/api/leasing/offers/{offer_id}/terms",
            json={
                "monthlyRent": 3000.0, "depositAmount": 3000.0,
                "startDate": (date.today() + timedelta(days=5)).isoformat(), "termMonths": 6,
                "cadence": "CUSTOM",
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text

    def test_custom_interval_days_rejected_for_a_non_custom_cadence(self, client, db_session: Session):
        user, listing_id = _make_verified_renter_with_published_listing(db_session, email="custom-extra-renter@test.com")
        user_cookies = auth_user_cookie(user)
        admin = _make_admin(db_session, email="custom-extra-admin@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/users/rentals/applications",
            json={"listingId": listing_id, "message": "hi", "desiredMoveIn": None},
            cookies=user_cookies,
        )
        application_id = r.json()["id"]
        assert client.post(
            f"/api/leasing/applications/{application_id}/decide", json={"decision": "APPROVED"}, cookies=admin_cookies,
        ).status_code == 200
        r = client.post(f"/api/leasing/applications/{application_id}/offers", cookies=admin_cookies)
        offer_id = r.json()["id"]

        r = client.post(
            f"/api/leasing/offers/{offer_id}/terms",
            json={
                "monthlyRent": 3000.0, "depositAmount": 3000.0,
                "startDate": (date.today() + timedelta(days=5)).isoformat(), "termMonths": 6,
                "cadence": "MONTHLY", "customIntervalDays": 10,
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text

    def test_custom_cadence_advances_the_next_due_date_by_the_configured_interval(self, db_session: Session):
        admin = _make_admin(db_session, email="sched-custom-admin@test.com", role="admin")
        agreement, offer, listing, room, guest, schedule = _make_signed_agreement_with_schedule(
            db_session, admin=admin, monthly_rent=650.0, cadence="CUSTOM", custom_interval_days=10,
            listing_id="L-SCHEDCUSTOM", guest_id="G-SCHEDCUSTOM",
        )
        assert schedule.cadence == "CUSTOM"
        assert schedule.custom_interval_days == 10
        occupancy = occupancy_crud.confirm_move_in(db_session, agreement, admin)

        obligation = occupancy_crud.generate_next_rent_obligation(db_session, occupancy, admin)
        assert obligation is not None
        assert obligation.due_date == date.today() + timedelta(days=10)
        assert float(obligation.amount) == 650.0

    def test_upfront_cadence_never_generates_a_recurring_obligation(self, db_session: Session):
        admin = _make_admin(db_session, email="sched-upfront-admin@test.com", role="admin")
        agreement, offer, listing, room, guest, schedule = _make_signed_agreement_with_schedule(
            db_session, admin=admin, monthly_rent=6000.0, cadence="UPFRONT",
            listing_id="L-SCHEDUPFRONT", guest_id="G-SCHEDUPFRONT",
        )
        assert schedule.cadence == "UPFRONT"
        occupancy = occupancy_crud.confirm_move_in(db_session, agreement, admin)

        obligation = occupancy_crud.generate_next_rent_obligation(db_session, occupancy, admin)
        assert obligation is None

    def test_create_agreement_bills_the_entire_term_upfront_as_one_obligation(self, client, db_session: Session):
        user, listing_id = _make_verified_renter_with_published_listing(db_session, email="upfront-renter@test.com")
        user_cookies = auth_user_cookie(user)
        admin = _make_admin(db_session, email="upfront-admin@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/users/rentals/applications",
            json={"listingId": listing_id, "message": "hi", "desiredMoveIn": None},
            cookies=user_cookies,
        )
        application_id = r.json()["id"]
        assert client.post(
            f"/api/leasing/applications/{application_id}/decide", json={"decision": "APPROVED"}, cookies=admin_cookies,
        ).status_code == 200
        r = client.post(f"/api/leasing/applications/{application_id}/offers", cookies=admin_cookies)
        offer_id = r.json()["id"]

        r = client.post(
            f"/api/leasing/offers/{offer_id}/terms",
            json={
                "monthlyRent": 1000.0, "depositAmount": 1000.0,
                "startDate": (date.today() + timedelta(days=5)).isoformat(), "termMonths": 6,
                "cadence": "UPFRONT",
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
        assert schedule.cadence == "UPFRONT"
        assert float(schedule.amount) == 6000.0

        rent_obligation = db_session.scalar(
            select(Obligation).where(Obligation.agreement_id == agreement_id, Obligation.obligation_type == "RENT")
        )
        assert float(rent_obligation.amount) == 6000.0
