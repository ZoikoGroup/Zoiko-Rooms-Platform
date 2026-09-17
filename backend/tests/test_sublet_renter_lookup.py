"""Sublet request submission used to require the current tenant to already know
the proposed renter's raw internal party ID (typed by hand, no way to discover
it in the product). This endpoint resolves an email to a party instead."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.identity_verification import IdentityVerification
from app.models.party import Party
from tests.conftest import _make_user, auth_user_cookie


class TestSubletRenterLookup:
    def test_found_and_verified(self, client, db_session: Session):
        requester = _make_user(db_session, email="requester@test.com")
        cookies = auth_user_cookie(requester)

        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.flush()
        candidate = _make_user(db_session, email="candidate@test.com")
        candidate.party_id = party.id
        candidate.full_name = "Jane Doe"
        db_session.add(
            IdentityVerification(
                party_id=party.id, document_type="passport", document_category="identity", status="verified"
            )
        )
        db_session.commit()

        r = client.get("/api/users/rentals/sublet-lookup", params={"email": "candidate@test.com"}, cookies=cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["found"] is True
        assert body["partyId"] == party.id
        assert body["name"] == "Jane Doe"
        assert body["identityVerified"] is True

    def test_found_but_not_verified(self, client, db_session: Session):
        requester = _make_user(db_session, email="requester2@test.com")
        cookies = auth_user_cookie(requester)

        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.flush()
        candidate = _make_user(db_session, email="unverified@test.com")
        candidate.party_id = party.id
        db_session.commit()

        r = client.get("/api/users/rentals/sublet-lookup", params={"email": "unverified@test.com"}, cookies=cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["found"] is True
        assert body["identityVerified"] is False

    def test_not_found(self, client, db_session: Session):
        requester = _make_user(db_session, email="requester3@test.com")
        cookies = auth_user_cookie(requester)

        r = client.get(
            "/api/users/rentals/sublet-lookup", params={"email": "nobody@test.com"}, cookies=cookies
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["found"] is False
        assert body["partyId"] is None

    def test_requires_auth(self, client):
        r = client.get("/api/users/rentals/sublet-lookup", params={"email": "candidate@test.com"})
        assert r.status_code in (401, 403)
