"""Automatic publication: no admin approval step. A listing whose host has
completed identity, property and authority verification is approved by the
system at submit and goes live -- straight away, or once the Listing Fee is
paid. Super admins are told about every automatic publish and can suspend or
quarantine the live listing. A market can still opt back into admin review.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.crud import listing_fee as lf_crud
from app.crud.occupancy_classification import ensure_default_classification
from app.models.listing import Listing
from app.models.listing_approval import ListingApproval
from app.models.market_release import MarketRelease
from app.models.notification import Notification
from app.models.room import Room
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_listing_workflow import LISTING_PAYLOAD, _make_host_with_room
from tests.test_zr_pay_002_acceptance_gates import _make_listing_fee_policy

pytestmark = pytest.mark.automatic_publication


def _create_listing(client, db: Session, email: str, *, publishable: bool = True,
                    overrides: dict | None = None) -> tuple[str, dict]:
    """A listing in an active market whose room has its occupancy class (both
    set up for real hosts: market launch config / room creation)."""
    user, room_id = _make_host_with_room(db, email=email, publishable=publishable)
    cookies = auth_user_cookie(user)
    r = client.post("/api/users/hosting/listings", json={**LISTING_PAYLOAD, "roomId": room_id}, cookies=cookies)
    assert r.status_code == 201, r.text
    release = MarketRelease(jurisdiction=f"IN-{email.split('@')[0]}", status="active",
                            policy_overrides=overrides or {})
    db.add(release)
    db.flush()
    listing = db.get(Listing, r.json()["id"])
    listing.market_release_id = release.id
    ensure_default_classification(db, db.get(Room, room_id))
    db.commit()
    return listing.id, cookies


def _submit(client, listing_id: str, cookies: dict):
    return client.post(f"/api/users/hosting/listings/{listing_id}/submit-for-review", cookies=cookies)


def _notifications(db: Session, kind: str, listing_id: str) -> list[Notification]:
    return list(db.query(Notification).filter(
        Notification.notification_type == kind, Notification.related_entity_id == listing_id))


class TestAutomaticPublication:
    def test_verified_listing_goes_live_at_submit_and_super_admins_are_told(self, client, db_session: Session):
        _make_admin(db_session, email="super-auto@test.com", role="super_admin")
        db_session.commit()
        listing_id, cookies = _create_listing(client, db_session, "auto-live@test.com")

        r = _submit(client, listing_id, cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "PUBLISHED"

        approval = db_session.query(ListingApproval).join(ListingApproval.listing_version).filter(
            ListingApproval.reviewer_authority_scope == "system").one()
        assert approval.decision == "APPROVED"
        assert approval.decision_reason_code == "all_verifications_passed"
        assert _notifications(db_session, "listing.auto_published", listing_id)
        # Nobody is asked to review it.
        assert not _notifications(db_session, "listing.submitted", listing_id)

    def test_missing_verification_refuses_submit_and_keeps_the_draft(self, client, db_session: Session):
        listing_id, cookies = _create_listing(client, db_session, "auto-missing@test.com", publishable=False)

        r = _submit(client, listing_id, cookies)
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail.startswith("Finish these before submitting")
        assert "Property verification is not approved" in detail
        assert "authority" in detail.lower()

        listing = db_session.get(Listing, listing_id)
        db_session.refresh(listing)
        assert listing.state == "DRAFT"
        assert db_session.query(ListingApproval).count() == 0

    def test_fee_market_approves_then_paying_publishes(self, client, db_session: Session):
        _make_admin(db_session, email="super-fee@test.com", role="super_admin")
        _make_listing_fee_policy(db_session)
        db_session.commit()
        listing_id, cookies = _create_listing(client, db_session, "auto-fee@test.com")

        r = _submit(client, listing_id, cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "APPROVED"  # never live unpaid
        assert _notifications(db_session, "listing.approved", listing_id)
        assert not _notifications(db_session, "listing.auto_published", listing_id)

        listing = db_session.get(Listing, listing_id)
        quote = lf_crud.create_quote(db_session, listing, listing.party)
        r = client.post(
            "/api/users/listing-fees/checkout-sessions",
            json={"quoteId": quote.id, "idempotencyKey": "auto-fee-pay", "billingCountry": "GB"},
            cookies=cookies,
        )
        assert r.status_code == 201, r.text
        db_session.refresh(listing)
        assert listing.state == "PUBLISHED"
        assert _notifications(db_session, "listing.auto_published", listing_id)

    def test_super_admin_can_suspend_an_automatically_published_listing(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="super-suspend@test.com", role="super_admin")
        db_session.commit()
        listing_id, cookies = _create_listing(client, db_session, "auto-suspend@test.com")
        assert _submit(client, listing_id, cookies).json()["state"] == "PUBLISHED"

        r = client.post(f"/api/listings/{listing_id}/suspend", json={"reason": "Suspicious photos"},
                        cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "SUSPENDED"

    def test_market_can_opt_back_into_admin_review(self, client, db_session: Session):
        listing_id, cookies = _create_listing(client, db_session, "auto-review@test.com",
                                              overrides={"publication.requires_approval": True})

        r = _submit(client, listing_id, cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "REVIEW"
