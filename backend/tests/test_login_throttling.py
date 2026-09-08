"""Confirms login throttling actually locks an account out after repeated
failed attempts, for both the admin and user login endpoints, and that a
successful login resets the counter."""

from __future__ import annotations

from tests.conftest import _make_admin, _make_user


class TestAdminLoginThrottling:
    def test_five_failed_attempts_locks_the_account(self, client, db_session):
        _make_admin(db_session, email="throttle-admin@test.com")
        db_session.commit()

        for _ in range(5):
            r = client.post(
                "/api/auth/login",
                json={"email": "throttle-admin@test.com", "password": "wrong-password"},
            )
            assert r.status_code == 401

        # A 6th attempt, even with the CORRECT password, must now be rejected
        # as locked rather than re-checked against the hash.
        r = client.post(
            "/api/auth/login",
            json={"email": "throttle-admin@test.com", "password": "password123"},
        )
        assert r.status_code == 429

    def test_successful_login_resets_the_counter(self, client, db_session):
        _make_admin(db_session, email="reset-admin@test.com")
        db_session.commit()

        for _ in range(3):
            r = client.post(
                "/api/auth/login",
                json={"email": "reset-admin@test.com", "password": "wrong-password"},
            )
            assert r.status_code == 401

        r = client.post(
            "/api/auth/login",
            json={"email": "reset-admin@test.com", "password": "password123"},
        )
        assert r.status_code == 200

        # Fresh failures after a successful login should not immediately lock --
        # proves the counter actually reset, not just that 3 < 5.
        for _ in range(4):
            r = client.post(
                "/api/auth/login",
                json={"email": "reset-admin@test.com", "password": "wrong-password"},
            )
            assert r.status_code == 401


class TestUserLoginThrottling:
    def test_five_failed_attempts_locks_the_account(self, client, db_session):
        _make_user(db_session, email="throttle-user@test.com")
        db_session.commit()

        for _ in range(5):
            r = client.post(
                "/api/users/login",
                json={"email": "throttle-user@test.com", "password": "wrong-password"},
            )
            assert r.status_code == 401

        r = client.post(
            "/api/users/login",
            json={"email": "throttle-user@test.com", "password": "password123"},
        )
        assert r.status_code == 429

    def test_unknown_email_never_locks_or_leaks_existence(self, client):
        """A nonexistent email must behave identically to a wrong password --
        never 429s, since there's no account to lock, and always the same
        generic 401 either way."""
        for _ in range(6):
            r = client.post(
                "/api/users/login",
                json={"email": "no-such-user@test.com", "password": "whatever123"},
            )
            assert r.status_code == 401
