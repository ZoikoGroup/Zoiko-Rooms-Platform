"""ZR-ENG-CLR-006 Section 9: habitability/unavailability incidents. H2/H3
freeze the room for new bookings by flipping Room.status to 'inactive' --
the same field crud/listing.py already checks for availability -- and an
open H2/H3 incident also blocks Host payout for obligations behind that
room (Section 9.2)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from datetime import datetime, timezone

from sqlalchemy import select

from app.crud import habitability_incident as habitability_crud
from app.models.domain_event import DomainEvent
from app.models.finance import Obligation, PaymentAllocation, PayoutBeneficiary, SimulatedPayment
from app.models.room import Room
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie
from tests.test_termination_case import _make_active_occupancy


def _verify_payout_beneficiary(db: Session, party_id: int) -> None:
    db.add(PayoutBeneficiary(
        party_id=party_id, account_holder_name="Test Landlord", bank_name="Test Bank",
        account_number_last4="1234", bank_identifier_code="TEST0123456", status="VERIFIED",
        verified_at=datetime.now(timezone.utc),
    ))
    db.commit()


def _pay_obligation(db: Session, guest_id: str, obligation: Obligation, amount: float, *, suffix: str) -> None:
    payment = SimulatedPayment(
        guest_id=guest_id, amount=amount, currency="INR", idempotency_key=f"habitability-{suffix}", status="SUCCEEDED",
    )
    db.add(payment)
    db.flush()
    db.add(PaymentAllocation(payment_id=payment.id, obligation_id=obligation.id, amount_allocated=amount))
    db.commit()


class TestReportHabitabilityIncident:
    def test_minor_severity_does_not_freeze_the_room(self, client, db_session: Session):
        admin = _make_admin(db_session, email="hab-h0-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="h0")
        renter = _make_user(db_session, email="hab-h0-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/habitability-incidents",
            json={"severity": "H0", "description": "squeaky door"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "OPEN"

        room = db_session.get(Room, occupancy.room_id)
        assert room.status == "active"

    def test_h2_severity_freezes_the_room(self, client, db_session: Session):
        admin = _make_admin(db_session, email="hab-h2-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="h2")
        renter = _make_user(db_session, email="hab-h2-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/habitability-incidents",
            json={"severity": "H2", "description": "no working plumbing"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        incident_id = r.json()["id"]

        room = db_session.get(Room, occupancy.room_id)
        assert room.status == "inactive"
        # The active occupancy itself is untouched by a habitability incident.
        db_session.refresh(occupancy)
        assert occupancy.status == "ACTIVE"

        event = db_session.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "property.habitability_incident_opened",
                DomainEvent.resource_id == str(incident_id),
            )
        )
        assert event is not None
        assert event.payload["severity"] == "H2"

    def test_admin_can_report_an_incident(self, client, db_session: Session):
        admin = _make_admin(db_session, email="hab-admin-report-admin@test.com", role="super_admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="adminreport")

        r = client.post(
            f"/api/occupancy/{occupancy.id}/habitability-incidents",
            json={"severity": "H3", "description": "fire damage"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        assert r.json()["reportedByAdminId"] == admin.id
        assert r.json()["reportedByGuestId"] is None

    def test_a_different_renter_cannot_report_for_this_occupancy(self, client, db_session: Session):
        admin = _make_admin(db_session, email="hab-owner-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="owner")
        owning_renter = _make_user(db_session, email="hab-owner-renter@test.com")
        guest.user_account_id = owning_renter.id
        db_session.commit()

        stranger = _make_user(db_session, email="hab-stranger@test.com")
        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/habitability-incidents",
            json={"severity": "H1"}, cookies=auth_user_cookie(stranger),
        )
        assert r.status_code == 403, r.text

    def test_an_outsider_admin_cannot_report_for_this_occupancy(self, client, db_session: Session):
        admin = _make_admin(db_session, email="hab-outsiderowner-admin@test.com", role="admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="outsiderowner")
        outsider = _make_admin(db_session, email="hab-outsider-admin@test.com", role="admin")

        r = client.post(
            f"/api/occupancy/{occupancy.id}/habitability-incidents",
            json={"severity": "H2"}, cookies=auth_admin_cookie(outsider),
        )
        assert r.status_code == 403, r.text

    def test_invalid_severity_is_rejected(self, client, db_session: Session):
        admin = _make_admin(db_session, email="hab-badseverity-admin@test.com", role="super_admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="badseverity")

        r = client.post(
            f"/api/occupancy/{occupancy.id}/habitability-incidents",
            json={"severity": "H9"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 400, r.text


class TestResolveHabitabilityIncident:
    def test_resolving_restores_the_room_when_no_other_open_severe_incident(self, client, db_session: Session):
        admin = _make_admin(db_session, email="hab-resolve-admin@test.com", role="super_admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="resolve")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            f"/api/occupancy/{occupancy.id}/habitability-incidents", json={"severity": "H2"}, cookies=admin_cookies,
        )
        incident_id = r.json()["id"]
        room = db_session.get(Room, occupancy.room_id)
        assert room.status == "inactive"

        r = client.post(
            f"/api/occupancy/habitability-incidents/{incident_id}/resolve",
            json={"resolutionNotes": "plumber fixed it"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "RESOLVED"

        db_session.refresh(room)
        assert room.status == "active"

        event = db_session.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "booking.inventory_reopened",
                DomainEvent.resource_type == "room",
                DomainEvent.resource_id == str(occupancy.room_id),
            )
        )
        assert event is not None
        assert event.payload["habitabilityIncidentId"] == incident_id

    def test_room_stays_frozen_while_another_severe_incident_remains_open(self, client, db_session: Session):
        admin = _make_admin(db_session, email="hab-multi-admin@test.com", role="super_admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="multi")
        admin_cookies = auth_admin_cookie(admin)

        r1 = client.post(
            f"/api/occupancy/{occupancy.id}/habitability-incidents", json={"severity": "H2"}, cookies=admin_cookies,
        )
        r2 = client.post(
            f"/api/occupancy/{occupancy.id}/habitability-incidents", json={"severity": "H3"}, cookies=admin_cookies,
        )
        incident1_id, incident2_id = r1.json()["id"], r2.json()["id"]

        client.post(f"/api/occupancy/habitability-incidents/{incident1_id}/resolve", cookies=admin_cookies)
        room = db_session.get(Room, occupancy.room_id)
        assert room.status == "inactive"  # incident2 is still open
        assert db_session.scalar(
            select(DomainEvent).where(DomainEvent.event_type == "booking.inventory_reopened")
        ) is None

        client.post(f"/api/occupancy/habitability-incidents/{incident2_id}/resolve", cookies=admin_cookies)
        db_session.refresh(room)
        assert room.status == "active"
        assert db_session.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "booking.inventory_reopened",
                DomainEvent.resource_id == str(occupancy.room_id),
            )
        ) is not None

    def test_resolving_an_already_resolved_incident_is_rejected(self, client, db_session: Session):
        admin = _make_admin(db_session, email="hab-doubleresolve-admin@test.com", role="super_admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="doubleresolve")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            f"/api/occupancy/{occupancy.id}/habitability-incidents", json={"severity": "H1"}, cookies=admin_cookies,
        )
        incident_id = r.json()["id"]
        client.post(f"/api/occupancy/habitability-incidents/{incident_id}/resolve", cookies=admin_cookies)

        r = client.post(f"/api/occupancy/habitability-incidents/{incident_id}/resolve", cookies=admin_cookies)
        assert r.status_code == 409, r.text


class TestListHabitabilityIncidentsForAdmin:
    """The admin-wide '/habitability-incidents' inbox -- the habitability
    counterpart to termination-cases' list_termination_cases_for_admin,
    same provider-ownership scoping."""

    def test_owner_sees_their_own_incident(self, client, db_session: Session):
        admin = _make_admin(db_session, email="hab-inbox-owner@test.com", role="admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="inboxowner")
        admin_cookies = auth_admin_cookie(admin)
        client.post(f"/api/occupancy/{occupancy.id}/habitability-incidents", json={"severity": "H1"}, cookies=admin_cookies)

        r = client.get("/api/occupancy/habitability-incidents", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1
        assert r.json()[0]["occupancyId"] == occupancy.id

    def test_outsider_admin_does_not_see_it(self, client, db_session: Session):
        admin = _make_admin(db_session, email="hab-inbox-owner2@test.com", role="admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="inboxowner2")
        client.post(
            f"/api/occupancy/{occupancy.id}/habitability-incidents", json={"severity": "H1"}, cookies=auth_admin_cookie(admin),
        )

        outsider = _make_admin(db_session, email="hab-inbox-outsider@test.com", role="admin")
        r = client.get("/api/occupancy/habitability-incidents", cookies=auth_admin_cookie(outsider))
        assert r.status_code == 200, r.text
        assert r.json() == []

    def test_super_admin_sees_every_incident(self, client, db_session: Session):
        owner = _make_admin(db_session, email="hab-inbox-owner3@test.com", role="admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=owner, suffix="inboxowner3")
        client.post(
            f"/api/occupancy/{occupancy.id}/habitability-incidents", json={"severity": "H1"}, cookies=auth_admin_cookie(owner),
        )

        super_admin = _make_admin(db_session, email="hab-inbox-super@test.com", role="super_admin")
        r = client.get("/api/occupancy/habitability-incidents", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1


class TestHabitabilityBlocksPayout:
    def test_payout_is_held_while_a_severe_incident_is_open(self, client, db_session: Session):
        admin = _make_admin(db_session, email="hab-payout-admin@test.com", role="super_admin")
        occupancy, guest, _listing, agreement = _make_active_occupancy(db_session, admin=admin, suffix="payout1")
        admin_cookies = auth_admin_cookie(admin)

        from sqlalchemy import select
        initial_obligation = db_session.scalar(
            select(Obligation).where(Obligation.agreement_id == agreement.id, Obligation.obligation_type == "RENT")
        )
        _pay_obligation(db_session, guest.id, initial_obligation, 1000.0, suffix="payout1")
        party_id = agreement.offer.listing.room.property.owner_party_id
        _verify_payout_beneficiary(db_session, party_id)

        client.post(f"/api/occupancy/{occupancy.id}/habitability-incidents", json={"severity": "H2"}, cookies=admin_cookies)

        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        payout = r.json()
        assert payout["status"] == "HELD"
        assert "habitability" in payout["holdReason"].lower()

    def test_payout_succeeds_once_the_incident_is_resolved(self, client, db_session: Session):
        admin = _make_admin(db_session, email="hab-payout2-admin@test.com", role="super_admin")
        occupancy, guest, _listing, agreement = _make_active_occupancy(db_session, admin=admin, suffix="payout2")
        admin_cookies = auth_admin_cookie(admin)

        from sqlalchemy import select
        initial_obligation = db_session.scalar(
            select(Obligation).where(Obligation.agreement_id == agreement.id, Obligation.obligation_type == "RENT")
        )
        _pay_obligation(db_session, guest.id, initial_obligation, 1000.0, suffix="payout2")
        party_id = agreement.offer.listing.room.property.owner_party_id
        _verify_payout_beneficiary(db_session, party_id)

        r = client.post(f"/api/occupancy/{occupancy.id}/habitability-incidents", json={"severity": "H2"}, cookies=admin_cookies)
        incident_id = r.json()["id"]
        client.post(f"/api/occupancy/habitability-incidents/{incident_id}/resolve", cookies=admin_cookies)

        r = client.post("/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PAID"


class TestApplyHabitabilityCredit:
    """ZR-ENG-CLR-006 Section 9.1 H1: 'possible rent adjustment/credit' --
    an admin-applied amount against a paid RENT obligation, executed as a
    real refund, never a computed abatement formula."""

    def _open_h1_incident_with_paid_obligation(self, client, db_session: Session, *, suffix: str):
        admin = _make_admin(db_session, email=f"hab-credit-{suffix}@test.com", role="super_admin")
        occupancy, guest, _listing, agreement = _make_active_occupancy(db_session, admin=admin, suffix=suffix)
        admin_cookies = auth_admin_cookie(admin)

        from sqlalchemy import select
        obligation = db_session.scalar(
            select(Obligation).where(Obligation.agreement_id == agreement.id, Obligation.obligation_type == "RENT")
        )
        _pay_obligation(db_session, guest.id, obligation, 1000.0, suffix=suffix)

        r = client.post(f"/api/occupancy/{occupancy.id}/habitability-incidents", json={"severity": "H1"}, cookies=admin_cookies)
        incident_id = r.json()["id"]
        return admin, admin_cookies, obligation, incident_id

    def test_admin_applies_a_credit_against_a_paid_obligation(self, client, db_session: Session):
        _admin, admin_cookies, obligation, incident_id = self._open_h1_incident_with_paid_obligation(client, db_session, suffix="credit1")

        r = client.post(
            f"/api/occupancy/habitability-incidents/{incident_id}/apply-credit",
            json={"obligationId": obligation.id, "amount": 150.0, "reason": "No hot water for 3 days"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert float(body["creditedAmount"]) == 150.0
        assert body["creditedRefundRequestId"] is not None

        db_session.refresh(obligation)
        assert obligation.status in ("PARTIALLY_PAID", "PAID")

    def test_a_second_credit_on_the_same_incident_is_rejected(self, client, db_session: Session):
        _admin, admin_cookies, obligation, incident_id = self._open_h1_incident_with_paid_obligation(client, db_session, suffix="credit2")
        client.post(
            f"/api/occupancy/habitability-incidents/{incident_id}/apply-credit",
            json={"obligationId": obligation.id, "amount": 100.0, "reason": "First credit"},
            cookies=admin_cookies,
        )

        r = client.post(
            f"/api/occupancy/habitability-incidents/{incident_id}/apply-credit",
            json={"obligationId": obligation.id, "amount": 50.0, "reason": "Second credit"},
            cookies=admin_cookies,
        )
        assert r.status_code == 409, r.text

    def test_a_credit_on_a_non_h1_incident_is_rejected(self, client, db_session: Session):
        admin = _make_admin(db_session, email="hab-credit-nonh1@test.com", role="super_admin")
        occupancy, guest, _listing, agreement = _make_active_occupancy(db_session, admin=admin, suffix="creditnonh1")
        admin_cookies = auth_admin_cookie(admin)

        from sqlalchemy import select
        obligation = db_session.scalar(
            select(Obligation).where(Obligation.agreement_id == agreement.id, Obligation.obligation_type == "RENT")
        )
        _pay_obligation(db_session, guest.id, obligation, 1000.0, suffix="creditnonh1")

        r = client.post(f"/api/occupancy/{occupancy.id}/habitability-incidents", json={"severity": "H0"}, cookies=admin_cookies)
        incident_id = r.json()["id"]

        r = client.post(
            f"/api/occupancy/habitability-incidents/{incident_id}/apply-credit",
            json={"obligationId": obligation.id, "amount": 50.0, "reason": "trying anyway"},
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text

    def test_a_blank_reason_is_rejected(self, client, db_session: Session):
        _admin, admin_cookies, obligation, incident_id = self._open_h1_incident_with_paid_obligation(client, db_session, suffix="credit4")

        r = client.post(
            f"/api/occupancy/habitability-incidents/{incident_id}/apply-credit",
            json={"obligationId": obligation.id, "amount": 50.0, "reason": ""},
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text

    def test_an_obligation_from_a_different_occupancy_is_rejected(self, client, db_session: Session):
        admin, admin_cookies, _obligation, incident_id = self._open_h1_incident_with_paid_obligation(client, db_session, suffix="credit5")

        _other_occupancy, other_guest, _other_listing, other_agreement = _make_active_occupancy(db_session, admin=admin, suffix="credit5other")
        from sqlalchemy import select
        other_obligation = db_session.scalar(
            select(Obligation).where(Obligation.agreement_id == other_agreement.id, Obligation.obligation_type == "RENT")
        )
        _pay_obligation(db_session, other_guest.id, other_obligation, 1000.0, suffix="credit5other")

        r = client.post(
            f"/api/occupancy/habitability-incidents/{incident_id}/apply-credit",
            json={"obligationId": other_obligation.id, "amount": 50.0, "reason": "wrong occupancy"},
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text

    def test_no_paid_allocation_to_credit_against_is_rejected(self, client, db_session: Session):
        admin = _make_admin(db_session, email="hab-credit-nopay@test.com", role="super_admin")
        occupancy, _guest, _listing, agreement = _make_active_occupancy(db_session, admin=admin, suffix="creditnopay")
        admin_cookies = auth_admin_cookie(admin)

        from datetime import date, timedelta
        unpaid_obligation = Obligation(
            obligation_type="RENT", money_plane="OCCUPANCY", amount=500.0, currency="INR",
            due_date=date.today() + timedelta(days=30), status="PENDING", occupancy_id=occupancy.id,
        )
        db_session.add(unpaid_obligation)
        db_session.commit()

        r = client.post(f"/api/occupancy/{occupancy.id}/habitability-incidents", json={"severity": "H1"}, cookies=admin_cookies)
        incident_id = r.json()["id"]

        r = client.post(
            f"/api/occupancy/habitability-incidents/{incident_id}/apply-credit",
            json={"obligationId": unpaid_obligation.id, "amount": 50.0, "reason": "no payment yet"},
            cookies=admin_cookies,
        )
        assert r.status_code == 409, r.text
