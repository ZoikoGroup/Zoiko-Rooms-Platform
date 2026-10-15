"""publication.requires_approval is platform-fixed: publication is always
automatic, and no market release can turn admin review back on
(services/policy.py:_PLATFORM_FIXED_KEYS). The suite-wide conftest fixture
that pins admin review is test-only scaffolding for older tests."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.crud.occupancy_classification import ensure_default_classification
from app.models.listing import Listing
from app.models.listing_approval import ListingApproval
from app.models.market_release import MarketRelease
from app.services.policy import get_policy, set_policy_overrides
from tests.conftest import auth_user_cookie
from tests.test_listing_workflow import LISTING_PAYLOAD, _make_host_with_room


@pytest.mark.automatic_publication
class TestPublicationIsAlwaysAutomatic:
    def test_a_release_override_to_require_approval_is_ignored(self, client, db_session: Session):
        user, room_id = _make_host_with_room(db_session, email="fixed-host@test.com")
        cookies = auth_user_cookie(user)
        release = MarketRelease(
            jurisdiction="IN-FIXED", status="active", policy_overrides={"publication.requires_approval": True},
        )
        db_session.add(release)
        db_session.commit()
        assert get_policy(release, "publication.requires_approval") is False

        r = client.post("/api/users/hosting/listings", json={**LISTING_PAYLOAD, "roomId": room_id}, cookies=cookies)
        assert r.status_code == 201, r.text
        listing_id = r.json()["id"]
        listing = db_session.get(Listing, listing_id)
        listing.market_release_id = release.id
        ensure_default_classification(db_session, listing.room)
        db_session.commit()

        r = client.post(f"/api/users/hosting/listings/{listing_id}/submit-for-review", cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "PUBLISHED"
        approvals = db_session.query(ListingApproval).join(ListingApproval.listing_version).filter(
            ListingApproval.reviewer_authority_scope == "system",
        ).all()
        assert any(a.decision_reason_code == "all_verifications_passed" for a in approvals)

    def test_set_policy_overrides_drops_the_key(self, db_session: Session):
        release = MarketRelease(jurisdiction="IN-FIXED2", status="active")
        set_policy_overrides(release, {"publication.requires_approval": True, "payment.checkout_lock_duration_minutes": 10})
        assert release.policy_overrides == {"payment.checkout_lock_duration_minutes": 10}
