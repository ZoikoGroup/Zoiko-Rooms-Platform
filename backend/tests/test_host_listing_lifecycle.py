"""Host pause / resume / archive of their own listing (user_hosting.py)."""

from sqlalchemy.orm import Session

from app.models.listing import Listing
from tests.conftest import _make_user, auth_user_cookie
from tests.test_self_listing_restriction import _make_host_and_listing


class TestHostListingLifecycle:
    def test_pause_then_archive(self, client, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="life-host-a@test.com")
        cookies = auth_user_cookie(host)

        r = client.post(f"/api/users/hosting/listings/{listing_id}/pause", cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "PAUSED"

        # Pausing twice is a state error, not a silent success.
        assert client.post(f"/api/users/hosting/listings/{listing_id}/pause", cookies=cookies).status_code == 409

        r = client.post(f"/api/users/hosting/listings/{listing_id}/archive", cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "ARCHIVED"

    def test_resume_requires_paused(self, client, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="life-host-b@test.com")
        r = client.post(f"/api/users/hosting/listings/{listing_id}/resume", cookies=auth_user_cookie(host))
        assert r.status_code == 409

    def test_archive_refused_while_room_is_committed(self, client, db_session: Session, monkeypatch):
        from app.crud import listing as listing_crud

        host, listing_id = _make_host_and_listing(db_session, email="life-host-c@test.com")
        monkeypatch.setattr(listing_crud, "_has_unresolved_commitments", lambda db, room_id: True)

        r = client.post(f"/api/users/hosting/listings/{listing_id}/archive", cookies=auth_user_cookie(host))
        assert r.status_code == 409
        db_session.expire_all()
        assert db_session.get(Listing, listing_id).state == "PUBLISHED"

    def test_other_users_cannot_change_the_listing(self, client, db_session: Session):
        _host, listing_id = _make_host_and_listing(db_session, email="life-host-d@test.com")
        stranger = _make_user(db_session, email="life-stranger-d@test.com")
        db_session.commit()
        for action in ("pause", "resume", "archive"):
            r = client.post(f"/api/users/hosting/listings/{listing_id}/{action}", cookies=auth_user_cookie(stranger))
            assert r.status_code in (400, 403, 404), (action, r.status_code)
