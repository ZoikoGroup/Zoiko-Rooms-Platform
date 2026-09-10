"""ZR-ENG-CLR-001 Section 1: immutable Listing Version + structured Approval
object model.

Unit tests for the pure classification logic in services/listing_versioning.py,
plus integration tests (over real HTTP routes, matching test_listing_workflow.py's
style) proving the two behaviors the spec actually cares about:

- AC-03: the public read path serves the approved, immutable snapshot -- a
  pending draft edit (material or not) never changes what's already public
  until it's explicitly approved and promoted.
- Rule 2: every APPROVED/REJECTED decision on a version is backed by a
  structured, attributable ListingApproval row, not a bare status flip.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.listing_approval import ListingApproval
from app.models.listing_version import ListingVersion
from app.services.listing_versioning import (
    MATERIAL_FIELDS,
    NON_MATERIAL_FIELDS,
    build_snapshot,
    classify_material_change,
    compute_content_hash,
)
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_listing_workflow import LISTING_PAYLOAD, _make_host_with_room


# ── Unit tests: services/listing_versioning.py ─────────────────────────────


class FakeListing:
    """Bare object with just the VERSIONED_FIELDS attributes build_snapshot needs."""

    def __init__(self, **overrides):
        defaults = {f: f"{f}-value" for f in MATERIAL_FIELDS | NON_MATERIAL_FIELDS}
        defaults.update(overrides)
        for key, value in defaults.items():
            setattr(self, key, value)


class TestBuildSnapshot:
    def test_snapshot_contains_only_versioned_fields(self):
        listing = FakeListing()
        snapshot = build_snapshot(listing)
        assert set(snapshot.keys()) == MATERIAL_FIELDS | NON_MATERIAL_FIELDS

    def test_snapshot_reflects_current_values(self):
        listing = FakeListing(price_per_night=999)
        snapshot = build_snapshot(listing)
        assert snapshot["price_per_night"] == 999


class TestComputeContentHash:
    def test_same_snapshot_same_hash(self):
        snapshot = build_snapshot(FakeListing())
        assert compute_content_hash(snapshot) == compute_content_hash(dict(snapshot))

    def test_different_snapshot_different_hash(self):
        a = build_snapshot(FakeListing(price_per_night=100))
        b = build_snapshot(FakeListing(price_per_night=200))
        assert compute_content_hash(a) != compute_content_hash(b)

    def test_key_order_does_not_affect_hash(self):
        snapshot = build_snapshot(FakeListing())
        reordered = dict(reversed(list(snapshot.items())))
        assert compute_content_hash(snapshot) == compute_content_hash(reordered)


class TestClassifyMaterialChange:
    def test_first_version_is_never_material(self):
        current = build_snapshot(FakeListing())
        flags, is_material = classify_material_change(None, current)
        assert is_material is False
        assert all(v is False for v in flags.values())

    def test_material_field_change_is_material(self):
        previous = build_snapshot(FakeListing())
        current = build_snapshot(FakeListing(price_per_night=12345))
        flags, is_material = classify_material_change(previous, current)
        assert is_material is True
        assert flags["price_per_night"] is True

    def test_non_material_field_change_is_not_material(self):
        previous = build_snapshot(FakeListing())
        current = build_snapshot(FakeListing(description="a brand new description"))
        flags, is_material = classify_material_change(previous, current)
        assert is_material is False
        assert flags["description"] is True

    def test_no_change_at_all_is_not_material(self):
        snapshot = build_snapshot(FakeListing())
        flags, is_material = classify_material_change(snapshot, dict(snapshot))
        assert is_material is False
        assert all(v is False for v in flags.values())


# ── Integration tests: version/approval lifecycle over real routes ─────────


def _create_submit_approve_publish(client, db_session: Session, *, email: str) -> tuple[str, dict, dict]:
    user, room_id = _make_host_with_room(db_session, email=email)
    user_cookies = auth_user_cookie(user)

    r = client.post(
        "/api/users/hosting/listings",
        json={**LISTING_PAYLOAD, "roomId": room_id},
        cookies=user_cookies,
    )
    assert r.status_code == 201, r.text
    listing_id = r.json()["id"]

    r = client.post(f"/api/users/hosting/listings/{listing_id}/submit-for-review", cookies=user_cookies)
    assert r.status_code == 200, r.text

    admin = _make_admin(db_session, email=f"admin-{email}")
    admin_cookies = auth_admin_cookie(admin)
    r = client.post(f"/api/listings/{listing_id}/approve", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/listings/{listing_id}/publish", cookies=admin_cookies)
    assert r.status_code == 200, r.text

    return listing_id, user_cookies, admin_cookies


class TestVersionCreatedOnListingCreate:
    def test_creating_a_listing_creates_version_one_in_draft(self, client, db_session: Session):
        user, room_id = _make_host_with_room(db_session, email="v1@test.com")
        cookies = auth_user_cookie(user)
        r = client.post(
            "/api/users/hosting/listings",
            json={**LISTING_PAYLOAD, "roomId": room_id},
            cookies=cookies,
        )
        assert r.status_code == 201, r.text
        listing_id = r.json()["id"]

        versions = db_session.scalars(
            select(ListingVersion).where(ListingVersion.listing_id == listing_id)
        ).all()
        assert len(versions) == 1
        assert versions[0].version_no == 1
        assert versions[0].approval_status == "DRAFT"
        assert versions[0].is_material is False  # first version is never material


class TestApprovePromotesVersionAndRecordsApproval:
    def test_approve_and_publish_promotes_current_public_version(self, client, db_session: Session):
        listing_id, _, _ = _create_submit_approve_publish(client, db_session, email="v2@test.com")

        version = db_session.scalars(
            select(ListingVersion).where(ListingVersion.listing_id == listing_id)
        ).one()
        assert version.approval_status == "APPROVED"
        assert version.approved_at is not None

        approvals = db_session.scalars(
            select(ListingApproval).where(ListingApproval.listing_version_id == version.id)
        ).all()
        assert len(approvals) == 1
        assert approvals[0].decision == "APPROVED"
        assert approvals[0].reviewer_admin_id is not None

    def test_public_read_serves_the_promoted_version_snapshot(self, client, db_session: Session):
        listing_id, _, _ = _create_submit_approve_publish(client, db_session, email="v3@test.com")

        r = client.get(f"/api/public/listings/{listing_id}")
        assert r.status_code == 200, r.text
        assert r.json()["name"] == LISTING_PAYLOAD["name"]


class TestRejectRecordsApprovalWithoutPromoting:
    def test_reject_records_rejected_decision_and_reason(self, client, db_session: Session):
        user, room_id = _make_host_with_room(db_session, email="v4@test.com")
        user_cookies = auth_user_cookie(user)
        r = client.post(
            "/api/users/hosting/listings",
            json={**LISTING_PAYLOAD, "roomId": room_id},
            cookies=user_cookies,
        )
        listing_id = r.json()["id"]
        client.post(f"/api/users/hosting/listings/{listing_id}/submit-for-review", cookies=user_cookies)

        admin = _make_admin(db_session, email="admin-v4@test.com")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/listings/{listing_id}/reject", json={"reason": "Bad photos"}, cookies=admin_cookies
        )
        assert r.status_code == 200, r.text

        version = db_session.scalars(
            select(ListingVersion).where(ListingVersion.listing_id == listing_id)
        ).one()
        assert version.approval_status == "REJECTED"

        approval = db_session.scalars(
            select(ListingApproval).where(ListingApproval.listing_version_id == version.id)
        ).one()
        assert approval.decision == "REJECTED"
        assert approval.reason_note == "Bad photos"

        # No public version was ever promoted -- the listing was never published.
        r = client.get(f"/api/public/listings/{listing_id}")
        assert r.status_code == 404


class TestMaterialChangeAfterPublishDoesNotAffectPublicContent:
    def test_material_edit_creates_new_draft_but_public_snapshot_unchanged(self, client, db_session: Session):
        listing_id, user_cookies, _ = _create_submit_approve_publish(client, db_session, email="v5@test.com")

        r = client.get(f"/api/public/listings/{listing_id}")
        original_price = r.json()["pricePerNight"]
        assert original_price == LISTING_PAYLOAD["pricePerNight"]

        r = client.put(
            f"/api/users/hosting/listings/{listing_id}",
            json={"pricePerNight": original_price + 500},
            cookies=user_cookies,
        )
        assert r.status_code == 200, r.text

        versions = db_session.scalars(
            select(ListingVersion)
            .where(ListingVersion.listing_id == listing_id)
            .order_by(ListingVersion.version_no)
        ).all()
        assert len(versions) == 2
        assert versions[1].is_material is True
        assert versions[1].approval_status == "DRAFT"  # still pending review

        # Public content is untouched by the pending material edit.
        r = client.get(f"/api/public/listings/{listing_id}")
        assert r.status_code == 200, r.text
        assert r.json()["pricePerNight"] == original_price


class TestNonMaterialChangeAfterPublishAutoPromotes:
    def test_non_material_edit_promotes_immediately_without_review(self, client, db_session: Session):
        listing_id, user_cookies, _ = _create_submit_approve_publish(client, db_session, email="v6@test.com")

        r = client.put(
            f"/api/users/hosting/listings/{listing_id}",
            json={"description": "A freshly updated description"},
            cookies=user_cookies,
        )
        assert r.status_code == 200, r.text

        versions = db_session.scalars(
            select(ListingVersion)
            .where(ListingVersion.listing_id == listing_id)
            .order_by(ListingVersion.version_no)
        ).all()
        assert len(versions) == 2
        assert versions[1].is_material is False
        assert versions[1].approval_status == "APPROVED"  # auto-promoted, no review

        approvals = db_session.scalars(
            select(ListingApproval).where(ListingApproval.listing_version_id == versions[1].id)
        ).all()
        assert len(approvals) == 1
        assert approvals[0].decision_reason_code == "non_material_auto_promote"

        # Public content reflects the auto-promoted change immediately.
        r = client.get(f"/api/public/listings/{listing_id}")
        assert r.status_code == 200, r.text
        assert r.json()["description"] == "A freshly updated description"
