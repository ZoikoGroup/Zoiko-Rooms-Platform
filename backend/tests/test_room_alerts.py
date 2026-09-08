"""Coverage for the public room-alerts feature (ported from a teammate's
branch): an anonymous visitor subscribes to be emailed when a new room
matching their criteria is published, with a token-based unsubscribe link.
No auth on any of these endpoints -- reached from the public marketing site
before a visitor has any account.
"""

from __future__ import annotations

from datetime import date, timedelta, timezone

from sqlalchemy.orm import Session

from app.crud.room_alert import create_alert, list_active_alerts, mark_notified
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from app.models.room_alert import RoomAlert
from app.schemas.room_alert import RoomAlertCreate


class TestCreateAlert:
    def test_creates_an_active_alert_with_unsubscribe_token(self, client, db_session: Session):
        resp = client.post(
            "/api/public/alerts",
            json={"email": "Alert-Fan@Test.com", "city": "Bengaluru", "maxPrice": 20000},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["city"] == "Bengaluru"
        assert body["isActive"] is True

        alert = db_session.get(RoomAlert, body["id"])
        assert alert is not None
        assert alert.email == "alert-fan@test.com"  # normalized lowercase
        assert alert.unsubscribe_token

    def test_rejects_missing_email(self, client, db_session: Session):
        resp = client.post("/api/public/alerts", json={"email": "", "city": "Bengaluru"})
        assert resp.status_code == 422

    def test_rejects_invalid_email(self, client, db_session: Session):
        resp = client.post("/api/public/alerts", json={"email": "not-an-email", "city": "Bengaluru"})
        assert resp.status_code == 422

    def test_rejects_missing_city(self, client, db_session: Session):
        resp = client.post("/api/public/alerts", json={"email": "a@test.com", "city": "  "})
        assert resp.status_code == 422

    def test_no_auth_required(self, client, db_session: Session):
        # Same call as above but explicit about intent: no cookies at all.
        resp = client.post("/api/public/alerts", json={"email": "anon@test.com", "city": "Mumbai"})
        assert resp.status_code == 201


class TestUnsubscribe:
    def test_valid_token_deactivates_and_returns_html(self, client, db_session: Session):
        created = client.post("/api/public/alerts", json={"email": "unsub@test.com", "city": "Pune"}).json()
        alert = db_session.get(RoomAlert, created["id"])
        token = alert.unsubscribe_token

        resp = client.get(f"/api/public/alerts/{created['id']}/unsubscribe", params={"token": token})
        assert resp.status_code == 200
        assert "unsubscribed" in resp.text.lower()

        db_session.refresh(alert)
        assert alert.is_active is False

    def test_invalid_token_returns_404_page(self, client, db_session: Session):
        created = client.post("/api/public/alerts", json={"email": "unsub2@test.com", "city": "Pune"}).json()

        resp = client.get(f"/api/public/alerts/{created['id']}/unsubscribe", params={"token": "wrong-token"})
        assert resp.status_code == 404
        assert "no longer valid" in resp.text.lower()

    def test_unsubscribe_is_idempotent(self, client, db_session: Session):
        """The token is reusable (not single-use, per RoomAlert's own docstring)
        -- clicking an old confirmation/match email's unsubscribe link a second
        time is a harmless no-op, not an error."""
        created = client.post("/api/public/alerts", json={"email": "unsub3@test.com", "city": "Pune"}).json()
        alert = db_session.get(RoomAlert, created["id"])
        token = alert.unsubscribe_token

        first = client.get(f"/api/public/alerts/{created['id']}/unsubscribe", params={"token": token})
        assert first.status_code == 200

        second = client.get(f"/api/public/alerts/{created['id']}/unsubscribe", params={"token": token})
        assert second.status_code == 200


class TestListingPublishedAt:
    def test_publishing_sets_published_at_once(self, db_session: Session):
        from app.crud.listing import publish_listing
        from app.models.listing import Listing

        party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.flush()
        prop = Property(owner_party_id=party.id, address="1 Alert St", city="Bengaluru", status="active")
        db_session.add(prop)
        db_session.flush()
        room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
        db_session.add(room)
        db_session.flush()

        listing = Listing(
            id="L-ALERTPUB", slug="alertpub", name="Alert Pub Listing", room_type="Private room",
            city="Bengaluru", location="Koramangala", price_per_night=15000, guests=1,
            rating=0.0, review_count=0, party_id=party.id, owner_id=None, room_id=room.id,
            state="APPROVED",
        )
        db_session.add(listing)
        db_session.commit()
        assert listing.published_at is None

        published = publish_listing(db_session, listing)
        assert published.published_at is not None
        first_published_at = published.published_at

        # Pausing and republishing must not reset the original published_at.
        published.state = "PAUSED"
        db_session.commit()
        republished = publish_listing(db_session, published)
        assert republished.published_at == first_published_at


class TestActiveAlertsQuery:
    def test_list_active_alerts_excludes_unsubscribed(self, db_session: Session):
        active = create_alert(db_session, RoomAlertCreate(email="active@test.com", city="Chennai"))
        unsub = create_alert(db_session, RoomAlertCreate(email="inactive@test.com", city="Chennai"))
        unsub.is_active = False
        db_session.commit()

        results = list_active_alerts(db_session)
        result_ids = {a.id for a in results}
        assert active.id in result_ids
        assert unsub.id not in result_ids

    def test_mark_notified_updates_timestamp(self, db_session: Session):
        alert = create_alert(db_session, RoomAlertCreate(email="notify@test.com", city="Delhi"))
        assert alert.last_notified_at is None

        when = date.today()
        from datetime import datetime
        mark_notified(db_session, alert, datetime.combine(when, datetime.min.time(), tzinfo=timezone.utc))
        db_session.refresh(alert)
        assert alert.last_notified_at is not None
