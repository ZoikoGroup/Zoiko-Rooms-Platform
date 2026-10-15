"""Termination decided between host and renter(s), and tenancies ending on
their own -- crud/termination_responses.py, crud/occupancy_lifecycle.py.
No admin is involved in any of these paths."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.crud.occupancy_lifecycle import end_due_occupancies
from app.crud.termination_responses import apply_due_responses
from app.models.guest import Guest
from app.models.leasing import Agreement
from app.models.occupancy import Occupancy, OccupancyCoTenant
from app.models.occupancy_activation import OccupancyHandoverEvent
from app.models.termination_case import TerminationCase
from tests.conftest import _make_user, auth_user_cookie
from tests.test_host_tenancy import _setup


def _occupancy(db_session: Session, agreement_id: int) -> Occupancy:
    agreement = db_session.get(Agreement, agreement_id)
    return db_session.query(Occupancy).filter(Occupancy.offer_id == agreement.offer_id).one()


def _host_ground(client, host, occ_id: int, days: int = 40, cause: str = "RENTER_BREACH"):
    return client.post(
        f"/api/users/hosting/occupancies/{occ_id}/termination-cases",
        json={"causeCode": cause, "notes": "Repeated breach", "proposedEffectiveDate": (date.today() + timedelta(days=days)).isoformat()},
        cookies=auth_user_cookie(host),
    )


class TestContestedGroundsBetweenParties:
    def test_ground_needs_minimum_notice_then_renter_accepts(self, client, db_session: Session):
        agreement_id, host, renter = _setup(client, db_session, "pa")
        occ = _occupancy(db_session, agreement_id)

        assert _host_ground(client, host, occ.id, days=5).status_code == 400  # under the 28-day minimum
        r = _host_ground(client, host, occ.id)
        assert r.status_code == 201, r.text
        case = r.json()
        assert case["status"] == "AWAITING_RESPONSE" and case["proposer"] == "HOST"
        assert case["pendingResponders"] == [occ.guest_id] and case["responseDueAt"]

        r = client.post(f"/api/users/rentals/termination-cases/{case['id']}/respond", json={"response": "ACCEPT"}, cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "EFFECTIVE_DATE_SET"
        assert r.json()["effectiveTerminationDate"] == case["earliestEffectiveDate"]

    def test_counter_starts_a_new_round_for_the_proposer(self, client, db_session: Session):
        agreement_id, host, renter = _setup(client, db_session, "pb")
        occ = _occupancy(db_session, agreement_id)
        case = _host_ground(client, host, occ.id).json()
        later = (date.today() + timedelta(days=60)).isoformat()

        r = client.post(
            f"/api/users/rentals/termination-cases/{case['id']}/respond",
            json={"response": "COUNTER", "proposedDate": later, "note": "Need two months"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        assert r.json()["responseRound"] == 2 and r.json()["pendingResponders"] == ["HOST"]

        # The renter can't answer their own counter; the host accepts it.
        assert client.post(f"/api/users/rentals/termination-cases/{case['id']}/respond", json={"response": "ACCEPT"}, cookies=auth_user_cookie(renter)).status_code == 409
        r = client.post(f"/api/users/hosting/termination-cases/{case['id']}/respond", json={"response": "ACCEPT"}, cookies=auth_user_cookie(host))
        assert r.json()["status"] == "EFFECTIVE_DATE_SET" and r.json()["effectiveTerminationDate"] == later

    def test_contested_case_is_settled_by_an_outside_order(self, client, db_session: Session):
        agreement_id, host, renter = _setup(client, db_session, "pc")
        occ = _occupancy(db_session, agreement_id)
        case = _host_ground(client, host, occ.id).json()

        r = client.post(f"/api/users/rentals/termination-cases/{case['id']}/respond", json={"response": "CONTEST"}, cookies=auth_user_cookie(renter))
        assert r.json()["status"] == "CONTESTED"

        ordered = (date.today() + timedelta(days=90)).isoformat()
        r = client.post(
            f"/api/users/hosting/termination-cases/{case['id']}/external-decision",
            json={"outcome": "ENDS_ON_DATE", "orderRef": "Tribunal 2026/1234", "effectiveDate": ordered}, cookies=auth_user_cookie(host),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "EFFECTIVE_DATE_SET" and r.json()["effectiveTerminationDate"] == ordered

    def test_outside_order_can_keep_the_tenancy(self, client, db_session: Session):
        agreement_id, host, renter = _setup(client, db_session, "pd")
        occ = _occupancy(db_session, agreement_id)
        case = _host_ground(client, host, occ.id).json()
        client.post(f"/api/users/rentals/termination-cases/{case['id']}/respond", json={"response": "CONTEST"}, cookies=auth_user_cookie(renter))
        r = client.post(
            f"/api/users/rentals/termination-cases/{case['id']}/external-decision",
            json={"outcome": "TENANCY_CONTINUES", "orderRef": "Court ref 88"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "REJECTED_PATHWAY"
        # A new case can be opened afterwards.
        assert _host_ground(client, host, occ.id).status_code == 201

    def test_silence_counts_as_acceptance_for_a_ground(self, client, db_session: Session):
        agreement_id, host, _renter = _setup(client, db_session, "pe")
        occ = _occupancy(db_session, agreement_id)
        case_id = _host_ground(client, host, occ.id).json()["id"]
        case = db_session.get(TerminationCase, case_id)
        case.response_due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()

        assert apply_due_responses(db_session) == 1
        db_session.refresh(case)
        assert case.status == "EFFECTIVE_DATE_SET"
        assert case.decisions[-1].decision_basis == "DEEMED_ACCEPTED"
        assert apply_due_responses(db_session) == 0  # idempotent

    def test_host_can_withdraw_their_own_case(self, client, db_session: Session):
        agreement_id, host, renter = _setup(client, db_session, "pf")
        occ = _occupancy(db_session, agreement_id)
        case_id = _host_ground(client, host, occ.id).json()["id"]
        assert client.post(f"/api/users/hosting/termination-cases/{case_id}/withdraw", cookies=auth_user_cookie(host)).json()["status"] == "WITHDRAWN"


class TestJointTenancy:
    def _add_co_tenant(self, db_session: Session, occ: Occupancy, suffix: str):
        user = _make_user(db_session, email=f"cotenant-{suffix}@test.com")
        guest = Guest(id=f"G-CO-{suffix}", name="Co Tenant", email=f"cotenant-{suffix}@test.com", joined_at=date.today(), user_account_id=user.id)
        db_session.add(guest)
        db_session.flush()
        db_session.add(OccupancyCoTenant(occupancy_id=occ.id, guest_id=guest.id))
        db_session.commit()
        return user, guest

    def test_every_tenant_and_the_host_must_agree_a_surrender(self, client, db_session: Session):
        agreement_id, host, renter = _setup(client, db_session, "pg")
        occ = _occupancy(db_session, agreement_id)
        co_user, co_guest = self._add_co_tenant(db_session, occ, "pg")

        r = client.post(
            f"/api/users/rentals/occupancies/{occ.id}/termination-cases",
            json={"causeCode": "MUTUAL_SURRENDER", "proposedEffectiveDate": (date.today() + timedelta(days=30)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        case = r.json()
        assert case["status"] == "AWAITING_RESPONSE"
        assert sorted(case["pendingResponders"]) == sorted(["HOST", co_guest.id])

        r = client.post(f"/api/users/hosting/termination-cases/{case['id']}/respond", json={"response": "ACCEPT"}, cookies=auth_user_cookie(host))
        assert r.json()["status"] == "AWAITING_RESPONSE" and r.json()["pendingResponders"] == [co_guest.id]
        r = client.post(f"/api/users/rentals/termination-cases/{case['id']}/respond", json={"response": "ACCEPT"}, cookies=auth_user_cookie(co_user))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "EFFECTIVE_DATE_SET"

    def test_a_surrender_lapses_on_silence(self, client, db_session: Session):
        agreement_id, _host, renter = _setup(client, db_session, "ph")
        occ = _occupancy(db_session, agreement_id)
        self._add_co_tenant(db_session, occ, "ph")
        case_id = client.post(
            f"/api/users/rentals/occupancies/{occ.id}/termination-cases",
            json={"causeCode": "MUTUAL_SURRENDER", "proposedEffectiveDate": (date.today() + timedelta(days=30)).isoformat()},
            cookies=auth_user_cookie(renter),
        ).json()["id"]
        case = db_session.get(TerminationCase, case_id)
        case.response_due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()
        apply_due_responses(db_session)
        db_session.refresh(case)
        assert case.status == "SURRENDER_DECLINED"


class TestTenancyEndsOnItsOwn:
    def test_ends_when_date_reached_and_host_confirms_move_out(self, client, db_session: Session):
        agreement_id, host, renter = _setup(client, db_session, "pi")
        occ = _occupancy(db_session, agreement_id)
        case = _host_ground(client, host, occ.id).json()
        client.post(f"/api/users/rentals/termination-cases/{case['id']}/respond", json={"response": "ACCEPT"}, cookies=auth_user_cookie(renter))
        # Pretend the agreed date has arrived.
        row = db_session.get(TerminationCase, case["id"])
        row.effective_termination_date = date.today()
        db_session.commit()

        client.post(f"/api/users/rentals/occupancies/{occ.id}/move-out/notice", json={}, cookies=auth_user_cookie(renter))
        client.post(f"/api/users/rentals/occupancies/{occ.id}/move-out/ready", json={}, cookies=auth_user_cookie(renter))
        r = client.post(f"/api/users/hosting/occupancies/{occ.id}/move-out/confirm", json={}, cookies=auth_user_cookie(host))
        assert r.status_code == 200, r.text

        db_session.expire_all()
        assert db_session.get(Occupancy, occ.id).status == "ENDED"
        assert db_session.get(TerminationCase, case["id"]).status == "TERMINATED"

    def test_natural_expiry_ends_after_renter_leaves_and_host_stays_silent(self, client, db_session: Session):
        agreement_id, _host, renter = _setup(client, db_session, "pj")
        occ = _occupancy(db_session, agreement_id)
        occ.expected_end_date = date.today() - timedelta(days=1)
        db_session.commit()
        client.post(f"/api/users/rentals/occupancies/{occ.id}/move-out/notice", json={}, cookies=auth_user_cookie(renter))
        client.post(f"/api/users/rentals/occupancies/{occ.id}/move-out/ready", json={}, cookies=auth_user_cookie(renter))

        assert end_due_occupancies(db_session) == 0  # host still has time to confirm
        ready = db_session.query(OccupancyHandoverEvent).filter_by(occupancy_id=occ.id, event_type="MOVE_OUT_READY").one()
        ready.created_at = datetime.now(timezone.utc) - timedelta(days=8)
        db_session.commit()
        assert end_due_occupancies(db_session) == 1
        db_session.expire_all()
        assert db_session.get(Occupancy, occ.id).status == "ENDED"

    def test_a_renter_still_in_the_room_stays_a_tenant(self, client, db_session: Session):
        agreement_id, _host, _renter = _setup(client, db_session, "pk")
        occ = _occupancy(db_session, agreement_id)
        occ.expected_end_date = date.today() - timedelta(days=30)
        db_session.commit()
        assert end_due_occupancies(db_session) == 0
        db_session.expire_all()
        assert db_session.get(Occupancy, occ.id).status == "ACTIVE"
