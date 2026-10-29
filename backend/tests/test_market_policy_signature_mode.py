"""A market can require sublease / lodger agreements to be really signed. The switch lived only in the
database (nothing in the API or screens could change it), so every market stayed on 'treated as signed
when the host approves'. A super admin can now set it per market."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.crud import sublet as sublet_crud
from app.models.leasing import Agreement
from app.models.market_policy import MarketPolicyPack
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_sublet_arrangement_classification import _make_active_tenancy
from tests.test_sublet_esignature_gate import _allow_co_tenant, _host_for


def _england(db: Session) -> MarketPolicyPack:
    return db.query(MarketPolicyPack).filter_by(jurisdiction_code="England").order_by(MarketPolicyPack.version.desc()).first()


class TestSignatureModeIsEditable:
    def test_a_super_admin_can_read_and_change_it(self, client, db_session: Session):
        admin = _make_admin(db_session, email="sigmode-admin@test.com", role="super_admin")
        db_session.commit()
        cookies = auth_admin_cookie(admin)
        pack_id = _england(db_session).id

        assert client.get(f"/api/market-policy-packs/{pack_id}", cookies=cookies).json()["subletSignatureMode"] == "NONE"
        r = client.patch(f"/api/market-policy-packs/{pack_id}", json={"subletSignatureMode": "E_SIGNATURE"}, cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["subletSignatureMode"] == "E_SIGNATURE"
        assert client.get(f"/api/market-policy-packs/{pack_id}", cookies=cookies).json()["subletSignatureMode"] == "E_SIGNATURE"

    def test_only_real_modes_are_accepted(self, client, db_session: Session):
        admin = _make_admin(db_session, email="sigmode-admin2@test.com", role="super_admin")
        db_session.commit()
        pack_id = _england(db_session).id
        for bad in ("ACKNOWLEDGMENT", "yes", ""):
            r = client.patch(f"/api/market-policy-packs/{pack_id}", json={"subletSignatureMode": bad}, cookies=auth_admin_cookie(admin))
            assert r.status_code == 422, bad

    def test_editing_other_things_leaves_it_alone(self, client, db_session: Session):
        admin = _make_admin(db_session, email="sigmode-admin3@test.com", role="super_admin")
        db_session.commit()
        cookies = auth_admin_cookie(admin)
        pack_id = _england(db_session).id
        client.patch(f"/api/market-policy-packs/{pack_id}", json={"subletSignatureMode": "E_SIGNATURE"}, cookies=cookies)
        r = client.patch(f"/api/market-policy-packs/{pack_id}", json={"subletConsentResponseDays": 21}, cookies=cookies)
        assert r.status_code == 200 and r.json()["subletSignatureMode"] == "E_SIGNATURE"

    def test_a_regular_user_cannot_change_it(self, client, db_session: Session):
        tenant_user, _c, _p, _o = _make_active_tenancy(db_session, suffix="sigmode-user")
        r = client.patch(f"/api/market-policy-packs/{_england(db_session).id}", json={"subletSignatureMode": "E_SIGNATURE"}, cookies=auth_user_cookie(tenant_user))
        assert r.status_code in (401, 403)

    def test_switching_it_on_makes_a_sublease_agreement_wait_for_real_signatures(self, client, db_session: Session):
        admin = _make_admin(db_session, email="sigmode-admin4@test.com", role="super_admin")
        db_session.commit()
        client.patch(
            f"/api/market-policy-packs/{_england(db_session).id}", json={"subletSignatureMode": "E_SIGNATURE"}, cookies=auth_admin_cookie(admin),
        )
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="sigmode-flow")
        _allow_co_tenant(db_session, occupancy_id)
        sr = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "SUBLEASE_PARTIAL")
        r = client.post(
            f"/api/users/hosting/sublet-requests/{sr.id}/approve", cookies=auth_user_cookie(_host_for(db_session, "sigmode-flow")),
        )
        assert r.status_code == 200, r.text
        agreement = db_session.get(Agreement, r.json()["newAgreementId"])
        assert agreement.status == "SENT"
        assert agreement.signed_by_provider_at is None and agreement.signed_by_renter_at is None
