"""Someone who takes a room over (full handover / replacement) talks to the host in the sublet's own thread, which
the host reads under Sublet Requests -- not in the renter's application thread on the host's Applications page."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.crud import sublet as sublet_crud
from app.models.notification import Notification
from app.models.occupancy import Occupancy
from tests.conftest import auth_user_cookie
from tests.test_sublet_arrangement_classification import _host_for, _make_active_tenancy

URL = "/api/users/rentals/sublet-requests/{id}/messages?thread=host"
HOST_URL = "/api/users/hosting/sublet-requests/{id}/messages"


def _handover(client, db: Session, suffix: str, arrangement: str):
    tenant_user, incoming, party_id, occupancy_id = _make_active_tenancy(db, suffix=suffix)
    sr = sublet_crud.submit_sublet_request(db, tenant_user, occupancy_id, party_id, arrangement)
    host = _host_for(db, suffix)
    return sr, tenant_user, incoming, host, occupancy_id


@pytest.mark.parametrize("arrangement", ["ASSIGNMENT_FULL", "REPLACEMENT_OCCUPANT"])
class TestHandoverHostThread:
    def test_no_host_thread_until_the_host_has_approved(self, client, db_session: Session, arrangement):
        sr, _t, incoming, host, _o = _handover(client, db_session, f"hh1{arrangement[:3]}", arrangement)
        assert client.get(URL.format(id=sr.id), cookies=auth_user_cookie(incoming)).status_code == 404
        assert client.get(HOST_URL.format(id=sr.id), cookies=auth_user_cookie(host)).status_code == 404

    def test_after_approval_the_incoming_tenant_and_host_talk_in_the_sublet_thread(self, client, db_session: Session, arrangement):
        sr, _t, incoming, host, occupancy_id = _handover(client, db_session, f"hh2{arrangement[:3]}", arrangement)
        r = client.post(f"/api/users/hosting/sublet-requests/{sr.id}/approve", json={"stepUpPassword": "password123"}, cookies=auth_user_cookie(host))
        assert r.status_code == 200, r.text
        assert r.json()["hostThreadAvailable"] is True

        assert client.post(URL.format(id=sr.id), json={"body": "Hi host"}, cookies=auth_user_cookie(incoming)).status_code == 201
        assert [m["body"] for m in client.get(HOST_URL.format(id=sr.id), cookies=auth_user_cookie(host)).json()] == ["Hi host"]
        assert db_session.query(Notification).filter_by(recipient_user_id=host.id, notification_type="sublet_message.host_received").count() == 1

        # The occupancy the incoming tenant now holds points them at this thread.
        occ = client.get("/api/users/rentals/occupancies", cookies=auth_user_cookie(incoming)).json()
        mine = [o for o in occ if o["id"] == occupancy_id]
        assert mine and mine[0]["subletFromHostThread"] is True and mine[0]["subletFromRequestId"] == sr.id
        db_session.expire_all()
        assert db_session.get(Occupancy, occupancy_id).guest_id != sr.requested_by_guest_id
