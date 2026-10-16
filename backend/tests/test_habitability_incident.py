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


def _parties(db: Session, occupancy, suffix: str):
    """A host user on the room's owning party and a renter user on the
    occupancy's guest -- the two people who settle an incident now."""
    from app.models.guest import Guest

    host = _make_user(db, email=f"hab-host-{suffix}@test.com")
    host.party_id = occupancy.room.property.owner_party_id
    renter = _make_user(db, email=f"hab-renter-{suffix}@test.com")
    guest = db.get(Guest, occupancy.guest_id)
    guest.user_account_id = renter.id
    db.commit()
    return host, renter


def _resolve_by_parties(client, db: Session, occupancy, incident_id: int, suffix: str, parties=None):
    host, renter = parties or _parties(db, occupancy, suffix)
    r = client.post(f"/api/users/hosting/habitability-incidents/{incident_id}/fixed", json={"notes": "fixed"}, cookies=auth_user_cookie(host))
    assert r.status_code == 200, r.text
    r = client.post(f"/api/users/rentals/habitability-incidents/{incident_id}/confirm-fixed", cookies=auth_user_cookie(renter))
    assert r.status_code == 200, r.text
    return r


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

        r = _resolve_by_parties(client, db_session, occupancy, incident_id, "resolve")
        assert r.json()["status"] == "RESOLVED" and r.json()["resolutionBasis"] == "RENTER_CONFIRMED"

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

        parties = _parties(db_session, occupancy, "multi")
        _resolve_by_parties(client, db_session, occupancy, incident1_id, "multi", parties)
        room = db_session.get(Room, occupancy.room_id)
        assert room.status == "inactive"  # incident2 is still open
        assert db_session.scalar(
            select(DomainEvent).where(DomainEvent.event_type == "booking.inventory_reopened")
        ) is None

        _resolve_by_parties(client, db_session, occupancy, incident2_id, "multi", parties)
        db_session.refresh(room)
        assert room.status == "active"
        assert db_session.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "booking.inventory_reopened",
                DomainEvent.resource_id == str(occupancy.room_id),
            )
        ) is not None

    def test_confirming_an_already_resolved_incident_is_rejected(self, client, db_session: Session):
        admin = _make_admin(db_session, email="hab-doubleresolve-admin@test.com", role="super_admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="doubleresolve")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            f"/api/occupancy/{occupancy.id}/habitability-incidents", json={"severity": "H1"}, cookies=admin_cookies,
        )
        incident_id = r.json()["id"]
        host, renter = _parties(db_session, occupancy, "doubleresolve")
        _resolve_by_parties(client, db_session, occupancy, incident_id, "doubleresolve", (host, renter))

        r = client.post(f"/api/users/rentals/habitability-incidents/{incident_id}/confirm-fixed", cookies=auth_user_cookie(renter))
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
        _resolve_by_parties(client, db_session, occupancy, incident_id, "payout2")

        r = client.post("/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PAID"


class TestHabitabilitySettledBetweenParties:
    """The host acknowledges and reports a fix; the renter confirms or
    rejects it; deadlines confirm unanswered fixes and credit rent for a
    serious issue not fixed in time. No admin step."""

    def _incident(self, client, db_session: Session, suffix: str, severity: str):
        admin = _make_admin(db_session, email=f"hab-auto-{suffix}@test.com", role="super_admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix=suffix)
        r = client.post(f"/api/occupancy/{occupancy.id}/habitability-incidents", json={"severity": severity}, cookies=auth_admin_cookie(admin))
        assert r.status_code == 201, r.text
        assert r.json()["fixDueAt"] is not None
        host, renter = _parties(db_session, occupancy, suffix)
        return occupancy, r.json()["id"], host, renter

    def test_acknowledge_fix_reject_then_confirm(self, client, db_session: Session):
        _occ, incident_id, host, renter = self._incident(client, db_session, "a1", "H1")
        r = client.post(f"/api/users/hosting/habitability-incidents/{incident_id}/acknowledge", cookies=auth_user_cookie(host))
        assert r.json()["status"] == "ACKNOWLEDGED"
        client.post(f"/api/users/hosting/habitability-incidents/{incident_id}/fixed", json={"notes": "replaced"}, cookies=auth_user_cookie(host))
        r = client.post(f"/api/users/rentals/habitability-incidents/{incident_id}/reject-fix", json={"note": "still leaking"}, cookies=auth_user_cookie(renter))
        assert r.json()["status"] == "ACKNOWLEDGED" and r.json()["fixRejectedCount"] == 1
        _resolve_by_parties(client, db_session, _occ, incident_id, "a1", (host, renter))

    def test_unanswered_fix_is_confirmed_after_the_window(self, client, db_session: Session):
        from datetime import timedelta

        from app.models.habitability_incident import HabitabilityIncident

        _occ, incident_id, host, _renter = self._incident(client, db_session, "a2", "H2")
        client.post(f"/api/users/hosting/habitability-incidents/{incident_id}/fixed", json={}, cookies=auth_user_cookie(host))
        incident = db_session.get(HabitabilityIncident, incident_id)
        incident.fix_reported_at = datetime.now(timezone.utc) - timedelta(days=6)
        db_session.commit()
        habitability_crud.apply_habitability_deadlines(db_session)
        db_session.refresh(incident)
        assert incident.status == "RESOLVED" and incident.resolution_basis == "DEEMED_CONFIRMED"

    def test_serious_issue_past_its_fix_date_reduces_the_next_rent_once(self, client, db_session: Session):
        from datetime import date, timedelta

        from app.models.habitability_incident import HabitabilityIncident
        from app.models.rental_payment import RentalPaymentObligation

        occ, incident_id, host, _renter = self._incident(client, db_session, "a3", "H3")
        rent = RentalPaymentObligation(
            obligation_type="RENT", occupancy_id=occ.id, tenant_guest_id=occ.guest_id, recipient_party_id=host.party_id,
            amount=1000.0, currency="INR", due_date=date.today() + timedelta(days=10), status="UPCOMING",
        )
        db_session.add(rent)
        incident = db_session.get(HabitabilityIncident, incident_id)
        incident.fix_due_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db_session.commit()

        assert habitability_crud.apply_habitability_deadlines(db_session) == 1
        db_session.refresh(rent)
        db_session.refresh(incident)
        assert float(rent.amount) == 500.0 and float(incident.credited_amount) == 500.0
        assert habitability_crud.apply_habitability_deadlines(db_session) == 0  # once only

    def test_only_the_rooms_host_and_tenant_can_act(self, client, db_session: Session):
        _occ, incident_id, _host, _renter = self._incident(client, db_session, "a4", "H1")
        stranger = _make_user(db_session, email="hab-stranger-a4@test.com")
        db_session.commit()
        assert client.post(f"/api/users/hosting/habitability-incidents/{incident_id}/fixed", json={}, cookies=auth_user_cookie(stranger)).status_code == 403
        assert client.post(f"/api/users/rentals/habitability-incidents/{incident_id}/confirm-fixed", cookies=auth_user_cookie(stranger)).status_code == 403

    def test_admin_resolve_and_credit_routes_are_gone(self, client, db_session: Session):
        admin = _make_admin(db_session, email="hab-noadmin@test.com", role="super_admin")
        db_session.commit()
        for path in ("resolve", "apply-credit"):
            r = client.post(f"/api/occupancy/habitability-incidents/1/{path}", json={}, cookies=auth_admin_cookie(admin))
            assert r.status_code in (404, 405)


