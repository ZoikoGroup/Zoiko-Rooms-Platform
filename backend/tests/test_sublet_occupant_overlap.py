"""ZR-ENG-CLR-001 Rule 6 / Section 9 for sublets: taking a tenancy over by
sublet runs the same occupant-overlap check as accepting an offer
(services/overlap.py) -- a near-full overlap with the incoming occupant's
other live tenancy is a BLOCK the approver can only pass with a recorded
override reason; a partial one is approved and flagged REVIEW."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import sublet as sublet_crud
from app.models.guest import Guest
from app.models.leasing import Application, Offer
from app.models.occupancy import Occupancy
from app.models.user_account import UserAccount
from app.services.overlap import evaluate_occupant_overlap
from tests.conftest import auth_user_cookie
from tests.test_sublet_arrangement_classification import _make_active_tenancy


def _host_for(db: Session, suffix: str) -> UserAccount:
    return db.scalar(select(UserAccount).where(UserAccount.email == f"host-{suffix}@test.com"))


def _give_existing_tenancy(db: Session, proposed_user: UserAccount, *, suffix: str, starts_in_days: int) -> Offer:
    """The proposed renter already holds a live 2-month tenancy on another
    listing, starting starts_in_days from today -- about the 60 days the
    sublet itself covers, so starting today is a near-full overlap."""
    guest = Guest(
        id=f"G-PROP-{suffix}", name="Proposed", email=proposed_user.email,
        joined_at=date.today(), user_account_id=proposed_user.id,
    )
    db.add(guest)
    db.flush()
    _make_active_tenancy(db, suffix=f"{suffix}-other")
    other = db.scalar(select(Offer).where(Offer.listing_id == f"L-CLASS-{suffix}-other"))
    other.guest_id = guest.id
    other.application.guest_id = guest.id
    other.terms[-1].start_date = date.today() + timedelta(days=starts_in_days)
    other.terms[-1].term_months = 2
    db.commit()
    return other


def _assignment(db: Session, suffix: str, *, starts_in_days: int = 0):
    tenant_user, proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db, suffix=suffix)
    _give_existing_tenancy(db, proposed_user, suffix=suffix, starts_in_days=starts_in_days)
    sublet_request = sublet_crud.submit_sublet_request(db, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")
    return sublet_request, occupancy_id


class TestSubletOccupantOverlap:
    def test_near_full_overlap_blocks_approval_without_an_override(self, client, db_session: Session):
        sublet_request, occupancy_id = _assignment(db_session, "ovl1")

        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve",
            json={"stepUpPassword": "password123", "authorityConfirmed": True},
            cookies=auth_user_cookie(_host_for(db_session, "ovl1")),
        )
        assert r.status_code == 409, r.text
        assert "overlapping tenancy" in r.text
        db_session.expire_all()
        assert db_session.get(Occupancy, occupancy_id).guest_id == "G-TENANT-ovl1"  # nothing handed over

    def test_pending_request_shows_the_block_to_the_reviewer(self, db_session: Session):
        sublet_request, _ = _assignment(db_session, "ovl2")

        read = sublet_crud.to_sublet_request_read(db_session, sublet_request)
        assert read.occupant_risk_tier == "BLOCK"
        assert "near-full" in read.occupant_risk_reason

    def test_override_reason_lets_it_through_and_is_recorded(self, client, db_session: Session):
        sublet_request, occupancy_id = _assignment(db_session, "ovl3")

        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve",
            json={"stepUpPassword": "password123", "authorityConfirmed": True,
                  "overrideReason": "Moving out of the other room next week"},
            cookies=auth_user_cookie(_host_for(db_session, "ovl3")),
        )
        assert r.status_code == 200, r.text
        assert r.json()["occupantRiskTier"] == "BLOCK"
        assert "Moving out of the other room next week" in r.json()["occupantRiskReason"]
        db_session.expire_all()
        offer = db_session.get(Occupancy, occupancy_id).offer
        assert offer.occupant_risk_tier == "BLOCK"
        assert "override" in offer.occupant_risk_reason

    def test_partial_overlap_is_approved_and_flagged_review(self, client, db_session: Session):
        # The other tenancy starts 45 days in, so only the last ~15 days of
        # the 60 remaining here overlap.
        sublet_request, _ = _assignment(db_session, "ovl4", starts_in_days=45)

        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve",
            json={"stepUpPassword": "password123", "authorityConfirmed": True},
            cookies=auth_user_cookie(_host_for(db_session, "ovl4")),
        )
        assert r.status_code == 200, r.text
        assert r.json()["occupantRiskTier"] == "REVIEW"

    def test_no_other_tenancy_means_no_risk(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="ovl5")
        sublet_request = sublet_crud.submit_sublet_request(
            db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL",
        )
        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve",
            json={"stepUpPassword": "password123", "authorityConfirmed": True},
            cookies=auth_user_cookie(_host_for(db_session, "ovl5")),
        )
        assert r.status_code == 200, r.text
        assert r.json()["occupantRiskTier"] == "NONE"


class TestAssignedTenancyCountsForTheAssignee:
    def test_overlap_follows_the_offer_to_the_assignee(self, client, db_session: Session):
        """After a full assignment the tenancy is the assignee's: it counts
        against them in later overlap checks, not against the released
        original applicant."""
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="ovl6")
        sublet_request = sublet_crud.submit_sublet_request(
            db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL",
        )
        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve",
            json={"stepUpPassword": "password123", "authorityConfirmed": True},
            cookies=auth_user_cookie(_host_for(db_session, "ovl6")),
        )
        assert r.status_code == 200, r.text
        db_session.expire_all()
        offer = db_session.get(Occupancy, occupancy_id).offer
        assignee_guest_id = offer.guest_id
        assert assignee_guest_id != "G-TENANT-ovl6"
        assert db_session.get(Application, offer.application_id).guest_id == "G-TENANT-ovl6"

        window = dict(listing_id="L-ELSEWHERE", start_date=date.today(), term_months=6)
        assert evaluate_occupant_overlap(db_session, occupant_guest_id=assignee_guest_id, **window)[0] == "BLOCK"
        assert evaluate_occupant_overlap(db_session, occupant_guest_id="G-TENANT-ovl6", **window)[0] == "NONE"
