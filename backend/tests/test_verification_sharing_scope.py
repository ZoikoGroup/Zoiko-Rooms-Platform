"""ZR-ENG-CLR-012 AC-18: 'Each requirement records... sharing.' These are
fixed, code-enforced facts (which routes exist for which role), surfaced
in every read response rather than left implicit."""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie
from tests.test_property_compliance_credentials import _eligible_listing, _england_policy_pack


class TestSharingScopeSurfaced:
    def test_occupancy_eligibility_check_includes_sharing_scope(self, client, db_session: Session):
        admin = _make_admin(db_session, email="sscope-admin-01@test.com", role="super_admin")
        from app.models.party import Party

        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.commit()

        r = client.post(
            "/api/verification/occupancy-eligibility-checks",
            json={"partyId": party.id, "jurisdictionCode": "England", "method": "MANUAL_DOCUMENT_CHECK"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        assert "Host" in r.json()["sharingScope"]

    def test_screening_check_includes_sharing_scope(self, client, db_session: Session):
        admin = _make_admin(db_session, email="sscope-admin-02@test.com", role="super_admin")
        from app.models.party import Party

        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.commit()

        r = client.post(
            "/api/verification/screening-checks",
            json={"partyId": party.id, "jurisdictionCode": "England", "checkType": "AFFORDABILITY", "permissiblePurpose": "test"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        assert "admins only" in r.json()["sharingScope"]

    def test_property_compliance_credential_includes_sharing_scope(self, client, db_session: Session):
        _england_policy_pack(db_session, required_codes=["GAS_SAFETY_CERT"])
        _listing, room = _eligible_listing(db_session)
        admin = _make_admin(db_session, email="sscope-admin-03@test.com", role="super_admin")

        r = client.post(
            "/api/verification/property-compliance-credentials",
            json={"roomId": room.id, "requirementCode": "GAS_SAFETY_CERT"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        assert "Host" in r.json()["sharingScope"]
