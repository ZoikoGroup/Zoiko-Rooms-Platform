"""ZR-ENG-CLR-006 Section 7: renter-initiated early termination -- the first
Section 6 increment. A renter can open a termination case for their own
active occupancy (only for models.termination_case.UNILATERAL_CAUSE_CODES);
the earliest_effective_date is resolved deterministically from the market
pack's termination_notice_days for a notice-based cause, or immediate for an
immediate cause; an admin later finalizes it via the existing end_occupancy,
which links the case and flips it to TERMINATED."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import occupancy as occupancy_crud
from app.crud import termination as termination_crud
from app.crud.party import get_or_create_default_party
from app.models.authority_record import AuthorityRecord
from app.models.finance import Obligation
from app.models.guest import Guest
from app.models.leasing import Agreement, Application, Offer, OfferTerms
from app.models.listing import Listing
from app.models.market_release import MarketRelease
from app.models.market_policy import MarketPolicyPack
from app.models.occupancy_classification import OccupancyClassification
from app.models.domain_event import DomainEvent
from app.models.property import Property
from app.models.room import Room
from app.models.termination_record import TerminationRecord
from app.schemas.termination import TerminationCaseCreate
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie


def _make_active_occupancy(db: Session, *, admin, suffix: str, monthly_rent: float = 1000.0, term_months: int = 12):
    owner_party = get_or_create_default_party(db, admin)
    prop = Property(owner_party_id=owner_party.id, address=f"{suffix} Term St", city="Bengaluru", status="active")
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
