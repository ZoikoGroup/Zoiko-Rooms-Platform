"""ZR-SUB-003 Section 5.2/8: 'If a signature is legally or contractually
required, invoke the approved e-signature workflow before status becomes
APPROVED.' Opt-in via MarketPolicyPack.sublet_signature_mode == "E_SIGNATURE"
(default "NONE" preserves the original auto-signed co-tenancy behavior
exactly -- verified by the full existing sublet test suite staying green).

This also closes a real bug this investigation surfaced: the auto-signed
shortcut never called crud/leasing.py:_ensure_pending_move_in_occupancy, so
a co-tenant approved that way could never actually receive an Occupancy row
through any existing code path. Routing through the real, already-tested
host_sign_agreement/user_sign_agreement -> _apply_signature machinery fixes
that for free."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import sublet as sublet_crud
from app.models.leasing import Agreement
from app.models.market_policy import MarketPolicyPack
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.user_account import UserAccount
from tests.conftest import _make_user, auth_user_cookie
from tests.test_sublet_arrangement_classification import _make_active_tenancy


def _allow_co_tenant(db: Session, occupancy_id: int) -> None:
    occupancy = db.get(Occupancy, occupancy_id)
    occupancy.room.max_occupants = 2
    db.commit()


def _host_for(db: Session, suffix: str) -> UserAccount:
    return db.scalar(select(UserAccount).where(UserAccount.email == f"host-{suffix}@test.com"))


def _enable_e_signature(db: Session) -> None:
    # _make_active_tenancy's Property never sets jurisdiction_code, which
    # defaults to "England", not "IN".
    policy = db.query(MarketPolicyPack).filter_by(jurisdiction_code="England").one()
    policy.sublet_signature_mode = "E_SIGNATURE"
    db.commit()


class TestESignatureGateOptIn:
    def test_default_mode_still_auto_signs_unchanged(self, client, db_session: Session):
        """NONE (the default) must behave exactly as before this change."""
        tenant_user, proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="esig1")
        _allow_co_tenant(db_session, occupancy_id)
        sublet_request = sublet_crud.submit_sublet_request(
            db_session, tenant_user, occupancy_id, proposed_party_id, "ADD_CO_TENANT",
        )
        host_user = _host_for(db_session, "esig1")

        r = client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve", cookies=auth_user_cookie(host_user))
        assert r.status_code == 200, r.text
        new_agreement_id = r.json()["newAgreementId"]
        agreement = db_session.get(Agreement, new_agreement_id)
        assert agreement.status == "SIGNED"
        assert agreement.signed_by_provider_at is not None
        assert agreement.signed_by_renter_at is not None

    def test_e_signature_mode_leaves_the_agreement_unsigned(self, client, db_session: Session):
        _enable_e_signature(db_session)
        tenant_user, proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="esig2")
        _allow_co_tenant(db_session, occupancy_id)
        sublet_request = sublet_crud.submit_sublet_request(
            db_session, tenant_user, occupancy_id, proposed_party_id, "ADD_CO_TENANT",
        )
        host_user = _host_for(db_session, "esig2")

        r = client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve", cookies=auth_user_cookie(host_user))
        assert r.status_code == 200, r.text
        new_agreement_id = r.json()["newAgreementId"]
        agreement = db_session.get(Agreement, new_agreement_id)
        assert agreement.status == "SENT"
        assert agreement.signed_by_provider_at is None
        assert agreement.signed_by_renter_at is None
        assert len(agreement.versions) == 1

    def test_both_parties_can_sign_through_the_real_existing_signing_endpoints(self, client, db_session: Session):
        _enable_e_signature(db_session)
        tenant_user, proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="esig3")
        _allow_co_tenant(db_session, occupancy_id)
        sublet_request = sublet_crud.submit_sublet_request(
            db_session, tenant_user, occupancy_id, proposed_party_id, "ADD_CO_TENANT",
        )
        host_user = _host_for(db_session, "esig3")

        r = client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve", cookies=auth_user_cookie(host_user))
        assert r.status_code == 200, r.text
        new_agreement_id = r.json()["newAgreementId"]

        # The co-tenant signs their own new agreement via the existing,
        # already-tested renter sign endpoint.
        r = client.post(f"/api/users/rentals/agreements/{new_agreement_id}/sign", cookies=auth_user_cookie(proposed_user))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PARTIALLY_EXECUTED"

        # The host signs via the existing host sign endpoint.
        r = client.post(f"/api/users/hosting/agreements/{new_agreement_id}/sign", cookies=auth_user_cookie(host_user))
        assert r.status_code == 200, r.text
        # Obligations aren't paid yet -- both signed but not yet executed.
        assert r.json()["status"] == "PAYMENT_IN_PROGRESS"

        # Both signed but obligations aren't paid yet -- correctly still no
        # Occupancy, exactly like any other ordinary tenancy at this same
        # PAYMENT_IN_PROGRESS point (crud/leasing.py:_apply_signature only
        # calls _ensure_pending_move_in_occupancy once truly SIGNED, i.e.
        # after payment clears too). What this test proves is the real
        # bug-fix: the co-tenant's agreement now goes through that exact
        # same real state machine at all -- the old auto-signed shortcut
        # never reached _apply_signature, and so could never reach
        # _ensure_pending_move_in_occupancy either, no matter how long you
        # waited or what you paid.
        new_occupancy = db_session.scalar(select(Occupancy).where(Occupancy.offer_id == db_session.get(Agreement, new_agreement_id).offer_id))
        assert new_occupancy is None

    def test_a_stranger_cannot_sign_the_co_tenants_agreement(self, client, db_session: Session):
        _enable_e_signature(db_session)
        tenant_user, proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="esig4")
        _allow_co_tenant(db_session, occupancy_id)
        sublet_request = sublet_crud.submit_sublet_request(
            db_session, tenant_user, occupancy_id, proposed_party_id, "ADD_CO_TENANT",
        )
        host_user = _host_for(db_session, "esig4")
        r = client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve", cookies=auth_user_cookie(host_user))
        new_agreement_id = r.json()["newAgreementId"]

        stranger = _make_user(db_session, email="esig4-stranger@test.com")
        r = client.post(f"/api/users/rentals/agreements/{new_agreement_id}/sign", cookies=auth_user_cookie(stranger))
        assert r.status_code == 403
