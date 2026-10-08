"""Integration tests for the listing submit -> review -> approve -> publish
workflow (and pause/republish, reject, ownership/authorization boundaries).

Deliberately exercises the real HTTP endpoints end-to-end (USER creates and
submits, ADMIN/SUPER_ADMIN reviews) rather than poking the database directly,
so these tests fail if the actual wiring between routes and CRUD breaks, not
just the CRUD functions in isolation.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from app.models.user_account import UserAccount
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie, make_room_publishable

LISTING_PAYLOAD = {
    "name": "Sunny Room in Koramangala",
    "roomType": "Private room",
    "city": "Bengaluru",
    "location": "Koramangala",
    "pricePerNight": 500,
    "currency": "INR",
    "guests": 1,
    "bedrooms": 1,
    "bathrooms": 1,
    "size": 100,
    "description": "A nice room",
    "amenities": ["WiFi"],
    "images": [],
    "minStayNights": 30,
    "contactName": "Test Host",
    "contactPhone": "9999999999",
    "contactEmail": "host@test.com",
}


def _make_host_with_room(db: Session, *, email: str = "host@test.com",
                         publishable: bool = True) -> tuple[UserAccount, int]:
    """A USER with a party, owning one property with one room -- the minimum a
    host needs to be able to create a listing. `publishable` also satisfies
    the identity / property / authority publication gates."""
    party = Party(party_type="renter", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()

    user = _make_user(db, email=email)
    user.party_id = party.id
    db.flush()

    prop = Property(owner_party_id=party.id, address="123 Test St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()

    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()
    db.commit()
    if publishable:
        make_room_publishable(db, room)

    return user, room.id


def _create_and_submit_listing(client, db_session: Session, *, email: str = "host@test.com",
                               publishable: bool = True) -> tuple[str, dict]:
    """USER creates a listing and submits it for review. Returns (listing_id, user_cookies)."""
    user, room_id = _make_host_with_room(db_session, email=email, publishable=publishable)
    cookies = auth_user_cookie(user)

    r = client.post(
        "/api/users/hosting/listings",
        json={**LISTING_PAYLOAD, "roomId": room_id},
        cookies=cookies,
    )
    assert r.status_code == 201, r.text
    listing_id = r.json()["id"]
    assert r.json()["state"] == "DRAFT"

    r = client.post(f"/api/users/hosting/listings/{listing_id}/submit-for-review", cookies=cookies)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "REVIEW"

    return listing_id, cookies


# ── 1-2: create + submit ────────────────────────────────────────────────────


class TestCreateAndSubmit:
    def test_user_can_create_listing(self, client, db_session: Session):
        user, room_id = _make_host_with_room(db_session)
        cookies = auth_user_cookie(user)
        r = client.post(
            "/api/users/hosting/listings",
            json={**LISTING_PAYLOAD, "roomId": room_id},
            cookies=cookies,
        )
        assert r.status_code == 201, r.text
        assert r.json()["state"] == "DRAFT"

    def test_user_can_submit_listing_for_review(self, client, db_session: Session):
        listing_id, cookies = _create_and_submit_listing(client, db_session)
        r = client.get("/api/users/hosting/listings", cookies=cookies)
        assert r.status_code == 200
        listing = next(l for l in r.json() if l["id"] == listing_id)
        assert listing["state"] == "REVIEW"


# ── 3: USER cannot directly publish ─────────────────────────────────────────


class TestUserCannotPublish:
    def test_no_user_facing_publish_endpoint(self, client, db_session: Session):
        listing_id, cookies = _create_and_submit_listing(client, db_session)
        # The old /publish endpoint under the USER hosting router no longer exists.
        r = client.post(f"/api/users/hosting/listings/{listing_id}/publish", cookies=cookies)
        assert r.status_code == 404

    def test_user_cannot_call_admin_publish_endpoint(self, client, db_session: Session):
        listing_id, cookies = _create_and_submit_listing(client, db_session)
        # The admin-only endpoint rejects a USER session outright (no admin cookie).
        r = client.post(f"/api/listings/{listing_id}/publish", cookies=cookies)
        assert r.status_code == 401


# ── 4-5: ADMIN / SUPER_ADMIN approve + publish ──────────────────────────────


class TestApproveAndPublish:
    def test_admin_can_approve_and_publish(self, client, db_session: Session):
        listing_id, _ = _create_and_submit_listing(client, db_session)
        admin = _make_admin(db_session, email="admin@test.com", role="admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(f"/api/listings/{listing_id}/approve", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        # No Listing Fee is owed in this test market, so approval publishes
        # straight away (crud/listing.py:approve_listing).
        assert r.json()["state"] == "PUBLISHED"

        r = client.post(f"/api/listings/{listing_id}/publish", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "PUBLISHED"

    def test_super_admin_can_approve_and_publish(self, client, db_session: Session):
        listing_id, _ = _create_and_submit_listing(client, db_session, email="host2@test.com")
        super_admin = _make_admin(db_session, email="superadmin@test.com", role="super_admin")
        cookies = auth_admin_cookie(super_admin)

        r = client.post(f"/api/listings/{listing_id}/approve", cookies=cookies)
        assert r.status_code == 200, r.text
        # No Listing Fee is owed in this test market, so approval publishes
        # straight away (crud/listing.py:approve_listing).
        assert r.json()["state"] == "PUBLISHED"

        r = client.post(f"/api/listings/{listing_id}/publish", cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "PUBLISHED"


# ── 6-8: identity / property / authority are hard publication gates ─────
# ZR-AUTHORITY-002 Section 2.4 / P0 #10: an admin's approval can't stand in
# for them; occupancy classification stays an informational review signal.


class TestPublicationGates:
    def test_publish_blocked_without_listing_authority(self, client, db_session: Session):
        from app.models.authority_verification import AuthorityVerification

        listing_id, _ = _create_and_submit_listing(client, db_session, email="host3@test.com")
        for v in db_session.scalars(select(AuthorityVerification)):
            db_session.delete(v)
        db_session.commit()
        admin = _make_admin(db_session, email="admin3@test.com")
        cookies = auth_admin_cookie(admin)
        r = client.post(f"/api/listings/{listing_id}/approve", cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "APPROVED"  # approved, but not published

        r = client.post(f"/api/listings/{listing_id}/publish", cookies=cookies)
        assert r.status_code == 409
        assert "authority" in r.json()["detail"].lower()

    def test_publish_succeeds_without_occupancy_classification(self, client, db_session: Session):
        from app.models.occupancy_classification import OccupancyClassification

        listing_id, _ = _create_and_submit_listing(client, db_session, email="host4@test.com")
        admin = _make_admin(db_session, email="admin4@test.com")
        cookies = auth_admin_cookie(admin)
        client.post(f"/api/listings/{listing_id}/approve", cookies=cookies)

        assert db_session.scalars(select(OccupancyClassification)).first() is None

        r = client.post(f"/api/listings/{listing_id}/publish", cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "PUBLISHED"

    def test_publish_blocked_without_identity_verification(self, client, db_session: Session):
        listing_id, _ = _create_and_submit_listing(client, db_session, email="host5@test.com", publishable=False)
        admin = _make_admin(db_session, email="admin5@test.com")
        cookies = auth_admin_cookie(admin)
        client.post(f"/api/listings/{listing_id}/approve", cookies=cookies)

        r = client.post(f"/api/listings/{listing_id}/publish", cookies=cookies)
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert "identity" in detail and "Property verification" in detail and "authority" in detail


# ── 9-10: pause / republish lifecycle ───────────────────────────────────────


class TestPauseAndRepublish:
    def test_published_listing_can_be_paused(self, client, db_session: Session):
        listing_id, _ = _create_and_submit_listing(client, db_session, email="host6@test.com")
        admin = _make_admin(db_session, email="admin6@test.com")
        cookies = auth_admin_cookie(admin)
        client.post(f"/api/listings/{listing_id}/approve", cookies=cookies)
        client.post(f"/api/listings/{listing_id}/publish", cookies=cookies)

        r = client.post(f"/api/listings/{listing_id}/pause", cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "PAUSED"

    def test_paused_listing_republish_rechecks_gates(self, client, db_session: Session):
        from app.models.authority_verification import AuthorityVerification

        listing_id, _ = _create_and_submit_listing(client, db_session, email="host7@test.com")
        admin = _make_admin(db_session, email="admin7@test.com")
        cookies = auth_admin_cookie(admin)
        client.post(f"/api/listings/{listing_id}/approve", cookies=cookies)
        client.post(f"/api/listings/{listing_id}/publish", cookies=cookies)
        client.post(f"/api/listings/{listing_id}/pause", cookies=cookies)

        # Content review isn't re-run, and with the gates still met it republishes.
        r = client.post(f"/api/listings/{listing_id}/publish", cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "PUBLISHED"

        # Authority withdrawn while paused: republishing is refused.
        client.post(f"/api/listings/{listing_id}/pause", cookies=cookies)
        for v in db_session.scalars(select(AuthorityVerification)):
            v.state = "REVOKED"
        db_session.commit()
        r = client.post(f"/api/listings/{listing_id}/publish", cookies=cookies)
        assert r.status_code == 409


# ── 11: reject with reason ──────────────────────────────────────────────────


class TestReject:
    def test_admin_can_reject_with_reason(self, client, db_session: Session):
        listing_id, _ = _create_and_submit_listing(client, db_session, email="host8@test.com")
        admin = _make_admin(db_session, email="admin8@test.com")
        cookies = auth_admin_cookie(admin)

        r = client.post(f"/api/listings/{listing_id}/reject", json={"reason": "Photos are missing"}, cookies=cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "REJECTED"
        assert body["rejectionReason"] == "Photos are missing"

    def test_reject_requires_a_reason(self, client, db_session: Session):
        listing_id, _ = _create_and_submit_listing(client, db_session, email="host9@test.com")
        admin = _make_admin(db_session, email="admin9@test.com")
        cookies = auth_admin_cookie(admin)
        r = client.post(f"/api/listings/{listing_id}/reject", json={"reason": ""}, cookies=cookies)
        assert r.status_code == 400


# ── 12-13: USER notifications ───────────────────────────────────────────────


class TestUserNotifications:
    def test_user_notified_after_approval_and_publish(self, client, db_session: Session):
        listing_id, cookies = _create_and_submit_listing(client, db_session, email="host10@test.com")
        admin = _make_admin(db_session, email="admin10@test.com")
        admin_cookies = auth_admin_cookie(admin)
        client.post(f"/api/listings/{listing_id}/approve", cookies=admin_cookies)
        client.post(f"/api/listings/{listing_id}/publish", cookies=admin_cookies)

        user = db_session.scalar(select(UserAccount).where(UserAccount.email == "host10@test.com"))
        notif = db_session.scalars(
            select(Notification).where(
                Notification.recipient_user_id == user.id,
                Notification.notification_type == "listing.published",
            )
        ).first()
        assert notif is not None
        assert "approved and published" in notif.message

    def test_user_notified_after_rejection(self, client, db_session: Session):
        listing_id, cookies = _create_and_submit_listing(client, db_session, email="host11@test.com")
        admin = _make_admin(db_session, email="admin11@test.com")
        admin_cookies = auth_admin_cookie(admin)
        client.post(f"/api/listings/{listing_id}/reject", json={"reason": "Needs better photos"}, cookies=admin_cookies)

        user = db_session.scalar(select(UserAccount).where(UserAccount.email == "host11@test.com"))
        notif = db_session.scalars(
            select(Notification).where(
                Notification.recipient_user_id == user.id,
                Notification.notification_type == "listing.rejected",
            )
        ).first()
        assert notif is not None
        assert "Needs better photos" in notif.message

    def test_admins_notified_when_listing_submitted(self, client, db_session: Session):
        admin = _make_admin(db_session, email="admin12@test.com")
        _create_and_submit_listing(client, db_session, email="host12@test.com")

        notif = db_session.scalars(
            select(Notification).where(
                Notification.recipient_admin_id == admin.id,
                Notification.notification_type == "listing.submitted",
            )
        ).first()
        assert notif is not None


# ── 14: authorization boundaries ────────────────────────────────────────────


class TestAuthorizationBoundaries:
    def test_user_cannot_approve_or_publish(self, client, db_session: Session):
        listing_id, cookies = _create_and_submit_listing(client, db_session, email="host13@test.com")
        r = client.post(f"/api/listings/{listing_id}/approve", cookies=cookies)
        assert r.status_code == 401
        r = client.post(f"/api/listings/{listing_id}/publish", cookies=cookies)
        assert r.status_code == 401

    def test_user_cannot_manage_another_users_listing(self, client, db_session: Session):
        listing_id, _owner_cookies = _create_and_submit_listing(client, db_session, email="owner@test.com")
        other_user, _ = _make_host_with_room(db_session, email="intruder@test.com")
        other_cookies = auth_user_cookie(other_user)

        r = client.put(
            f"/api/users/hosting/listings/{listing_id}",
            json={"name": "Hijacked"},
            cookies=other_cookies,
        )
        assert r.status_code == 403

    def test_unapproved_admin_cannot_authenticate(self, client, db_session: Session):
        # Sanity check for the broader "unauthorized users" boundary: an admin
        # account pending approval cannot even establish a session.
        pending = _make_admin(db_session, email="pending@test.com")
        pending.approval_status = "pending"
        db_session.commit()
        cookies = auth_admin_cookie(pending)
        r = client.get("/api/listings", cookies=cookies)
        assert r.status_code == 401
