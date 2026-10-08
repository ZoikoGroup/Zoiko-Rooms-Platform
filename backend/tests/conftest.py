"""Shared test fixtures for chat integration tests.

Uses an in-memory SQLite database so tests never touch the real PostgreSQL
instance.  The FastAPI ``TestClient`` talks to the app with ``get_db`` overridden
to yield a session bound to the SQLite engine.
"""

from __future__ import annotations

import datetime as dt
import os
import typing

# Force the file mailer before app settings load, so a developer's .env with
# EMAIL_PROVIDER=smtp never makes the suite send real email.
os.environ["EMAIL_PROVIDER"] = "file"
# The app now assumes production unless told otherwise (core/config.py) --
# the suite is explicitly a development environment.
os.environ["ENVIRONMENT"] = "development"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.rate_limit import sublet_document_limiter, sublet_submit_limiter
from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.admin_user import AdminUser
from app.models.market_policy import MarketPolicyPack
from app.models.user_account import UserAccount


@pytest.fixture(autouse=True)
def _isolate_from_real_provider_credentials(monkeypatch):
    """Tests must never depend on -- or be silently changed by -- whatever
    real credentials happen to be in a developer's local backend/.env
    (Settings() loads that file unconditionally). Without this, a real
    STRIPE_SECRET_KEY flips every 'unconfigured Stripe -> simulated/
    synchronous fallback' code path this suite relies on, and worse, makes
    tests place real (if sandbox) API calls. Forced blank for every test
    regardless of what .env says; monkeypatch restores it afterward."""
    monkeypatch.setattr(settings, "stripe_secret_key", "")
    # The in-process scheduler would open real DB sessions in the background.
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(settings, "stripe_webhook_secret", "")
    monkeypatch.setattr(settings, "stripe_listing_fee_webhook_secret", "")
    # Same for Veriff: tests that need it set fake keys (tests/test_identity_veriff.py).
    # The env vars cover tests that build a fresh Settings() (which would
    # otherwise read real keys from backend/.env).
    monkeypatch.setattr(settings, "veriff_api_key", "")
    monkeypatch.setattr(settings, "veriff_shared_secret", "")
    # " " not "": on Windows an empty value unsets the variable (so .env would
    # win); Settings strips it back to "" (config.py:_strip_secret).
    monkeypatch.setenv("VERIFF_API_KEY", " ")
    monkeypatch.setenv("VERIFF_SHARED_SECRET", " ")
    # Location lookups stay offline unless a test opts in (services/location.py).
    monkeypatch.setattr(settings, "google_maps_api_key", "")
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", " ")
    monkeypatch.setattr(settings, "mapbox_access_token", "")
    monkeypatch.setenv("MAPBOX_ACCESS_TOKEN", " ")
    monkeypatch.setattr(settings, "here_api_key", "")
    monkeypatch.setenv("HERE_API_KEY", " ")
    # A deployment may run Google only (LOCATION_FALLBACK_ENABLED=false); tests
    # use the code defaults and opt into what they exercise.
    monkeypatch.setattr(settings, "location_provider", "google")
    monkeypatch.setattr(settings, "location_fallback_enabled", True)
    monkeypatch.setattr(settings, "location_fallback_providers", "mapbox,here")
    # External listing/search APIs (ZR-AI-SEARCH-001): never call them for real.
    for key in ("rentcast_api_key", "domain_api_key", "brave_search_api_key", "parallel_api_key", "web_search_provider"):
        monkeypatch.setattr(settings, key, "")


@pytest.fixture(autouse=True)
def _email_log_disabled(monkeypatch):
    """The email delivery log (services/email/message_log.py) opens its own
    session; by default that would be the real database from .env. Off for
    every test -- tests of the log itself point it at db_session."""
    from app.services.email import message_log

    monkeypatch.setattr(message_log, "_session_factory", lambda: None)


@pytest.fixture(autouse=True)
def _offline_geocoder(monkeypatch):
    """Property verification looks the address up on a map
    (services/geocoding.py). Tests must never call a real geocoder, so every
    address "resolves" at house level in the region's own country by
    default. Tests of the map check itself patch geocode_address again."""
    from app.services import geocoding

    def _found(address, city, landmark, jurisdiction_code):
        expected = geocoding.JURISDICTION_COUNTRIES.get(jurisdiction_code, "")
        return geocoding.GeocodeResult(
            geocoding.FOUND, "test", geocoding.build_address_query(address, city, landmark, jurisdiction_code),
            latitude=51.5074, longitude=-0.1278, formatted_address=f"{address}, {city}", precision="HOUSE",
            country_code=expected, expected_country_code=expected,
        )

    monkeypatch.setattr(geocoding, "geocode_address", _found)


@pytest.fixture(autouse=True)
def _legacy_payment_capabilities(request, monkeypatch):
    """ZR-PAY-CFG-001 turned off every rental money-movement path and made
    the Listing Fee and PAYMENT_RECEIPT authority fail closed. Most of this
    suite predates that and exercises those paths directly, so it opts back
    in here. Tests marked @pytest.mark.payment_boundary run with the real
    (production) defaults instead -- that's where the boundary itself is
    tested."""
    if request.node.get_closest_marker("payment_boundary"):
        return
    from app.services import policy

    for flag in ("rent_collection_enabled", "deposit_collection_enabled", "host_payouts_enabled"):
        monkeypatch.setattr(settings, flag, True)
    monkeypatch.setattr(settings, "listing_fee_fail_closed", False)
    monkeypatch.setattr(settings, "payment_receipt_authority_required", False)
    monkeypatch.setitem(policy._DEFAULTS, "payment.external_handoff_approved", lambda: True)


@pytest.fixture(autouse=True)
def _admin_review_publication(request, monkeypatch):
    """Publication is automatic by default (policy publication.requires_approval
    = False). Most of this suite predates that and drives the admin review
    flow (submit -> REVIEW -> approve -> publish), so it keeps admin review
    on. Tests marked @pytest.mark.automatic_publication use the real default."""
    if request.node.get_closest_marker("automatic_publication"):
        return
    from app.services import policy

    monkeypatch.setitem(policy._DEFAULTS, "publication.requires_approval", lambda: True)


@pytest.fixture(autouse=True)
def _reset_sublet_rate_limiters():
    """The sublet submit/document-upload limiters (app/core/rate_limit.py)
    are true module-level singletons, keyed on user.id -- but each test gets
    a brand-new in-memory SQLite DB (db_engine below), so autoincrement ids
    restart at 1 every time. Without this reset, an unrelated test earlier
    in the same pytest run can leave hits recorded against an id a later
    test's tenant happens to reuse, producing a flaky 429 that has nothing
    to do with that test's own behavior."""
    sublet_submit_limiter.reset()
    sublet_document_limiter.reset()
    yield

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

    # Every test DB needs a resolvable market policy pack -- deposit funding,
    # offer-terms creation, and sublet submission/approval all call
    # resolve_market_policy(db) and fail closed (409) with none configured.
    # Mirrors alembic/versions/0027_market_policy_packs.py's seed row (IN)
    # and 294dc9f9f823's (England, this platform's actual default
    # jurisdiction since the jurisdiction/currency default-value fix) --
    # this doesn't run Alembic migrations, so both have to be inserted
    # directly.
    with Session(bind=eng) as seed_session:
        seed_session.add(MarketPolicyPack(
            jurisdiction_code="IN",
            version=1,
            effective_from=dt.date(2026, 1, 1),
            confidence="REVIEW_REQUIRED",
            legal_source_note="Test fixture placeholder, not verified legal research.",
        ))
        seed_session.add(MarketPolicyPack(
            jurisdiction_code="England",
            version=1,
            effective_from=dt.date(2026, 1, 1),
            confidence="REVIEW_REQUIRED",
            legal_source_note="Test fixture placeholder, not verified legal research.",
            # Left at the model's own default (False) -- unlike the real
            # production seed, this shared fixture must not turn on a gate
            # every other England-jurisdiction test would then have to
            # satisfy. test_occupancy_eligibility_verification.py already
            # opts this on for its own tests by mutating this row directly.
        ))
        seed_session.commit()

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


@pytest.fixture()
def external_activated(db_session: Session) -> None:
    """Turn on the ZR-AI-SEARCH-001 market activation flags (off by default)
    for tests that exercise external discovery and provider outreach."""
    from app.models.feature_flag import FeatureFlag

    for name in ("external.search_fallback", "external.provider_outreach"):
        db_session.add(FeatureFlag(name=name, value=True, note="test", enabled_by="test"))
    db_session.flush()


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


def approve_identity_via_provider(db: Session, record):
    """Verifies an identity the only way production can: a Veriff
    "approved" decision applied to the attempt (services/identity/
    service.py:apply_result). For tests that need a verified identity as
    setup rather than testing the Veriff flow itself."""
    from app.services.identity import providers
    from app.services.identity import service as identity_service

    if record.session_state not in ("IN_PROGRESS", "PROCESSING", "ACTION_REQUIRED"):
        record.session_state = "PROCESSING"
    record.provider_code = "veriff"
    db.flush()
    return identity_service.apply_result(db, record, providers.NormalizedResult(
        provider_code="veriff", normalized_outcome="PASS", provider_decision="approved",
    ))


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


def make_room_publishable(db: Session, room) -> None:
    """Satisfies the publication gates (ZR-AUTHORITY-002 Section 2.4) for a
    test that is about something else: a verified identity for the listing
    party, a verified property and current VERIFIED listing authority."""
    from app.crud.identity_verification import get_verified_identity_for_party
    from app.models.authority_verification import AuthorityVerification
    from app.models.identity_verification import IdentityVerification
    from app.models.property import Property
    from app.models.property_location import PropertyLocationVerification
    from app.services.authority_service import valid_for_room
    from app.services.property_location_service import valid_for_property

    prop = db.get(Property, room.property_id)
    party_id = prop.owner_party_id
    if get_verified_identity_for_party(db, party_id) is None:
        db.add(IdentityVerification(party_id=party_id, document_type="passport", status="verified"))
    if valid_for_property(db, prop.id) is None:
        db.add(PropertyLocationVerification(property_id=prop.id, party_id=party_id, state="VERIFIED"))
    if valid_for_room(db, room.id) is None:
        now = dt.datetime.now(dt.timezone.utc)
        db.add(AuthorityVerification(property_id=prop.id, party_id=party_id, relationship_type="OWNER",
                                     state="VERIFIED", assurance_level="AV-1", scope_codes=["ADVERTISE", "RENT"],
                                     verified_at=now, expires_at=now + dt.timedelta(days=365)))
    db.commit()


def _make_room_owned_by(db: Session, party) -> "Room":
    """A property + room owned by `party` -- shared by the rental payment tests."""
    from app.models.property import Property
    from app.models.room import Room

    prop = Property(owner_party_id=party.id, address="1 Recipient St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.commit()
    return room


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
