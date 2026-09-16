"""ZR-ENG-CLR-012 Section 31: Observability, SLAs and Operational Controls.
Computed on demand from existing tables -- same posture as
services/operational_metrics.py (ZR-ENG-CLR-001 Section 15)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.crud.occupancy_eligibility import open_occupancy_eligibility_check, record_occupancy_eligibility_result
from app.crud.screening import open_screening_check
from app.models.party import Party
from tests.conftest import _make_admin, auth_admin_cookie


class TestVerificationOperationalMetrics:
    def test_metrics_endpoint_returns_counts(self, client, db_session: Session):
        admin = _make_admin(db_session, email="vom-admin-01@test.com", role="super_admin")
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.commit()

        check = open_occupancy_eligibility_check(
            db_session, admin, party_id=party.id, jurisdiction_code="England", method="MANUAL_DOCUMENT_CHECK",
        )
        record_occupancy_eligibility_result(db_session, check, admin, result_status="PASS")

        open_screening_check(
            db_session, admin, party_id=party.id, jurisdiction_code="England",
            check_type="AFFORDABILITY", permissible_purpose="smoke test",
        )

        r = client.get("/api/verification/operational-metrics", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["screeningPendingCount"] >= 1
        assert body["occupancyEligibilityAvgTurnaroundSeconds"] is not None

    def test_metrics_are_null_not_zero_with_no_data(self, client, db_session: Session):
        admin = _make_admin(db_session, email="vom-admin-02@test.com", role="super_admin")
        r = client.get("/api/verification/operational-metrics", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["occupancyEligibilityPendingCount"] == 0
