"""Shared test fixtures for chat integration tests.

Uses an in-memory SQLite database so tests never touch the real PostgreSQL
instance.  The FastAPI ``TestClient`` talks to the app with ``get_db`` overridden
to yield a session bound to the SQLite engine.
"""

from __future__ import annotations

import datetime as dt
import typing

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session, sessionmaker

from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.admin_user import AdminUser
from app.models.user_account import UserAccount

# ---------------------------------------------------------------------------
# Monkey-patch: teach SQLite's type compiler how to handle PostgreSQL ARRAY
# columns.  In tests we only care about the schema DDL succeeding; the actual
# data stored is JSON-serialised text, which SQLite handles fine.
# ---------------------------------------------------------------------------


def _compile_array_sqlite(self, type_, **kw):  # noqa: D401
    """Render ARRAY(String) as TEXT when targeting SQLite."""
    return "TEXT"


# The DDL patch above only gets CREATE TABLE to succeed -- it says nothing about
# how values are bound/read. postgresql.ARRAY has no generic (non-psycopg) bind/
# result processor, so without this, inserting an actual Python list (e.g.
# Listing.images/amenities/tags) raises "Error binding parameter: type 'list' is
# not supported". JSON-encode on the way in, decode on the way out -- this is
# process-global but conftest.py is only ever imported by pytest against the
# in-memory SQLite engine, never by the real (Postgres-backed) app.
import json  # noqa: E402


def _array_bind_processor(self, dialect):
    def process(value):
        return None if value is None else json.dumps(value)

    return process


def _array_result_processor(self, dialect, coltype):
    def process(value):
        return None if value is None else json.loads(value)

    return process


ARRAY.bind_processor = _array_bind_processor  # type: ignore[assignment]
ARRAY.result_processor = _array_result_processor  # type: ignore[assignment]


# Also patch the DDL compiler so ``Base.metadata.create_all`` succeeds.
# The DDL compiler uses ``get_column_specification`` → ``type_compiler.process``.
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler  # noqa: E402

SQLiteTypeCompiler.visit_ARRAY = _compile_array_sqlite  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Monkey-patch: SQLite drops tzinfo on DateTime(timezone=True) columns -- it
# has no native timezone-aware storage, so a value written as e.g.
# datetime.now(timezone.utc) reads back naive. Every timestamp in this app is
# always UTC (see the `default=lambda: datetime.now(timezone.utc)` pattern on
# every model), so re-attaching UTC on the way out is safe and exactly
# mirrors what PostgreSQL actually returns. Without this, any code comparing
# a stored datetime against datetime.now(timezone.utc) (e.g.
# crud/password_reset.py's expiry check) raises "can't compare offset-naive
# and offset-aware datetimes" under the SQLite test harness even though the
# same comparison works fine against the real Postgres-backed app.
# ---------------------------------------------------------------------------
from sqlalchemy.dialects.sqlite.base import DATETIME as _SQLiteDATETIME  # noqa: E402

_original_datetime_result_processor = _SQLiteDATETIME.result_processor


def _datetime_result_processor_with_tz(self, dialect, coltype):
    base_process = _original_datetime_result_processor(self, dialect, coltype)
    if not self.timezone:
        return base_process

    def process(value):
        result = base_process(value)
        return result if result is None or result.tzinfo is not None else result.replace(tzinfo=dt.timezone.utc)

    return process


_SQLiteDATETIME.result_processor = _datetime_result_processor_with_tz  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# SQLite engine + session factory (in-memory, isolated per test)
# ---------------------------------------------------------------------------

SQLITE_URL = "sqlite:///:memory:"


@pytest.fixture()
def db_engine():
    """Yield a fresh SQLite engine for one test, then dispose it."""
    eng = create_engine(
        SQLITE_URL,
        connect_args={"check_same_thread": False},
    )
    # Enable foreign-key support for SQLite.
    @event.listens_for(eng, "connect")
    def _set_sqlite_pragma(dbapi_conn, _rec):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    # Apply the CHECK constraint that migration 0018 adds on PostgreSQL.
    # create_all() doesn't run Alembic migrations, so we do it manually.
    # SQLite uses unnamed CHECK via CREATE TABLE, but we can use this form:
    with eng.connect() as conn:
        conn.execute(text(
            "CREATE TRIGGER IF NOT EXISTS ck_chat_conversations_one_actor "
            "INSERT ON chat_conversations "
            "BEGIN "
            "  SELECT CASE "
            "    WHEN (NEW.admin_id IS NOT NULL AND NEW.user_id IS NOT NULL) "
            "      OR (NEW.admin_id IS NULL AND NEW.user_id IS NULL) "
            "    THEN RAISE(ABORT, 'Exactly one of admin_id or user_id must be set') "
            "  END; "
            "END"
        ))
        conn.commit()
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def db_session(db_engine) -> typing.Generator[Session, None, None]:
    """Yield a transactional session that rolls back after the test."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# FastAPI TestClient with ``get_db`` dependency overridden
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(db_session: Session) -> typing.Generator[TestClient, None, None]:
    """TestClient that talks to the app backed by the in-memory SQLite DB."""

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

ADMIN_COOKIE = "zoiko_admin_token"
USER_COOKIE = "zoiko_user_token"


def _make_admin(db: Session, *, email: str = "admin@test.com", role: str = "admin") -> AdminUser:
    admin = AdminUser(
        email=email,
        hashed_password=hash_password("password123"),
        full_name="Test Admin",
        role=role,
        is_active=True,
        approval_status="approved",
    )
    db.add(admin)
    db.flush()
    return admin


def _make_user(db: Session, *, email: str = "user@test.com") -> UserAccount:
    user = UserAccount(
        email=email,
        hashed_password=hash_password("password123"),
        full_name="Test User",
        phone="",
        is_active=True,
        email_verified=True,
    )
    db.add(user)
    db.flush()
    return user


def auth_admin_cookie(admin: AdminUser) -> dict[str, str]:
    token = create_access_token(admin.email, token_type="admin")
    return {ADMIN_COOKIE: token}


def auth_user_cookie(user: UserAccount) -> dict[str, str]:
    token = create_access_token(user.email, token_type="user")
    return {USER_COOKIE: token}


def deliver_all_disclosures(client, admin_cookies: dict, agreement_id: int) -> None:
    """ZR-ENG-CLR-004 AC-15: signing is blocked until every required
    disclosure is at least DELIVERED (see crud/leasing.py:_apply_signature).
    Test helper -- marks every disclosure this agreement was seeded with as
    delivered, so tests whose actual concern is something else (payment,
    effectiveness, expiry...) aren't tripped up by the unrelated gate."""
    r = client.get(f"/api/leasing/agreements/{agreement_id}/disclosures", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    for disclosure in r.json():
        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/disclosures/{disclosure['id']}/deliver", cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
