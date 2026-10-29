"""ZR-70: Sublet policy settings said 'saved' but reopened ticked again --
nothing was stored. The minimum-notice rule is now a saved setting that is
enforced when a renter submits a sublet request."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from tests.conftest import auth_user_cookie
from tests.test_rental_messages import _make_verified_renter

URL = "/api/users/hosting/sublet-policy"


def _signed_tenancy(client, db_session: Session, tag: str):
    from tests.test_host_move_in_confirmation import _drive_agreement_to_signed

    host, renter, host_cookies, _rc, occupancy_id = _drive_agreement_to_signed(
        client, db_session, host_email=f"sp-{tag}-host@test.com", renter_email=f"sp-{tag}-renter@test.com",
    )
    assert client.post(f"/api/users/hosting/occupancies/{occupancy_id}/confirm-move-in", cookies=host_cookies).status_code == 200
    return host, renter, host_cookies, occupancy_id


class TestHostSubletPolicySettings:
    def test_nothing_is_required_until_the_host_sets_it(self, client, db_session: Session):
        host, _r, host_cookies, _o = _signed_tenancy(client, db_session, "default")
        assert client.get(URL, cookies=host_cookies).json() == {"minNoticeEnabled": False, "minNoticeDays": 14}

    def test_what_the_host_saves_is_what_they_get_back(self, client, db_session: Session):
        _h, _r, host_cookies, _o = _signed_tenancy(client, db_session, "save")
        r = client.put(URL, json={"minNoticeEnabled": True, "minNoticeDays": 21}, cookies=host_cookies)
        assert r.status_code == 200, r.text
        assert client.get(URL, cookies=host_cookies).json() == {"minNoticeEnabled": True, "minNoticeDays": 21}
        # Switching it off again sticks too (the reported bug).
        assert client.put(URL, json={"minNoticeEnabled": False, "minNoticeDays": 21}, cookies=host_cookies).status_code == 200
        assert client.get(URL, cookies=host_cookies).json()["minNoticeEnabled"] is False

    def test_a_bad_number_of_days_is_rejected(self, client, db_session: Session):
        _h, _r, host_cookies, _o = _signed_tenancy(client, db_session, "bad")
        assert client.put(URL, json={"minNoticeEnabled": True, "minNoticeDays": 0}, cookies=host_cookies).status_code == 422

    def test_each_host_has_their_own_settings(self, client, db_session: Session):
        _h, _r, host_cookies, _o = _signed_tenancy(client, db_session, "own")
        client.put(URL, json={"minNoticeEnabled": True, "minNoticeDays": 30}, cookies=host_cookies)
        other = _make_verified_renter(db_session, email="sp-own-other@test.com")
        db_session.commit()
        assert client.get(URL, cookies=auth_user_cookie(other)).json()["minNoticeEnabled"] is False


class TestMinimumNoticeIsEnforced:
    def _submit(self, db_session, renter, occupancy_id, subtenant, start):
        from app.crud import sublet as sublet_crud

        return sublet_crud.submit_sublet_request(
            db_session, renter, occupancy_id, subtenant.party_id, "ASSIGNMENT_FULL", proposed_start_date=start,
        )

    def test_a_request_starting_too_soon_is_refused_then_allowed_once_off(self, client, db_session: Session):
        _h, renter, host_cookies, occupancy_id = _signed_tenancy(client, db_session, "enf")
        subtenant = _make_verified_renter(db_session, email="sp-enf-sub@test.com")
        db_session.commit()
        client.put(URL, json={"minNoticeEnabled": True, "minNoticeDays": 14}, cookies=host_cookies)

        with pytest.raises(HTTPException) as err:
            self._submit(db_session, renter, occupancy_id, subtenant, date.today() + timedelta(days=5))
        assert err.value.status_code == 400 and "14 days" in err.value.detail

        # Far enough ahead is fine.
        assert self._submit(db_session, renter, occupancy_id, subtenant, date.today() + timedelta(days=20)).id

    def test_with_the_rule_off_a_near_start_date_is_fine(self, client, db_session: Session):
        _h, renter, host_cookies, occupancy_id = _signed_tenancy(client, db_session, "off")
        subtenant = _make_verified_renter(db_session, email="sp-off-sub@test.com")
        db_session.commit()
        client.put(URL, json={"minNoticeEnabled": False, "minNoticeDays": 14}, cookies=host_cookies)
        assert self._submit(db_session, renter, occupancy_id, subtenant, date.today() + timedelta(days=2)).id
