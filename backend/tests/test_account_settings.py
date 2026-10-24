"""Account settings (routes/user_account_settings.py, crud/user_settings.py)."""


from sqlalchemy.orm import Session

from app.models.user_account import UserAccount
from tests.conftest import _make_user, auth_user_cookie
from tests.test_self_listing_restriction import _make_host_and_listing

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class TestPreferences:
    def test_defaults_then_save_and_validate(self, client, db_session: Session):
        user = _make_user(db_session, email="set-pref@test.com")
        db_session.commit()
        cookies = auth_user_cookie(user)

        prefs = client.get("/api/users/preferences", cookies=cookies).json()
        assert prefs["timeFormat"] == "24h" and "marketingEmails" not in prefs

        r = client.put("/api/users/preferences", json={"timeFormat": "12h", "timeZone": "Asia/Kolkata"}, cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["timeFormat"] == "12h" and r.json()["timeZone"] == "Asia/Kolkata"

        assert client.put("/api/users/preferences", json={"timeFormat": "13h"}, cookies=cookies).status_code == 422
        assert client.put("/api/users/preferences", json={"timeZone": "Mars/Base"}, cookies=cookies).status_code == 422
        assert client.put("/api/users/preferences", json={"nope": 1}, cookies=cookies).status_code == 422
        # Product-news emails were removed from Settings.
        assert client.put("/api/users/preferences", json={"marketingEmails": True}, cookies=cookies).status_code == 422

    def test_english_only_and_no_analytics_setting(self, client, db_session: Session):
        from app.models.user_settings import UserPreference

        user = _make_user(db_session, email="set-lang@test.com")
        # Saved before Hindi / Telugu and the analytics toggle were removed.
        db_session.add(UserPreference(user_id=user.id, data={"language": "te-IN", "optionalAnalytics": True}))
        db_session.commit()
        cookies = auth_user_cookie(user)

        prefs = client.get("/api/users/preferences", cookies=cookies).json()
        assert prefs["language"] == "en-GB" and "optionalAnalytics" not in prefs
        assert client.put("/api/users/preferences", json={"language": "hi-IN"}, cookies=cookies).status_code == 422
        assert client.put("/api/users/preferences", json={"optionalAnalytics": True}, cookies=cookies).status_code == 422
        # Prices always show in the listing's own currency.
        assert "currencyDisplay" not in prefs
        assert client.put("/api/users/preferences", json={"currencyDisplay": "USD"}, cookies=cookies).status_code == 422
        assert client.put("/api/users/preferences", json={"language": "en-US"}, cookies=cookies).json()["language"] == "en-US"


class TestAvatar:
    def test_upload_read_replace_delete(self, client, db_session: Session):
        user = _make_user(db_session, email="set-avatar@test.com")
        db_session.commit()
        cookies = auth_user_cookie(user)

        r = client.post("/api/users/profile/avatar", files={"file": ("me.png", PNG, "image/png")}, cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["hasAvatar"] is True
        r = client.get("/api/users/profile/avatar", cookies=cookies)
        assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"

        bad = client.post("/api/users/profile/avatar", files={"file": ("x.gif", b"GIF89a....", "image/gif")}, cookies=cookies)
        assert bad.status_code == 400

        r = client.delete("/api/users/profile/avatar", cookies=cookies)
        assert r.json()["hasAvatar"] is False
        assert client.get("/api/users/profile/avatar", cookies=cookies).status_code == 404


class TestEmailChange:
    def test_change_needs_password_and_confirmation_from_new_inbox(self, client, db_session: Session, monkeypatch):
        from app.api.routes import user_account_settings as routes

        sent = {}
        monkeypatch.setattr(routes, "send_email_change_confirmation_email", lambda to, link, mins, name="": sent.update(to=to, link=link))
        user = _make_user(db_session, email="set-old@test.com")
        _make_user(db_session, email="set-taken@test.com")
        db_session.commit()
        cookies = auth_user_cookie(user)

        bad = client.post("/api/users/email-change", json={"newEmail": "set-new@test.com", "currentPassword": "wrong"}, cookies=cookies)
        assert bad.status_code == 400
        taken = client.post("/api/users/email-change", json={"newEmail": "set-taken@test.com", "currentPassword": "password123"}, cookies=cookies)
        assert taken.status_code == 409

        r = client.post("/api/users/email-change", json={"newEmail": "Set-New@test.com", "currentPassword": "password123"}, cookies=cookies)
        assert r.status_code == 202, r.text
        assert sent["to"] == "set-new@test.com"
        assert client.get("/api/users/email-change", cookies=cookies).json()["pendingEmail"] == "set-new@test.com"
        db_session.expire_all()
        assert db_session.get(UserAccount, user.id).email == "set-old@test.com"  # unchanged until confirmed

        token = sent["link"].split("token=")[1]
        r = client.post("/api/users/email-change/confirm", json={"token": token})
        assert r.status_code == 200, r.text
        assert r.json()["email"] == "set-new@test.com"
        assert "zoiko_user_token" in r.cookies
        # Single use.
        assert client.post("/api/users/email-change/confirm", json={"token": token}).status_code == 400


class TestSessions:
    def test_revoke_others_keeps_the_caller_signed_in(self, client, db_session: Session, monkeypatch):
        import time

        user = _make_user(db_session, email="set-sessions@test.com")
        db_session.commit()
        old_cookies = auth_user_cookie(user)
        time.sleep(1.1)  # tokens carry whole-second iat

        r = client.post("/api/users/sessions/revoke-others", cookies=old_cookies)
        assert r.status_code == 200, r.text
        fresh = r.cookies.get("zoiko_user_token")
        assert fresh

        assert client.get("/api/users/me", cookies=old_cookies).status_code == 401
        assert client.get("/api/users/me", cookies={"zoiko_user_token": fresh}).status_code == 200

    def test_password_change_signs_out_other_sessions(self, client, db_session: Session):
        import time

        user = _make_user(db_session, email="set-pw@test.com")
        db_session.commit()
        other_device = auth_user_cookie(user)
        time.sleep(1.1)
        r = client.put("/api/users/password", json={"currentPassword": "password123", "newPassword": "newpassword1"}, cookies=other_device)
        assert r.status_code == 200, r.text
        assert client.get("/api/users/me", cookies=other_device).status_code == 401
        assert client.get("/api/users/me", cookies={"zoiko_user_token": r.cookies.get("zoiko_user_token")}).status_code == 200


class TestDataExportAndClosure:
    def test_export_contains_profile_and_tickets(self, client, db_session: Session):
        user = _make_user(db_session, email="set-export@test.com")
        db_session.commit()
        cookies = auth_user_cookie(user)
        client.post("/api/users/support/tickets", data={"category": "tech", "issue": "Page error", "description": "broken"}, cookies=cookies)
        r = client.get("/api/users/data-export", cookies=cookies)
        assert r.status_code == 200
        assert "attachment" in r.headers["content-disposition"]
        body = r.json()
        assert body["profile"]["email"] == "set-export@test.com"
        assert [t["issue"] for t in body["supportTickets"]] == ["Page error"]

    def test_host_with_live_listing_cannot_close_until_resolved(self, client, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="set-close-host@test.com")
        cookies = auth_user_cookie(host)
        check = client.get("/api/users/account/closure-check", cookies=cookies).json()
        assert check["canClose"] is False
        assert "HOST_LISTINGS" in [b["code"] for b in check["blockers"]]
        r = client.post("/api/users/account/close", json={"password": "password123"}, cookies=cookies)
        assert r.status_code == 409

        assert client.post(f"/api/users/hosting/listings/{listing_id}/archive", cookies=cookies).status_code == 200
        assert client.get("/api/users/account/closure-check", cookies=cookies).json()["canClose"] is True
        assert client.post("/api/users/account/close", json={"password": "wrong"}, cookies=cookies).status_code == 400
        r = client.post("/api/users/account/close", json={"password": "password123", "reason": "moving"}, cookies=cookies)
        assert r.status_code == 200, r.text
        assert client.get("/api/users/me", cookies=cookies).status_code == 401
        db_session.expire_all()
        closed = db_session.get(UserAccount, host.id)
        assert closed.is_active is False and closed.closed_at is not None


class TestSignupEmailConfirmation:
    """A new account gets a confirm-your-email link; opening it marks the
    email confirmed. The link can be resent from settings."""

    def _capture(self, monkeypatch) -> list[dict]:
        from app.api.routes import user_auth

        sent: list[dict] = []
        monkeypatch.setattr(
            user_auth, "send_signup_email_confirmation_email",
            lambda to, link, hours, name="": sent.append({"to": to, "link": link, "hours": hours}),
        )
        return sent

    def test_signup_sends_a_link_that_confirms_the_email(self, client, db_session: Session, monkeypatch):
        sent = self._capture(monkeypatch)
        r = client.post(
            "/api/users/register",
            json={"email": "confirm-new@test.com", "password": "password123", "fullName": "Confirm New", "phone": ""},
        )
        assert r.status_code == 201, r.text
        [mail] = sent
        assert mail["to"] == "confirm-new@test.com" and mail["hours"] == 24
        assert "/account/confirm-email?token=" in mail["link"] and "signup=1" in mail["link"]
        user = db_session.query(UserAccount).filter(UserAccount.email == "confirm-new@test.com").one()
        assert user.email_verified is False

        # The signup link is not shown as a pending email change.
        login = client.post("/api/users/login", json={"email": "confirm-new@test.com", "password": "password123"})
        assert client.get("/api/users/email-change", cookies=login.cookies).json()["pendingEmail"] is None

        token = mail["link"].split("token=")[1].split("&")[0]
        r = client.post("/api/users/email-change/confirm", json={"token": token})
        assert r.status_code == 200, r.text
        assert r.json()["email"] == "confirm-new@test.com"
        db_session.refresh(user)
        assert user.email_verified is True
        # Single use.
        assert client.post("/api/users/email-change/confirm", json={"token": token}).status_code == 400

    def test_resend_supersedes_the_old_link(self, client, db_session: Session, monkeypatch):
        sent = self._capture(monkeypatch)
        client.post(
            "/api/users/register",
            json={"email": "confirm-resend@test.com", "password": "password123", "fullName": "Resend", "phone": ""},
        )
        login = client.post("/api/users/login", json={"email": "confirm-resend@test.com", "password": "password123"})
        r = client.post("/api/users/email-confirmation/resend", cookies=login.cookies)
        assert r.status_code == 202 and r.json() == {"emailVerified": False}
        assert len(sent) == 2
        old, new = (m["link"].split("token=")[1].split("&")[0] for m in sent)
        assert client.post("/api/users/email-change/confirm", json={"token": old}).status_code == 400
        assert client.post("/api/users/email-change/confirm", json={"token": new}).status_code == 200

        # Already confirmed: nothing more is sent.
        r = client.post("/api/users/email-confirmation/resend", cookies=login.cookies)
        assert r.json() == {"emailVerified": True}
        assert len(sent) == 2
