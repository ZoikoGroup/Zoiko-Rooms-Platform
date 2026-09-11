"""ZR-ENG-CLR-004: verifies the nine acceptance-criteria gaps that a code
audit found only partially built -- AC-06 (real version invalidation),
AC-08 (market-pack/clause version stamped into the snapshot), AC-10
(assurance level resolved via policy lookup, not a bare literal), AC-15
(ACKNOWLEDGED is a reachable state, not dead code), AC-16 (per-party
delivery + a real attached document), AC-17 (approved-optional-clause
selection, allow-listed), AC-20 (separate notice/liability/termination
dates on Occupancy), AC-25 (clause registry is really versioned,
effective-dated and rollback-capable), AC-11 (a real wet-ink fallback path).
"""

from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO

from sqlalchemy.orm import Session

from app.models.agreement_clause import ClauseDefinition
from app.models.leasing import Agreement, Offer, SignatureEvent
from app.services.agreement_profile import AGREEMENT_CLASS, SUPPORTED_JURISDICTION
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie, deliver_all_disclosures
from tests.test_renter_offer_agreement_flow import _make_agreement_eligible
from tests.test_room_hold_atomicity import _apply_and_send_offer, _make_listing_with_room, _make_verified_renter


def _apply_send_accept_add_terms(client, db_session: Session, admin_cookies: dict, renter, listing_id: str, *, start_date: date, term_months: int = 6):
    _app_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)
    r = client.post(
        f"/api/leasing/offers/{offer_id}/terms",
        json={
            "monthlyRent": 500, "depositAmount": 500,
            "startDate": start_date.isoformat(), "termMonths": term_months,
        },
        cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(renter))
    assert r.status_code == 200, r.text
    return _app_id, offer_id


class TestVersionInvalidationOnMaterialChange:
    """AC-06: a material term change to an offer that already has a WORKING
    agreement version supersedes it and opens a fresh one, instead of being
    unreachable/only structurally prevented."""

    def test_changing_rent_after_agreement_created_supersedes_the_working_version(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac06-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac06-renter1@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        agreement_id = r.json()["id"]

        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)
        r = client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PARTIALLY_EXECUTED"

        # Host changes the rent while the renter has already signed but the
        # version is still WORKING (17.1's own edge case).
        r = client.post(
            f"/api/leasing/offers/{offer_id}/terms",
            json={
                "monthlyRent": 650, "depositAmount": 500,
                "startDate": (date.today() + timedelta(days=5)).isoformat(), "termMonths": 6,
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        agreement = db_session.get(Agreement, agreement_id)
        db_session.refresh(agreement)
        assert agreement.status == "DRAFT"
        assert agreement.signed_by_renter_at is None, "the prior signature must not carry over to the new version"

        versions = sorted(agreement.versions, key=lambda v: v.version_no)
        assert len(versions) == 2
        assert versions[0].status == "SUPERSEDED"
        assert versions[1].status == "WORKING"
        assert versions[1].snapshot["monthly_rent"] == 650.0

        # The superseded version's own signature event is preserved as
        # evidence, not deleted or reused.
        events = list(db_session.query(SignatureEvent).filter(SignatureEvent.agreement_version_id == versions[0].id))
        assert len(events) == 1
        new_version_events = list(db_session.query(SignatureEvent).filter(SignatureEvent.agreement_version_id == versions[1].id))
        assert len(new_version_events) == 0

    def test_material_change_blocked_once_version_is_frozen(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac06-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac06-renter2@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today(),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)
        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)

        from tests.test_agreement_engine_foundation import _pay_off_agreement_obligations
        _pay_off_agreement_obligations(client, db_session, admin_cookies, agreement_id)
        agreement = db_session.get(Agreement, agreement_id)
        assert agreement.status == "SIGNED"

        r = client.post(
            f"/api/leasing/offers/{offer_id}/terms",
            json={"monthlyRent": 999, "depositAmount": 500, "startDate": date.today().isoformat(), "termMonths": 6},
            cookies=admin_cookies,
        )
        assert r.status_code == 409, r.text


class TestSnapshotMarketPackVersion:
    """AC-08: the snapshot records which clause versions and which
    market-pack tag it was actually built from, not just bare clause ids."""

    def test_snapshot_records_clause_versions_and_a_market_pack_tag(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac08-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac08-renter1@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        agreement_id = r.json()["id"]

        agreement = db_session.get(Agreement, agreement_id)
        snapshot = agreement.versions[0].snapshot
        assert snapshot["market_pack_version"]
        assert snapshot["clause_versions"]
        assert set(snapshot["clause_versions"]) == set(snapshot["clause_ids"])
        assert all(v == 1 for v in snapshot["clause_versions"].values())


class TestAssuranceLevelIsResolved:
    """AC-10: assurance_level comes from a (jurisdiction, agreement_class)
    policy lookup, not a bare literal in the resolver's return statement."""

    def test_resolve_looks_up_assurance_level_not_hardcodes_it(self, db_session: Session):
        from app.services.agreement_profile import ASSURANCE_LEVEL_BY_PROFILE, _resolve_assurance_level

        assert (SUPPORTED_JURISDICTION, AGREEMENT_CLASS) in ASSURANCE_LEVEL_BY_PROFILE
        assert _resolve_assurance_level(SUPPORTED_JURISDICTION, AGREEMENT_CLASS) == "SIMPLE_ESIGN"
        assert _resolve_assurance_level("Some Other Jurisdiction", "some_other_class") == "SIMPLE_ESIGN"


class TestDisclosureAcknowledgment:
    """AC-15: ACKNOWLEDGED is reachable via a real renter-facing endpoint,
    not a status value nothing ever writes."""

    def test_renter_can_acknowledge_a_delivered_disclosure(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac15-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac15-renter1@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)

        disclosures = client.get(f"/api/leasing/agreements/{agreement_id}/disclosures", cookies=admin_cookies).json()
        disclosure_id = disclosures[0]["id"]
        assert disclosures[0]["status"] == "DELIVERED"

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/disclosures/{disclosure_id}/acknowledge",
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ACKNOWLEDGED"
        assert r.json()["acknowledgedAt"] is not None

    def test_cannot_acknowledge_before_delivery(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac15-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac15-renter2@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)

        disclosures = client.get(f"/api/leasing/agreements/{agreement_id}/disclosures", cookies=admin_cookies).json()
        disclosure_id = disclosures[0]["id"]

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/disclosures/{disclosure_id}/acknowledge",
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 409, r.text

    def test_other_renter_cannot_acknowledge(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac15-admin3@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac15-renter3@test.com")
        other_renter = _make_verified_renter(db_session, email="ac15-other3@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)

        disclosures = client.get(f"/api/leasing/agreements/{agreement_id}/disclosures", cookies=admin_cookies).json()
        disclosure_id = disclosures[0]["id"]

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/disclosures/{disclosure_id}/acknowledge",
            cookies=auth_user_cookie(other_renter),
        )
        assert r.status_code == 403, r.text


class TestDisclosureAttachmentAndDeliveryParty:
    """AC-16: a real hash-verified document is attached at first delivery,
    and the delivery records which party received it."""

    def test_delivering_a_disclosure_attaches_a_real_document(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac16-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac16-renter1@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)

        disclosures = client.get(f"/api/leasing/agreements/{agreement_id}/disclosures", cookies=admin_cookies).json()
        disclosure_id = disclosures[0]["id"]

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/disclosures/{disclosure_id}/deliver",
            json={"toParty": "renter"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["deliveredToParty"] == "renter"
        assert r.json()["documentContentHash"]

        r = client.get(f"/api/leasing/agreements/{agreement_id}/disclosures/{disclosure_id}/document", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/pdf"
        assert len(r.content) > 0

    def test_document_download_before_delivery_is_404(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac16-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac16-renter2@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]

        disclosures = client.get(f"/api/leasing/agreements/{agreement_id}/disclosures", cookies=admin_cookies).json()
        disclosure_id = disclosures[0]["id"]

        r = client.get(f"/api/leasing/agreements/{agreement_id}/disclosures/{disclosure_id}/document", cookies=admin_cookies)
        assert r.status_code == 404, r.text


class TestApprovedOptionalClauseSelection:
    """AC-17: optional-term selection is allow-listed against the resolved
    profile's own optional_clause_ids -- nothing outside that set, and
    nothing free-text, can enter the snapshot."""

    def test_selecting_an_approved_optional_clause_is_recorded_in_the_snapshot(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac17-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac17-renter1@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)

        r = client.post(
            f"/api/leasing/offers/{offer_id}/agreement",
            json={"selectedOptionalClauseIds": ["pets_and_animals_policy"]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        agreement_id = r.json()["id"]

        agreement = db_session.get(Agreement, agreement_id)
        snapshot = agreement.versions[0].snapshot
        assert "pets_and_animals_policy" in snapshot["clause_ids"]
        assert snapshot["optional_clause_ids_selected"] == ["pets_and_animals_policy"]

    def test_an_unapproved_clause_id_is_rejected(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac17-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac17-renter2@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)

        r = client.post(
            f"/api/leasing/offers/{offer_id}/agreement",
            json={"selectedOptionalClauseIds": ["some_free_text_clause_nobody_approved"]},
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text


class TestOccupancyTerminationDates:
    """AC-20: notice/liability/termination-effective dates are stored
    separately from the physical move-out date."""

    def test_end_occupancy_accepts_distinct_dates(self, client, db_session: Session):
        listing_id, room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac20-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac20-renter1@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today(),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)
        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)
        from tests.test_agreement_engine_foundation import _pay_off_agreement_obligations
        _pay_off_agreement_obligations(client, db_session, admin_cookies, agreement_id)

        r = client.post(f"/api/occupancy/agreements/{agreement_id}/confirm-move-in", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        occupancy_id = r.json()["id"]

        notice_date = "2026-01-01T00:00:00Z"
        liability_end = (date.today() + timedelta(days=10)).isoformat()
        termination_effective = (date.today() + timedelta(days=15)).isoformat()
        physical_move_out = (date.today() + timedelta(days=20)).isoformat()

        r = client.post(
            f"/api/occupancy/{occupancy_id}/end",
            json={
                "noticeGivenAt": notice_date, "liabilityEndDate": liability_end,
                "terminationEffectiveDate": termination_effective, "moveOutDate": physical_move_out,
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["liabilityEndDate"] == liability_end
        assert body["terminationEffectiveDate"] == termination_effective
        assert body["moveOutDate"] == physical_move_out
        assert body["noticeGivenAt"] is not None

    def test_end_occupancy_without_a_body_defaults_every_date_to_today(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac20-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac20-renter2@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today(),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)
        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)
        from tests.test_agreement_engine_foundation import _pay_off_agreement_obligations
        _pay_off_agreement_obligations(client, db_session, admin_cookies, agreement_id)

        r = client.post(f"/api/occupancy/agreements/{agreement_id}/confirm-move-in", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        occupancy_id = r.json()["id"]

        r = client.post(f"/api/occupancy/{occupancy_id}/end", json={}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        today = date.today().isoformat()
        assert body["moveOutDate"] == today
        assert body["liabilityEndDate"] == today
        assert body["terminationEffectiveDate"] == today
        assert body["noticeGivenAt"] is None


class TestClauseGovernanceVersioningAndRollback:
    """AC-25: the clause registry is really versioned (not mutated in
    place), effective-dated, and rollback-capable."""

    def test_approving_a_new_version_retires_the_previous_one_and_flows_into_new_snapshots(self, client, db_session: Session):
        from app.services.agreement_profile import ensure_default_clause_registry

        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac25-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac25-renter1@test.com")
        _make_agreement_eligible(db_session, listing_id)

        ensure_default_clause_registry(db_session)
        db_session.commit()

        signatures_rows = client.get("/api/leasing/agreement-clauses?clause_id=signatures", cookies=admin_cookies).json()
        assert len(signatures_rows) == 1
        v1 = signatures_rows[0]
        assert v1["status"] == "APPROVED"
        assert v1["version"] == 1

        r = client.post(
            "/api/leasing/agreement-clauses",
            json={
                "clauseId": "signatures", "jurisdictionScope": SUPPORTED_JURISDICTION, "agreementClass": AGREEMENT_CLASS,
                "mandatoryLevel": "MANDATORY", "title": "Signatures v2", "approvalNote": "test revision",
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        v2 = r.json()
        assert v2["version"] == 2
        assert v2["status"] == "DRAFT"

        r = client.post(f"/api/leasing/agreement-clauses/{v2['id']}/approve", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "APPROVED"
        assert r.json()["effectiveFrom"] is not None

        v1_row = db_session.get(ClauseDefinition, v1["id"])
        db_session.refresh(v1_row)
        assert v1_row.status == "RETIRED"
        assert v1_row.effective_to is not None

        # A fresh agreement created now must resolve off v2, not v1.
        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        agreement = db_session.get(Agreement, r.json()["id"])
        assert agreement.versions[0].snapshot["clause_versions"]["signatures"] == 2

        # Rollback: v2 turns out to be bad -- reactivate v1.
        r = client.post(f"/api/leasing/agreement-clauses/{v1['id']}/rollback", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "APPROVED"

        v2_row = db_session.get(ClauseDefinition, v2["id"])
        db_session.refresh(v2_row)
        assert v2_row.status == "RETIRED"

    def test_clause_governance_routes_require_super_admin(self, client, db_session: Session):
        plain_admin = _make_admin(db_session, email="ac25-plain-admin@test.com", role="admin")
        r = client.post(
            "/api/leasing/agreement-clauses",
            json={
                "clauseId": "x", "jurisdictionScope": "England", "agreementClass": "room_share_agreement",
                "mandatoryLevel": "OPTIONAL", "title": "x",
            },
            cookies=auth_admin_cookie(plain_admin),
        )
        assert r.status_code == 403, r.text


class TestWetInkSignatureFallback:
    """AC-11: a real, functioning alternate execution method -- a scanned
    signature upload, not just an unused enum value."""

    def test_admin_records_a_wet_ink_signature_for_the_provider(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac11-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac11-renter1@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)

        fake_pdf = b"%PDF-1.4 fake scanned signature page"
        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/sign/wet-ink?as_party=provider",
            files={"scan": ("signed.pdf", BytesIO(fake_pdf), "application/pdf")},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PARTIALLY_EXECUTED"
        assert r.json()["signedByProviderAt"] is not None

        events = list(db_session.query(SignatureEvent).filter(SignatureEvent.agreement_id == agreement_id))
        assert len(events) == 1
        assert events[0].method == "WET_INK"
        assert events[0].evidence_storage_ref
        assert events[0].document_hash

        r = client.get(f"/api/leasing/agreements/{agreement_id}/signature-events", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()[0]["method"] == "WET_INK"

    def test_wet_ink_upload_rejects_non_pdf_content_type(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ac11-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ac11-renter2@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/sign/wet-ink?as_party=provider",
            files={"scan": ("signed.jpg", BytesIO(b"not a pdf"), "image/jpeg")},
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text
