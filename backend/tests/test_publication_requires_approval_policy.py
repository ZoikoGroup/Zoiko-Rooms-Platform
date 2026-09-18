"""ZR-ENG-CLR-001 Section 14 policy key publication.requires_approval:
'Future low-risk automation may approve through the same auditable approval
object.' Never exercised at England launch (the platform default is always
True) -- these tests cover the branch for a future market whose
MarketRelease overrides it to False.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.listing import Listing
from app.models.listing_approval import ListingApproval
from app.models.market_release import MarketRelease
from app.models.notification import Notification
from tests.conftest import auth_user_cookie
from tests.test_listing_workflow import LISTING_PAYLOAD, _make_host_with_room


class TestPublicationRequiresApprovalFalse:
    def test_submit_auto_approves_and_publishes_for_a_low_risk_market(self, client, db_session: Session):
        user, room_id = _make_host_with_room(db_session, email="lowrisk-host@test.com")
        cookies = auth_user_cookie(user)

        release = MarketRelease(
            jurisdiction="IN-LOWRISK", status="active",
            policy_overrides={"publication.requires_approval": False},
        )
        db_session.add(release)
        db_session.commit()

        r = client.post(
            "/api/users/hosting/listings", json={**LISTING_PAYLOAD, "roomId": room_id}, cookies=cookies,
        )
        assert r.status_code == 201, r.text
        listing_id = r.json()["id"]

        listing = db_session.get(Listing, listing_id)
        listing.market_release_id = release.id
        db_session.commit()

        r = client.post(f"/api/users/hosting/listings/{listing_id}/submit-for-review", cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "PUBLISHED"

        approvals = list(
            db_session.query(ListingApproval).join(ListingApproval.listing_version).filter(
                ListingApproval.reviewer_authority_scope == "system",
            )
        )
        assert any(a.decision_reason_code == "publication_requires_approval_false" for a in approvals)

        published_notification = db_session.query(Notification).filter(
            Notification.notification_type == "listing.published",
        ).first()
        assert published_notification is not None

    def test_default_market_still_requires_human_approval(self, client, db_session: Session):
        user, room_id = _make_host_with_room(db_session, email="normal-host@test.com")
        cookies = auth_user_cookie(user)

        r = client.post(
            "/api/users/hosting/listings", json={**LISTING_PAYLOAD, "roomId": room_id}, cookies=cookies,
        )
        assert r.status_code == 201, r.text
        listing_id = r.json()["id"]

        r = client.post(f"/api/users/hosting/listings/{listing_id}/submit-for-review", cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "REVIEW"
