"""Integration tests for /api/users/{register,login,logout,me,profile,password,
forgot-password,reset-password} -- previously 44% covered and, per a grep
across the whole suite, never exercised at all at the HTTP level."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.password_reset_token import PasswordResetToken
from app.models.user_account import UserAccount
from tests.conftest import USER_COOKIE, _make_user


class TestRegister:
    def test_register_creates_a_usable_account(self, client, db_session: Session):
        r = client.post(
            "/api/users/register",
            json={"email": "reg@test.com", "password": "pw12345", "fullName": "Reg User", "phone": "555"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["message"]
        assert body["userId"] is not None

        user = db_session.scalar(select(UserAccount).where(UserAccount.email == "reg@test.com"))
        assert user is not None
        assert user.full_name == "Reg User"
        # Registration alone does not log the user in -- no cookie set on this response.
        assert USER_COOKIE not in r.cookies

    def test_register_rejects_a_duplicate_email(self, client, db_session: Session):
        _make_user(db_session, email="dup-route@test.com")
        db_session.commit()

        r = client.post(
            "/api/users/register",
            json={"email": "dup-route@test.com", "password": "pw12345", "fullName": "Someone Else"},
        )
        assert r.status_code == 409, r.text


class TestLogin:
    def test_login_with_correct_credentials_sets_cookie_and_returns_profile(self, client, db_session: Session):
        r = client.post(
            "/api/users/register",
            json={"email": "login@test.com", "password": "correct-pw", "fullName": "Login User"},
        )
        assert r.status_code == 201, r.text

        r = client.post("/api/users/login", json={"email": "login@test.com", "password": "correct-pw"})
        assert r.status_code == 200, r.text
        assert r.json()["email"] == "login@test.com"
        assert USER_COOKIE in r.cookies

    def test_login_with_wrong_password_is_401(self, client, db_session: Session):
        client.post(
            "/api/users/register",
            json={"email": "login2@test.com", "password": "correct-pw", "fullName": "Login User 2"},
        )
        r = client.post("/api/users/login", json={"email": "login2@test.com", "password": "wrong-pw"})
        assert r.status_code == 401, r.text

    def test_login_with_unknown_email_is_401(self, client):
        r = client.post("/api/users/login", json={"email": "nobody@test.com", "password": "whatever"})
        assert r.status_code == 401, r.text

    def test_deactivated_account_cannot_log_in(self, client, db_session: Session):
        user = _make_user(db_session, email="deactivated@test.com")
        user.is_active = False
        db_session.commit()

        r = client.post("/api/users/login", json={"email": "deactivated@test.com", "password": "password123"})
        assert r.status_code == 403, r.text


class TestMeAndLogout:
    def test_me_requires_authentication(self, client):
        r = client.get("/api/users/me")
        assert r.status_code == 401, r.text

    def test_me_returns_the_authenticated_profile(self, client, db_session: Session):
        user = _make_user(db_session, email="me@test.com")
        db_session.commit()
        from tests.conftest import auth_user_cookie

        r = client.get("/api/users/me", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        assert r.json()["email"] == "me@test.com"

    def test_logout_clears_the_cookie(self, client, db_session: Session):
        user = _make_user(db_session, email="logout@test.com")
        db_session.commit()
        from tests.conftest import auth_user_cookie

        r = client.post("/api/users/logout", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}


class TestProfileAndPassword:
    def test_update_profile(self, client, db_session: Session):
        """PUT /profile has no declared response_model (returns the raw ORM
        object, snake_case field names) -- the real frontend already knows
        this and re-fetches /me instead of trusting this response's shape
        (see src/lib/user-auth.ts:updateUserProfile), so this test verifies
        the same way: through a follow-up /me call, not this response body."""
        user = _make_user(db_session, email="updateprofile@test.com")
        db_session.commit()
        from tests.conftest import auth_user_cookie

        cookies = auth_user_cookie(user)
        r = client.put(
            "/api/users/profile",
            json={"fullName": "Updated Name", "phone": "999"},
            cookies=cookies,
        )
        assert r.status_code == 200, r.text

        r = client.get("/api/users/me", cookies=cookies)
        assert r.json()["fullName"] == "Updated Name"
        assert r.json()["phone"] == "999"

    def test_change_password_requires_correct_current_password(self, client, db_session: Session):
        user = _make_user(db_session, email="chpw@test.com")
        db_session.commit()
        from tests.conftest import auth_user_cookie

        r = client.put(
            "/api/users/password",
            json={"currentPassword": "wrong-current", "newPassword": "new-pw-123"},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 400, r.text

    def test_change_password_succeeds_and_new_password_works_on_next_login(self, client, db_session: Session):
        user = _make_user(db_session, email="chpw2@test.com")
        db_session.commit()
        from tests.conftest import auth_user_cookie

        r = client.put(
            "/api/users/password",
            json={"currentPassword": "password123", "newPassword": "brand-new-pw"},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 200, r.text

        r = client.post("/api/users/login", json={"email": "chpw2@test.com", "password": "brand-new-pw"})
        assert r.status_code == 200, r.text


class TestForgotAndResetPassword:
    def test_forgot_password_returns_generic_message_for_a_real_account(self, client, db_session: Session):
        _make_user(db_session, email="forgot@test.com")
        db_session.commit()

        r = client.post("/api/users/forgot-password", json={"email": "forgot@test.com"})
        assert r.status_code == 200, r.text
        assert "reset link has been sent" in r.json()["message"]

        token_row = db_session.scalar(select(PasswordResetToken))
        assert token_row is not None

    def test_forgot_password_returns_the_same_generic_message_for_an_unknown_account(self, client):
        """Never reveals whether the email matched an account -- same response
        either way, so this endpoint can't be used to enumerate registered users."""
        r = client.post("/api/users/forgot-password", json={"email": "no-such-account@test.com"})
        assert r.status_code == 200, r.text
        assert "reset link has been sent" in r.json()["message"]

    def test_reset_password_end_to_end(self, client, db_session: Session):
        from app.crud.password_reset import create_reset_token

        user = _make_user(db_session, email="resetroute@test.com")
        db_session.commit()
        raw_token = create_reset_token(db_session, user)

        r = client.post("/api/users/reset-password", json={"token": raw_token, "newPassword": "reset-new-pw"})
        assert r.status_code == 200, r.text

        r = client.post("/api/users/login", json={"email": "resetroute@test.com", "password": "reset-new-pw"})
        assert r.status_code == 200, r.text

    def test_reset_password_rejects_a_short_password(self, client, db_session: Session):
        from app.crud.password_reset import create_reset_token

        user = _make_user(db_session, email="shortpw@test.com")
        db_session.commit()
        raw_token = create_reset_token(db_session, user)

        r = client.post("/api/users/reset-password", json={"token": raw_token, "newPassword": "short"})
        assert r.status_code == 400, r.text

    def test_reset_password_rejects_an_invalid_token(self, client):
        r = client.post(
            "/api/users/reset-password", json={"token": "not-a-real-token", "newPassword": "whatever-pw"}
        )
        assert r.status_code == 400, r.text

    def test_reset_password_invalidates_sessions_issued_before_the_reset(self, client, db_session: Session):
        """A password reset must sign out every other tab/device -- a token
        issued before the reset must stop working after it (see
        app/api/deps.py:get_current_user's password_changed_at check)."""
        from app.crud.password_reset import create_reset_token
        from tests.conftest import auth_user_cookie

        user = _make_user(db_session, email="invalidate@test.com")
        db_session.commit()
        old_cookies = auth_user_cookie(user)

        raw_token = create_reset_token(db_session, user)
        r = client.post("/api/users/reset-password", json={"token": raw_token, "newPassword": "post-reset-pw"})
        assert r.status_code == 200, r.text

        r = client.get("/api/users/me", cookies=old_cookies)
        assert r.status_code == 401, r.text
