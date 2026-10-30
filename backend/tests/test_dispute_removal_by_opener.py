"""ZR-30: the person who opened a dispute can delete it once it is resolved or
closed -- no admin step. It leaves their own view and the evidence and messages
they contributed are erased; the other party keeps their view."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.dispute import DisputeResolutionCase
from app.models.dispute_evidence import DisputeEvidenceItem
from app.models.dispute_message import DisputeCaseMessage
from tests.conftest import auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties

RENTER = "/api/users/rentals/disputes"
HOST = "/api/users/hosting/disputes"


def _open_case(client, db_session: Session, tag: str):
    host, renter, occ = _make_occupancy_with_parties(db_session, host_email=f"rm-{tag}-host@test.com", renter_email=f"rm-{tag}-renter@test.com")
    renter_cookies, host_cookies = auth_user_cookie(renter), auth_user_cookie(host)
    r = client.post(
        RENTER, json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
        cookies=renter_cookies,
    )
    assert r.status_code == 201, r.text
    return renter_cookies, host_cookies, r.json()["id"]


def _finish(db_session: Session, case_id: int, status: str = "CLOSED") -> None:
    db_session.get(DisputeResolutionCase, case_id).status = status
    db_session.commit()


class TestRemoveCompletedDispute:
    def test_opener_deletes_a_closed_dispute_and_the_other_party_keeps_it(self, client, db_session: Session):
        renter_cookies, host_cookies, case_id = _open_case(client, db_session, "ok")
        assert client.post(f"{RENTER}/{case_id}/evidence", data={"note_text": "my private note"}, cookies=renter_cookies).status_code == 201
        assert client.post(f"{RENTER}/{case_id}/messages", json={"body": "secret message"}, cookies=renter_cookies).status_code in (200, 201)
        _finish(db_session, case_id)

        assert client.post(f"{RENTER}/{case_id}/remove", cookies=renter_cookies).status_code == 204

        # Gone from the opener's list and detail.
        assert case_id not in [c["id"] for c in client.get(RENTER, cookies=renter_cookies).json()]
        assert client.get(f"{RENTER}/{case_id}", cookies=renter_cookies).status_code == 404
        # Their note is erased; the case itself, and the host's view, remain.
        mine = db_session.query(DisputeEvidenceItem).filter(
            DisputeEvidenceItem.case_id == case_id, DisputeEvidenceItem.uploaded_by_guest_id.is_not(None)).one()
        assert mine.deleted_at is not None and mine.note_text == ""
        # The system's own snapshot of the tenancy is not theirs to erase.
        snapshot = db_session.query(DisputeEvidenceItem).filter(
            DisputeEvidenceItem.case_id == case_id, DisputeEvidenceItem.uploaded_by_guest_id.is_(None)).one()
        assert snapshot.deleted_at is None
        assert [m.body for m in db_session.query(DisputeCaseMessage).filter_by(case_id=case_id)] == ["(deleted by sender)"]
        assert db_session.get(DisputeResolutionCase, case_id).removed_by_opener_at is not None
        assert case_id in [c["id"] for c in client.get(HOST, cookies=host_cookies).json()]

    def test_it_must_be_resolved_or_closed_first(self, client, db_session: Session):
        renter_cookies, _hc, case_id = _open_case(client, db_session, "open")
        r = client.post(f"{RENTER}/{case_id}/remove", cookies=renter_cookies)
        assert r.status_code == 409 and "resolved or closed" in r.text
        assert client.get(f"{RENTER}/{case_id}", cookies=renter_cookies).status_code == 200

    def test_only_the_opener_can_delete_it(self, client, db_session: Session):
        _rc, host_cookies, case_id = _open_case(client, db_session, "other")
        _finish(db_session, case_id)
        r = client.post(f"{HOST}/{case_id}/remove", cookies=host_cookies)
        assert r.status_code == 403, r.text
        assert db_session.get(DisputeResolutionCase, case_id).removed_by_opener_at is None

    def test_a_legal_hold_blocks_it(self, client, db_session: Session):
        renter_cookies, _hc, case_id = _open_case(client, db_session, "hold")
        assert client.post(f"{RENTER}/{case_id}/evidence", data={"note_text": "held"}, cookies=renter_cookies).status_code == 201
        db_session.query(DisputeEvidenceItem).filter_by(case_id=case_id).update({"legal_hold": True})
        _finish(db_session, case_id)
        r = client.post(f"{RENTER}/{case_id}/remove", cookies=renter_cookies)
        assert r.status_code == 409 and "legal hold" in r.text
        assert client.get(f"{RENTER}/{case_id}", cookies=renter_cookies).status_code == 200

    def test_deleting_twice_is_refused(self, client, db_session: Session):
        renter_cookies, _hc, case_id = _open_case(client, db_session, "twice")
        _finish(db_session, case_id, "RESOLVED")
        assert client.post(f"{RENTER}/{case_id}/remove", cookies=renter_cookies).status_code == 204
        assert client.post(f"{RENTER}/{case_id}/remove", cookies=renter_cookies).status_code == 404
