"""ZR-ENG-CLR-001 Section 14: the Jurisdiction Adaptation Framework's thin
policy adapter (app/services/policy.py) -- per-market overrides of the small
set of policy keys this codebase actually branches on, falling back to the
platform-wide default (app/core/config.py settings) otherwise.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.authority_record import AuthorityRecord
from app.models.leasing import Offer
from app.models.listing import Listing
from app.models.market_release import MarketRelease
from app.models.occupancy_classification import OccupancyClassification
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from app.services.eligibility import failed_gate_visibility_allowed
from app.services.policy import get_policy, set_policy_overrides
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_room_hold_atomicity import _apply_and_send_offer, _make_listing_with_room, _make_verified_renter


class TestGetPolicyDefaults:
    def test_no_market_release_uses_platform_default(self):
        assert get_policy(None, "booking.acceptance_hold_duration_hours") == settings.offer_acceptance_confirmation_hours
        assert get_policy(None, "payment.checkout_lock_duration_minutes") == settings.payment_checkout_lock_minutes
        assert get_policy(None, "publication.requires_approval") is True
        assert get_policy(None, "visibility.failed_gate_behavior") == "hide"

    def test_market_release_with_no_overrides_uses_platform_default(self):
        release = MarketRelease(jurisdiction="IN-POLICY-1", policy_overrides={})
        assert get_policy(release, "booking.acceptance_hold_duration_hours") == settings.offer_acceptance_confirmation_hours

    def test_market_release_override_wins(self):
        release = MarketRelease(jurisdiction="IN-POLICY-2", policy_overrides={"booking.acceptance_hold_duration_hours": 6})
        assert get_policy(release, "booking.acceptance_hold_duration_hours") == 6
        # An unrelated key on the same release still falls back to the default.
        assert get_policy(release, "payment.checkout_lock_duration_minutes") == settings.payment_checkout_lock_minutes


class TestSetPolicyOverrides:
    def test_rejects_unknown_keys(self):
        release = MarketRelease(jurisdiction="IN-POLICY-3")
        try:
            set_policy_overrides(release, {"not.a.real.key": 1})
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "not.a.real.key" in str(exc)

    def test_replaces_the_full_override_set(self):
        release = MarketRelease(jurisdiction="IN-POLICY-4", policy_overrides={"booking.acceptance_hold_duration_hours": 6})
        set_policy_overrides(release, {"payment.checkout_lock_duration_minutes": 10})
        assert release.policy_overrides == {"payment.checkout_lock_duration_minutes": 10}


class TestPolicyRoute:
    def test_plain_admin_cannot_set_market_policy(self, client, db_session: Session):
        release = MarketRelease(jurisdiction="IN-POLICY-ROUTE-1", status="active")
        db_session.add(release)
        db_session.commit()
        admin = _make_admin(db_session, email="policy-plain@test.com", role="admin")
        r = client.put(
            f"/api/market-releases/{release.id}/policy",
            json={"overrides": {"booking.acceptance_hold_duration_hours": 6}},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 403, r.text

    def test_super_admin_can_set_and_read_back_market_policy(self, client, db_session: Session):
        release = MarketRelease(jurisdiction="IN-POLICY-ROUTE-2", status="active")
        db_session.add(release)
        db_session.commit()
        super_admin = _make_admin(db_session, email="policy-super@test.com", role="super_admin")
        cookies = auth_admin_cookie(super_admin)

        r = client.put(
            f"/api/market-releases/{release.id}/policy",
            json={"overrides": {"booking.acceptance_hold_duration_hours": 6}},
            cookies=cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["policyOverrides"] == {"booking.acceptance_hold_duration_hours": 6}

        r = client.get("/api/market-releases", cookies=cookies)
        assert r.status_code == 200, r.text
        found = next(m for m in r.json() if m["id"] == release.id)
        assert found["policyOverrides"] == {"booking.acceptance_hold_duration_hours": 6}

    def test_unknown_policy_key_is_rejected(self, client, db_session: Session):
        release = MarketRelease(jurisdiction="IN-POLICY-ROUTE-3", status="active")
        db_session.add(release)
        db_session.commit()
        super_admin = _make_admin(db_session, email="policy-super2@test.com", role="super_admin")

        r = client.put(
            f"/api/market-releases/{release.id}/policy",
            json={"overrides": {"not.a.real.key": 1}},
            cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 400, r.text


class TestPolicyAffectsAcceptanceWindow:
    def test_market_override_shortens_the_offer_acceptance_deadline(self, client, db_session: Session):
        listing_id, room_id = _make_listing_with_room(db_session)
        listing = db_session.get(Listing, listing_id)
        room = db_session.get(Room, room_id)

        release = MarketRelease(
            jurisdiction="IN-POLICY-E2E", status="active",
            policy_overrides={"booking.acceptance_hold_duration_hours": 1},
        )
        db_session.add(release)
        db_session.flush()
        listing.market_release_id = release.id
        db_session.add(AuthorityRecord(party_id=listing.party_id or 1, room_id=room.id, authority_type="lease_agreement", status="verified"))
        db_session.add(OccupancyClassification(room_id=room.id, classification="shared_residential_room", review_state="APPROVED"))
        db_session.commit()

        super_admin = _make_admin(db_session, email="policy-e2e-admin@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="policy-e2e-renter@test.com")

        _app_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)
        before = date.today()
        r = client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text

        offer = db_session.get(Offer, offer_id)
        # 1-hour override, not the platform default (24h) -- proves the
        # per-market policy actually reached the deadline computation.
        assert offer.confirmation_expires_at - offer.accepted_at == timedelta(hours=1)


class TestFailedGateVisibilityAllowed:
    """services/eligibility.py:failed_gate_visibility_allowed -- exercises
    the visibility.failed_gate_behavior policy key directly. Not wired into
    the live is_listing_available path (see that function's own docstring
    for why), but real and tested so a future call site can use it."""

    def _make_room_with_no_gates_passing(self, db: Session) -> Room:
        party = Party(party_type="provider", status="active", jurisdiction="IN")
        db.add(party)
        db.flush()
        prop = Property(owner_party_id=party.id, address="1 Gate St", city="Bengaluru", status="active")
        db.add(prop)
        db.flush()
        room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
        db.add(room)
        db.commit()
        return room

    def test_passing_gates_are_always_visible(self, db_session: Session):
        room = self._make_room_with_no_gates_passing(db_session)
        release = MarketRelease(jurisdiction="IN-GATE-1", status="active")
        db_session.add(release)
        db_session.add(AuthorityRecord(party_id=1, room_id=room.id, authority_type="lease_agreement", status="verified"))
        db_session.add(OccupancyClassification(room_id=room.id, classification="shared_residential_room", review_state="APPROVED"))
        db_session.commit()
        assert failed_gate_visibility_allowed(db_session, room, release) is True

    def test_failed_gates_hidden_by_default(self, db_session: Session):
        room = self._make_room_with_no_gates_passing(db_session)
        release = MarketRelease(jurisdiction="IN-GATE-2", status="active")
        db_session.add(release)
        db_session.commit()
        # No AuthorityRecord/OccupancyClassification -- gates fail.
        assert failed_gate_visibility_allowed(db_session, room, release) is False

    def test_visible_unbookable_override_stays_visible_despite_failed_gates(self, db_session: Session):
        room = self._make_room_with_no_gates_passing(db_session)
        release = MarketRelease(
            jurisdiction="IN-GATE-3", status="active",
            policy_overrides={"visibility.failed_gate_behavior": "visible_unbookable"},
        )
        db_session.add(release)
        db_session.commit()
        assert failed_gate_visibility_allowed(db_session, room, release) is True
