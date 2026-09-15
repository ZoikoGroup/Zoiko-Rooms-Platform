"""Integration tests for ZR-ENG-CLR-010 AC-28/AC-29, QA-Q21/Q22/Q23 --
the new dispute-specific fields on MarketPolicyPack
(dispute_response_window_days, dispute_external_filing_deadline_days,
dispute_conciliation_requirement, dispute_non_waivable_claim_families)
and the crud code that consumes them:
app/crud/dispute_settlement.py:_assert_not_non_waivable,
app/crud/dispute_external_proceeding.py:_assert_conciliation_prerequisite_met/
_compute_external_filing_deadline, and
app/crud/disputes.py:_create_party_response_deadline.

Covers: AC-28 a market pack listing a claim family as non-waivable blocks
settlement of that family; an unconfigured market blocks nothing (matches
"no universal arbitration"-style honesty); Q21 a filing after the
market-configured deadline is flagged (never blocked, never invents an
extension); Q22 mandatory conciliation gates a non-mediation filing behind
an already-concluded MEDIATION_ADR proceeding; Q23 optional/not-required
conciliation never gates anything; AC-29 the auto-created PARTY_RESPONSE
deadline is computed from the market pack's own response window, with a
real reminder_at."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.market_policy import MarketPolicyPack
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


def _set_jurisdiction(db_session: Session, occ, jurisdiction_code: str) -> None:
    occ.listing.room.property.jurisdiction_code = jurisdiction_code
    db_session.commit()


def _open_deposit_case(client, renter_cookies, occupancy_id: int, *, amount: float = 600) -> tuple[int, int]:
    r = client.post(
        "/api/users/rentals/disputes",
        json={"occupancyId": occupancy_id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": amount}},
        cookies=renter_cookies,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["id"], body["claims"][0]["id"]


class TestNonWaivableClaimFamilies:
    def test_a_market_pack_listing_the_claim_family_blocks_settlement(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="jpehost1@test.com", renter_email="jperenter1@test.com")
        _set_jurisdiction(db_session, occ, "NW")
        db_session.add(MarketPolicyPack(
            jurisdiction_code="NW", version=1, effective_from=date(2026, 1, 1), confidence="REVIEW_REQUIRED",
            legal_source_note="Test fixture -- not verified legal research.",
            dispute_non_waivable_claim_families=["DEPOSIT"],
        ))
        db_session.commit()

        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_deposit_case(client, renter_cookies, occ.id)

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/settlements",
            json={"claimIds": [claim_id], "termsText": "Refund $300", "amount": 300, "acknowledgesNoNonwaivableWaiver": True},
            cookies=renter_cookies,
        )
        assert r.status_code == 409, r.text
        assert "cannot be waived" in r.text

    def test_an_unconfigured_market_blocks_nothing(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="jpehost2@test.com", renter_email="jperenter2@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_deposit_case(client, renter_cookies, occ.id)

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/settlements",
            json={"claimIds": [claim_id], "termsText": "Refund $300", "amount": 300, "acknowledgesNoNonwaivableWaiver": True},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text


class TestExternalFilingDeadline:
    def test_a_filing_after_the_configured_deadline_is_flagged_not_blocked(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="jpehost3@test.com", renter_email="jperenter3@test.com")
        _set_jurisdiction(db_session, occ, "FD")
        db_session.add(MarketPolicyPack(
            jurisdiction_code="FD", version=1, effective_from=date(2026, 1, 1), confidence="REVIEW_REQUIRED",
            legal_source_note="Test fixture -- not verified legal research.",
            dispute_external_filing_deadline_days=1,
        ))
        db_session.commit()

        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_deposit_case(client, renter_cookies, occ.id)

        admin = _make_admin(db_session, email="jpe-admin3@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/{case_id}/external-proceedings",
            json={
                "authorityType": "DEPOSIT_SCHEME", "claimIds": [claim_id],
                "filedAt": (date.today() + timedelta(days=10)).isoformat(),
            },
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["filedAfterDeadline"] is True
        assert body["externalDeadlineAt"] is not None

    def test_a_filing_within_the_configured_deadline_is_not_flagged(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="jpehost4@test.com", renter_email="jperenter4@test.com")
        _set_jurisdiction(db_session, occ, "FD2")
        db_session.add(MarketPolicyPack(
            jurisdiction_code="FD2", version=1, effective_from=date(2026, 1, 1), confidence="REVIEW_REQUIRED",
            legal_source_note="Test fixture -- not verified legal research.",
            dispute_external_filing_deadline_days=365,
        ))
        db_session.commit()

        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_deposit_case(client, renter_cookies, occ.id)

        admin = _make_admin(db_session, email="jpe-admin4@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/{case_id}/external-proceedings",
            json={"authorityType": "DEPOSIT_SCHEME", "claimIds": [claim_id]},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["filedAfterDeadline"] is False
        assert body["externalDeadlineAt"] is not None

    def test_an_unconfigured_market_records_no_deadline_at_all(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="jpehost5@test.com", renter_email="jperenter5@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_deposit_case(client, renter_cookies, occ.id)

        admin = _make_admin(db_session, email="jpe-admin5@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/{case_id}/external-proceedings",
            json={"authorityType": "DEPOSIT_SCHEME", "claimIds": [claim_id]},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["externalDeadlineAt"] is None
        assert body["filedAfterDeadline"] is False


class TestConciliationGating:
    def test_mandatory_conciliation_blocks_a_tribunal_filing_until_concluded(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="jpehost6@test.com", renter_email="jperenter6@test.com")
        _set_jurisdiction(db_session, occ, "MC")
        db_session.add(MarketPolicyPack(
            jurisdiction_code="MC", version=1, effective_from=date(2026, 1, 1), confidence="REVIEW_REQUIRED",
            legal_source_note="Test fixture -- not verified legal research.",
            dispute_conciliation_requirement="MANDATORY",
        ))
        db_session.commit()

        renter_cookies = auth_user_cookie(renter)
        case_id, mediation_claim_id = _open_deposit_case(client, renter_cookies, occ.id)
        # A second, still-open claim on the same case -- conciliation is a
        # case-level (not per-claim) prerequisite, and update_status's own
        # DISMISSED/WITHDRAWN outcome resolves whatever claim the
        # concluded MEDIATION_ADR proceeding itself covered, so the
        # tribunal referral below targets a *different*, still-open claim.
        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/claims",
            json={"claimCode": "PROTECTION", "claimFamily": "DEPOSIT", "amount": 100},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        tribunal_claim_id = r.json()["id"]

        admin = _make_admin(db_session, email="jpe-admin6@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        # A direct tribunal filing is refused -- conciliation hasn't even started.
        r = client.post(
            f"/api/admin/disputes/{case_id}/external-proceedings",
            json={"authorityType": "TRIBUNAL_COURT", "claimIds": [tribunal_claim_id]},
            cookies=admin_cookies,
        )
        assert r.status_code == 409, r.text
        assert "conciliation" in r.text.lower()

        # Filing the MEDIATION_ADR step itself is never gated.
        r = client.post(
            f"/api/admin/disputes/{case_id}/external-proceedings",
            json={"authorityType": "MEDIATION_ADR", "claimIds": [mediation_claim_id]},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        mediation_id = r.json()["id"]

        # Still refused while mediation is only FILED, not yet concluded.
        r = client.post(
            f"/api/admin/disputes/{case_id}/external-proceedings",
            json={"authorityType": "TRIBUNAL_COURT", "claimIds": [tribunal_claim_id]},
            cookies=admin_cookies,
        )
        assert r.status_code == 409, r.text

        # Once mediation concludes (DISMISSED = unsuccessful), tribunal referral proceeds.
        r = client.post(
            f"/api/admin/disputes/external-proceedings/{mediation_id}/status",
            json={"status": "DISMISSED"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            f"/api/admin/disputes/{case_id}/external-proceedings",
            json={"authorityType": "TRIBUNAL_COURT", "claimIds": [tribunal_claim_id]},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text

    def test_optional_conciliation_never_blocks_a_direct_filing(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="jpehost7@test.com", renter_email="jperenter7@test.com")
        _set_jurisdiction(db_session, occ, "OC")
        db_session.add(MarketPolicyPack(
            jurisdiction_code="OC", version=1, effective_from=date(2026, 1, 1), confidence="REVIEW_REQUIRED",
            legal_source_note="Test fixture -- not verified legal research.",
            dispute_conciliation_requirement="OPTIONAL",
        ))
        db_session.commit()

        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_deposit_case(client, renter_cookies, occ.id)
        admin = _make_admin(db_session, email="jpe-admin7@test.com", role="super_admin")

        r = client.post(
            f"/api/admin/disputes/{case_id}/external-proceedings",
            json={"authorityType": "TRIBUNAL_COURT", "claimIds": [claim_id]},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text

    def test_the_default_not_required_never_blocks_anything(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="jpehost8@test.com", renter_email="jperenter8@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_deposit_case(client, renter_cookies, occ.id)
        admin = _make_admin(db_session, email="jpe-admin8@test.com", role="super_admin")

        r = client.post(
            f"/api/admin/disputes/{case_id}/external-proceedings",
            json={"authorityType": "TRIBUNAL_COURT", "claimIds": [claim_id]},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text


class TestForumPackComputedResponseDeadline:
    def test_the_auto_created_deadline_uses_the_market_packs_response_window(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="jpehost9@test.com", renter_email="jperenter9@test.com")
        _set_jurisdiction(db_session, occ, "RW")
        db_session.add(MarketPolicyPack(
            jurisdiction_code="RW", version=1, effective_from=date(2026, 1, 1), confidence="REVIEW_REQUIRED",
            legal_source_note="Test fixture -- not verified legal research.",
            dispute_response_window_days=20,
        ))
        db_session.commit()

        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_deposit_case(client, renter_cookies, occ.id)

        r = client.get(f"/api/users/rentals/disputes/{case_id}/deadlines", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        deadlines = r.json()
        assert len(deadlines) == 1
        deadline = deadlines[0]
        assert deadline["deadlineType"] == "PARTY_RESPONSE"
        assert deadline["source"] == "SYSTEM_DEFAULT"
        assert deadline["reminderAt"] is not None

        due_at = datetime.fromisoformat(deadline["dueAt"])
        created_at = datetime.fromisoformat(deadline["createdAt"])
        assert timedelta(days=19, hours=23) < (due_at - created_at) < timedelta(days=20, hours=1)

    def test_the_default_five_day_window_applies_with_no_market_pack(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="jpehost10@test.com", renter_email="jperenter10@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_deposit_case(client, renter_cookies, occ.id)

        r = client.get(f"/api/users/rentals/disputes/{case_id}/deadlines", cookies=renter_cookies)
        deadline = r.json()[0]
        due_at = datetime.fromisoformat(deadline["dueAt"])
        created_at = datetime.fromisoformat(deadline["createdAt"])
        assert timedelta(days=4, hours=23) < (due_at - created_at) < timedelta(days=5, hours=1)
