"""Coverage for the user->admin contact email flow (routes/user_contact.py and
routes/admin_contact.py) -- previously zero test references despite being a
real, reachable feature (a support/help "message the team" form)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie


class TestUserSendContactEmail:
    def test_user_can_send_a_message_to_admin(self, client, db_session: Session):
        user = _make_user(db_session, email="contactor@test.com")
        db_session.commit()

        resp = client.post(
            "/api/users/contact",
            json={"subject": "Question about my lease", "message": "When is my next rent due?"},
            cookies=auth_user_cookie(user),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["subject"] == "Question about my lease"
        assert body["message"] == "When is my next rent due?"
        assert body["isRead"] is False
        assert body["userEmail"] == "contactor@test.com"

    def test_blank_subject_is_rejected(self, client, db_session: Session):
        user = _make_user(db_session, email="contactor2@test.com")
        db_session.commit()

        resp = client.post(
            "/api/users/contact",
            json={"subject": "   ", "message": "hello"},
            cookies=auth_user_cookie(user),
        )
        assert resp.status_code == 422

    def test_blank_message_is_rejected(self, client, db_session: Session):
        user = _make_user(db_session, email="contactor3@test.com")
        db_session.commit()

        resp = client.post(
            "/api/users/contact",
            json={"subject": "hi", "message": "   "},
            cookies=auth_user_cookie(user),
        )
        assert resp.status_code == 422

    def test_oversized_subject_is_rejected(self, client, db_session: Session):
        user = _make_user(db_session, email="contactor4@test.com")
        db_session.commit()

        resp = client.post(
            "/api/users/contact",
            json={"subject": "x" * 256, "message": "hello"},
            cookies=auth_user_cookie(user),
        )
        assert resp.status_code == 422

    def test_requires_auth(self, client):
        resp = client.post("/api/users/contact", json={"subject": "hi", "message": "hello"})
        assert resp.status_code == 401


class TestAdminContactInbox:
    def test_admin_sees_sent_message(self, client, db_session: Session):
        user = _make_user(db_session, email="contactor5@test.com")
        admin = _make_admin(db_session, email="inbox-admin@test.com")
        db_session.commit()

        client.post(
            "/api/users/contact",
            json={"subject": "Broken sink", "message": "The sink in my room is leaking."},
            cookies=auth_user_cookie(user),
        )

        resp = client.get("/api/admin/contact-emails", cookies=auth_admin_cookie(admin))
        assert resp.status_code == 200
        subjects = [e["subject"] for e in resp.json()]
        assert "Broken sink" in subjects

    def test_unread_count_and_mark_read(self, client, db_session: Session):
        user = _make_user(db_session, email="contactor6@test.com")
        admin = _make_admin(db_session, email="inbox-admin2@test.com")
        db_session.commit()

        sent = client.post(
            "/api/users/contact",
            json={"subject": "Need help", "message": "Please call me."},
            cookies=auth_user_cookie(user),
        ).json()

        before = client.get("/api/admin/contact-emails/unread-count", cookies=auth_admin_cookie(admin)).json()
        assert before["count"] >= 1

        read_resp = client.put(f"/api/admin/contact-emails/{sent['id']}/read", cookies=auth_admin_cookie(admin))
        assert read_resp.status_code == 200
        assert read_resp.json()["isRead"] is True

        after = client.get("/api/admin/contact-emails/unread-count", cookies=auth_admin_cookie(admin)).json()
        assert after["count"] == before["count"] - 1

    def test_unread_filter_excludes_read_emails(self, client, db_session: Session):
        user = _make_user(db_session, email="contactor7@test.com")
        admin = _make_admin(db_session, email="inbox-admin3@test.com")
        db_session.commit()

        sent = client.post(
            "/api/users/contact",
            json={"subject": "Filter me out", "message": "..."},
            cookies=auth_user_cookie(user),
        ).json()
        client.put(f"/api/admin/contact-emails/{sent['id']}/read", cookies=auth_admin_cookie(admin))

        resp = client.get("/api/admin/contact-emails?unread=true", cookies=auth_admin_cookie(admin))
        subjects = [e["subject"] for e in resp.json()]
        assert "Filter me out" not in subjects

    def test_mark_read_on_missing_email_is_404(self, client, db_session: Session):
        admin = _make_admin(db_session, email="inbox-admin4@test.com")
        db_session.commit()

        resp = client.put("/api/admin/contact-emails/999999/read", cookies=auth_admin_cookie(admin))
        assert resp.status_code == 404

    def test_requires_admin_auth(self, client):
        resp = client.get("/api/admin/contact-emails")
        assert resp.status_code == 401
