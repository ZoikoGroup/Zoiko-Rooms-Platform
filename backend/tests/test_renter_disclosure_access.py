"""ZR-ENG-CLR-004 Section 9.3/AC-15: the renter needs to see which
disclosures exist on their own agreement before they can acknowledge any of
them -- previously only the admin-side list endpoint existed, so a renter
had no way to discover disclosures through their own API surface at all."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_agreement_engine_extensions_2 import _apply_send_accept_add_terms
from tests.test_renter_offer_agreement_flow import _make_agreement_eligible
from tests.test_room_hold_atomicity import _make_listing_with_room, _make_verified_renter


def _sent_agreement(client, db_session: Session, *, email_suffix: str):
    listing_id, _room_id = _make_listing_with_room(db_session)
    super_admin = _make_admin(db_session, email=f"disc-admin-{email_suffix}@test.com", role="super_admin")
    admin_cookies = auth_admin_cookie(super_admin)
    renter = _make_verified_renter(db_session, email=f"disc-renter-{email_suffix}@test.com")

    _app_id, offer_id = _apply_send_accept_add_terms(
        client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
    )
    _make_agreement_eligible(db_session, listing_id)
    r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    agreement_id = r.json()["id"]
    r = client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    return agreement_id, admin_cookies, renter


class TestRenterListsOwnDisclosures:
    def test_renter_can_list_their_own_agreements_disclosures(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _sent_agreement(client, db_session, email_suffix="1")

        r = client.get(f"/api/users/rentals/agreements/{agreement_id}/disclosures", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        disclosures = r.json()
        assert len(disclosures) >= 1
        assert all(d["status"] == "REQUIRED_MISSING" for d in disclosures)

    def test_a_different_renter_cannot_list_someone_elses_disclosures(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _sent_agreement(client, db_session, email_suffix="2")
        other_renter = _make_verified_renter(db_session, email="disc-other-2@test.com")

        r = client.get(
            f"/api/users/rentals/agreements/{agreement_id}/disclosures", cookies=auth_user_cookie(other_renter),
        )
        assert r.status_code == 403, r.text


class TestRenterAcknowledgesDisclosure:
    def test_renter_can_acknowledge_a_delivered_disclosure(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _sent_agreement(client, db_session, email_suffix="3")

        r = client.get(f"/api/users/rentals/agreements/{agreement_id}/disclosures", cookies=auth_user_cookie(renter))
        disclosure_id = r.json()[0]["id"]

        # Not yet delivered -- acknowledging should fail.
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/disclosures/{disclosure_id}/acknowledge",
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 409, r.text

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/disclosures/{disclosure_id}/deliver", cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/disclosures/{disclosure_id}/acknowledge",
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ACKNOWLEDGED"
