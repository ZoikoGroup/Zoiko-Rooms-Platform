"""ZR-ENG-CLR-001 Section 1, Rule 1 / Section 11.3: the Eligibility/Policy
Service (app/services/eligibility.py) is now the single place that computes
PUBLICATION_ELIGIBLE(listing_version) and JURISDICTION_GATES_PASS(listing,
request) -- previously duplicated independently in crud/listing.py and
crud/eligibility.py. These tests exercise the consolidated functions directly,
plus confirm both call sites (crud/listing.py and crud/eligibility.py) now
delegate to them rather than re-deriving the same checks.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.crud import eligibility as leasing_eligibility
from app.crud import listing as listing_crud
from app.crud.party import get_or_create_default_party
from app.models.authority_record import AuthorityRecord
from app.models.identity_verification import IdentityVerification
from app.models.listing import Listing
from app.models.market_release import MarketRelease
from app.models.occupancy_classification import OccupancyClassification
from app.models.property import Property
from app.models.room import Room
from app.services.eligibility import jurisdiction_gates_pass, listing_publication_eligible
from tests.conftest import _make_admin


def _make_room_with_good_standing(db: Session, admin) -> tuple[Room, MarketRelease]:
    owner_party = get_or_create_default_party(db, admin)
    prop = Property(owner_party_id=owner_party.id, address="1 Elig St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()

    market_release = MarketRelease(jurisdiction=owner_party.jurisdiction, status="active", min_stay_nights=30)
    db.add(market_release)
    db.add(AuthorityRecord(party_id=owner_party.id, room_id=room.id, authority_type="lease", status="verified"))
    db.add(OccupancyClassification(room_id=room.id, classification="long_term_residential", review_state="APPROVED"))
    # check_publish_eligibility (unlike jurisdiction_gates_pass/check_marketplace_standing)
    # also requires the provider's identity verification -- a check unique to the
    # Listing Service's admin-review screen, not part of the shared gate.
    db.add(IdentityVerification(party_id=owner_party.id, document_type="passport", status="verified"))
    db.flush()
    return room, market_release


def _make_listing(db: Session, admin, room: Room, market_release: MarketRelease, *, state: str = "PUBLISHED") -> Listing:
    listing = Listing(
        id=f"L-ELIG-{room.id}", slug=f"elig-{room.id}", name="Eligibility Test Listing", room_type="Private room",
        city="Bengaluru", location="Koramangala", price_per_night=500, guests=1,
        rating=4.5, review_count=0, owner_id=admin.id, room_id=room.id, state=state,
        market_release_id=market_release.id,
    )
    db.add(listing)
    db.flush()
    return listing


class TestListingPublicationEligible:
    def test_published_listing_has_no_reasons(self, db_session: Session):
        admin = _make_admin(db_session)
        room, market_release = _make_room_with_good_standing(db_session, admin)
        listing = _make_listing(db_session, admin, room, market_release, state="PUBLISHED")
        assert listing_publication_eligible(listing) == []

    def test_draft_listing_is_blocked(self, db_session: Session):
        admin = _make_admin(db_session)
        room, market_release = _make_room_with_good_standing(db_session, admin)
        listing = _make_listing(db_session, admin, room, market_release, state="DRAFT")
        reasons = listing_publication_eligible(listing)
        assert "not currently published" in reasons[0]

    def test_paused_listing_is_blocked(self, db_session: Session):
        admin = _make_admin(db_session)
        room, market_release = _make_room_with_good_standing(db_session, admin)
        listing = _make_listing(db_session, admin, room, market_release, state="PAUSED")
        assert listing_publication_eligible(listing) != []


class TestJurisdictionGatesPass:
    def test_good_standing_room_has_no_reasons(self, db_session: Session):
        admin = _make_admin(db_session)
        room, market_release = _make_room_with_good_standing(db_session, admin)
        assert jurisdiction_gates_pass(db_session, room, market_release) == []

    def test_no_market_release_is_blocked(self, db_session: Session):
        admin = _make_admin(db_session)
        room, _market_release = _make_room_with_good_standing(db_session, admin)
        reasons = jurisdiction_gates_pass(db_session, room, None)
        assert any("market release" in r for r in reasons)

    def test_inactive_market_release_is_blocked(self, db_session: Session):
        admin = _make_admin(db_session)
        room, market_release = _make_room_with_good_standing(db_session, admin)
        market_release.status = "closed"
        db_session.flush()
        reasons = jurisdiction_gates_pass(db_session, room, market_release)
        assert any("market release" in r for r in reasons)

    def test_no_authority_record_is_blocked(self, db_session: Session):
        admin = _make_admin(db_session)
        owner_party = get_or_create_default_party(db_session, admin)
        prop = Property(owner_party_id=owner_party.id, address="2 Elig St", city="Bengaluru", status="active")
        db_session.add(prop)
        db_session.flush()
        room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
        db_session.add(room)
        db_session.flush()
        market_release = MarketRelease(jurisdiction=owner_party.jurisdiction, status="active", min_stay_nights=30)
        db_session.add(market_release)
        db_session.add(OccupancyClassification(room_id=room.id, classification="long_term_residential", review_state="APPROVED"))
        db_session.flush()

        reasons = jurisdiction_gates_pass(db_session, room, market_release)
        assert any("authority record" in r for r in reasons)

    def test_unresolved_classification_is_blocked(self, db_session: Session):
        admin = _make_admin(db_session)
        room, market_release = _make_room_with_good_standing(db_session, admin)
        classification = db_session.query(OccupancyClassification).filter_by(room_id=room.id).one()
        classification.review_state = "UNKNOWN"
        db_session.flush()
        reasons = jurisdiction_gates_pass(db_session, room, market_release)
        assert any("classification" in r for r in reasons)


class TestCallSitesDelegateToTheSharedService:
    """crud/listing.py (Listing Service) and crud/eligibility.py (leasing
    pipeline) must agree exactly -- they now compute the same checks through
    the same functions rather than two independently maintained copies."""

    def test_listing_and_leasing_gates_agree_on_a_healthy_room(self, db_session: Session):
        admin = _make_admin(db_session)
        room, market_release = _make_room_with_good_standing(db_session, admin)
        listing = _make_listing(db_session, admin, room, market_release, state="PUBLISHED")

        assert listing_crud.check_publish_eligibility(db_session, listing) == []
        assert leasing_eligibility.check_marketplace_standing(db_session, room, market_release) == []
        assert listing_crud.is_listing_available(db_session, listing) is True

    def test_listing_and_leasing_gates_agree_on_an_expired_authority(self, db_session: Session):
        admin = _make_admin(db_session)
        room, market_release = _make_room_with_good_standing(db_session, admin)
        listing = _make_listing(db_session, admin, room, market_release, state="PUBLISHED")

        db_session.query(AuthorityRecord).filter(AuthorityRecord.room_id == room.id).update({"status": "expired"})
        db_session.flush()

        listing_reasons = listing_crud.check_publish_eligibility(db_session, listing)
        leasing_reasons = leasing_eligibility.check_marketplace_standing(db_session, room, market_release)
        authority_reason = "No verified, unexpired authority record for this room"
        assert authority_reason in listing_reasons
        assert leasing_reasons == [authority_reason]
