"""ZR-ENG-CLR-004 Section 3.3: the /agreement-eligibility pre-check used to
say "eligible: true" for a listing whose jurisdiction has no approved
agreement profile, because check_agreement_eligibility never actually called
resolve_agreement_profile -- only the real POST .../agreement call did, so
the toast the admin saw right before that 409 was misleading. Confirms the
pre-check now reports the same fail-closed reason the real creation call
raises, using a real ORM Offer/OfferTerms (not the HTTP flow, which already
routes every listing through the England-only agreement-profile helper)."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.crud.eligibility import check_agreement_eligibility
from app.crud.party import get_or_create_default_party
from app.models.authority_record import AuthorityRecord
from app.models.guest import Guest
from app.models.leasing import Application, Offer, OfferTerms
from app.models.listing import Listing
from app.models.market_release import MarketRelease
from app.models.occupancy_classification import OccupancyClassification
from app.models.property import Property
from app.models.room import Room
from tests.conftest import _make_admin, auth_admin_cookie


def _accepted_offer_on_india_jurisdiction_listing(db: Session) -> tuple[Offer, object]:
    """India is real 'good standing' per jurisdiction_gates_pass (active
    market release, verified authority, approved classification) -- but
    services/agreement_profile.py's SUPPORTED_JURISDICTION is 'England' only,
    so this offer is exactly the case the pre-check used to miss."""
    admin = _make_admin(db, email="precheck-admin@test.com")
    owner_party = get_or_create_default_party(db, admin)
    assert owner_party.jurisdiction == "IN"

    prop = Property(owner_party_id=owner_party.id, address="1 Precheck St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()
    market_release = MarketRelease(jurisdiction="IN", status="active", min_stay_nights=30)
    db.add(market_release)
    db.add(AuthorityRecord(party_id=owner_party.id, room_id=room.id, authority_type="lease", status="verified"))
    db.add(OccupancyClassification(room_id=room.id, classification="long_term_residential", review_state="APPROVED"))
    db.flush()

    listing = Listing(
        id=f"L-PRECHECK-{room.id}", slug=f"precheck-{room.id}", name="Precheck Listing", room_type="Private room",
        city="Bengaluru", location="Koramangala", price_per_night=500, guests=1,
        rating=4.5, review_count=0, owner_id=admin.id, room_id=room.id, state="PUBLISHED",
        market_release_id=market_release.id,
    )
    db.add(listing)
    guest = Guest(
        id="G-PRECHECK", name="Precheck Renter", email="precheck-renter@test.com",
        phone="", avatar="", location="", joined_at=date.today(),
    )
    db.add(guest)
    db.flush()
    application = Application(listing_id=listing.id, guest_id=guest.id, status="DECIDED")
    db.add(application)
    db.flush()
    offer = Offer(application_id=application.id, listing_id=listing.id, guest_id=guest.id, status="ACCEPTED")
    db.add(offer)
    db.flush()
    db.add(OfferTerms(
        offer_id=offer.id, version=1, monthly_rent=500, deposit_amount=500,
        start_date=date.today() - timedelta(days=1), term_months=6,
    ))
    db.flush()
    db.refresh(offer)
    return offer, admin


class TestAgreementEligibilityPrecheckMatchesRealCreation:
    def test_jurisdiction_with_no_approved_profile_is_reported_as_ineligible(self, db_session: Session):
        offer, _admin = _accepted_offer_on_india_jurisdiction_listing(db_session)
        reasons = check_agreement_eligibility(db_session, offer)
        assert any("No approved agreement profile" in r for r in reasons), reasons

    def test_pre_check_endpoint_reports_ineligible_for_the_same_offer(self, client, db_session: Session):
        offer, admin = _accepted_offer_on_india_jurisdiction_listing(db_session)
        r = client.get(f"/api/leasing/offers/{offer.id}/agreement-eligibility", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["eligible"] is False
        assert any("No approved agreement profile" in reason for reason in body["reasons"])
