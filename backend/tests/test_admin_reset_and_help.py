"""Admin password reset, admin "Remember me", and Help centre articles from
the knowledge base."""

import time

from sqlalchemy.orm import Session

from app.core import mailer
from app.services.help_centre import SEED_ARTICLES, seed_help_centre
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie


class TestAdminPasswordReset:
    def test_reset_flow_ends_old_sessions(self, client, db_session: Session, monkeypatch):
        admin = _make_admin(db_session, email="reset-admin@test.com")
        db_session.commit()
        old_cookie = auth_admin_cookie(admin)
        links: list[str] = []
        monkeypatch.setattr(mailer, "send_password_reset_email", lambda to, link, minutes, name="": links.append(link))

        r = client.post("/api/auth/forgot-password", json={"email": "nobody@test.com"})
        assert r.status_code == 200 and links == []
        r = client.post("/api/auth/forgot-password", json={"email": admin.email})
        assert r.status_code == 200 and len(links) == 1 and "/login/reset-password?token=" in links[0]
        token = links[0].split("token=")[1]

        assert client.post("/api/auth/reset-password", json={"token": token, "newPassword": "short"}).status_code == 400
        time.sleep(1)  # token iat is whole seconds
        r = client.post("/api/auth/reset-password", json={"token": token, "newPassword": "brand-new-pass-1"})
        assert r.status_code == 200, r.text
        assert client.post("/api/auth/reset-password", json={"token": token, "newPassword": "again-new-pass-1"}).status_code == 400

        assert client.get("/api/auth/me", cookies=old_cookie).status_code == 401, "sessions from before the reset end"
        r = client.post("/api/auth/login", json={"email": admin.email, "password": "brand-new-pass-1"})
        assert r.status_code == 200, r.text

    def test_user_token_cannot_reset_an_admin_and_vice_versa(self, client, db_session: Session):
        from app.crud.password_reset import create_reset_token, reset_admin_password_with_token

        user = _make_user(db_session, email="reset-user-x@test.com")
        db_session.commit()
        raw = create_reset_token(db_session, user)
        assert reset_admin_password_with_token(db_session, raw, "whatever-123") is False


class TestAdminRememberMe:
    def test_not_remembered_is_a_browser_session_cookie(self, client, db_session: Session):
        admin = _make_admin(db_session, email="remember-admin@test.com")
        db_session.commit()
        r = client.post("/api/auth/login", json={"email": admin.email, "password": "password123", "remember": False})
        assert r.status_code == 200, r.text
        cookie = r.headers["set-cookie"].lower()
        assert "max-age" not in cookie and "expires" not in cookie
        r = client.post("/api/auth/login", json={"email": admin.email, "password": "password123"})
        assert "max-age" in r.headers["set-cookie"].lower()


class TestHelpCentre:
    def test_seeded_articles_are_listed_and_feedback_is_recorded(self, client, db_session: Session):
        assert seed_help_centre(db_session) == len(SEED_ARTICLES)
        assert seed_help_centre(db_session) == 0, "idempotent"
        user = _make_user(db_session, email="help-reader@test.com")
        db_session.commit()

        r = client.get("/api/users/help/articles", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        articles = r.json()
        assert len(articles) == len(SEED_ARTICLES)
        first = articles[0]
        assert first["category"] == "payments" and first["type"] == "How-to"
        assert first["summary"].startswith("Use the payment instructions")
        assert any(a["report"] for a in articles)

        r = client.post(f"/api/users/help/articles/{first['id']}/feedback", json={"helpful": True}, cookies=auth_user_cookie(user))
        assert r.status_code == 200
        assert client.post("/api/users/help/articles/nope/feedback", json={"helpful": True}, cookies=auth_user_cookie(user)).status_code == 404

    def test_assistant_can_find_seeded_articles(self, db_session: Session):
        from app.services.kb import PUBLIC_ACCESS_CLASSES
        from app.services.rag import retrieve

        seed_help_centre(db_session)
        hits = retrieve(db_session, "payment instructions changed", access_classes=PUBLIC_ACCESS_CLASSES)
        assert hits and any("Payment instructions" in h.document.title for h in hits)
