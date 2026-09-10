"""Unit tests for the USER authentication CRUD layer (app/crud/user.py,
app/crud/password_reset.py) -- previously 33%/38% covered and, per a grep
across the whole suite, never exercised at all: no test called create_user,
authenticate_user, or reset_password_with_token anywhere."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.crud import password_reset as reset_crud
from app.crud import user as user_crud
from app.models.password_reset_token import PasswordResetToken
from app.models.user_account import UserAccount


class TestCreateUser:
    def test_creates_user_and_auto_provisions_a_renter_party(self, db_session: Session):
        user = user_crud.create_user(db_session, "new@test.com", "hunter2", "New User", "555-0100")

        assert user.id is not None
        assert user.email == "new@test.com"
        assert user.full_name == "New User"
        assert user.phone == "555-0100"
        assert user.is_active is True
        assert user.email_verified is False
        assert user.party_id is not None
        assert verify_password("hunter2", user.hashed_password)

    def test_raises_value_error_for_a_duplicate_email(self, db_session: Session):
        user_crud.create_user(db_session, "dup@test.com", "pw", "First")
        with pytest.raises(ValueError):
            user_crud.create_user(db_session, "dup@test.com", "pw2", "Second")


class TestAuthenticateUser:
    def test_correct_credentials_return_the_user(self, db_session: Session):
        user_crud.create_user(db_session, "auth@test.com", "correct-pw", "Auth Test")
        result = user_crud.authenticate_user(db_session, "auth@test.com", "correct-pw")
        assert result is not None
        assert result.email == "auth@test.com"

    def test_wrong_password_returns_none(self, db_session: Session):
        user_crud.create_user(db_session, "auth2@test.com", "correct-pw", "Auth Test 2")
        assert user_crud.authenticate_user(db_session, "auth2@test.com", "wrong-pw") is None

    def test_unknown_email_returns_none(self, db_session: Session):
        assert user_crud.authenticate_user(db_session, "nobody@test.com", "whatever") is None


class TestUserProfileMutations:
    def test_update_user_profile_changes_name_and_phone(self, db_session: Session):
        user = user_crud.create_user(db_session, "profile@test.com", "pw", "Old Name", "111")
        updated = user_crud.update_user_profile(db_session, user, "New Name", "222")
        assert updated.full_name == "New Name"
        assert updated.phone == "222"

    def test_update_user_password_changes_the_hash(self, db_session: Session):
        user = user_crud.create_user(db_session, "pwchange@test.com", "old-pw", "PW Test")
        old_hash = user.hashed_password
        user_crud.update_user_password(db_session, user, "new-pw")
        assert user.hashed_password != old_hash
        assert verify_password("new-pw", user.hashed_password)
        assert not verify_password("old-pw", user.hashed_password)

    def test_mark_email_verified_sets_the_flag(self, db_session: Session):
        user = user_crud.create_user(db_session, "verify@test.com", "pw", "Verify Test")
        assert user.email_verified is False
        user_crud.mark_email_verified(db_session, user)
        assert user.email_verified is True

    def test_deactivate_user_sets_is_active_false(self, db_session: Session):
        user = user_crud.create_user(db_session, "deactivate@test.com", "pw", "Deactivate Test")
        user_crud.deactivate_user(db_session, user)
        assert user.is_active is False


class TestGetUserByPartyId:
    def test_returns_none_for_a_null_party_id(self, db_session: Session):
        assert user_crud.get_user_by_party_id(db_session, None) is None

    def test_returns_none_for_a_party_with_no_linked_user(self, db_session: Session):
        assert user_crud.get_user_by_party_id(db_session, 999999) is None

    def test_returns_the_linked_user(self, db_session: Session):
        user = user_crud.create_user(db_session, "byparty@test.com", "pw", "By Party")
        found = user_crud.get_user_by_party_id(db_session, user.party_id)
        assert found is not None
        assert found.id == user.id


class TestPasswordReset:
    def test_create_reset_token_returns_a_raw_token_and_persists_only_its_hash(self, db_session: Session):
        user = user_crud.create_user(db_session, "reset@test.com", "old-pw", "Reset Test")
        raw_token = reset_crud.create_reset_token(db_session, user)

        assert raw_token
        record = db_session.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).one()
        assert record.token_hash != raw_token  # never stores the raw token

    def test_reset_password_with_token_changes_password_and_stamps_password_changed_at(self, db_session: Session):
        user = user_crud.create_user(db_session, "reset2@test.com", "old-pw", "Reset Test 2")
        raw_token = reset_crud.create_reset_token(db_session, user)
        assert user.password_changed_at is None

        ok = reset_crud.reset_password_with_token(db_session, raw_token, "brand-new-pw")
        assert ok is True
        db_session.refresh(user)
        assert verify_password("brand-new-pw", user.hashed_password)
        assert user.password_changed_at is not None

    def test_token_is_single_use(self, db_session: Session):
        user = user_crud.create_user(db_session, "reset3@test.com", "old-pw", "Reset Test 3")
        raw_token = reset_crud.create_reset_token(db_session, user)

        assert reset_crud.reset_password_with_token(db_session, raw_token, "first-new-pw") is True
        assert reset_crud.reset_password_with_token(db_session, raw_token, "second-new-pw") is False

    def test_expired_token_is_rejected(self, db_session: Session):
        user = user_crud.create_user(db_session, "reset4@test.com", "old-pw", "Reset Test 4")
        raw_token = reset_crud.create_reset_token(db_session, user)
        # Force the just-created token into the past.
        record = db_session.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).one()
        record.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()

        assert reset_crud.reset_password_with_token(db_session, raw_token, "new-pw") is False

    def test_garbage_token_is_rejected(self, db_session: Session):
        assert reset_crud.reset_password_with_token(db_session, "not-a-real-token", "new-pw") is False

    def test_deactivated_users_token_is_rejected(self, db_session: Session):
        user = user_crud.create_user(db_session, "reset5@test.com", "old-pw", "Reset Test 5")
        raw_token = reset_crud.create_reset_token(db_session, user)
        user_crud.deactivate_user(db_session, user)

        assert reset_crud.reset_password_with_token(db_session, raw_token, "new-pw") is False
