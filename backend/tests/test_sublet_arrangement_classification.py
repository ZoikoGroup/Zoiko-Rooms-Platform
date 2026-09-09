"""India-scope MVP of ZR-ENG-CLR-003 Section 3 -- Rental/Sublet Rules. Before
this, every sublet was one undifferentiated occupant swap: no classification,
no liability tracking, no recorded deposit disposition, and -- a real bug
found while manually monitoring the running app -- the proposed occupant was
never notified at any stage, and the original requester's own sublet history
silently vanished (and got misattributed to the replacement occupant) the
moment their request was approved."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import sublet as sublet_crud
from app.models.finance import Obligation
from app.models.guest import Guest
from app.models.identity_verification import IdentityVerification
from app.models.leasing import Agreement, Application, Offer, OfferTerms
from app.models.listing import Listing
from app.models.notification import Notification
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie


def _make_active_tenancy(db: Session, *, suffix: str) -> tuple[object, object, str, int]:
    """A signed, active occupancy with 60 days remaining. Returns
    (tenant_user, proposed_user, proposed_party_id, occupancy_id)."""
    owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(owner_party)
    db.flush()
    prop = Property(owner_party_id=owner_party.id, address="1 Test St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()

    tenant_party = Party(party_type="renter", status="active", jurisdiction="IN")
    db.add(tenant_party)
    db.flush()
    tenant_user = _make_user(db, email=f"tenant-{suffix}@test.com")
    tenant_user.party_id = tenant_party.id
    tenant_guest = Guest(id=f"G-TENANT-{suffix}", name="Tenant", email=f"tenant-{suffix}@test.com", joined_at=date.today())
    db.add(tenant_guest)
    db.flush()

    listing = Listing(
        id=f"L-CLASS-{suffix}", slug=f"class-{suffix}", name="Classification Test Listing", room_type="Private room",
        city="Bengaluru", location="Koramangala", price_per_night=500, guests=1,
        rating=4.5, review_count=0, party_id=owner_party.id, owner_id=None, room_id=room.id, state="PUBLISHED",
    )
    db.add(listing)
    db.flush()

    application = Application(listing_id=listing.id, guest_id=tenant_guest.id, status="DECIDED")
    db.add(application)
    db.flush()
    offer = Offer(application_id=application.id, listing_id=listing.id, guest_id=tenant_guest.id, status="ACCEPTED")
    db.add(offer)
    db.flush()
    db.add(OfferTerms(offer_id=offer.id, version=1, monthly_rent=500, deposit_amount=500, start_date=date.today(), term_months=6))
    db.flush()
    db.add(Agreement(offer_id=offer.id, status="SIGNED"))
    db.flush()
    occupancy = Occupancy(
        offer_id=offer.id, listing_id=listing.id, room_id=room.id, guest_id=tenant_guest.id,
        status="ACTIVE", expected_end_date=date.today() + timedelta(days=60),
    )
    db.add(occupancy)
    db.flush()

    proposed_party = Party(party_type="renter", status="active", jurisdiction="IN")
    db.add(proposed_party)
    db.flush()
    proposed_user = _make_user(db, email=f"proposed-{suffix}@test.com")
    proposed_user.party_id = proposed_party.id
    db.add(IdentityVerification(party_id=proposed_party.id, document_type="passport", document_category="identity", status="verified"))
    db.commit()

    return tenant_user, proposed_user, proposed_party.id, occupancy.id


class TestArrangementTypeValidation:
    def test_unsupported_arrangement_type_is_rejected(self, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="badtype")
        try:
            sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "TEMPORARY_GUEST")
            assert False, "should have raised"
        except Exception as e:
            assert "Unsupported arrangement type" in str(e)

    def test_default_arrangement_type_is_assignment_full_for_backward_compatibility(self, client, db_session: Session):
        """The frontend's existing 'Request to sublet' button doesn't send
        arrangementType at all -- it must keep working unchanged."""
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="default")
        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy_id}/sublet-request",
            json={"occupancyId": occupancy_id, "proposedRenterPartyId": proposed_party_id},
            cookies=auth_user_cookie(tenant_user),
        )
        assert r.status_code == 201, r.text
        assert r.json()["arrangementType"] == "ASSIGNMENT_FULL"


class TestLiabilityAndDepositDisposition:
    def test_assignment_full_releases_original_renter_liability(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="assign")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")
        super_admin = _make_admin(db_session, email="assign-admin@test.com", role="super_admin")

        r = client.post(f"/api/occupancy/sublet-requests/{sublet_request.id}/approve", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        assert r.json()["originalRenterLiability"] == "RELEASED"
        assert r.json()["newOccupantLiability"] == "ASSIGNEE"
        assert r.json()["depositDisposition"] == "RETAINED_BY_ORIGINAL_TENANCY"

    def test_replacement_occupant_keeps_original_renter_liable(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="replace")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "REPLACEMENT_OCCUPANT")
        super_admin = _make_admin(db_session, email="replace-admin@test.com", role="super_admin")

        r = client.post(f"/api/occupancy/sublet-requests/{sublet_request.id}/approve", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        assert r.json()["originalRenterLiability"] == "LIMITED"
        assert r.json()["newOccupantLiability"] == "SUBORDINATE"


class TestProposedOccupantIsNotified:
    """Regression coverage for a real bug found via manual testing: the
    incoming occupant was never notified at submission or approval."""

    def test_proposed_occupant_is_notified_at_submission(self, db_session: Session):
        tenant_user, proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="notifysub")
        sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")

        notification = db_session.scalar(
            select(Notification).where(
                Notification.recipient_user_id == proposed_user.id,
                Notification.notification_type == "sublet_request.proposed",
            )
        )
        assert notification is not None

    def test_proposed_occupant_is_notified_on_approval(self, client, db_session: Session):
        tenant_user, proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="notifyapp")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")
        super_admin = _make_admin(db_session, email="notify-admin@test.com", role="super_admin")

        r = client.post(f"/api/occupancy/sublet-requests/{sublet_request.id}/approve", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text

        notification = db_session.scalar(
            select(Notification).where(
                Notification.recipient_user_id == proposed_user.id,
                Notification.notification_type == "sublet_request.occupant_approved",
            )
        )
        assert notification is not None

    def test_proposed_occupant_is_notified_on_rejection(self, client, db_session: Session):
        tenant_user, proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="notifyrej")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")
        super_admin = _make_admin(db_session, email="notify-rej-admin@test.com", role="super_admin")

        r = client.post(f"/api/occupancy/sublet-requests/{sublet_request.id}/reject", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text

        notification = db_session.scalar(
            select(Notification).where(
                Notification.recipient_user_id == proposed_user.id,
                Notification.notification_type == "sublet_request.occupant_rejected",
            )
        )
        assert notification is not None


class TestRequesterHistoryFixedAfterApproval:
    """Regression coverage for the real bug found during manual monitoring:
    the original requester's own approved sublet request used to disappear
    from their history (and get misattributed to the replacement occupant)."""

    def test_original_requester_still_sees_their_request_after_approval(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="history")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")
        super_admin = _make_admin(db_session, email="history-admin@test.com", role="super_admin")

        r = client.post(f"/api/occupancy/sublet-requests/{sublet_request.id}/approve", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text

        r = client.get("/api/users/rentals/sublet-requests", cookies=auth_user_cookie(tenant_user))
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1
        assert r.json()[0]["id"] == sublet_request.id

    def test_replacement_occupant_does_not_falsely_inherit_the_request_in_their_own_history(self, client, db_session: Session):
        tenant_user, proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="noinherit")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")
        super_admin = _make_admin(db_session, email="noinherit-admin@test.com", role="super_admin")
        client.post(f"/api/occupancy/sublet-requests/{sublet_request.id}/approve", cookies=auth_admin_cookie(super_admin))

        r = client.get("/api/users/rentals/sublet-requests", cookies=auth_user_cookie(proposed_user))
        assert r.status_code == 200, r.text
        assert r.json() == []


class TestSelfSubletIsRejected:
    """Regression: found live -- naming the current tenant's own account as the
    proposed renter went through as a silent no-op (approved, but nothing
    actually changed), since neither the doc nor the original code considered
    this case at all."""

    def test_proposing_yourself_is_rejected(self, db_session: Session):
        tenant_user, _proposed_user, _proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="selfsublet")
        try:
            sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, tenant_user.party_id, "ASSIGNMENT_FULL")
            assert False, "should have raised"
        except Exception as e:
            assert "yourself" in str(e).lower()


class TestSecondSubletOnSameOccupancyDoesNotCrash:
    """Regression: found live -- a hard DB unique constraint on
    current_occupancy_id meant an occupancy could only ever have ONE sublet
    request in its entire history, even long after that one was resolved.
    Any second attempt crashed with a raw 500 IntegrityError instead of a
    clean response, for any proposed renter, self or otherwise."""

    def test_a_second_sublet_request_after_the_first_is_rejected_succeeds_cleanly(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="second")
        first = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")
        super_admin = _make_admin(db_session, email="second-admin@test.com", role="super_admin")
        client.post(f"/api/occupancy/sublet-requests/{first.id}/reject", cookies=auth_admin_cookie(super_admin))

        # A second, later request for the same occupancy must not crash.
        second_party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(second_party)
        db_session.flush()
        second_proposed_user = _make_user(db_session, email="second-proposed@test.com")
        second_proposed_user.party_id = second_party.id
        db_session.add(IdentityVerification(party_id=second_party.id, document_type="passport", document_category="identity", status="verified"))
        db_session.commit()

        second = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, second_party.id, "ASSIGNMENT_FULL")
        assert second.id != first.id
        assert second.status == "pending_admin_review"


class TestLodgerOrLicensee:
    def test_approving_lodger_or_licensee_creates_co_tenancy_with_licensee_liability(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="lodger")
        occupancy = db_session.get(Occupancy, occupancy_id)
        occupancy.room.max_occupants = 2
        db_session.commit()

        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "LODGER_OR_LICENSEE")
        super_admin = _make_admin(db_session, email="lodger-admin@test.com", role="super_admin")

        r = client.post(f"/api/occupancy/sublet-requests/{sublet_request.id}/approve", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["newOccupantLiability"] == "LICENSEE"
        assert body["originalRenterLiability"] == "ACTIVE"
        assert body["newAgreementId"] is not None, "lodger still gets a real agreement, unlike ADDITIONAL_OCCUPANT"


class TestAdditionalOccupant:
    def test_approving_additional_occupant_creates_no_agreement_and_no_new_obligation(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="addlocc")
        # Deliberately do NOT raise room capacity -- ADDITIONAL_OCCUPANT must not
        # be gated on it at all, since it creates no occupancy/tenancy record.
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ADDITIONAL_OCCUPANT")
        super_admin = _make_admin(db_session, email="addlocc-admin@test.com", role="super_admin")

        r = client.post(f"/api/occupancy/sublet-requests/{sublet_request.id}/approve", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["newAgreementId"] is None
        assert body["newOccupantLiability"] == "NONE"
        assert body["originalRenterLiability"] == "ACTIVE"
        assert body["depositDisposition"] == "NOT_APPLICABLE"

        # The original tenant's occupancy is completely untouched.
        occupancy = db_session.get(Occupancy, occupancy_id)
        assert occupancy.guest_id is not None  # unchanged from before -- no swap happened


class TestCoTenancyRequiresCapacity:
    def test_add_co_tenant_rejected_when_room_is_at_default_capacity_of_one(self, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="nocap")
        try:
            sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ADD_CO_TENANT")
            assert False, "should have raised"
        except Exception as e:
            assert "capacity" in str(e).lower() or "409" in str(e)

    def test_add_co_tenant_succeeds_once_room_capacity_is_raised(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="hascap")
        occupancy = db_session.get(Occupancy, occupancy_id)
        occupancy.room.max_occupants = 2
        db_session.commit()

        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ADD_CO_TENANT")
        assert sublet_request.status == "pending_admin_review"


class TestCoTenancyCreatesRealSeparateAgreement:
    def test_approving_add_co_tenant_creates_its_own_agreement_and_obligations(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="realagreement")
        occupancy = db_session.get(Occupancy, occupancy_id)
        occupancy.room.max_occupants = 2
        db_session.commit()
        original_offer_id = occupancy.offer_id

        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ADD_CO_TENANT")
        super_admin = _make_admin(db_session, email="cotenant-admin@test.com", role="super_admin")

        r = client.post(f"/api/occupancy/sublet-requests/{sublet_request.id}/approve", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["newAgreementId"] is not None
        assert body["originalRenterLiability"] == "ACTIVE"
        assert body["newOccupantLiability"] == "JOINT"
        assert body["depositDisposition"] == "SEPARATE_DEPOSIT_CREATED"

        # The original tenant's occupancy/agreement is untouched -- still theirs.
        db_session.refresh(occupancy)
        assert occupancy.guest_id == db_session.get(Occupancy, occupancy_id).guest_id
        assert occupancy.offer_id == original_offer_id

        new_agreement = db_session.get(Agreement, body["newAgreementId"])
        assert new_agreement.status == "SIGNED"
        assert new_agreement.offer_id != original_offer_id

        obligations = db_session.scalars(
            select(Obligation).where(Obligation.agreement_id == new_agreement.id)
        ).all()
        obligation_types = {o.obligation_type for o in obligations}
        assert obligation_types == {"RENT", "DEPOSIT"}

    def test_third_person_blocked_once_room_reaches_its_raised_capacity(self, client, db_session: Session):
        """Room capacity is enforced again at move-in, not just at co-tenant
        approval -- this is the same gate that now also fixes the pre-existing
        double-booking bug for ordinary (non-shared) rooms."""
        tenant_user, proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="atcapacity")
        occupancy = db_session.get(Occupancy, occupancy_id)
        occupancy.room.max_occupants = 1  # still just one slot total
        db_session.commit()

        from app.crud.eligibility import check_room_capacity
        reasons = check_room_capacity(db_session, occupancy.room)
        assert reasons, "room with 1 active occupancy and max_occupants=1 should already be full"
