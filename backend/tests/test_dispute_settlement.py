"""Integration tests for the ZR-ENG-CLR-010 Section 13 (P1)/22/23 bilateral
settlement service: app/models/dispute_settlement.py,
app/crud/dispute_settlement.py and the /settlements routes added to
app/api/routes/disputes.py.

Covers: proposing moves a claim to NEGOTIATION; A0 (Zoiko-controlled)
claims can never be settled bilaterally; the non-waivable-rights
acknowledgment is mandatory (AC-28); the proposer cannot respond to their
own proposal; accepting resolves the claim to SETTLED and the case toward
RESOLVED (AC-27's term-hash discipline); a counter-offer creates a new,
differently-hashed row and terminally COUNTERs the original (Q24); void
only works for the original proposer while SENT."""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


def _open_deposit_case(client, renter_cookies, occupancy_id: int) -> tuple[int, int]:
    r = client.post(
        "/api/users/rentals/disputes",
        json={"occupancyId": occupancy_id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
        cookies=renter_cookies,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["id"], body["claims"][0]["id"]


def _open_platform_fee_case(client, renter_cookies, occupancy_id: int) -> tuple[int, int]:
    r = client.post(
        "/api/users/rentals/disputes",
        json={"occupancyId": occupancy_id, "claim": {"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 50}},
        cookies=renter_cookies,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["id"], body["claims"][0]["id"]


class TestProposal:
    def test_proposing_a_settlement_moves_the_claim_to_negotiation(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="eshost1@test.com", renter_email="esrenter1@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_deposit_case(client, renter_cookies, occ.id)

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/settlements",
            json={"claimIds": [claim_id], "termsText": "Refund $300 of the $600 deduction", "amount": 300, "acknowledgesNoNonwaivableWaiver": True},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "SENT"
        assert body["proposedByRole"] == "RENTER"
        assert body["claimIds"] == [claim_id]
        assert body["termsHash"]

        r = client.get(f"/api/users/rentals/disputes/{case_id}", cookies=renter_cookies)
        assert r.json()["claims"][0]["status"] == "NEGOTIATION"

    def test_cannot_propose_a_settlement_for_an_a0_zoiko_controlled_claim(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="eshost2@test.com", renter_email="esrenter2@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_platform_fee_case(client, renter_cookies, occ.id)

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/settlements",
            json={"claimIds": [claim_id], "termsText": "Waive the fee", "acknowledgesNoNonwaivableWaiver": True},
            cookies=renter_cookies,
        )
        assert r.status_code == 409, r.text

    def test_nonwaivable_rights_acknowledgment_is_mandatory(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="eshost3@test.com", renter_email="esrenter3@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_deposit_case(client, renter_cookies, occ.id)

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/settlements",
            json={"claimIds": [claim_id], "termsText": "Refund $300", "acknowledgesNoNonwaivableWaiver": False},
            cookies=renter_cookies,
        )
        assert r.status_code == 400, r.text


class TestResponse:
    def test_proposer_cannot_respond_to_their_own_proposal(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="eshost4@test.com", renter_email="esrenter4@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_deposit_case(client, renter_cookies, occ.id)

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/settlements",
            json={"claimIds": [claim_id], "termsText": "Refund $300", "amount": 300, "acknowledgesNoNonwaivableWaiver": True},
            cookies=renter_cookies,
        )
        settlement_id = r.json()["id"]

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/settlements/{settlement_id}/respond",
            json={"action": "ACCEPT"},
            cookies=renter_cookies,
        )
        assert r.status_code == 403, r.text

    def test_host_accepts_and_resolves_claim_and_case(self, client, db_session: Session):
        host, renter, occ = _make_occupancy_with_parties(db_session, host_email="eshost5@test.com", renter_email="esrenter5@test.com")
        renter_cookies = auth_user_cookie(renter)
        host_cookies = auth_user_cookie(host)
        case_id, claim_id = _open_deposit_case(client, renter_cookies, occ.id)

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/settlements",
            json={"claimIds": [claim_id], "termsText": "Refund $300 of the $600 deduction", "amount": 300, "acknowledgesNoNonwaivableWaiver": True},
            cookies=renter_cookies,
        )
        settlement_id = r.json()["id"]

        r = client.post(
            f"/api/users/hosting/disputes/{case_id}/settlements/{settlement_id}/respond",
            json={"action": "ACCEPT"},
            cookies=host_cookies,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "EFFECTIVE"
        assert body["effectiveAt"] is not None

        r = client.get(f"/api/users/rentals/disputes/{case_id}", cookies=renter_cookies)
        case = r.json()
        assert case["claims"][0]["status"] == "SETTLED"
        assert case["status"] == "RESOLVED"

    def test_counter_offer_creates_a_new_row_and_terminally_counters_the_original(self, client, db_session: Session):
        host, renter, occ = _make_occupancy_with_parties(db_session, host_email="eshost6@test.com", renter_email="esrenter6@test.com")
        renter_cookies = auth_user_cookie(renter)
        host_cookies = auth_user_cookie(host)
        case_id, claim_id = _open_deposit_case(client, renter_cookies, occ.id)

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/settlements",
            json={"claimIds": [claim_id], "termsText": "Refund $300", "amount": 300, "acknowledgesNoNonwaivableWaiver": True},
            cookies=renter_cookies,
        )
        original = r.json()
        original_id = original["id"]
        original_hash = original["termsHash"]

        r = client.post(
            f"/api/users/hosting/disputes/{case_id}/settlements/{original_id}/respond",
            json={"action": "COUNTER", "counterTermsText": "Refund $150 instead", "counterAmount": 150},
            cookies=host_cookies,
        )
        assert r.status_code == 200, r.text
        counter = r.json()
        assert counter["id"] != original_id
        assert counter["proposedByRole"] == "HOST"
        assert counter["termsHash"] != original_hash
        assert counter["status"] == "SENT"

        r = client.get(f"/api/users/rentals/disputes/{case_id}/settlements", cookies=renter_cookies)
        by_id = {s["id"]: s for s in r.json()}
        assert by_id[original_id]["status"] == "COUNTERED"

        # The original (now terminal COUNTERED) proposal can no longer be accepted.
        r = client.post(
            f"/api/users/hosting/disputes/{case_id}/settlements/{original_id}/respond",
            json={"action": "ACCEPT"},
            cookies=host_cookies,
        )
        assert r.status_code == 409, r.text


class TestVoid:
    def test_only_the_proposer_can_void_while_sent(self, client, db_session: Session):
        host, renter, occ = _make_occupancy_with_parties(db_session, host_email="eshost7@test.com", renter_email="esrenter7@test.com")
        renter_cookies = auth_user_cookie(renter)
        host_cookies = auth_user_cookie(host)
        case_id, claim_id = _open_deposit_case(client, renter_cookies, occ.id)

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/settlements",
            json={"claimIds": [claim_id], "termsText": "Refund $300", "amount": 300, "acknowledgesNoNonwaivableWaiver": True},
            cookies=renter_cookies,
        )
        settlement_id = r.json()["id"]

        r = client.post(f"/api/users/hosting/disputes/{case_id}/settlements/{settlement_id}/void", cookies=host_cookies)
        assert r.status_code == 403, r.text

        r = client.post(f"/api/users/rentals/disputes/{case_id}/settlements/{settlement_id}/void", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "VOID"
