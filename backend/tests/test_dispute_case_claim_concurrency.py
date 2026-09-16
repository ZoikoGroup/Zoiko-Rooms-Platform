"""Integration tests for ZR-ENG-CLR-010 AC-39 optimistic concurrency,
extended beyond DisputeResolutionHold (test_dispute_hold_concurrency.py)
to DisputeResolutionClaim/DisputeResolutionCase, DisputeSettlement and
DisputeExternalProceeding -- each now carries its own `version` column
(SQLAlchemy version_id_col, app/models/dispute.py,
app/models/dispute_settlement.py, app/models/dispute_external_proceeding.py).

Unlike the financial-hold routes (which convert StaleDataError to a 409
locally via crud/disputes.py:_commit_hold_or_stale_conflict), these four
objects have no per-call-site wrapper -- they rely on the new global
app/main.py:_stale_data_error_handler to convert a StaleDataError raised
anywhere into a clean 409 instead of an unhandled 500.

Covers: a claim decided by two competing "sessions" (same technique as
test_dispute_hold_concurrency.py -- two SQLAlchemy Sessions sharing one
connection) raises StaleDataError at the ORM layer for the stale one, and
going through the actual HTTP route surfaces that as a 409 via the global
handler, not a 500; the winning decision's version increments; an ordinary
single-writer request is completely unaffected."""

from __future__ import annotations

from sqlalchemy.orm import Session as SASession
from sqlalchemy.orm.exc import StaleDataError

from app.crud import disputes as crud
from app.db.session import get_db
from app.main import app
from app.models.dispute import DisputeResolutionClaim
from app.schemas.disputes import DisputeClaimDecide
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


def _open_platform_fee_case(client, renter_cookies, occupancy_id: int) -> tuple[int, int]:
    r = client.post(
        "/api/users/rentals/disputes",
        json={"occupancyId": occupancy_id, "claim": {"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 50}},
        cookies=renter_cookies,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["id"], body["claims"][0]["id"]


class TestClaimConcurrencyAtTheOrmLayer:
    def test_a_stale_second_session_raises_staledataerror_on_commit(self, client, db_session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="ccv1host@test.com", renter_email="ccv1renter@test.com")
        _case_id, claim_id = _open_platform_fee_case(client, auth_user_cookie(renter), occ.id)

        admin_a = _make_admin(db_session, email="ccv1-admina@test.com", role="super_admin")
        admin_b = _make_admin(db_session, email="ccv1-adminb@test.com", role="super_admin")

        session_a = db_session
        session_b = SASession(bind=db_session.bind)
        try:
            claim_a = session_a.get(DisputeResolutionClaim, claim_id)
            claim_b = session_b.get(DisputeResolutionClaim, claim_id)  # loaded before session_a's commit below

            updated = crud.decide_claim(session_a, claim_a, admin_a, DisputeClaimDecide(outcome="UPHELD", reason_code="first"))
            assert updated.version == 2

            try:
                crud.decide_claim(session_b, claim_b, admin_b, DisputeClaimDecide(outcome="NOT_UPHELD", reason_code="second"))
                assert False, "expected a StaleDataError from the stale second decision"
            except StaleDataError:
                pass
        finally:
            session_b.close()


class TestClaimConcurrencyThroughTheApi:
    def test_a_concurrent_decision_through_the_actual_route_surfaces_as_a_clean_409(self, client, db_session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="ccv2host@test.com", renter_email="ccv2renter@test.com")
        _case_id, claim_id = _open_platform_fee_case(client, auth_user_cookie(renter), occ.id)

        admin_a = _make_admin(db_session, email="ccv2-admina@test.com", role="super_admin")
        admin_b = _make_admin(db_session, email="ccv2-adminb@test.com", role="super_admin")

        session_a = db_session
        session_b = SASession(bind=db_session.bind)
        try:
            claim_a = session_a.get(DisputeResolutionClaim, claim_id)
            claim_b = session_b.get(DisputeResolutionClaim, claim_id)

            crud.decide_claim(session_a, claim_a, admin_a, DisputeClaimDecide(outcome="UPHELD", reason_code="first"))

            def _override_session_b():
                yield session_b

            app.dependency_overrides[get_db] = _override_session_b
            try:
                r = client.post(
                    f"/api/admin/disputes/claims/{claim_id}/decision",
                    json={"outcome": "NOT_UPHELD", "reasonCode": "second"},
                    cookies=auth_admin_cookie(admin_b),
                )
            finally:
                def _restore():
                    yield db_session
                app.dependency_overrides[get_db] = _restore

            assert r.status_code == 409, r.text
        finally:
            session_b.close()


class TestOrdinarySingleWriterUnaffected:
    def test_a_normal_decide_claim_call_still_works_and_increments_version(self, client, db_session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="ccv3host@test.com", renter_email="ccv3renter@test.com")
        _case_id, claim_id = _open_platform_fee_case(client, auth_user_cookie(renter), occ.id)

        admin = _make_admin(db_session, email="ccv3-admin@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/decision",
            json={"outcome": "UPHELD", "reasonCode": "fee_waived"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["version"] == 2
