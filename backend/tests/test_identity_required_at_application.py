"""ZR-ENG-CLR-012 AC-03: "Application can proceed before full ID completion
unless the active market pack explicitly requires an earlier gate."
Progressive verification is the default -- most listings (no jurisdiction,
or a jurisdiction pack that hasn't opted in) never require identity before
application; only a jurisdiction pack with identity_required_at_application
=True blocks an unverified renter at this stage."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market_policy import MarketPolicyPack
from app.models.party import Party
from tests.conftest import _make_user, auth_user_cookie
from tests.test_renter_offer_agreement_flow import _make_agreement_eligible
from tests.test_room_hold_atomicity import _make_listing_with_room, _make_verified_renter


def _unverified_renter(db: Session, *, email: str):
    renter_party = Party(party_type="renter", status="active", jurisdiction="England")
    db.add(renter_party)
    db.flush()
    user = _make_user(db, email=email)
    user.party_id = renter_party.id
    db.commit()
    return user


def _england_policy_pack(db: Session, *, identity_required: bool) -> MarketPolicyPack:
    existing = db.scalar(select(MarketPolicyPack).where(MarketPolicyPack.jurisdiction_code == "England"))
    if existing:
        existing.identity_required_at_application = identity_required
        db.commit()
        db.refresh(existing)
        return existing
    pack = MarketPolicyPack(jurisdiction_code="England", version=1, effective_from=date.today(), identity_required_at_application=identity_required)
    db.add(pack)
    db.commit()
    db.refresh(pack)
    return pack


class TestIdentityRequiredAtApplication:
    def test_unverified_renter_can_apply_when_no_policy_pack_configured(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        renter = _unverified_renter(db_session, email="ida-01@test.com")
        r = client.post(
            "/api/users/rentals/applications",
            json={"listingId": listing_id, "message": "test", "desiredMoveIn": None},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text

    def test_unverified_renter_can_apply_when_jurisdiction_does_not_opt_in(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        _make_agreement_eligible(db_session, listing_id)
        _england_policy_pack(db_session, identity_required=False)
        renter = _unverified_renter(db_session, email="ida-02@test.com")
        r = client.post(
            "/api/users/rentals/applications",
            json={"listingId": listing_id, "message": "test", "desiredMoveIn": None},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text

    def test_unverified_renter_blocked_when_jurisdiction_opts_in(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        _make_agreement_eligible(db_session, listing_id)
        _england_policy_pack(db_session, identity_required=True)
        renter = _unverified_renter(db_session, email="ida-03@test.com")
        r = client.post(
            "/api/users/rentals/applications",
            json={"listingId": listing_id, "message": "test", "desiredMoveIn": None},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 403, r.text

    def test_verified_renter_can_apply_even_when_jurisdiction_opts_in(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        _make_agreement_eligible(db_session, listing_id)
        _england_policy_pack(db_session, identity_required=True)
        renter = _make_verified_renter(db_session, email="ida-04@test.com")
        r = client.post(
            "/api/users/rentals/applications",
            json={"listingId": listing_id, "message": "test", "desiredMoveIn": None},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
