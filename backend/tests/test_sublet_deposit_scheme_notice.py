"""In the UK a sublease deposit has to go to a deposit protection scheme. Until the scheme's details are added
the incoming tenant can't pay it -- and only the landlord or the original tenant can add them. The people who
can fix it are told, the person who is stuck is told what to expect, and is told again once it's fixed."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.occupancy import Occupancy
from app.models.sublet_payment import SubletPaymentArrangement
from app.models.user_account import UserAccount
from app.services import sublet_payments as sp
from tests.conftest import auth_user_cookie
from tests.test_sublet_payments import _approve
from tests.test_sublet_arrangement_classification import _make_active_tenancy

NOTICE = "Add the deposit protection scheme details"


def _setup(client, db: Session, suffix: str):
    tenant_user, proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db, suffix=suffix)
    body = _approve(client, db, occupancy_id, tenant_user, proposed_party_id, "SUBLEASE_PARTIAL", suffix, proposed_monthly_rent=300)
    occupancy = db.get(Occupancy, occupancy_id)
    host = db.scalar(select(UserAccount).where(UserAccount.party_id == occupancy.listing.party_id))
    return body, tenant_user, proposed_user, host


def _titles(db: Session, user: UserAccount) -> list[str]:
    return list(db.scalars(select(Notification.title).where(Notification.recipient_user_id == user.id)))


class TestMissingSchemeDetails:
    def test_the_people_who_can_add_them_are_told_not_the_person_who_is_stuck(self, client, db_session: Session):
        _body, tenant_user, proposed_user, host = _setup(client, db_session, "dep1")
        assert NOTICE in _titles(db_session, host)          # the landlord
        assert NOTICE in _titles(db_session, tenant_user)   # the original tenant
        assert NOTICE not in _titles(db_session, proposed_user)

    def test_the_notice_names_who_cannot_pay_and_what_to_do(self, client, db_session: Session):
        _body, _tenant, proposed_user, host = _setup(client, db_session, "dep2")
        message = db_session.scalar(select(Notification.message).where(
            Notification.recipient_user_id == host.id, Notification.title == NOTICE))
        assert proposed_user.full_name in message and "Sublet payments" in message

    def test_the_blocked_deposit_tells_the_payer_who_fixes_it_and_that_they_will_hear(self, client, db_session: Session):
        body, _tenant, _proposed, _host = _setup(client, db_session, "dep3")
        arrangement = db_session.query(SubletPaymentArrangement).filter_by(sublet_request_id=body["id"]).one()
        deposit = sp.evaluate(db_session, arrangement)["deposit"]
        assert deposit["state"] == "ACTION_REQUIRED"
        assert "landlord or the original tenant" in deposit["message"] and "you'll be told" in deposit["message"]

    def test_one_notice_per_sublet_however_often_it_is_rechecked(self, client, db_session: Session):
        body, _tenant, _proposed, host = _setup(client, db_session, "dep4")
        arrangement = db_session.query(SubletPaymentArrangement).filter_by(sublet_request_id=body["id"]).one()
        sp._notify_setup_incomplete(db_session, arrangement)
        sp._notify_setup_incomplete(db_session, arrangement)
        assert _titles(db_session, host).count(NOTICE) == 1

    def test_adding_the_details_unblocks_the_deposit_and_tells_the_payer(self, client, db_session: Session):
        body, _tenant, proposed_user, host = _setup(client, db_session, "dep5")
        r = client.post(
            f"/api/users/sublets/{body['id']}/payment-arrangement",
            json={"custodianName": "Deposit Protection Service", "custodianInstructions": "Pay via the DPS portal, ref on your letter"},
            cookies=auth_user_cookie(host),
        )
        assert r.status_code == 200, r.text
        arrangement = db_session.query(SubletPaymentArrangement).filter_by(sublet_request_id=body["id"]).one()
        assert sp.evaluate(db_session, arrangement)["deposit"]["state"] == "READY"
        assert "Sublet payment arrangement updated" in _titles(db_session, proposed_user)

    def test_the_incoming_tenant_cannot_add_the_details_themselves(self, client, db_session: Session):
        body, _tenant, proposed_user, _host = _setup(client, db_session, "dep6")
        r = client.post(
            f"/api/users/sublets/{body['id']}/payment-arrangement",
            json={"custodianName": "Mine", "custodianInstructions": "Mine"}, cookies=auth_user_cookie(proposed_user),
        )
        assert r.status_code == 403


class TestCatchUpForEarlierSublets:
    def test_a_sublet_approved_before_the_notice_existed_is_caught_up_once(self, client, db_session: Session):
        body, tenant_user, _proposed, host = _setup(client, db_session, "dep7")
        db_session.query(Notification).filter(Notification.title == NOTICE).delete()  # as if approved earlier
        db_session.commit()
        assert sp.remind_missing_custodian_details(db_session) == 1
        assert NOTICE in _titles(db_session, host) and NOTICE in _titles(db_session, tenant_user)
        assert sp.remind_missing_custodian_details(db_session) == 0  # told already

    def test_nothing_is_sent_once_the_details_are_in(self, client, db_session: Session):
        body, _tenant, _proposed, host = _setup(client, db_session, "dep8")
        client.post(f"/api/users/sublets/{body['id']}/payment-arrangement",
                    json={"custodianName": "DPS", "custodianInstructions": "Pay via the portal"}, cookies=auth_user_cookie(host))
        db_session.query(Notification).filter(Notification.title == NOTICE).delete()
        db_session.commit()
        assert sp.remind_missing_custodian_details(db_session) == 0
