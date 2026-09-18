"""Integration tests for ZR-ENG-CLR-010 Section 7 jurisdiction-aware forum
resolution: app/services/dispute_forum_resolver.py's new
market_policy_pack parameter, wired into crud/disputes.py's
open_case/add_claim via _resolve_market_policy_for_occupancy.

Covers: an unconfigured jurisdiction still resolves to the static default
(no 409, no intake blocked); a MarketPolicyPack override changes a
DEPOSIT claim's authority class; ZOIKO_SERVICE stays A0 regardless of any
market pack; a claim family with no forum mapping at all
(PROTECTED_SAFETY) stays LEGAL_REVIEW_REQUIRED even with a market pack
present; a resolved market pack is recorded on the claim as real,
queryable policy_pack_id/policy_pack_version columns (not just embedded in
resolver_notes' free text), and both stay null when no pack was
consulted."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.market_policy import MarketPolicyPack
from tests.conftest import auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


def _set_jurisdiction(db_session: Session, occ, jurisdiction_code: str) -> None:
    occ.listing.room.property.jurisdiction_code = jurisdiction_code
    db_session.commit()


class TestFallbackWithNoMarketPack:
    def test_unconfigured_jurisdiction_falls_back_to_static_default(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="jfhost1@test.com", renter_email="jfrenter1@test.com")
        _set_jurisdiction(db_session, occ, "ZZ")  # no MarketPolicyPack seeded for "ZZ"

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        claim = r.json()["claims"][0]
        assert claim["authorityClass"] == "A2"
        # No market pack was consulted -- both structural fields stay null.
        assert claim["policyPackId"] is None
        assert claim["policyPackVersion"] is None


class TestMarketPolicyOverride:
    def test_market_pack_override_changes_deposit_authority_class(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="jfhost2@test.com", renter_email="jfrenter2@test.com")
        _set_jurisdiction(db_session, occ, "GB")
        pack = MarketPolicyPack(
            jurisdiction_code="GB", version=1, effective_from=date(2026, 1, 1), confidence="REVIEW_REQUIRED",
            legal_source_note="Test fixture -- not verified legal research.",
            dispute_deposit_authority_class="A4",
        )
        db_session.add(pack)
        db_session.commit()

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        claim = r.json()["claims"][0]
        assert claim["authorityClass"] == "A4"
        assert "GB" in claim["resolverNotes"]
        # Section 7: the same pack identity, now also as real, queryable columns.
        assert claim["policyPackId"] == pack.id
        assert claim["policyPackVersion"] == 1

    def test_add_claim_also_records_the_policy_pack_structurally(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="jfhost2b@test.com", renter_email="jfrenter2b@test.com")
        _set_jurisdiction(db_session, occ, "DE")
        pack = MarketPolicyPack(
            jurisdiction_code="DE", version=3, effective_from=date(2026, 1, 1), confidence="REVIEW_REQUIRED",
            legal_source_note="Test fixture -- not verified legal research.",
            dispute_deposit_authority_class="A1",
        )
        db_session.add(pack)
        db_session.commit()

        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 50}},
            cookies=renter_cookies,
        )
        case_id = r.json()["id"]

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/claims",
            json={"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        claim = r.json()
        assert claim["authorityClass"] == "A1"
        assert claim["policyPackId"] == pack.id
        assert claim["policyPackVersion"] == 3

    def test_zoiko_service_stays_a0_even_with_a_market_pack(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="jfhost3@test.com", renter_email="jfrenter3@test.com")
        _set_jurisdiction(db_session, occ, "FR")
        db_session.add(MarketPolicyPack(
            jurisdiction_code="FR", version=1, effective_from=date(2026, 1, 1), confidence="REVIEW_REQUIRED",
            legal_source_note="Test fixture -- not verified legal research.",
        ))
        db_session.commit()

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 50}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        assert r.json()["claims"][0]["authorityClass"] == "A0"

    def test_unmapped_family_stays_legal_review_required_even_with_a_market_pack(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="jfhost4@test.com", renter_email="jfrenter4@test.com")
        _set_jurisdiction(db_session, occ, "AU")
        db_session.add(MarketPolicyPack(
            jurisdiction_code="AU", version=1, effective_from=date(2026, 1, 1), confidence="REVIEW_REQUIRED",
            legal_source_note="Test fixture -- not verified legal research.",
        ))
        db_session.commit()

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DISCRIMINATION", "claimFamily": "PROTECTED_SAFETY"}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        claim = r.json()["claims"][0]
        assert claim["authorityClass"] is None
        assert claim["resolverConfidence"] == "LEGAL_REVIEW_REQUIRED"
