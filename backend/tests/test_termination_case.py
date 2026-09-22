"""ZR-ENG-CLR-006 Section 7: renter-initiated early termination -- the first
Section 6 increment. A renter can open a termination case for their own
active occupancy (only for models.termination_case.UNILATERAL_CAUSE_CODES);
the earliest_effective_date is resolved deterministically from the market
pack's termination_notice_days for a notice-based cause, or immediate for an
immediate cause; an admin later finalizes it via the existing end_occupancy,
which links the case and flips it to TERMINATED."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import occupancy as occupancy_crud
from app.crud import termination as termination_crud
from app.crud.party import get_or_create_default_party
from app.models.audit import AuditEvent
from app.models.authority_record import AuthorityRecord
from app.models.finance import Obligation
from app.models.guest import Guest
from app.models.leasing import Agreement, Application, Offer, OfferTerms
from app.models.listing import Listing
from app.models.market_release import MarketRelease
from app.models.market_policy import MarketPolicyPack
from app.models.occupancy import Occupancy
from app.models.occupancy_classification import OccupancyClassification
from app.models.domain_event import DomainEvent
from app.models.property import Property
from app.models.room import Room
from app.models.termination_record import TerminationRecord
from app.schemas.termination import TerminationCaseCreate
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie


def _make_active_occupancy(
    db: Session, *, admin, suffix: str, monthly_rent: float = 1000.0, term_months: int = 12, jurisdiction_code: str = "IN",
):
    owner_party = get_or_create_default_party(db, admin)
    prop = Property(
        owner_party_id=owner_party.id, address=f"{suffix} Term St", city="Bengaluru", status="active",
        jurisdiction_code=jurisdiction_code,
    )
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()

    market_release = db.query(MarketRelease).filter_by(jurisdiction=owner_party.jurisdiction).first()
    if market_release is None:
        market_release = MarketRelease(jurisdiction=owner_party.jurisdiction, status="active", min_stay_nights=30)
        db.add(market_release)
        db.flush()
    db.add(AuthorityRecord(party_id=owner_party.id, room_id=room.id, authority_type="lease", status="verified"))
    db.add(OccupancyClassification(room_id=room.id, classification="long_term_residential", review_state="APPROVED"))
    db.flush()

    listing = Listing(
        id=f"L-TERM-{suffix}", slug=f"term-{suffix.lower()}", name="Termination Test Listing", room_type="Private room",
        city="Bengaluru", location="Koramangala", price_per_night=500, guests=1,
        rating=4.5, review_count=0, party_id=owner_party.id, owner_id=admin.id, room_id=room.id, state="PUBLISHED",
        market_release_id=market_release.id,
    )
    db.add(listing)
    db.flush()

    guest = Guest(id=f"G-TERM-{suffix}", name="Renter", email=f"term-renter-{suffix.lower()}@test.com", joined_at=date.today())
    db.add(guest)
    db.flush()

    application = Application(listing_id=listing.id, guest_id=guest.id, status="DECIDED")
    db.add(application)
    db.flush()
    offer = Offer(application_id=application.id, listing_id=listing.id, guest_id=guest.id, status="ACCEPTED")
    db.add(offer)
    db.flush()
    db.add(OfferTerms(
        offer_id=offer.id, version=1, monthly_rent=monthly_rent, deposit_amount=monthly_rent,
        start_date=date.today(), term_months=term_months,
    ))
    db.flush()
    agreement = Agreement(offer_id=offer.id, status="SIGNED")
    db.add(agreement)
    db.flush()
    db.add(Obligation(
        obligation_type="RENT", money_plane="OCCUPANCY", amount=monthly_rent, currency="INR",
        due_date=date.today(), status="PAID", agreement_id=agreement.id,
    ))
    # dev's occupancy-lifecycle change: the occupancy row itself is now
    # created at PARTIALLY_EXECUTED (PENDING_MOVE_IN), not by confirm_move_in
    # -- this fixture builds the agreement directly (bypassing that normal
    # signing flow), so it creates the same row confirm_move_in now expects
    # to already exist.
    db.add(Occupancy(
        offer_id=offer.id, listing_id=listing.id, room_id=room.id, guest_id=guest.id,
        status="PENDING_MOVE_IN", expected_end_date=date.today() + timedelta(days=30 * term_months),
    ))
    db.commit()

    occupancy = occupancy_crud.confirm_move_in(db, agreement, admin)
    return occupancy, guest, listing, agreement


class TestOpenTerminationCase:
    def test_ordinary_early_exit_resolves_effective_date_from_market_policy(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-notice-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="notice1")
        renter = _make_user(db_session, email="term-notice-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        policy = db_session.query(MarketPolicyPack).filter_by(jurisdiction_code="IN").one()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT", "notes": "moving for a new job"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "EFFECTIVE_DATE_SET"
        assert body["earliestEffectiveDate"] == (date.today() + timedelta(days=policy.termination_notice_days)).isoformat()

    def test_host_fault_resolves_to_immediate_effective_date(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-immediate-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="immediate1")
        renter = _make_user(db_session, email="term-immediate-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        assert r.json()["earliestEffectiveDate"] == date.today().isoformat()

    def test_casualty_or_force_event_resolves_to_immediate_effective_date_for_a_renter(self, client, db_session: Session):
        """CASUALTY_OR_FORCE_EVENT (fire/flood/disaster) is nobody's fault --
        added to IMMEDIATE_CAUSE_CODES alongside PROPERTY_UNINHABITABLE/
        HOST_FAULT_OR_NONPERFORMANCE, same immediate/zero-liability treatment."""
        admin = _make_admin(db_session, email="term-casualty-renter-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="casualty1")
        renter = _make_user(db_session, email="term-casualty-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "CASUALTY_OR_FORCE_EVENT", "notes": "Building flooded overnight"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "EFFECTIVE_DATE_SET"
        assert body["earliestEffectiveDate"] == date.today().isoformat()

    def test_casualty_or_force_event_resolves_to_immediate_effective_date_for_a_host(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-casualty-host-admin@test.com", role="super_admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="casualty2")

        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "CASUALTY_OR_FORCE_EVENT", "notes": "Fire department condemned the building"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        assert r.json()["earliestEffectiveDate"] == date.today().isoformat()

    def test_renter_statutory_right_without_evidence_lands_in_pending_review(self, client, db_session: Session):
        """AC-35/Section 21.1: an unsubstantiated claim of a protected ground
        still opens a case (never rejected outright) but can't auto-resolve
        without evidence -- see EVIDENCE_GATED_CAUSE_CODES."""
        admin = _make_admin(db_session, email="term-statutory-noevidence-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="statutorynoevidence")
        renter = _make_user(db_session, email="term-statutory-noevidence-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_STATUTORY_RIGHT", "notes": "Qualifying safety concern"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "PENDING_REVIEW"
        assert body["earliestEffectiveDate"] is None

    def test_renter_statutory_right_with_evidence_resolves_immediately(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-statutory-evidence-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="statutoryevidence")
        renter = _make_user(db_session, email="term-statutory-evidence-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_STATUTORY_RIGHT", "evidenceRefs": ["doc-123"]},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "EFFECTIVE_DATE_SET"
        assert body["earliestEffectiveDate"] == date.today().isoformat()
        assert body["evidenceRefs"] == ["doc-123"]

        # Section 21.1: a general Host/Admin sees the notes redacted for this
        # sensitive cause code -- but the case itself still resolved, and
        # evidence_refs (opaque IDs, not the sensitive narrative) are not
        # redacted.
        general_admin = _make_admin(db_session, email="term-statutory-evidence-generaladmin@test.com", role="admin")
        db_session.query(Listing).filter_by(id=_listing.id).update({"owner_id": general_admin.id})
        db_session.commit()
        r = client.get(f"/api/occupancy/{occupancy.id}/termination-cases", cookies=auth_admin_cookie(general_admin))
        assert r.status_code == 200, r.text
        [case_read] = r.json()
        assert case_read["notes"] == "[redacted -- sensitive protected-ground details, Super Admin only]"

    def test_a_cause_this_build_cannot_auto_resolve_lands_in_pending_review(self, client, db_session: Session):
        """ZR-ENG-CLR-006 AC-35: 'An unsupported or ambiguous required legal
        pathway fails closed to authorized review' -- not rejected outright.
        RENTER_CONTRACT_BREAK is in TERMINATION_CAUSE_CODES (Section 5's
        mandatory taxonomy) but not in UNILATERAL_CAUSE_CODES/MUTUAL_CAUSE_
        CODES, so it opens as PENDING_REVIEW with no computed date."""
        admin = _make_admin(db_session, email="term-unsupported-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="unsupported1")
        renter = _make_user(db_session, email="term-unsupported-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_CONTRACT_BREAK"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "PENDING_REVIEW"
        assert body["earliestEffectiveDate"] is None
        assert body["effectiveTerminationDate"] is None

    def test_a_host_only_cause_cannot_be_opened_by_a_renter(self, client, db_session: Session):
        """RENTER_BREACH describes the Host alleging a breach -- a renter
        cannot invoke it against themselves."""
        admin = _make_admin(db_session, email="term-hostonly-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="hostonly1")
        renter = _make_user(db_session, email="term-hostonly-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_BREACH"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 400, r.text

    def test_unrecognized_cause_code_is_rejected(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-unknown-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="unknown1")
        renter = _make_user(db_session, email="term-unknown-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "NOT_A_REAL_CODE"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 400, r.text

    def test_duplicate_open_case_is_rejected(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-dup-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="dup1")
        renter = _make_user(db_session, email="term-dup-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        cookies = auth_user_cookie(renter)
        r1 = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=cookies,
        )
        assert r1.status_code == 201, r1.text

        r2 = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=cookies,
        )
        assert r2.status_code == 409, r2.text

    def test_policy_snapshot_is_captured_and_stays_reproducible_after_the_pack_changes(self, client, db_session: Session):
        """ZR-ENG-CLR-006 AC-02/Section 6's own CONTROL: 'A refund calculation
        must always be reproducible from the policy_snapshot_id, not from
        whatever policy happens to be current when an auditor later opens the
        case.' Changing termination_notice_days after the case is opened must
        not change what the case's own snapshot says was used."""
        admin = _make_admin(db_session, email="term-snapshot-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="snapshot1")
        renter = _make_user(db_session, email="term-snapshot-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        policy = db_session.query(MarketPolicyPack).filter_by(jurisdiction_code="IN").one()
        original_notice_days = policy.termination_notice_days
        original_version = policy.version

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["policySnapshot"]["policy_pack_id"] == policy.id
        assert body["policySnapshot"]["policy_pack_version"] == original_version
        assert body["policySnapshot"]["jurisdiction"] == "IN"

        original_effective_date = body["earliestEffectiveDate"]

        # Mutating the live pack afterward must not retroactively change what
        # this already-opened case's own stored snapshot/effective date say.
        policy.termination_notice_days = original_notice_days + 100
        db_session.commit()

        r = client.get(f"/api/occupancy/{occupancy.id}/termination-cases", cookies=auth_admin_cookie(admin))
        refetched = next(c for c in r.json() if c["id"] == body["id"])
        assert refetched["earliestEffectiveDate"] == original_effective_date
        assert refetched["policySnapshot"]["policy_pack_version"] == original_version

    def test_opening_a_case_emits_a_domain_event(self, client, db_session: Session):
        """ZR-ENG-CLR-006 Section 20.2: termination.case_opened, via the same
        domain-event outbox (crud/events.py) already used elsewhere."""
        admin = _make_admin(db_session, email="term-event-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="event1")
        renter = _make_user(db_session, email="term-event-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]

        event = db_session.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "termination.case_opened",
                DomainEvent.resource_type == "termination_case",
                DomainEvent.resource_id == str(case_id),
            )
        )
        assert event is not None
        assert event.payload["occupancyId"] == occupancy.id

        policy_event = db_session.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "termination.policy_resolved",
                DomainEvent.resource_type == "termination_case",
                DomainEvent.resource_id == str(case_id),
            )
        )
        assert policy_event is not None
        assert policy_event.payload["policyPackId"] is not None

        # ZR-ENG-CLR-006 Section 19: actor/state fields on the event itself.
        assert event.actor_kind == "guest"
        assert event.actor_id == str(guest.id)
        assert event.new_state == "EFFECTIVE_DATE_SET"
        assert event.payload_hash is not None

    def test_a_different_renter_cannot_open_a_case_for_this_occupancy(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-owner-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="owner1")
        owning_renter = _make_user(db_session, email="term-owner-renter@test.com")
        guest.user_account_id = owning_renter.id
        db_session.commit()

        stranger = _make_user(db_session, email="term-stranger@test.com")
        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=auth_user_cookie(stranger),
        )
        assert r.status_code == 403, r.text


class TestWithdrawTerminationCase:
    def test_renter_can_withdraw_their_own_case_and_then_open_a_new_one(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-withdraw-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="withdraw1")
        renter = _make_user(db_session, email="term-withdraw-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()
        cookies = auth_user_cookie(renter)

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=cookies,
        )
        case_id = r.json()["id"]

        r = client.post(f"/api/users/rentals/termination-cases/{case_id}/withdraw", cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "WITHDRAWN"

        event = db_session.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "termination.case_withdrawn",
                DomainEvent.resource_type == "termination_case",
                DomainEvent.resource_id == str(case_id),
            )
        )
        assert event is not None

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=cookies,
        )
        assert r.status_code == 201, r.text


class TestFinalizeTerminationCaseViaEndOccupancy:
    def test_end_occupancy_links_and_closes_the_case(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-finalize-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="finalize1")
        renter = _make_user(db_session, email="term-finalize-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"}, cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]

        r = client.post(
            f"/api/occupancy/{occupancy.id}/end",
            json={"terminationCaseId": case_id, "basis": "STATUTORY_GROUND"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ENDED"

        case = termination_crud.get_termination_case_or_404(db_session, case_id)
        db_session.refresh(case)
        assert case.status == "TERMINATED"

        record = db_session.query(TerminationRecord).filter_by(occupancy_id=occupancy.id).one()
        assert record.termination_case_id == case_id

    def test_end_occupancy_rejects_a_case_from_a_different_occupancy(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-mismatch-admin@test.com", role="super_admin")
        occupancy1, guest1, _listing1, _agreement1 = _make_active_occupancy(db_session, admin=admin, suffix="mismatch1")
        occupancy2, guest2, _listing2, _agreement2 = _make_active_occupancy(db_session, admin=admin, suffix="mismatch2")
        renter2 = _make_user(db_session, email="term-mismatch-renter2@test.com")
        guest2.user_account_id = renter2.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy2.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"}, cookies=auth_user_cookie(renter2),
        )
        case_id = r.json()["id"]

        r = client.post(
            f"/api/occupancy/{occupancy1.id}/end",
            json={"terminationCaseId": case_id},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 404, r.text


class TestHostInitiatedTerminationCase:
    """ZR-ENG-CLR-006 Section 8: 'Start termination / possession process' --
    the Host-initiated counterpart to Section 7's renter flow, sharing the
    same TerminationCase engine/effective-date resolution."""

    def test_admin_can_start_a_host_fault_case(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-host-admin@test.com", role="super_admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="host1")

        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE", "notes": "burst pipe, can't fix in time"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["initiatorAdminId"] == admin.id
        assert body["initiatorGuestId"] is None
        assert body["earliestEffectiveDate"] == date.today().isoformat()

    def test_a_host_cause_this_build_cannot_auto_resolve_lands_in_pending_review(self, client, db_session: Session):
        """RENTER_BREACH (Section 8.1's RENTER_BREACH_ALLEGED) needs an
        evidence/due-process workflow this build doesn't have -- AC-35 means
        it still opens a case, just PENDING_REVIEW."""
        admin = _make_admin(db_session, email="term-host-unsupported-admin@test.com", role="super_admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="host2")

        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_BREACH"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "PENDING_REVIEW"

    def test_a_renter_only_cause_cannot_be_opened_by_a_host(self, client, db_session: Session):
        """RENTER_STATUTORY_RIGHT describes the renter's own right -- a Host
        cannot invoke it on the renter's behalf."""
        admin = _make_admin(db_session, email="term-hostonly2-admin@test.com", role="super_admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="hostonly2")

        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_STATUTORY_RIGHT"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 400, r.text

    def test_an_outsider_admin_cannot_start_a_case_for_this_occupancy(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-host-owner-admin@test.com", role="admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="host3")
        outsider = _make_admin(db_session, email="term-host-outsider-admin@test.com", role="admin")

        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"},
            cookies=auth_admin_cookie(outsider),
        )
        assert r.status_code == 403, r.text

    def test_a_renters_open_case_blocks_the_host_from_opening_a_second_one(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-host-dup-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="host4")
        renter = _make_user(db_session, email="term-host-dup-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text

        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 409, r.text

    def test_host_case_finalizes_via_end_occupancy(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-host-finalize-admin@test.com", role="super_admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="host5")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"},
            cookies=admin_cookies,
        )
        case_id = r.json()["id"]

        r = client.post(
            f"/api/occupancy/{occupancy.id}/end",
            json={"terminationCaseId": case_id, "basis": "OTHER"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        case = termination_crud.get_termination_case_or_404(db_session, case_id)
        db_session.refresh(case)
        assert case.status == "TERMINATED"


class TestMutualSurrender:
    """ZR-ENG-CLR-006 Section 7.1 Step 9/Section 8.1 MUTUAL_SURRENDER_PROPOSAL:
    either party may propose an agreed end date; it only becomes binding once
    the OTHER party affirmatively accepts -- no auto-accept/auto-expire
    default exists (Step 9's own 'never an invented default')."""

    def test_renter_proposes_and_host_accepts(self, client, db_session: Session):
        admin = _make_admin(db_session, email="surrender-accept-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="surrender1")
        renter = _make_user(db_session, email="surrender-accept-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()
        proposed_date = (date.today() + timedelta(days=14)).isoformat()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "MUTUAL_SURRENDER", "proposedEffectiveDate": proposed_date},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        case_id = body["id"]
        assert body["status"] == "SURRENDER_PROPOSED"
        assert body["earliestEffectiveDate"] == proposed_date
        assert body["effectiveTerminationDate"] is None

        r = client.post(f"/api/occupancy/termination-cases/{case_id}/accept-surrender", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "EFFECTIVE_DATE_SET"
        assert body["effectiveTerminationDate"] == proposed_date

        audit = db_session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "termination_case.accept_surrender", AuditEvent.resource_id == str(case_id),
            )
        )
        assert audit is not None
        assert audit.actor_admin_id == admin.id

    def test_renter_proposes_and_host_declines(self, client, db_session: Session):
        admin = _make_admin(db_session, email="surrender-decline-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="surrender2")
        renter = _make_user(db_session, email="surrender-decline-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()
        proposed_date = (date.today() + timedelta(days=14)).isoformat()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "MUTUAL_SURRENDER", "proposedEffectiveDate": proposed_date},
            cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]

        r = client.post(f"/api/occupancy/termination-cases/{case_id}/decline-surrender", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "SURRENDER_DECLINED"

        audit = db_session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "termination_case.decline_surrender", AuditEvent.resource_id == str(case_id),
            )
        )
        assert audit is not None

        # QT-07: original tenancy remains active, and a new case may be opened.
        db_session.refresh(occupancy)
        assert occupancy.status == "ACTIVE"
        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text

    def test_host_proposes_and_renter_accepts(self, client, db_session: Session):
        admin = _make_admin(db_session, email="surrender-host-accept-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="surrender3")
        renter = _make_user(db_session, email="surrender-host-accept-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()
        proposed_date = (date.today() + timedelta(days=10)).isoformat()

        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "MUTUAL_SURRENDER", "proposedEffectiveDate": proposed_date},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        case_id = r.json()["id"]

        r = client.post(f"/api/users/rentals/termination-cases/{case_id}/accept-surrender", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "EFFECTIVE_DATE_SET"
        assert body["effectiveTerminationDate"] == proposed_date

    def test_proposer_cannot_accept_their_own_offer(self, client, db_session: Session):
        admin = _make_admin(db_session, email="surrender-selfaccept-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="surrender4")
        renter = _make_user(db_session, email="surrender-selfaccept-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "MUTUAL_SURRENDER", "proposedEffectiveDate": (date.today() + timedelta(days=5)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]

        r = client.post(f"/api/users/rentals/termination-cases/{case_id}/accept-surrender", cookies=auth_user_cookie(renter))
        assert r.status_code == 409, r.text

    def test_missing_proposed_date_is_rejected(self, client, db_session: Session):
        admin = _make_admin(db_session, email="surrender-nodate-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="surrender5")
        renter = _make_user(db_session, email="surrender-nodate-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "MUTUAL_SURRENDER"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 400, r.text

    def test_accepting_emits_a_domain_event(self, client, db_session: Session):
        admin = _make_admin(db_session, email="surrender-event-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="surrender6")
        renter = _make_user(db_session, email="surrender-event-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "MUTUAL_SURRENDER", "proposedEffectiveDate": (date.today() + timedelta(days=5)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]
        client.post(f"/api/occupancy/termination-cases/{case_id}/accept-surrender", cookies=auth_admin_cookie(admin))

        event = db_session.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "termination.mutual_surrender_accepted",
                DomainEvent.resource_type == "termination_case",
                DomainEvent.resource_id == str(case_id),
            )
        )
        assert event is not None

    def test_a_new_case_cannot_be_opened_while_a_surrender_proposal_is_pending(self, client, db_session: Session):
        admin = _make_admin(db_session, email="surrender-openconflict-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="surrender7")
        renter = _make_user(db_session, email="surrender-openconflict-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "MUTUAL_SURRENDER", "proposedEffectiveDate": (date.today() + timedelta(days=5)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 409, r.text


class TestDecideTerminationCase:
    """ZR-ENG-CLR-006 Section 20.1 POST /termination-cases/{id}/decision --
    the only way a PENDING_REVIEW case (AC-35) moves forward."""

    def _open_pending_review_case(self, client, db_session: Session, *, suffix: str):
        admin = _make_admin(db_session, email=f"term-decide-{suffix}@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix=suffix)
        renter = _make_user(db_session, email=f"term-decide-renter-{suffix}@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_CONTRACT_BREAK"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        return admin, occupancy, r.json()["id"]

    def test_super_admin_approves_with_a_date_and_reason(self, client, db_session: Session):
        admin, _occupancy, case_id = self._open_pending_review_case(client, db_session, suffix="decide1")
        effective = (date.today() + timedelta(days=7)).isoformat()

        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/decision",
            json={"approve": True, "effectiveTerminationDate": effective, "reason": "Break clause verified against the lease"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "EFFECTIVE_DATE_SET"
        assert body["earliestEffectiveDate"] == effective
        assert body["effectiveTerminationDate"] == effective

    def test_super_admin_rejects_the_pathway_with_a_reason(self, client, db_session: Session):
        admin, _occupancy, case_id = self._open_pending_review_case(client, db_session, suffix="decide2")

        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/decision",
            json={"approve": False, "reason": "No break clause exists in this agreement"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "REJECTED_PATHWAY"
        assert body["earliestEffectiveDate"] is None

    def test_approving_without_a_date_is_rejected(self, client, db_session: Session):
        admin, _occupancy, case_id = self._open_pending_review_case(client, db_session, suffix="decide3")

        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/decision",
            json={"approve": True, "reason": "Verified"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 400, r.text

    def test_a_blank_reason_is_rejected(self, client, db_session: Session):
        admin, _occupancy, case_id = self._open_pending_review_case(client, db_session, suffix="decide4")

        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/decision",
            json={"approve": False, "reason": ""},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 400, r.text

    def test_a_regular_admin_cannot_decide_a_case(self, client, db_session: Session):
        _owner_admin, _occupancy, case_id = self._open_pending_review_case(client, db_session, suffix="decide5")
        regular_admin = _make_admin(db_session, email="term-decide-regular@test.com", role="admin")

        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/decision",
            json={"approve": True, "effectiveTerminationDate": date.today().isoformat(), "reason": "trying anyway"},
            cookies=auth_admin_cookie(regular_admin),
        )
        assert r.status_code == 403, r.text

    def test_deciding_a_case_that_is_not_pending_review_is_rejected(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-decide-notpending-admin@test.com", role="super_admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="decide6")

        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"}, cookies=auth_admin_cookie(admin),
        )
        case_id = r.json()["id"]

        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/decision",
            json={"approve": True, "effectiveTerminationDate": date.today().isoformat(), "reason": "N/A"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 409, r.text

    def test_deciding_emits_a_domain_event(self, client, db_session: Session):
        admin, _occupancy, case_id = self._open_pending_review_case(client, db_session, suffix="decide7")

        client.post(
            f"/api/occupancy/termination-cases/{case_id}/decision",
            json={"approve": True, "effectiveTerminationDate": date.today().isoformat(), "reason": "Verified"},
            cookies=auth_admin_cookie(admin),
        )

        event = db_session.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "termination.effective_date_set",
                DomainEvent.resource_type == "termination_case",
                DomainEvent.resource_id == str(case_id),
            )
        )
        assert event is not None

    def test_deciding_logs_an_audit_event(self, client, db_session: Session):
        """A Super Admin override this consequential needs its own entry in
        the audit trail, not just the generic event outbox."""
        admin, _occupancy, case_id = self._open_pending_review_case(client, db_session, suffix="decide9")

        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/decision",
            json={"approve": True, "effectiveTerminationDate": date.today().isoformat(), "reason": "Break clause verified"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text

        audit = db_session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "termination_case.decision",
                AuditEvent.resource_type == "termination_case",
                AuditEvent.resource_id == str(case_id),
            )
        )
        assert audit is not None
        assert audit.reason == "Break clause verified"
        assert audit.after_state == "EFFECTIVE_DATE_SET"
        assert audit.actor_admin_id == admin.id

    def test_after_rejection_a_new_case_can_be_opened(self, client, db_session: Session):
        admin, occupancy, case_id = self._open_pending_review_case(client, db_session, suffix="decide8")
        client.post(
            f"/api/occupancy/termination-cases/{case_id}/decision",
            json={"approve": False, "reason": "Not applicable"},
            cookies=auth_admin_cookie(admin),
        )

        # REJECTED_PATHWAY is a closed state -- opened here via the Host side
        # (no renter cookie plumbing needed) purely to confirm it doesn't
        # block a fresh case on the same occupancy.
        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text


class TestAdminListsTerminationCases:
    def test_super_admin_sees_all_cases(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-list-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="list1")
        renter = _make_user(db_session, email="term-list-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=auth_user_cookie(renter),
        )

        r = client.get("/api/occupancy/termination-cases", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert len(r.json()) >= 1


class TestSensitiveCausePrivacy:
    """ZR-ENG-CLR-006 Section 21.1: RENTER_STATUTORY_RIGHT notes (which may
    disclose e.g. a domestic-violence ground) are redacted from a regular
    admin's view of the Host-facing inbox, but a Super Admin and the
    renter's own view still see them in full."""

    def _open_sensitive_case(self, client, db_session: Session, *, admin, occupancy, guest):
        renter = _make_user(db_session, email=f"term-sensitive-renter-{occupancy.id}@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_STATUTORY_RIGHT", "notes": "Fleeing a domestic violence situation"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        return renter, r.json()["id"]

    def test_a_regular_admin_sees_redacted_notes_in_the_inbox(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-sensitive-owner@test.com", role="admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="sensitive1")
        self._open_sensitive_case(client, db_session, admin=admin, occupancy=occupancy, guest=guest)

        r = client.get("/api/occupancy/termination-cases", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert "domestic violence" not in r.json()[0]["notes"].lower()
        assert r.json()[0]["notes"] == "[redacted -- sensitive protected-ground details, Super Admin only]"

    def test_a_regular_admin_sees_redacted_notes_via_the_occupancy_scoped_list(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-sensitive-owner2@test.com", role="admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="sensitive2")
        self._open_sensitive_case(client, db_session, admin=admin, occupancy=occupancy, guest=guest)

        r = client.get(f"/api/occupancy/{occupancy.id}/termination-cases", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert "domestic violence" not in r.json()[0]["notes"].lower()

    def test_a_super_admin_sees_the_real_notes(self, client, db_session: Session):
        owner = _make_admin(db_session, email="term-sensitive-owner3@test.com", role="admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=owner, suffix="sensitive3")
        self._open_sensitive_case(client, db_session, admin=owner, occupancy=occupancy, guest=guest)

        super_admin = _make_admin(db_session, email="term-sensitive-super@test.com", role="super_admin")
        r = client.get("/api/occupancy/termination-cases", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        assert "domestic violence" in r.json()[0]["notes"].lower()

    def test_the_renter_sees_their_own_notes_in_full(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-sensitive-owner4@test.com", role="admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="sensitive4")
        renter, _case_id = self._open_sensitive_case(client, db_session, admin=admin, occupancy=occupancy, guest=guest)

        r = client.get(f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        assert "domestic violence" in r.json()[0]["notes"].lower()

    def test_a_non_sensitive_cause_is_never_redacted(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-sensitive-owner5@test.com", role="admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="sensitive5")
        renter = _make_user(db_session, email="term-sensitive-renter5@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT", "notes": "moving for a new job"},
            cookies=auth_user_cookie(renter),
        )

        r = client.get("/api/occupancy/termination-cases", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert r.json()[0]["notes"] == "moving for a new job"


class TestPreviewTerminationCase:
    """ZR-ENG-CLR-006 Section 7.1 Step 5: POST .../termination-cases/preview
    -- computes the same pathway/date/cost estimate open_termination_case
    would actually produce, but persists nothing."""

    def test_preview_persists_nothing_and_matches_a_real_calculation(self, client, db_session: Session):
        from app.models.finance import PaymentAllocation, SimulatedPayment

        admin = _make_admin(db_session, email="term-preview-admin1@test.com", role="super_admin")
        occupancy, guest, _listing, agreement = _make_active_occupancy(db_session, admin=admin, suffix="preview1")
        renter = _make_user(db_session, email="term-preview-renter1@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        def _pay(obligation: Obligation, amount: float, suffix: str) -> None:
            payment = SimulatedPayment(guest_id=guest.id, amount=amount, currency="INR", idempotency_key=f"preview-{suffix}", status="SUCCEEDED")
            db_session.add(payment)
            db_session.flush()
            db_session.add(PaymentAllocation(payment_id=payment.id, obligation_id=obligation.id, amount_allocated=amount))
            db_session.commit()

        initial_obligation = db_session.scalar(
            select(Obligation).where(Obligation.agreement_id == agreement.id, Obligation.obligation_type == "RENT")
        )
        _pay(initial_obligation, 1000.0, "initial")
        future_obligation = Obligation(
            obligation_type="RENT", money_plane="OCCUPANCY", amount=1000.0, currency="INR",
            due_date=date.today() + timedelta(days=30), status="PENDING", occupancy_id=occupancy.id,
        )
        db_session.add(future_obligation)
        db_session.commit()
        _pay(future_obligation, 1000.0, "future")

        assert db_session.scalars(select(termination_crud.TerminationCase)).all() == []

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases/preview",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        preview = r.json()
        assert preview["resolvedStatus"] == "EFFECTIVE_DATE_SET"
        assert preview["earliestEffectiveDate"] == date.today().isoformat()
        assert preview["estimatedEarnedRent"] == 1000.0
        assert preview["estimatedRefundableUnearnedRent"] == 1000.0
        assert preview["estimatedLiabilityAmount"] == 0.0
        assert preview["estimatedNetRefund"] == 1000.0
        assert preview["requiresHostConsent"] is False
        assert preview["requiresEvidenceToResolveNow"] is False
        assert "deposit" in preview["depositDisclaimer"].lower()

        # Nothing was persisted by the preview itself.
        assert db_session.scalars(select(termination_crud.TerminationCase)).all() == []

        # Submitting for real and calculating the actual entitlement must
        # agree with what the preview said -- the whole point of reusing the
        # same resolution/calculation logic.
        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"}, cookies=auth_admin_cookie(admin),
        )
        case_id = r.json()["id"]
        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=auth_admin_cookie(admin))
        assert float(r.json()["netRefund"]) == preview["estimatedNetRefund"]

    def test_preview_of_a_cause_that_cannot_auto_resolve_has_no_estimate(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-preview-admin2@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="preview2")
        renter = _make_user(db_session, email="term-preview-renter2@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases/preview",
            json={"causeCode": "RENTER_CONTRACT_BREAK"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        preview = r.json()
        assert preview["resolvedStatus"] == "PENDING_REVIEW"
        assert preview["earliestEffectiveDate"] is None
        assert preview["estimatedNetRefund"] is None
        assert preview["estimatedLiabilityNote"] != ""

    def test_preview_of_statutory_right_reflects_the_evidence_gate(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-preview-admin3@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="preview3")
        renter = _make_user(db_session, email="term-preview-renter3@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases/preview",
            json={"causeCode": "RENTER_STATUTORY_RIGHT"},
            cookies=auth_user_cookie(renter),
        )
        body = r.json()
        assert body["resolvedStatus"] == "PENDING_REVIEW"
        assert body["requiresEvidenceToResolveNow"] is True

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases/preview",
            json={"causeCode": "RENTER_STATUTORY_RIGHT", "evidenceRefs": ["doc-1"]},
            cookies=auth_user_cookie(renter),
        )
        body = r.json()
        assert body["resolvedStatus"] == "EFFECTIVE_DATE_SET"
        assert body["requiresEvidenceToResolveNow"] is False
        assert body["estimatedLiabilityAmount"] == 0.0

    def test_preview_of_mutual_surrender_shows_host_consent_is_needed(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-preview-admin4@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="preview4")
        renter = _make_user(db_session, email="term-preview-renter4@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        proposed = date.today() + timedelta(days=10)
        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases/preview",
            json={"causeCode": "MUTUAL_SURRENDER", "proposedEffectiveDate": proposed.isoformat()},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resolvedStatus"] == "SURRENDER_PROPOSED"
        assert body["requiresHostConsent"] is True
        assert body["earliestEffectiveDate"] == proposed.isoformat()

    def test_a_host_only_cause_cannot_be_previewed_by_a_renter(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-preview-admin5@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="preview5")
        renter = _make_user(db_session, email="term-preview-renter5@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases/preview",
            json={"causeCode": "RENTER_BREACH"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 400, r.text


class TestGetTerminationCase:
    """ZR-ENG-CLR-006 Section 20.1 GET /termination-cases/{id}."""

    def test_provider_owner_can_fetch_their_own_case(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-get-owner@test.com", role="admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="get1")
        renter = _make_user(db_session, email="term-get-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]

        r = client.get(f"/api/occupancy/termination-cases/{case_id}", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert r.json()["id"] == case_id

    def test_an_unrelated_provider_cannot_fetch_the_case(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-get-owner2@test.com", role="admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="get2")
        renter = _make_user(db_session, email="term-get-renter2@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]

        other_admin = _make_admin(db_session, email="term-get-other@test.com", role="admin")
        r = client.get(f"/api/occupancy/termination-cases/{case_id}", cookies=auth_admin_cookie(other_admin))
        assert r.status_code == 403, r.text

    def test_a_missing_case_is_404(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-get-missing@test.com", role="super_admin")
        r = client.get("/api/occupancy/termination-cases/999999999", cookies=auth_admin_cookie(admin))
        assert r.status_code == 404, r.text


class TestSetAdjudicatedEffectiveDate:
    """ZR-ENG-CLR-006 Section 10 adjudicated_effective_date/Section 20.1 --
    a Super Admin's own entry standing in for a real court/tribunal/
    authority decision, taking precedence over whatever date this build
    already resolved."""

    def test_super_admin_sets_the_adjudicated_date_and_it_takes_effect(self, client, db_session: Session):
        admin, occupancy, case_id = TestDecideTerminationCase()._open_pending_review_case(client, db_session, suffix="adj1")
        adjudicated = (date.today() + timedelta(days=14)).isoformat()

        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/adjudicated-effective-date",
            json={"effectiveDate": adjudicated, "reason": "Tribunal order #TR-4471"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "EFFECTIVE_DATE_SET"
        assert body["adjudicatedEffectiveDate"] == adjudicated
        assert body["adjudicatedEffectiveDateReason"] == "Tribunal order #TR-4471"
        assert body["effectiveTerminationDate"] == adjudicated

        audit = db_session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "termination_case.adjudicated_effective_date",
                AuditEvent.resource_id == str(case_id),
            )
        )
        assert audit is not None
        assert audit.reason == "Tribunal order #TR-4471"

        events = list(db_session.scalars(
            select(DomainEvent).where(
                DomainEvent.event_type == "termination.effective_date_set",
                DomainEvent.resource_id == str(case_id),
            )
        ))
        assert any(e.payload.get("basis") == "adjudicated" for e in events)

    def test_a_regular_admin_cannot_set_it(self, client, db_session: Session):
        admin, _occupancy, case_id = TestDecideTerminationCase()._open_pending_review_case(client, db_session, suffix="adj2")
        regular_admin = _make_admin(db_session, email="term-adj-regular@test.com", role="admin")

        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/adjudicated-effective-date",
            json={"effectiveDate": date.today().isoformat(), "reason": "trying anyway"},
            cookies=auth_admin_cookie(regular_admin),
        )
        assert r.status_code == 403, r.text

    def test_a_blank_reason_is_rejected(self, client, db_session: Session):
        admin, _occupancy, case_id = TestDecideTerminationCase()._open_pending_review_case(client, db_session, suffix="adj3")

        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/adjudicated-effective-date",
            json={"effectiveDate": date.today().isoformat(), "reason": ""},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 400, r.text

    def test_a_terminated_case_cannot_be_adjudicated(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-adj-terminated@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="adj4")
        renter = _make_user(db_session, email="term-adj-terminated-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]
        r = client.post(f"/api/users/rentals/termination-cases/{case_id}/withdraw", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text

        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/adjudicated-effective-date",
            json={"effectiveDate": date.today().isoformat(), "reason": "N/A"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 409, r.text


class TestRecordMitigationRoute:
    """ZR-ENG-CLR-006 Section 11.2/20.1 POST /termination-cases/{id}/
    mitigation -- previously implemented only at the crud layer with no
    reachable route; see TestLiabilityModels in test_refund_entitlement.py
    for the resulting MITIGATION_CREDIT calculation."""

    def _open_case(self, client, db_session: Session, *, suffix: str):
        admin = _make_admin(db_session, email=f"term-mit-{suffix}@test.com", role="admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix=suffix)
        renter = _make_user(db_session, email=f"term-mit-renter-{suffix}@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=auth_user_cookie(renter),
        )
        return admin, r.json()["id"]

    def test_provider_owner_can_record_mitigation_evidence(self, client, db_session: Session):
        admin, case_id = self._open_case(client, db_session, suffix="mit1")

        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/mitigation",
            json={"reasonableRelettingCosts": 150.0, "notes": "Advertising costs"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["terminationCaseId"] == case_id
        assert float(body["reasonableRelettingCosts"]) == 150.0
        assert body["notes"] == "Advertising costs"
        assert body["recordedByAdminId"] == admin.id

        r = client.get(f"/api/occupancy/termination-cases/{case_id}/mitigation", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1

        audit = db_session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "termination_case.mitigation_recorded",
                AuditEvent.resource_id == str(case_id),
            )
        )
        assert audit is not None

        event = db_session.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "termination.mitigation_updated",
                DomainEvent.resource_id == str(case_id),
            )
        )
        assert event is not None

    def test_an_unrelated_provider_cannot_record_mitigation(self, client, db_session: Session):
        _admin, case_id = self._open_case(client, db_session, suffix="mit2")
        other_admin = _make_admin(db_session, email="term-mit-other@test.com", role="admin")

        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/mitigation",
            json={"notes": "trying anyway"},
            cookies=auth_admin_cookie(other_admin),
        )
        assert r.status_code == 403, r.text


class TestTerminationDecisionHistory:
    """ZR-ENG-CLR-006 Section 19's own termination_decision entity: an
    append-only history of how effective_termination_date was actually
    decided at each point in a case's life -- GET /termination-cases/{id}/
    decisions."""

    def test_auto_resolved_unilateral_case_records_a_decision(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-dec-auto@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="dec-auto")
        renter = _make_user(db_session, email="term-dec-auto-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]

        r = client.get(f"/api/occupancy/termination-cases/{case_id}/decisions", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1
        assert r.json()[0]["decisionBasis"] == "AUTO_RESOLVED_UNILATERAL"
        assert r.json()[0]["authority"] == "system"
        assert r.json()[0]["effectiveTerminationAt"] is not None

    def test_evidence_gated_case_records_a_different_basis(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-dec-evidence@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="dec-evidence")
        renter = _make_user(db_session, email="term-dec-evidence-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_STATUTORY_RIGHT", "evidenceRefs": ["doc-1"]}, cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]

        r = client.get(f"/api/occupancy/termination-cases/{case_id}/decisions", cookies=auth_admin_cookie(admin))
        assert r.json()[0]["decisionBasis"] == "AUTO_RESOLVED_EVIDENCE_GATED"

    def test_pending_review_decision_records_an_authority_and_reason(self, client, db_session: Session):
        admin, _occupancy, case_id = TestDecideTerminationCase()._open_pending_review_case(client, db_session, suffix="dec-pending")

        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/decision",
            json={"approve": True, "effectiveTerminationDate": date.today().isoformat(), "reason": "Verified against the lease"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text

        r = client.get(f"/api/occupancy/termination-cases/{case_id}/decisions", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        decision = r.json()[-1]
        assert decision["decisionBasis"] == "PENDING_REVIEW_APPROVED"
        assert decision["authority"] == "super_admin"
        assert decision["approvedByAdminId"] == admin.id
        assert decision["reason"] == "Verified against the lease"

    def test_rejecting_records_a_rejected_basis_with_no_effective_date(self, client, db_session: Session):
        admin, _occupancy, case_id = TestDecideTerminationCase()._open_pending_review_case(client, db_session, suffix="dec-rejected")

        client.post(
            f"/api/occupancy/termination-cases/{case_id}/decision",
            json={"approve": False, "reason": "No lawful ground established"},
            cookies=auth_admin_cookie(admin),
        )

        r = client.get(f"/api/occupancy/termination-cases/{case_id}/decisions", cookies=auth_admin_cookie(admin))
        decision = r.json()[-1]
        assert decision["decisionBasis"] == "PENDING_REVIEW_REJECTED"
        assert decision["effectiveTerminationAt"] is None

    def test_mutual_surrender_acceptance_records_a_decision(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-dec-surrender@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="dec-surrender")
        renter = _make_user(db_session, email="term-dec-surrender-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "MUTUAL_SURRENDER", "proposedEffectiveDate": (date.today() + timedelta(days=5)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]
        # A proposal alone (not yet accepted) records no decision.
        r = client.get(f"/api/occupancy/termination-cases/{case_id}/decisions", cookies=auth_admin_cookie(admin))
        assert r.json() == []

        client.post(f"/api/occupancy/termination-cases/{case_id}/accept-surrender", cookies=auth_admin_cookie(admin))
        r = client.get(f"/api/occupancy/termination-cases/{case_id}/decisions", cookies=auth_admin_cookie(admin))
        assert len(r.json()) == 1
        assert r.json()[0]["decisionBasis"] == "MUTUAL_SURRENDER_ACCEPTED"
        assert r.json()[0]["authority"] == "host_admin"
        assert r.json()[0]["approvedByAdminId"] == admin.id

    def test_adjudicated_date_records_a_decision(self, client, db_session: Session):
        admin, _occupancy, case_id = TestDecideTerminationCase()._open_pending_review_case(client, db_session, suffix="dec-adj")
        adjudicated = (date.today() + timedelta(days=21)).isoformat()

        client.post(
            f"/api/occupancy/termination-cases/{case_id}/adjudicated-effective-date",
            json={"effectiveDate": adjudicated, "reason": "Tribunal order #TR-9911"},
            cookies=auth_admin_cookie(admin),
        )

        r = client.get(f"/api/occupancy/termination-cases/{case_id}/decisions", cookies=auth_admin_cookie(admin))
        decision = r.json()[-1]
        assert decision["decisionBasis"] == "ADJUDICATED"
        assert decision["effectiveTerminationAt"] == adjudicated
        assert decision["reason"] == "Tribunal order #TR-9911"


class TestJurisdictionRouting:
    """ZR-ENG-CLR-006 Section 6: 'The Termination Policy Resolver must
    select an effective-dated market rule set using the property
    jurisdiction.' Every other test in this suite uses the default "IN"
    property/pack -- these prove the resolver actually varies by the
    occupancy's own room->property->jurisdiction_code, not a hardcoded
    default (crud/market_policy.py:jurisdiction_code_for_occupancy)."""

    def test_a_different_jurisdictions_pack_is_actually_resolved(self, client, db_session: Session):
        # A second jurisdiction's own market pack, deliberately different
        # from the "IN" default's termination_notice_days=30.
        db_session.add(MarketPolicyPack(
            jurisdiction_code="US", version=1, effective_from=date(2026, 1, 1),
            confidence="REVIEW_REQUIRED", legal_source_note="Test fixture placeholder, not verified legal research.",
            termination_notice_days=45,
        ))
        db_session.commit()

        admin = _make_admin(db_session, email="term-jur-us@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="jurus", jurisdiction_code="US")
        renter = _make_user(db_session, email="term-jur-us-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["policySnapshot"]["jurisdiction"] == "US"
        expected = (date.today() + timedelta(days=45)).isoformat()
        assert body["earliestEffectiveDate"] == expected

    def test_default_in_property_still_resolves_the_in_pack(self, client, db_session: Session):
        """The same occupancy fixture every other test uses, unchanged --
        confirms the jurisdiction wiring is a no-op for this build's only
        real jurisdiction to date."""
        admin = _make_admin(db_session, email="term-jur-in@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="jurin")
        renter = _make_user(db_session, email="term-jur-in-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        assert r.json()["policySnapshot"]["jurisdiction"] == "IN"
        assert r.json()["earliestEffectiveDate"] == (date.today() + timedelta(days=30)).isoformat()

    def test_an_unsupported_jurisdiction_fails_closed(self, client, db_session: Session):
        """AC-35: no market pack for this property's own jurisdiction ->
        409, not a silent fallback to the "IN" default."""
        admin = _make_admin(db_session, email="term-jur-unsupported@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(
            db_session, admin=admin, suffix="jurzz", jurisdiction_code="ZZ",
        )
        renter = _make_user(db_session, email="term-jur-unsupported-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 409, r.text


class TestJointTenancyRouting:
    """ZR-ENG-CLR-006 AC-25: 'Joint-tenancy ... relationships route through
    Section 3/4 relationship data instead of assuming one renter = one
    agreement.' Adding a co-tenant to an occupancy makes a later
    termination case fall to PENDING_REVIEW regardless of cause code,
    instead of silently auto-resolving as if only one tenant existed."""

    def test_admin_can_add_and_list_co_tenants(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-cotenant-add@test.com", role="admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="cotenant-add")
        other_guest = Guest(id="G-COTENANT-ADD", name="Co-Tenant", email="cotenant-add@test.com", joined_at=date.today())
        db_session.add(other_guest)
        db_session.commit()

        r = client.post(
            f"/api/occupancy/{occupancy.id}/co-tenants", json={"guestId": other_guest.id}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        assert r.json()["guestId"] == other_guest.id
        assert r.json()["addedByAdminId"] == admin.id

        r = client.get(f"/api/occupancy/{occupancy.id}/co-tenants", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1

    def test_adding_the_occupancys_own_tenant_is_rejected(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-cotenant-self@test.com", role="admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="cotenant-self")

        r = client.post(
            f"/api/occupancy/{occupancy.id}/co-tenants", json={"guestId": guest.id}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 400, r.text

    def test_adding_the_same_co_tenant_twice_is_rejected(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-cotenant-dup@test.com", role="admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="cotenant-dup")
        other_guest = Guest(id="G-COTENANT-DUP", name="Co-Tenant", email="cotenant-dup@test.com", joined_at=date.today())
        db_session.add(other_guest)
        db_session.commit()

        client.post(f"/api/occupancy/{occupancy.id}/co-tenants", json={"guestId": other_guest.id}, cookies=auth_admin_cookie(admin))
        r = client.post(f"/api/occupancy/{occupancy.id}/co-tenants", json={"guestId": other_guest.id}, cookies=auth_admin_cookie(admin))
        assert r.status_code == 409, r.text

    def test_an_unrelated_provider_cannot_add_a_co_tenant(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-cotenant-outsider-owner@test.com", role="admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="cotenant-outsider")
        other_guest = Guest(id="G-COTENANT-OUTSIDER", name="Co-Tenant", email="cotenant-outsider@test.com", joined_at=date.today())
        db_session.add(other_guest)
        db_session.commit()
        outsider = _make_admin(db_session, email="term-cotenant-outsider@test.com", role="admin")

        r = client.post(
            f"/api/occupancy/{occupancy.id}/co-tenants", json={"guestId": other_guest.id}, cookies=auth_admin_cookie(outsider),
        )
        assert r.status_code == 403, r.text

    def test_an_otherwise_unilateral_cause_falls_to_pending_review_with_a_co_tenant(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-cotenant-route1@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="cotenant-route1")
        renter = _make_user(db_session, email="term-cotenant-route1-renter@test.com")
        guest.user_account_id = renter.id
        other_guest = Guest(id="G-COTENANT-ROUTE1", name="Co-Tenant", email="cotenant-route1@test.com", joined_at=date.today())
        db_session.add(other_guest)
        db_session.commit()
        client.post(f"/api/occupancy/{occupancy.id}/co-tenants", json={"guestId": other_guest.id}, cookies=auth_admin_cookie(admin))

        # RENTER_ORDINARY_EARLY_EXIT is a UNILATERAL_CAUSE_CODES member --
        # every other test in this suite shows it auto-resolving to
        # EFFECTIVE_DATE_SET. With a co-tenant present, it must not.
        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "PENDING_REVIEW"
        assert r.json()["earliestEffectiveDate"] is None

    def test_a_mutual_surrender_proposal_also_falls_to_pending_review_with_a_co_tenant(self, client, db_session: Session):
        """A single tenant + Host agreeing to a surrender is exactly the
        'one user cannot necessarily terminate only their share' scenario
        AC-25 warns about -- it must not reach SURRENDER_PROPOSED either."""
        admin = _make_admin(db_session, email="term-cotenant-route2@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="cotenant-route2")
        renter = _make_user(db_session, email="term-cotenant-route2-renter@test.com")
        guest.user_account_id = renter.id
        other_guest = Guest(id="G-COTENANT-ROUTE2", name="Co-Tenant", email="cotenant-route2@test.com", joined_at=date.today())
        db_session.add(other_guest)
        db_session.commit()
        client.post(f"/api/occupancy/{occupancy.id}/co-tenants", json={"guestId": other_guest.id}, cookies=auth_admin_cookie(admin))

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "MUTUAL_SURRENDER", "proposedEffectiveDate": (date.today() + timedelta(days=5)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "PENDING_REVIEW"

    def test_the_preview_reflects_pending_review_with_a_co_tenant(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-cotenant-preview@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="cotenant-preview")
        renter = _make_user(db_session, email="term-cotenant-preview-renter@test.com")
        guest.user_account_id = renter.id
        other_guest = Guest(id="G-COTENANT-PREVIEW", name="Co-Tenant", email="cotenant-preview@test.com", joined_at=date.today())
        db_session.add(other_guest)
        db_session.commit()
        client.post(f"/api/occupancy/{occupancy.id}/co-tenants", json={"guestId": other_guest.id}, cookies=auth_admin_cookie(admin))

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases/preview",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resolvedStatus"] == "PENDING_REVIEW"
        assert body["requiresHostConsent"] is False
        assert body["earliestEffectiveDate"] is None

    def test_without_a_co_tenant_behavior_is_unchanged(self, client, db_session: Session):
        admin = _make_admin(db_session, email="term-cotenant-none@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="cotenant-none")
        renter = _make_user(db_session, email="term-cotenant-none-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "EFFECTIVE_DATE_SET"


class TestRecordNoticeService:
    """Section 11 gap: a non-PORTAL notice_method previously left
    notice_served_at permanently null with no way to ever record real-world
    delivery proof -- see models/termination_case.py's own field docstring."""

    def test_recording_service_for_a_non_portal_method_sets_served_at(self, client, db_session: Session):
        admin = _make_admin(db_session, email="notice-svc-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="noticesvc1")
        renter = _make_user(db_session, email="notice-svc-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT", "noticeMethod": "POSTAL"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        case_id = r.json()["id"]
        assert r.json()["noticeServedAt"] is None

        served_at = datetime.now(timezone.utc).isoformat()
        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/notice-service",
            json={"servedAt": served_at, "proofRef": "courier-receipt-778"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["noticeServedAt"] is not None
        assert body["noticeServiceProofRef"] == "courier-receipt-778"
        assert body["noticeServiceRecordedByAdminId"] == admin.id

    def test_cannot_record_service_twice(self, client, db_session: Session):
        admin = _make_admin(db_session, email="notice-svc-twice-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="noticesvc2")
        renter = _make_user(db_session, email="notice-svc-twice-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT", "noticeMethod": "EMAIL"},
            cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]
        served_at = datetime.now(timezone.utc).isoformat()
        client.post(
            f"/api/occupancy/termination-cases/{case_id}/notice-service",
            json={"servedAt": served_at, "proofRef": "email-read-receipt"},
            cookies=auth_admin_cookie(admin),
        )

        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/notice-service",
            json={"servedAt": served_at, "proofRef": "second-attempt"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 409

    def test_cannot_record_service_for_a_portal_case(self, client, db_session: Session):
        admin = _make_admin(db_session, email="notice-svc-portal-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="noticesvc3")
        renter = _make_user(db_session, email="notice-svc-portal-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]

        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/notice-service",
            json={"servedAt": datetime.now(timezone.utc).isoformat(), "proofRef": "n/a"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 409

    def test_a_blank_proof_ref_is_rejected(self, client, db_session: Session):
        admin = _make_admin(db_session, email="notice-svc-blank-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="noticesvc4")
        renter = _make_user(db_session, email="notice-svc-blank-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT", "noticeMethod": "SMS"},
            cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]

        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/notice-service",
            json={"servedAt": datetime.now(timezone.utc).isoformat(), "proofRef": "  "},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 400
