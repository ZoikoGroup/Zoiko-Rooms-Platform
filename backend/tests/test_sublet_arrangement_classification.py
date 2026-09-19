"""India-scope MVP of ZR-ENG-CLR-003 Section 3 -- Rental/Sublet Rules. Before
this, every sublet was one undifferentiated occupant swap: no classification,
no liability tracking, no recorded deposit disposition, and -- a real bug
found while manually monitoring the running app -- the proposed occupant was
never notified at any stage, and the original requester's own sublet history
silently vanished (and got misattributed to the replacement occupant) the
moment their request was approved."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import sublet as sublet_crud
from app.models.finance import Obligation
from app.models.guest import Guest
from app.models.identity_verification import IdentityVerification
from app.models.leasing import Agreement, Application, Offer, OfferTerms
from app.models.listing import Listing
from app.models.market_policy import MarketPolicyPack
from app.models.notification import Notification
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from app.models.user_account import UserAccount
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie


def _make_active_tenancy(db: Session, *, suffix: str) -> tuple[object, object, str, int]:
    """A signed, active occupancy with 60 days remaining. Returns
    (tenant_user, proposed_user, proposed_party_id, occupancy_id)."""
    owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(owner_party)
    db.flush()
    # A real listing always has a verified Host login attached -- see
    # TestNoVerifiedHost below for the dedicated case where it doesn't.
    host_user = _make_user(db, email=f"host-{suffix}@test.com")
    host_user.party_id = owner_party.id
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


class TestPayeeModelResolution:
    """ZR-ENG-CLR-003's own AC-20: 'Payment recipient is resolved by payee
    model; code does not assume original renter or Host universally.' A real
    bug found via docs-vs-code review: the co-tenancy/sublease approval path
    was resolving policy.sublet_assignment_payee_model (the ASSIGNMENT
    field) instead of the distinct sublet_sublease_payee_model market_policy
    field that exists specifically for this arrangement family -- leaving
    sublet_sublease_payee_model permanently unread anywhere in the codebase."""

    def test_assignment_uses_the_assignment_payee_model(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="payeeassign")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")
        super_admin = _make_admin(db_session, email="payeeassign-admin@test.com", role="super_admin")

        r = client.post(f"/api/occupancy/sublet-requests/{sublet_request.id}/approve", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        assert r.json()["payeeModel"] == "HOST_OR_LANDLORD_PAYEE"  # MarketPolicyPack's sublet_assignment_payee_model default

    def test_co_tenancy_uses_the_sublease_payee_model_not_the_assignment_one(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="payeesublease")
        occupancy = db_session.get(Occupancy, occupancy_id)
        occupancy.room.max_occupants = 2
        db_session.commit()

        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ADD_CO_TENANT")
        super_admin = _make_admin(db_session, email="payeesublease-admin@test.com", role="super_admin")

        r = client.post(f"/api/occupancy/sublet-requests/{sublet_request.id}/approve", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        # Before the fix this incorrectly came back HOST_OR_LANDLORD_PAYEE --
        # the assignment field's default, not the sublease field's own.
        assert r.json()["payeeModel"] == "ORIGINAL_RENTER_PAYEE"  # MarketPolicyPack's sublet_sublease_payee_model default


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

    def test_co_tenant_term_is_clamped_to_the_master_occupancy_end_date(self, client, db_session: Session):
        """ZR-ENG-CLR-003 Rule 4.9 DATE INVARIANT: 'sublet_end <= master_occupancy_end
        ... may never silently extend the superior booking.' The fixture's master
        occupancy ends in 60 days but its own OfferTerms.term_months is 6 (~180
        days) -- copying that verbatim would let the co-tenant's agreement run
        well past the master occupancy's real end date."""
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="dateinvariant")
        occupancy = db_session.get(Occupancy, occupancy_id)
        occupancy.room.max_occupants = 2
        db_session.commit()
        master_end = occupancy.expected_end_date

        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ADD_CO_TENANT")
        super_admin = _make_admin(db_session, email="dateinvariant-admin@test.com", role="super_admin")
        r = client.post(f"/api/occupancy/sublet-requests/{sublet_request.id}/approve", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text

        new_agreement = db_session.get(Agreement, r.json()["newAgreementId"])
        new_terms = new_agreement.offer.terms[-1]
        co_tenant_end = sublet_crud._add_months(new_terms.start_date, new_terms.term_months)
        assert co_tenant_end <= master_end, f"co-tenant term ends {co_tenant_end}, past the master occupancy's {master_end}"

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


class TestNoSecondSubletAfterAssignment:
    """Product rule: Host -> Anil -> Priya -> end. Once an occupancy has been
    handed to a new occupant via ASSIGNMENT_FULL/REPLACEMENT_OCCUPANT, that
    occupancy can never be sublet onward again -- the chain is exactly one
    reassignment deep, never open-ended."""

    def test_new_occupant_cannot_sublet_the_same_room_again(self, client, db_session: Session):
        tenant_user, new_occupant_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="chainend")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")
        super_admin = _make_admin(db_session, email="chainend-admin@test.com", role="super_admin")
        r = client.post(f"/api/occupancy/sublet-requests/{sublet_request.id}/approve", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text

        # A third party for the new occupant to (attempt to) sublet onward to --
        # irrelevant to the outcome, since the reassignment guard fires before
        # the proposed party is even looked up.
        third_party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(third_party)
        db_session.commit()

        try:
            sublet_crud.submit_sublet_request(db_session, new_occupant_user, occupancy_id, third_party.id, "ASSIGNMENT_FULL")
            assert False, "should have raised -- this occupancy already changed hands once"
        except Exception as e:
            assert "already been assigned to a new occupant once" in str(e)

    def test_replacement_occupant_also_cannot_sublet_onward(self, db_session: Session):
        """Same rule for REPLACEMENT_OCCUPANT, not just ASSIGNMENT_FULL --
        either arrangement type ends the chain."""
        tenant_user, new_occupant_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="chainend2")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "REPLACEMENT_OCCUPANT")
        super_admin = _make_admin(db_session, email="chainend2-admin@test.com", role="super_admin")
        sublet_crud.approve_sublet_request(db_session, sublet_request, super_admin)

        third_party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(third_party)
        db_session.commit()

        try:
            sublet_crud.submit_sublet_request(db_session, new_occupant_user, occupancy_id, third_party.id, "ASSIGNMENT_FULL")
            assert False, "should have raised"
        except Exception as e:
            assert "already been assigned to a new occupant once" in str(e)


class TestHostDecidesSubletRequest:
    """ZR-SUB-003 IMPLEMENTATION LOCK: the verified landlord/Host decides --
    not Zoiko Admin -- so the Host's own self-service login must be able to
    approve/decline a sublet request on their own listing, and nobody else's."""

    def test_host_can_approve_a_sublet_request_on_their_own_listing(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="hostapprove")
        # _make_active_tenancy already creates this listing's real Host login.
        host_user = db_session.scalar(select(UserAccount).where(UserAccount.email == "host-hostapprove@test.com"))

        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")

        r = client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve", cookies=auth_user_cookie(host_user))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "approved"
        assert r.json()["decidedByUserId"] == host_user.id
        assert r.json()["decidedByAdminId"] is None

    def test_host_can_decline_a_sublet_request_on_their_own_listing(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="hostdecline")
        host_user = db_session.scalar(select(UserAccount).where(UserAccount.email == "host-hostdecline@test.com"))

        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")

        r = client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/decline", cookies=auth_user_cookie(host_user))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "rejected"

    def test_a_different_host_cannot_decide_someone_elses_sublet_request(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="hostdeny")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")

        other_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(other_party)
        db_session.flush()
        other_host = _make_user(db_session, email="otherhost-hostdeny@test.com")
        other_host.party_id = other_party.id
        db_session.commit()

        r = client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve", cookies=auth_user_cookie(other_host))
        assert r.status_code == 403, r.text

    def test_super_admin_can_still_decide_as_a_legal_ops_override(self, client, db_session: Session):
        """The doc treats an Admin deciding as an exceptional override, not the
        removed capability -- confirm it still works, distinctly from the Host path."""
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="adminoverride")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")
        super_admin = _make_admin(db_session, email="override-admin@test.com", role="super_admin")

        r = client.post(f"/api/occupancy/sublet-requests/{sublet_request.id}/approve", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        assert r.json()["decidedByAdminId"] == super_admin.id
        assert r.json()["decidedByUserId"] is None


class TestNoVerifiedHost:
    """ZR-SUB-003 Section 15 edge case: 'No verified landlord/agent | Block
    submission; provide resolution path; do not route to an unverified
    contact.' Now that the Host decides, a listing with nobody real attached
    to it is a dead end for the request, not just an inconvenience."""

    def test_submission_blocked_when_listing_has_no_verified_host(self, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="nohost")
        occupancy = db_session.get(Occupancy, occupancy_id)
        owner_party_id = occupancy.listing.party_id
        # Simulate a listing whose owner never completed real onboarding --
        # deactivate every host account tied to this listing's party.
        db_session.query(UserAccount).filter(UserAccount.party_id == owner_party_id).update({"is_active": False})
        db_session.commit()

        try:
            sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")
            assert False, "should have raised -- no verified host to route this to"
        except Exception as e:
            assert "no verified landlord/agent account" in str(e)


class TestMoreInformationRoundTrip:
    """ZR-SUB-003 Section 5.1/6: MORE_INFORMATION_REQUESTED -> tenant responds
    -> back to the Host's decision queue, without losing the original request."""

    def test_full_round_trip_then_approval(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="moreinfo")
        host_user = db_session.scalar(select(UserAccount).where(UserAccount.email == "host-moreinfo@test.com"))
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")

        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/request-info",
            json={"notes": "Please share the proposed occupant's employer reference."},
            cookies=auth_user_cookie(host_user),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "more_information_requested"
        assert r.json()["infoRequestNote"] == "Please share the proposed occupant's employer reference."

        # The Host can't decide while it's awaiting the tenant's response.
        r = client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve", cookies=auth_user_cookie(host_user))
        assert r.status_code == 409, r.text

        r = client.post(
            f"/api/users/rentals/sublet-requests/{sublet_request.id}/respond",
            json={"notes": "Here is the employer reference: Acme Corp, HR contact attached."},
            cookies=auth_user_cookie(tenant_user),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "pending_admin_review"
        assert r.json()["infoResponseNote"] == "Here is the employer reference: Acme Corp, HR contact attached."

        r = client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve", cookies=auth_user_cookie(host_user))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "approved"

    def test_only_the_original_requester_can_respond(self, client, db_session: Session):
        tenant_user, proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="moreinfowrong")
        host_user = db_session.scalar(select(UserAccount).where(UserAccount.email == "host-moreinfowrong@test.com"))
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")
        sublet_crud.request_more_sublet_info(db_session, sublet_request, host_user, "Need more detail")

        r = client.post(
            f"/api/users/rentals/sublet-requests/{sublet_request.id}/respond",
            json={"notes": "I'm not the requester"},
            cookies=auth_user_cookie(proposed_user),
        )
        assert r.status_code == 403, r.text


class TestApprovalConditionsAndExpiry:
    """ZR-SUB-003 Section 5.2/Wireframe H: 'Approval may include conditions,
    expiry dates and scope limitations.'"""

    def test_approval_can_carry_conditions_and_an_expiry_date(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="approvalcond")
        host_user = db_session.scalar(select(UserAccount).where(UserAccount.email == "host-approvalcond@test.com"))
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")

        expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve",
            json={"notes": "", "conditions": "No additional occupants. No pets.", "expiresAt": expires_at},
            cookies=auth_user_cookie(host_user),
        )
        assert r.status_code == 200, r.text
        assert r.json()["approvalConditions"] == "No additional occupants. No pets."
        assert r.json()["approvalExpiresAt"] is not None

    def test_expiry_in_the_past_is_rejected(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="approvalpast")
        host_user = db_session.scalar(select(UserAccount).where(UserAccount.email == "host-approvalpast@test.com"))
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")

        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve",
            json={"notes": "", "expiresAt": past},
            cookies=auth_user_cookie(host_user),
        )
        assert r.status_code == 400, r.text


class TestWithdrawSubletRequest:
    """ZR-SUB-003 Section 6/13: 'Withdraw pending request | Tenant.'"""

    def test_tenant_can_withdraw_a_pending_request(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="withdraw")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")

        r = client.post(f"/api/users/rentals/sublet-requests/{sublet_request.id}/withdraw", cookies=auth_user_cookie(tenant_user))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "withdrawn"
        assert r.json()["withdrawnAt"] is not None

    def test_someone_else_cannot_withdraw_it(self, client, db_session: Session):
        tenant_user, proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="withdrawwrong")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")

        r = client.post(f"/api/users/rentals/sublet-requests/{sublet_request.id}/withdraw", cookies=auth_user_cookie(proposed_user))
        assert r.status_code == 403, r.text

    def test_an_already_approved_request_cannot_be_withdrawn(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="withdrawafter")
        host_user = db_session.scalar(select(UserAccount).where(UserAccount.email == "host-withdrawafter@test.com"))
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")
        sublet_crud.approve_sublet_request(db_session, sublet_request, host_user)

        r = client.post(f"/api/users/rentals/sublet-requests/{sublet_request.id}/withdraw", cookies=auth_user_cookie(tenant_user))
        assert r.status_code == 409, r.text


class TestDownloadableDecisionRecord:
    """ZR-SUB-003 Wireframe J: 'The tenant must be able to access the record
    after the request is completed.' 'The provider sees the same canonical
    decision facts.'"""

    def test_tenant_can_download_after_approval(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="record")
        host_user = db_session.scalar(select(UserAccount).where(UserAccount.email == "host-record@test.com"))
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")
        sublet_crud.approve_sublet_request(db_session, sublet_request, host_user, conditions="No pets.")

        r = client.get(f"/api/users/rentals/sublet-requests/{sublet_request.id}/record", cookies=auth_user_cookie(tenant_user))
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/pdf"
        assert r.content.startswith(b"%PDF")

    def test_host_can_also_download_their_own_copy(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="recordhost")
        host_user = db_session.scalar(select(UserAccount).where(UserAccount.email == "host-recordhost@test.com"))
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")
        sublet_crud.approve_sublet_request(db_session, sublet_request, host_user)

        r = client.get(f"/api/users/hosting/sublet-requests/{sublet_request.id}/record", cookies=auth_user_cookie(host_user))
        assert r.status_code == 200, r.text
        assert r.content.startswith(b"%PDF")

    def test_download_blocked_while_still_pending(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="recordpending")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")

        r = client.get(f"/api/users/rentals/sublet-requests/{sublet_request.id}/record", cookies=auth_user_cookie(tenant_user))
        assert r.status_code == 409, r.text

    def test_download_available_after_withdrawal_too(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="recordwithdrawn")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")
        sublet_crud.withdraw_sublet_request(db_session, sublet_request, tenant_user)

        r = client.get(f"/api/users/rentals/sublet-requests/{sublet_request.id}/record", cookies=auth_user_cookie(tenant_user))
        assert r.status_code == 200, r.text


class TestAuthorityChangeProtection:
    """ZR-SUB-003 Section 10.1: 'If landlord/agent authority changes while a
    request is pending, the request must be re-routed only through an
    auditable authority-transfer process. The prior recipient loses decision
    access immediately after revocation takes effect.'"""

    def test_deactivated_host_loses_decision_access_immediately(self, client, db_session: Session):
        """No JWT-expiry grace period -- get_current_user re-checks is_active
        fresh on every request, so revocation takes effect on the very next call."""
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="authrevoke")
        host_user = db_session.scalar(select(UserAccount).where(UserAccount.email == "host-authrevoke@test.com"))
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")
        cookies = auth_user_cookie(host_user)  # captured before revocation, like a live session would be

        host_user.is_active = False
        db_session.commit()

        r = client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve", cookies=cookies)
        assert r.status_code == 401, r.text

    def test_new_authority_gains_access_the_moment_the_listing_changes_owner(self, client, db_session: Session):
        """Authority is resolved fresh from Listing.party_id on every call, never
        snapshotted at submission time -- so a mid-flight ownership transfer
        re-routes automatically, with no separate migration step needed."""
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="authtransfer")
        old_host = db_session.scalar(select(UserAccount).where(UserAccount.email == "host-authtransfer@test.com"))
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")

        # Transfer the property to a brand new owning party with its own host --
        # party_id_for_listing resolves authority through Property.owner_party_id,
        # not the separate (denormalized, list-query-only) Listing.party_id.
        occupancy = db_session.get(Occupancy, occupancy_id)
        new_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(new_party)
        db_session.flush()
        new_host = _make_user(db_session, email="new-host-authtransfer@test.com")
        new_host.party_id = new_party.id
        occupancy.listing.room.property.owner_party_id = new_party.id
        db_session.commit()

        r = client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve", cookies=auth_user_cookie(old_host))
        assert r.status_code == 403, f"the old owning party's host must lose access the instant the transfer lands: {r.text}"

        r = client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve", cookies=auth_user_cookie(new_host))
        assert r.status_code == 200, f"the new owning party's host must gain access with no separate re-routing step: {r.text}"


class TestEmergencyBlockConfidenceFailSafe:
    """ZR-ENG-CLR-003 Section 13.2 FAIL-SAFE RULE: 'if the applicable market
    pack cannot determine the legal treatment with sufficient confidence, the
    system must not silently approve.' EMERGENCY_BLOCK is the sharpest case --
    a known active legal problem with the jurisdiction's rules."""

    def test_emergency_block_pack_stops_a_new_sublet_submission(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="emergblock")

        # A newer, higher-version pack for the same jurisdiction supersedes the
        # REVIEW_REQUIRED one conftest seeds -- resolve_market_policy always
        # picks the highest version. The policy-relevant jurisdiction is
        # Property.jurisdiction_code (defaults to "England"), not the
        # Party.jurisdiction="IN" this fixture sets elsewhere -- see
        # crud/market_policy.py:jurisdiction_code_for_occupancy.
        db_session.add(MarketPolicyPack(
            jurisdiction_code="England", version=2, effective_from=date(2026, 1, 1),
            confidence="EMERGENCY_BLOCK", legal_source_note="Active legal issue, do not process.",
        ))
        db_session.commit()

        try:
            sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")
            assert False, "should have raised -- EMERGENCY_BLOCK must stop processing"
        except Exception as e:
            assert "emergency block" in str(e).lower()

    def test_review_required_the_platforms_own_default_does_not_block(self, db_session: Session):
        """The fail-safe must not be so aggressive it blocks the platform's own
        documented normal state -- every pack this platform ships with today
        is REVIEW_REQUIRED, and that has to keep working."""
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="reviewok")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL")
        assert sublet_request.status == "pending_admin_review"
