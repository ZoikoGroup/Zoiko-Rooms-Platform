"""Unit tests for app/crud/occupancy.py (previously 47% covered) -- confirm
move-in, the idempotent recurring-rent generator (this stack has no job
scheduler; see crud/occupancy.py's own docstring), ending an occupancy, and
the manual "rent due" substitute for a cron tick."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.crud import occupancy as crud
from app.crud.party import get_or_create_default_party
from app.models.authority_record import AuthorityRecord
from app.models.finance import Obligation
from app.models.guest import Guest
from app.models.leasing import Agreement, Application, Offer, OfferTerms
from app.models.listing import Listing
from app.models.market_release import MarketRelease
from app.models.occupancy import Occupancy
from app.models.occupancy_classification import OccupancyClassification
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from tests.conftest import _make_admin


def _make_signed_agreement(db: Session, *, admin, monthly_rent: float = 1000.0, term_months: int = 12):
    # assert_provider_access checks the admin's Membership in the *Property's*
    # owning party (party_id_for_listing), not Listing.owner_id -- so the admin
    # needs a real membership in whatever party owns this room's property.
    owner_party = get_or_create_default_party(db, admin)
    prop = Property(owner_party_id=owner_party.id, address="1 Occ St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()

    # check_move_in_eligibility (shared with listing publish eligibility) requires
    # all three of these to be in good standing, re-checked at move-in time since
    # any of them can lapse between signing and move-in.
    market_release = MarketRelease(jurisdiction=owner_party.jurisdiction, status="active", min_stay_nights=30)
    db.add(market_release)
    db.add(AuthorityRecord(
        party_id=owner_party.id, room_id=room.id, authority_type="lease", status="verified",
    ))
    db.add(OccupancyClassification(room_id=room.id, classification="long_term_residential", review_state="APPROVED"))
    db.flush()

    listing = Listing(
        id="L-OCCTEST", slug="occtest", name="Occupancy Test Listing", room_type="Private room",
        city="Bengaluru", location="Koramangala", price_per_night=500, guests=1,
        rating=4.5, review_count=0, owner_id=admin.id, room_id=room.id, state="PUBLISHED",
        market_release_id=market_release.id,
    )
    db.add(listing)
    db.flush()

    guest = Guest(id="G-OCCTEST", name="Renter", email="occtest-renter@test.com", joined_at=date.today())
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
    db.add(Obligation(
        obligation_type="RENT", money_plane="OCCUPANCY", amount=monthly_rent, currency="INR",
        due_date=date.today(), status="PAID", agreement_id=agreement.id,
    ))
    db.commit()
    return agreement, offer, listing, room, guest


class TestConfirmMoveIn:
    def test_creates_an_active_occupancy(self, db_session: Session):
        admin = _make_admin(db_session, email="occ-movein@test.com", role="admin")
        agreement, offer, listing, room, guest = _make_signed_agreement(db_session, admin=admin)

        occupancy = crud.confirm_move_in(db_session, agreement, admin)
        assert occupancy.status == "ACTIVE"
        assert occupancy.move_in_date == date.today()
        assert occupancy.guest_id == guest.id
        assert occupancy.expected_end_date == crud._add_months(date.today(), 12)

    def test_is_idempotent_for_the_same_offer(self, db_session: Session):
        admin = _make_admin(db_session, email="occ-movein-idem@test.com", role="admin")
        agreement, offer, listing, room, guest = _make_signed_agreement(db_session, admin=admin)

        first = crud.confirm_move_in(db_session, agreement, admin)
        second = crud.confirm_move_in(db_session, agreement, admin)
        assert first.id == second.id

    def test_requires_provider_access(self, db_session: Session):
        owner_admin = _make_admin(db_session, email="occ-movein-owner@test.com", role="admin")
        outsider_admin = _make_admin(db_session, email="occ-movein-outsider@test.com", role="admin")
        agreement, offer, listing, room, guest = _make_signed_agreement(db_session, admin=owner_admin)

        with pytest.raises(HTTPException) as exc:
            crud.confirm_move_in(db_session, agreement, outsider_admin)
        assert exc.value.status_code == 403

    def test_rejects_move_in_when_not_eligible(self, db_session: Session):
        """Re-checked at move-in even though the agreement is already signed --
        an authority record can expire (or be revoked) in the days between
        signing and move-in."""
        admin = _make_admin(db_session, email="occ-movein-ineligible@test.com", role="admin")
        agreement, offer, listing, room, guest = _make_signed_agreement(db_session, admin=admin)

        from app.models.authority_record import AuthorityRecord
        db_session.query(AuthorityRecord).filter(AuthorityRecord.room_id == room.id).update({"status": "expired"})
        db_session.commit()

        with pytest.raises(HTTPException) as exc:
            crud.confirm_move_in(db_session, agreement, admin)
        assert exc.value.status_code == 409
        assert "reasons" in exc.value.detail


class TestGetOccupancyOr404:
    def test_returns_the_occupancy(self, db_session: Session):
        admin = _make_admin(db_session, email="occ-get1@test.com", role="admin")
        agreement, offer, listing, room, guest = _make_signed_agreement(db_session, admin=admin)
        occupancy = crud.confirm_move_in(db_session, agreement, admin)

        found = crud.get_occupancy_or_404(db_session, occupancy.id)
        assert found.id == occupancy.id

    def test_unknown_id_is_404(self, db_session: Session):
        with pytest.raises(HTTPException) as exc:
            crud.get_occupancy_or_404(db_session, 999999)
        assert exc.value.status_code == 404


class TestListOccupanciesFor:
    def test_plain_admin_sees_only_their_own_listings_occupancies(self, db_session: Session):
        admin_a = _make_admin(db_session, email="occ-list-a@test.com", role="admin")
        admin_b = _make_admin(db_session, email="occ-list-b@test.com", role="admin")
        agreement_a, *_ = _make_signed_agreement(db_session, admin=admin_a)
        occupancy_a = crud.confirm_move_in(db_session, agreement_a, admin_a)

        results_a = crud.list_occupancies_for(db_session, admin_a)
        assert [o.id for o in results_a] == [occupancy_a.id]

        results_b = crud.list_occupancies_for(db_session, admin_b)
        assert results_b == []

    def test_super_admin_sees_every_occupancy(self, db_session: Session):
        admin_a = _make_admin(db_session, email="occ-list-a2@test.com", role="admin")
        super_admin = _make_admin(db_session, email="occ-list-super@test.com", role="super_admin")
        agreement_a, *_ = _make_signed_agreement(db_session, admin=admin_a)
        occupancy_a = crud.confirm_move_in(db_session, agreement_a, admin_a)

        results = crud.list_occupancies_for(db_session, super_admin)
        assert any(o.id == occupancy_a.id for o in results)


class TestGenerateNextRentObligation:
    def test_generates_the_following_months_obligation(self, db_session: Session):
        admin = _make_admin(db_session, email="occ-rent1@test.com", role="admin")
        agreement, offer, listing, room, guest = _make_signed_agreement(db_session, admin=admin, monthly_rent=1500.0)
        occupancy = crud.confirm_move_in(db_session, agreement, admin)

        obligation = crud.generate_next_rent_obligation(db_session, occupancy, admin)
        assert obligation is not None
        assert obligation.amount == 1500.0
        assert obligation.occupancy_id == occupancy.id

        first_due = agreement.obligations[0].due_date
        assert obligation.due_date == crud._add_months(first_due, 1)

    def test_calling_it_repeatedly_advances_one_period_at_a_time(self, db_session: Session):
        admin = _make_admin(db_session, email="occ-rent2b@test.com", role="admin")
        agreement, offer, listing, room, guest = _make_signed_agreement(db_session, admin=admin, term_months=6)
        occupancy = crud.confirm_move_in(db_session, agreement, admin)
        first_due = agreement.obligations[0].due_date

        first = crud.generate_next_rent_obligation(db_session, occupancy, admin)
        second = crud.generate_next_rent_obligation(db_session, occupancy, admin)
        assert first.due_date == crud._add_months(first_due, 1)
        assert second.due_date == crud._add_months(first_due, 2)

    def test_refuses_to_run_past_the_leases_expected_end_date(self, db_session: Session):
        admin = _make_admin(db_session, email="occ-rent3@test.com", role="admin")
        # A 1-month term means expected_end_date == the first period's next due
        # date exactly -- that final period is still allowed (only *exceeding*
        # the end date is refused), so the boundary is the call after that one.
        agreement, offer, listing, room, guest = _make_signed_agreement(db_session, admin=admin, term_months=1)
        occupancy = crud.confirm_move_in(db_session, agreement, admin)

        final_period = crud.generate_next_rent_obligation(db_session, occupancy, admin)
        assert final_period is not None
        assert final_period.due_date == occupancy.expected_end_date

        beyond_lease_end = crud.generate_next_rent_obligation(db_session, occupancy, admin)
        assert beyond_lease_end is None

    def test_rejects_generation_for_a_non_active_occupancy(self, db_session: Session):
        admin = _make_admin(db_session, email="occ-rent4@test.com", role="admin")
        agreement, offer, listing, room, guest = _make_signed_agreement(db_session, admin=admin)
        occupancy = crud.confirm_move_in(db_session, agreement, admin)
        occupancy.status = "ENDED"
        db_session.commit()

        with pytest.raises(HTTPException) as exc:
            crud.generate_next_rent_obligation(db_session, occupancy, admin)
        assert exc.value.status_code == 409

    def test_requires_provider_access(self, db_session: Session):
        owner_admin = _make_admin(db_session, email="occ-rent-owner@test.com", role="admin")
        outsider_admin = _make_admin(db_session, email="occ-rent-outsider@test.com", role="admin")
        agreement, offer, listing, room, guest = _make_signed_agreement(db_session, admin=owner_admin)
        occupancy = crud.confirm_move_in(db_session, agreement, owner_admin)

        with pytest.raises(HTTPException) as exc:
            crud.generate_next_rent_obligation(db_session, occupancy, outsider_admin)
        assert exc.value.status_code == 403


class TestEndOccupancy:
    def test_ends_an_active_occupancy(self, db_session: Session):
        admin = _make_admin(db_session, email="occ-end1@test.com", role="admin")
        agreement, offer, listing, room, guest = _make_signed_agreement(db_session, admin=admin)
        occupancy = crud.confirm_move_in(db_session, agreement, admin)

        updated = crud.end_occupancy(db_session, occupancy, admin)
        assert updated.status == "ENDED"
        assert updated.move_out_date == date.today()
        assert updated.ended_at is not None

    def test_requires_provider_access(self, db_session: Session):
        owner_admin = _make_admin(db_session, email="occ-end-owner@test.com", role="admin")
        outsider_admin = _make_admin(db_session, email="occ-end-outsider@test.com", role="admin")
        agreement, offer, listing, room, guest = _make_signed_agreement(db_session, admin=owner_admin)
        occupancy = crud.confirm_move_in(db_session, agreement, owner_admin)

        with pytest.raises(HTTPException) as exc:
            crud.end_occupancy(db_session, occupancy, outsider_admin)
        assert exc.value.status_code == 403


class TestListOccupanciesMissingUpcomingRent:
    def test_flags_an_active_occupancy_with_no_upcoming_pending_rent(self, db_session: Session):
        admin = _make_admin(db_session, email="occ-missing1@test.com", role="admin")
        agreement, offer, listing, room, guest = _make_signed_agreement(db_session, admin=admin)
        occupancy = crud.confirm_move_in(db_session, agreement, admin)
        # No next rent obligation generated yet -- this occupancy is missing one.

        missing = crud.list_occupancies_missing_upcoming_rent(db_session, admin)
        assert any(o.id == occupancy.id for o in missing)

    def test_does_not_flag_an_occupancy_that_already_has_a_pending_obligation(self, db_session: Session):
        admin = _make_admin(db_session, email="occ-missing2@test.com", role="admin")
        agreement, offer, listing, room, guest = _make_signed_agreement(db_session, admin=admin)
        occupancy = crud.confirm_move_in(db_session, agreement, admin)
        crud.generate_next_rent_obligation(db_session, occupancy, admin)

        missing = crud.list_occupancies_missing_upcoming_rent(db_session, admin)
        assert not any(o.id == occupancy.id for o in missing)
