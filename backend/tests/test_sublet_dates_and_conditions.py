"""ZR-SUB-003 gaps closed on request: proposed sublet dates (previously
entirely absent from the model), structured approval conditions, structured
info-request document types/due date, the authority-confirmation checkbox,
and the sublet.* jurisdiction config keys."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.crud import sublet as sublet_crud
from app.models.market_policy import MarketPolicyPack
from app.models.user_account import UserAccount
from sqlalchemy import select
from tests.conftest import auth_user_cookie
from tests.test_sublet_arrangement_classification import _make_active_tenancy


def _host_for(db: Session, suffix: str) -> UserAccount:
    return db.scalar(select(UserAccount).where(UserAccount.email == f"host-{suffix}@test.com"))


class TestProposedDates:
    def test_a_valid_date_range_is_accepted_and_returned(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="dates1")
        start = date.today() + timedelta(days=5)
        end = date.today() + timedelta(days=35)

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy_id}/sublet-request",
            json={
                "occupancyId": occupancy_id, "proposedRenterPartyId": proposed_party_id,
                "proposedStartDate": start.isoformat(), "proposedEndDate": end.isoformat(),
            },
            cookies=auth_user_cookie(tenant_user),
        )
        assert r.status_code == 201, r.text
        assert r.json()["proposedStartDate"] == start.isoformat()
        assert r.json()["proposedEndDate"] == end.isoformat()

    def test_start_must_be_before_end(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="dates2")
        start = date.today() + timedelta(days=35)
        end = date.today() + timedelta(days=5)

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy_id}/sublet-request",
            json={
                "occupancyId": occupancy_id, "proposedRenterPartyId": proposed_party_id,
                "proposedStartDate": start.isoformat(), "proposedEndDate": end.isoformat(),
            },
            cookies=auth_user_cookie(tenant_user),
        )
        assert r.status_code == 400

    def test_end_date_cannot_exceed_the_tenancy_end(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="dates3")
        # _make_active_tenancy gives a 60-day-remaining occupancy.
        start = date.today() + timedelta(days=5)
        end = date.today() + timedelta(days=200)

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy_id}/sublet-request",
            json={
                "occupancyId": occupancy_id, "proposedRenterPartyId": proposed_party_id,
                "proposedStartDate": start.isoformat(), "proposedEndDate": end.isoformat(),
            },
            cookies=auth_user_cookie(tenant_user),
        )
        assert r.status_code == 400

    def test_dates_are_optional(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="dates4")
        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy_id}/sublet-request",
            json={"occupancyId": occupancy_id, "proposedRenterPartyId": proposed_party_id},
            cookies=auth_user_cookie(tenant_user),
        )
        assert r.status_code == 201, r.text
        assert r.json()["proposedStartDate"] is None
        assert r.json()["proposedEndDate"] is None

    def test_max_duration_config_is_enforced_when_set(self, client, db_session: Session):
        from app.models.occupancy import Occupancy

        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="dates5")
        # _make_active_tenancy's Property never sets jurisdiction_code, which
        # defaults to "England", not "IN" -- resolve_market_policy resolves
        # against the room's real property jurisdiction, so the mutated row
        # must match that, not the party's own (unrelated) jurisdiction field.
        policy = db_session.query(MarketPolicyPack).filter_by(jurisdiction_code="England").one()
        policy.sublet_max_duration_months = 1
        # _make_active_tenancy's own 60-day-remaining window can never itself
        # exceed a 1-month cap (2 whole months needs ~60-62 days minimum, right
        # at that boundary) -- widen it here so a genuinely-over-cap range is
        # constructible without also tripping the separate "within the
        # tenancy" check.
        occupancy = db_session.get(Occupancy, occupancy_id)
        occupancy.expected_end_date = date.today() + timedelta(days=200)
        db_session.commit()

        start = date.today() + timedelta(days=1)
        end = date.today() + timedelta(days=100)
        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy_id}/sublet-request",
            json={
                "occupancyId": occupancy_id, "proposedRenterPartyId": proposed_party_id,
                "proposedStartDate": start.isoformat(), "proposedEndDate": end.isoformat(),
            },
            cookies=auth_user_cookie(tenant_user),
        )
        assert r.status_code == 400, r.text


class TestStructuredApprovalConditions:
    def test_condition_list_and_authority_confirmation_are_recorded(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="cond1")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        host_user = _host_for(db_session, "cond1")

        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve",
            json={
                "conditionList": ["No pets", "No subletting further"], "authorityConfirmed": True,
                "stepUpPassword": "password123",
            },
            cookies=auth_user_cookie(host_user),
        )
        assert r.status_code == 200, r.text
        assert r.json()["approvalConditionList"] == ["No pets", "No subletting further"]
        assert r.json()["approvedWithAuthorityConfirmation"] is True

    def test_approval_still_succeeds_without_confirmation_backward_compat(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="cond2")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        host_user = _host_for(db_session, "cond2")

        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve",
            json={"stepUpPassword": "password123"}, cookies=auth_user_cookie(host_user),
        )
        assert r.status_code == 200, r.text
        assert r.json()["approvedWithAuthorityConfirmation"] is False


class TestStructuredInfoRequest:
    def test_document_types_and_due_date_are_recorded(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="info1")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        host_user = _host_for(db_session, "info1")

        due = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/request-info",
            json={"notes": "Please provide an employer reference.", "requestedDocumentTypes": ["Employer reference"], "dueAt": due},
            cookies=auth_user_cookie(host_user),
        )
        assert r.status_code == 200, r.text
        assert r.json()["infoRequestedDocumentTypes"] == ["Employer reference"]
        assert r.json()["infoRequestDueAt"] is not None

    def test_due_date_must_be_in_the_future(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="info2")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        host_user = _host_for(db_session, "info2")

        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/request-info",
            json={"notes": "Please provide a reference.", "dueAt": past},
            cookies=auth_user_cookie(host_user),
        )
        assert r.status_code == 400


class TestSubletTerminology:
    def test_default_ui_term_is_sublet(self, client, db_session: Session):
        tenant_user, _proposed_user, _proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="term1")
        r = client.get(f"/api/users/rentals/occupancies/{occupancy_id}/sublet-terminology", cookies=auth_user_cookie(tenant_user))
        assert r.status_code == 200, r.text
        assert r.json()["uiTerm"] == "sublet"

    def test_a_jurisdiction_configured_term_is_returned(self, client, db_session: Session):
        tenant_user, _proposed_user, _proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="term2")
        policy = db_session.query(MarketPolicyPack).filter_by(jurisdiction_code="England").one()
        policy.sublet_ui_term = "sublease"
        db_session.commit()

        r = client.get(f"/api/users/rentals/occupancies/{occupancy_id}/sublet-terminology", cookies=auth_user_cookie(tenant_user))
        assert r.status_code == 200, r.text
        assert r.json()["uiTerm"] == "sublease"
