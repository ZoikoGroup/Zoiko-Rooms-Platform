"""When 'Create agreement' is refused for authority / classification reasons, the
response says what is wrong in the host's words and where to fix it."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.authority_record import AuthorityRecord
from app.models.authority_verification import AuthorityVerification
from app.models.occupancy_classification import OccupancyClassification
from tests.conftest import auth_admin_cookie
from tests.test_agreement_eligibility_precheck import _accepted_offer_on_india_jurisdiction_listing

NOW = datetime.now(timezone.utc)


def _strip_standing(db: Session, offer):
    """The fixture's room starts in good standing; remove it so each test sets its own."""
    room = offer.listing.room
    db.query(AuthorityRecord).filter_by(room_id=room.id).delete()
    db.query(OccupancyClassification).filter_by(room_id=room.id).delete()
    db.flush()
    return room


def _authority(db: Session, room, *, state: str, **fields) -> AuthorityVerification:
    v = AuthorityVerification(
        property_id=room.property_id, party_id=room.property.owner_party_id, relationship_type="OWNER",
        state=state, country_code="GB-ENG", **fields,
    )
    db.add(v)
    db.flush()
    return v


def _fixes(client, db: Session, offer, admin) -> dict[str, dict]:
    r = client.get(f"/api/leasing/offers/{offer.id}/agreement-eligibility", cookies=auth_admin_cookie(admin))
    assert r.status_code == 200, r.text
    return {f["code"]: f for f in r.json()["fixes"]}


class TestAgreementFixHints:
    def test_nothing_to_fix_when_authority_and_classification_are_fine(self, client, db_session: Session):
        offer, admin = _accepted_offer_on_india_jurisdiction_listing(db_session)
        assert _fixes(client, db_session, offer, admin) == {}

    def test_never_started_points_to_the_verification_page(self, client, db_session: Session):
        offer, admin = _accepted_offer_on_india_jurisdiction_listing(db_session)
        _strip_standing(db_session, offer)
        fixes = _fixes(client, db_session, offer, admin)
        assert fixes["AUTHORITY"]["status"] == "NOT_STARTED"
        assert fixes["AUTHORITY"]["href"] == "/account/host/verification"
        assert "haven't completed authority verification" in fixes["AUTHORITY"]["message"]
        assert fixes["CLASSIFICATION"]["href"] == "/account/support"

    @pytest.mark.parametrize("state, fields, expected, phrase", [
        ("COLLECTING", {}, "NOT_SUBMITTED", "haven't submitted"),
        ("SUBMITTED", {}, "CHECKING", "still being checked"),
        ("ACTION_REQUIRED", {"reason_codes": ["REQUIREMENT_MISSING"]}, "ACTION_REQUIRED", "needs your attention"),
        ("REJECTED", {}, "REJECTED", "wasn't accepted"),
        ("REVOKED", {"revocation_reason_code": "REVOKED_BY_HOST"}, "REVOKED", "was withdrawn"),
        ("VERIFIED", {"expires_at": NOW - timedelta(days=2)}, "EXPIRED", "has expired"),
    ])
    def test_each_authority_state_gets_its_own_message(self, client, db_session: Session, state, fields, expected, phrase):
        offer, admin = _accepted_offer_on_india_jurisdiction_listing(db_session)
        room = _strip_standing(db_session, offer)
        _authority(db_session, room, state=state, **fields)
        fix = _fixes(client, db_session, offer, admin)["AUTHORITY"]
        assert fix["status"] == expected, fix
        assert phrase in fix["message"]
        assert fix["href"] == "/account/host/verification"

    def test_verified_for_other_rooms_only(self, client, db_session: Session):
        offer, admin = _accepted_offer_on_india_jurisdiction_listing(db_session)
        room = _strip_standing(db_session, offer)
        _authority(db_session, room, state="VERIFIED", expires_at=NOW + timedelta(days=200), room_scope_ids=[room.id + 999])
        assert _fixes(client, db_session, offer, admin)["AUTHORITY"]["status"] == "ROOM_NOT_COVERED"

    def test_unresolved_classification_is_reported_even_with_valid_authority(self, client, db_session: Session):
        offer, admin = _accepted_offer_on_india_jurisdiction_listing(db_session)
        db_session.query(OccupancyClassification).filter_by(room_id=offer.listing.room_id).update({"review_state": "UNKNOWN"})
        db_session.flush()
        fixes = _fixes(client, db_session, offer, admin)
        assert set(fixes) == {"CLASSIFICATION"}

    def test_the_refusal_itself_carries_the_fixes(self, client, db_session: Session):
        from app.crud.leasing import create_agreement
        from fastapi import HTTPException

        offer, admin = _accepted_offer_on_india_jurisdiction_listing(db_session)
        _strip_standing(db_session, offer)
        with pytest.raises(HTTPException) as err:
            create_agreement(db_session, offer, admin)
        detail = err.value.detail
        assert detail["message"] == "Not eligible to create an agreement"
        assert any("No verified, unexpired authority record" in r for r in detail["reasons"])
        assert {f["code"] for f in detail["fixes"]} == {"AUTHORITY", "CLASSIFICATION"}
