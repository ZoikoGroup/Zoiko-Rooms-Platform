"""Direct tests for the Section 9 Phase 2A PENDING_MOVE_IN lifecycle:

    Agreement reaches SIGNED -> Occupancy created PENDING_MOVE_IN -> room
    unavailable -> existing Confirm Move-In action -> eligibility checks ->
    PENDING_MOVE_IN transitions to ACTIVE.

test_availability.py already covers PENDING_MOVE_IN's effect on listing
visibility in isolation, and test_notification_coverage.py already covers
notification-type/routing wiring in isolation via direct crud calls -- this
file is the direct, end-to-end (real API) test of _apply_signature's
occupancy-creation side effect and confirm_move_in's state machine.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.identity_verification import IdentityVerification
from app.models.leasing import Agreement
from app.models.listing import Listing
from app.models.notification import Notification
from app.models.occupancy import Occupancy
from app.models.domain_event import DomainEvent
from app.models.finance import DisputeCase
from app.models.occupancy_activation import OccupancyActivationDecision, OccupancyHandoverEvent
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie
from tests.test_renter_offer_agreement_flow import _make_agreement_eligible, _submit_and_approve_application


def _make_host_and_verified_renter_with_published_listing(db: Session, *, suffix: str):
    """Same shape as test_application_workflow.py's
    _make_verified_renter_with_published_listing, but the provider party also
    has a real UserAccount -- needed here (unlike that file) because these
    tests assert on the HOST's own notifications, not just the renter's."""
    renter_party = Party(party_type="renter", status="active", jurisdiction="IN")
    db.add(renter_party)
    db.flush()
    renter_user = _make_user(db, email=f"occ-renter-{suffix}@test.com")
    renter_user.party_id = renter_party.id
    db.flush()
    db.add(
        IdentityVerification(
            party_id=renter_party.id, document_type="passport", document_category="identity", status="verified",
        )
    )

    provider_party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(provider_party)
    db.flush()
    host_user = _make_user(db, email=f"occ-host-{suffix}@test.com")
    host_user.party_id = provider_party.id
    db.flush()

    prop = Property(owner_party_id=provider_party.id, address="1 Occupancy St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()
    listing = Listing(
        id=f"L-OCC-{suffix}", slug=f"occ-{suffix}", name=f"Occupancy Test Listing {suffix}",
        room_type="Private room", city="Bengaluru", location="Koramangala", price_per_night=500,
        guests=1, rating=4.5, review_count=0, party_id=provider_party.id, owner_id=None,
        room_id=room.id, state="PUBLISHED",
    )
    db.add(listing)
    db.commit()
    return host_user, renter_user, listing.id


def _build_signed_agreement(client, db: Session, suffix: str, *, pay_obligations: bool = True):
    """Drives the real API from a published listing through a SIGNED agreement,
    mirroring test_renter_offer_agreement_flow.py's own end-to-end path exactly
    (offer sent/accepted by the renter's own session, agreement sent/signed by
    both sides). Signing itself is what creates the PENDING_MOVE_IN occupancy
    (see _apply_signature in crud/leasing.py) -- pay_obligations=False leaves
    the initial RENT+DEPOSIT obligations PENDING, for the eligibility-rejection
    test, without affecting occupancy creation itself."""
    host_user, renter_user, listing_id = _make_host_and_verified_renter_with_published_listing(db, suffix=suffix)
    renter_cookies = auth_user_cookie(renter_user)
    application_id, admin_cookies = _submit_and_approve_application(client, db, renter_user, listing_id)

    r = client.post(f"/api/leasing/applications/{application_id}/offers", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    offer_id = r.json()["id"]

    r = client.post(
        f"/api/leasing/offers/{offer_id}/terms",
        json={"monthlyRent": 500, "depositAmount": 500, "startDate": date.today().isoformat(), "termMonths": 6},
        cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text

    r = client.post(f"/api/leasing/offers/{offer_id}/send", cookies=admin_cookies)
    assert r.status_code == 200, r.text

    r = client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=renter_cookies)
    assert r.status_code == 200, r.text

    _make_agreement_eligible(db, listing_id)
    r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    agreement_id = r.json()["id"]

    r = client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
    assert r.status_code == 200, r.text

    r = client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=renter_cookies)
    assert r.status_code == 200, r.text

    r = client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "SIGNED"

    agreement = db.get(Agreement, agreement_id)
    if pay_obligations:
        for obligation in agreement.obligations:
            obligation.status = "PAID"
        db.commit()
        db.refresh(agreement)

    return agreement, host_user, renter_user, listing_id, admin_cookies


def _get_occupancy(db: Session, agreement: Agreement) -> Occupancy | None:
    return db.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id))


class TestSigningCreatesPendingOccupancy:
    def test_signing_creates_exactly_one_pending_move_in_occupancy(self, client, db_session: Session):
        agreement, _host, _renter, _listing_id, _admin_cookies = _build_signed_agreement(
            client, db_session, "sign-pending", pay_obligations=False,
        )

        occupancies = db_session.scalars(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id)).all()
        assert len(occupancies) == 1
        occupancy = occupancies[0]
        assert occupancy.status == "PENDING_MOVE_IN"
        assert occupancy.move_in_date is None
        assert occupancy.guest_id == agreement.offer.guest_id
        assert occupancy.room_id == agreement.offer.listing.room_id

    def test_expected_end_date_is_set_at_signing_and_unchanged_by_gate_waiting(self, client, db_session: Session):
        agreement, _host, _renter, _listing_id, admin_cookies = _build_signed_agreement(client, db_session, "end-date")
        occupancy = _get_occupancy(db_session, agreement)
        pending_expected_end_date = occupancy.expected_end_date
        assert pending_expected_end_date is not None

        r = client.post(f"/api/occupancy/agreements/{agreement.id}/confirm-move-in", cookies=admin_cookies)
        assert r.status_code == 409, r.text

        db_session.refresh(occupancy)
        assert occupancy.expected_end_date == pending_expected_end_date

    def test_repeated_provider_signature_does_not_duplicate_occupancy(self, client, db_session: Session):
        agreement, _host, _renter, _listing_id, admin_cookies = _build_signed_agreement(client, db_session, "resign")
        occupancy = _get_occupancy(db_session, agreement)
        assert occupancy is not None

        r = client.post(
            f"/api/leasing/agreements/{agreement.id}/sign", json={"asParty": "provider"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        occupancies = db_session.scalars(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id)).all()
        assert len(occupancies) == 1
        assert occupancies[0].id == occupancy.id

    def test_pending_move_in_occupancy_makes_room_unavailable(self, client, db_session: Session):
        agreement, _host, _renter, listing_id, _admin_cookies = _build_signed_agreement(
            client, db_session, "unavailable", pay_obligations=False,
        )
        occupancy = _get_occupancy(db_session, agreement)
        assert occupancy.status == "PENDING_MOVE_IN"

        r = client.get(f"/api/public/listings/{listing_id}")
        assert r.status_code == 404, r.text


class TestConfirmMoveIn:
    def test_confirm_move_in_waits_for_unresolved_date_policy(self, client, db_session: Session):
        agreement, _host, _renter, _listing_id, admin_cookies = _build_signed_agreement(client, db_session, "success")
        occupancy = _get_occupancy(db_session, agreement)
        assert occupancy.status == "PENDING_MOVE_IN"

        r = client.post(f"/api/occupancy/agreements/{agreement.id}/confirm-move-in", cookies=admin_cookies)

        assert r.status_code == 409, r.text
        assert r.json()["detail"]["outcome"] == "WAITING_FOR_GATE"
        assert "DATE_ELIGIBILITY_UNRESOLVED" in r.json()["detail"]["reasonCodes"]

        db_session.refresh(occupancy)
        assert occupancy.status == "PENDING_MOVE_IN"
        occupancies = db_session.scalars(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id)).all()
        assert len(occupancies) == 1

    def test_active_occupancy_cannot_be_confirmed_again(self, client, db_session: Session):
        agreement, _host, _renter, _listing_id, admin_cookies = _build_signed_agreement(client, db_session, "already-active")
        occupancy = _get_occupancy(db_session, agreement)
        occupancy.status = "ACTIVE"
        occupancy.move_in_date = date.today()
        db_session.commit()

        r = client.post(f"/api/occupancy/agreements/{agreement.id}/confirm-move-in", cookies=admin_cookies)
        assert r.status_code == 409, r.text

        occupancies = db_session.scalars(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id)).all()
        assert len(occupancies) == 1

    def test_ended_occupancy_cannot_be_reactivated(self, client, db_session: Session):
        agreement, _host, _renter, _listing_id, admin_cookies = _build_signed_agreement(client, db_session, "ended")
        occupancy = _get_occupancy(db_session, agreement)
        occupancy.status = "ENDED"
        db_session.commit()

        r = client.post(f"/api/occupancy/agreements/{agreement.id}/confirm-move-in", cookies=admin_cookies)
        assert r.status_code == 409, r.text

        occupancies = db_session.scalars(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id)).all()
        assert len(occupancies) == 1
        assert occupancies[0].status == "ENDED"

    def test_missing_occupancy_returns_error_and_does_not_create_one(self, client, db_session: Session):
        agreement, _host, _renter, _listing_id, admin_cookies = _build_signed_agreement(client, db_session, "missing")
        occupancy = _get_occupancy(db_session, agreement)
        assert occupancy is not None
        db_session.delete(occupancy)
        db_session.commit()

        r = client.post(f"/api/occupancy/agreements/{agreement.id}/confirm-move-in", cookies=admin_cookies)

        assert r.status_code == 404, r.text
        assert db_session.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id)) is None

    def test_move_in_rejected_when_eligibility_fails(self, client, db_session: Session):
        # Obligations left PENDING (never paid) -> check_move_in_eligibility must reject,
        # even though the PENDING_MOVE_IN occupancy already exists from signing.
        agreement, _host, _renter, _listing_id, admin_cookies = _build_signed_agreement(
            client, db_session, "ineligible", pay_obligations=False,
        )
        occupancy = _get_occupancy(db_session, agreement)
        assert occupancy.status == "PENDING_MOVE_IN"

        r = client.post(f"/api/occupancy/agreements/{agreement.id}/confirm-move-in", cookies=admin_cookies)

        assert r.status_code == 409, r.text
        assert "REQUIRED_PAYMENT_PENDING" in r.json()["detail"]["reasonCodes"]
        db_session.refresh(occupancy)
        assert occupancy.status == "PENDING_MOVE_IN"

    def test_authorization_is_enforced(self, client, db_session: Session):
        agreement, _host, _renter, _listing_id, _admin_cookies = _build_signed_agreement(client, db_session, "authz")
        outsider = _make_admin(db_session, email="occ-confirm-outsider@test.com", role="admin")
        db_session.commit()

        r = client.post(
            f"/api/occupancy/agreements/{agreement.id}/confirm-move-in", cookies=auth_admin_cookie(outsider),
        )

        assert r.status_code == 403, r.text
        occupancy = _get_occupancy(db_session, agreement)
        assert occupancy.status == "PENDING_MOVE_IN"

    def test_waiting_gate_does_not_generate_activation_notifications(self, client, db_session: Session):
        agreement, host, renter, _listing_id, admin_cookies = _build_signed_agreement(client, db_session, "notify")

        r = client.post(f"/api/occupancy/agreements/{agreement.id}/confirm-move-in", cookies=admin_cookies)
        assert r.status_code == 409, r.text

        renter_notices = db_session.scalars(
            select(Notification).where(
                Notification.recipient_user_id == renter.id,
                Notification.notification_type == "occupancy.move_in_confirmed",
            )
        ).all()
        assert renter_notices == []

        host_notice = db_session.scalar(
            select(Notification).where(
                Notification.recipient_user_id == host.id,
                Notification.notification_type == "occupancy.move_in_confirmed_for_host",
            )
        )
        assert host_notice is None


class TestPhase2BActivationGate:
    def _record_all_handover_evidence(self, client, db: Session, agreement: Agreement, renter, admin_cookies):
        occupancy = _get_occupancy(db, agreement)
        r = client.post(
            f"/api/occupancy/{occupancy.id}/handover/prepare",
            json={"evidenceRef": "provider-ready"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        r = client.post(
            f"/api/occupancy/{occupancy.id}/handover/events",
            json={"evidenceRef": "provider-delivered"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/handover/receipt",
            json={"evidenceRef": "renter-received"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        return occupancy

    def test_all_determinable_checks_pass_but_date_unresolved_waits(self, client, db_session: Session):
        agreement, _host, renter, _listing_id, admin_cookies = _build_signed_agreement(client, db_session, "gate-date")
        occupancy = self._record_all_handover_evidence(client, db_session, agreement, renter, admin_cookies)

        r = client.post(f"/api/occupancy/{occupancy.id}/activation-gate/evaluate", cookies=admin_cookies)

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["outcome"] == "WAITING_FOR_GATE"
        assert body["reasonCodes"] == ["DATE_ELIGIBILITY_UNRESOLVED"]
        assert body["checks"]["date_eligibility"] == "UNRESOLVED"
        assert all(value == "PASSED" for key, value in body["checks"].items() if key != "date_eligibility")
        db_session.refresh(occupancy)
        assert occupancy.status == "PENDING_MOVE_IN"

    def test_confirm_move_in_persists_waiting_decision_without_activation_side_effects(self, client, db_session: Session):
        agreement, host, renter, _listing_id, admin_cookies = _build_signed_agreement(client, db_session, "gate-confirm")
        occupancy = self._record_all_handover_evidence(client, db_session, agreement, renter, admin_cookies)

        r = client.post(f"/api/occupancy/agreements/{agreement.id}/confirm-move-in", cookies=admin_cookies)

        assert r.status_code == 409, r.text
        assert r.json()["detail"]["outcome"] == "WAITING_FOR_GATE"
        assert "DATE_ELIGIBILITY_UNRESOLVED" in r.json()["detail"]["reasonCodes"]
        db_session.refresh(occupancy)
        assert occupancy.status == "PENDING_MOVE_IN"
        assert db_session.scalar(select(OccupancyActivationDecision).where(
            OccupancyActivationDecision.occupancy_id == occupancy.id,
            OccupancyActivationDecision.outcome == "WAITING_FOR_GATE",
        )) is not None
        assert db_session.scalar(select(Notification).where(
            Notification.recipient_user_id.in_([host.id, renter.id]),
            Notification.notification_type.in_(("occupancy.move_in_confirmed", "occupancy.move_in_confirmed_for_host")),
        )) is None
        assert db_session.scalar(select(DomainEvent).where(
            DomainEvent.resource_type == "occupancy", DomainEvent.resource_id == str(occupancy.id),
            DomainEvent.event_type == "occupancy.active",
        )) is None

    def test_repeated_evaluation_appends_sequential_immutable_decisions(self, client, db_session: Session):
        agreement, _host, renter, _listing_id, admin_cookies = _build_signed_agreement(client, db_session, "gate-versions")
        occupancy = self._record_all_handover_evidence(client, db_session, agreement, renter, admin_cookies)
        url = f"/api/occupancy/{occupancy.id}/activation-gate/evaluate"

        first = client.post(url, cookies=admin_cookies)
        second = client.post(url, cookies=admin_cookies)

        assert first.status_code == second.status_code == 200
        assert first.json()["decisionVersion"] == 1
        assert second.json()["decisionVersion"] == 2
        decisions = db_session.scalars(select(OccupancyActivationDecision).where(
            OccupancyActivationDecision.occupancy_id == occupancy.id,
        ).order_by(OccupancyActivationDecision.decision_version)).all()
        assert [decision.decision_version for decision in decisions] == [1, 2]
        assert decisions[0].reason_codes == ["DATE_ELIGIBILITY_UNRESOLVED"]

    def test_unauthorized_provider_cannot_record_handover(self, client, db_session: Session):
        agreement, _host, _renter, _listing_id, _admin_cookies = _build_signed_agreement(client, db_session, "gate-provider-auth")
        occupancy = _get_occupancy(db_session, agreement)
        outsider = _make_admin(db_session, email="gate-provider-outsider@test.com", role="admin")
        db_session.commit()

        r = client.post(
            f"/api/occupancy/{occupancy.id}/handover/prepare", json={}, cookies=auth_admin_cookie(outsider),
        )

        assert r.status_code == 403, r.text

    def test_handover_events_are_idempotent_and_conflicts_are_rejected(self, client, db_session: Session):
        agreement, _host, _renter, _listing_id, admin_cookies = _build_signed_agreement(client, db_session, "gate-idempotent")
        occupancy = _get_occupancy(db_session, agreement)
        url = f"/api/occupancy/{occupancy.id}/handover/prepare"

        first = client.post(url, json={"notes": "ready"}, cookies=admin_cookies)
        retry = client.post(url, json={"notes": "ready"}, cookies=admin_cookies)
        conflict = client.post(url, json={"notes": "different"}, cookies=admin_cookies)

        assert first.status_code == retry.status_code == 200
        assert first.json()["id"] == retry.json()["id"]
        assert conflict.status_code == 409
        assert len(db_session.scalars(select(OccupancyHandoverEvent).where(
            OccupancyHandoverEvent.occupancy_id == occupancy.id,
        )).all()) == 1

    def test_renter_cannot_record_receipt_for_another_renter(self, client, db_session: Session):
        agreement, _host, _renter, _listing_id, _admin_cookies = _build_signed_agreement(client, db_session, "gate-receipt-auth")
        occupancy = _get_occupancy(db_session, agreement)
        outsider = _make_user(db_session, email="gate-other-renter@test.com")
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/handover/receipt",
            json={}, cookies=auth_user_cookie(outsider),
        )

        assert r.status_code == 403, r.text

    def test_admin_cannot_impersonate_renter_receipt(self, client, db_session: Session):
        agreement, _host, _renter, _listing_id, admin_cookies = _build_signed_agreement(client, db_session, "gate-admin-receipt")
        occupancy = _get_occupancy(db_session, agreement)

        r = client.post(f"/api/users/rentals/occupancies/{occupancy.id}/handover/receipt", json={}, cookies=admin_cookies)

        assert r.status_code == 401, r.text

    def test_missing_identity_and_compliance_block_or_wait_without_activation(self, client, db_session: Session):
        agreement, _host, renter, _listing_id, admin_cookies = _build_signed_agreement(client, db_session, "gate-compliance")
        occupancy = self._record_all_handover_evidence(client, db_session, agreement, renter, admin_cookies)
        renter.party_id = None
        agreement.offer.listing.market_release.status = "disabled"
        db_session.commit()

        r = client.post(f"/api/occupancy/{occupancy.id}/activation-gate/evaluate", cookies=admin_cookies)

        assert r.status_code == 200, r.text
        assert r.json()["outcome"] == "BLOCKED"
        assert "RENTER_IDENTITY_VERIFICATION_REQUIRED" in r.json()["reasonCodes"]
        assert any(code.startswith("MARKETPLACE_") for code in r.json()["reasonCodes"])

    def test_open_occupancy_dispute_requires_manual_review(self, client, db_session: Session):
        agreement, _host, renter, _listing_id, admin_cookies = _build_signed_agreement(client, db_session, "gate-dispute")
        occupancy = self._record_all_handover_evidence(client, db_session, agreement, renter, admin_cookies)
        db_session.add(DisputeCase(occupancy_id=occupancy.id, category="OTHER", status="OPEN"))
        db_session.commit()

        r = client.post(f"/api/occupancy/{occupancy.id}/activation-gate/evaluate", cookies=admin_cookies)

        assert r.status_code == 200, r.text
        assert r.json()["outcome"] == "MANUAL_REVIEW"
        assert "OPEN_OCCUPANCY_DISPUTE_REQUIRES_REVIEW" in r.json()["reasonCodes"]
        assert occupancy.status == "PENDING_MOVE_IN"


class TestEndOccupancy:
    def _active_occupancy(self, client, db: Session, suffix: str):
        agreement, host, renter, _listing_id, admin_cookies = _build_signed_agreement(client, db, suffix)
        occupancy = _get_occupancy(db, agreement)
        # Date eligibility deliberately fails closed in Phase 2B, so the legacy
        # move-out tests construct an already-active occupancy directly.
        occupancy.status = "ACTIVE"
        occupancy.move_in_date = date.today()
        db.commit()
        return occupancy.id, host, renter, admin_cookies

    def test_active_occupancy_ended_by_authorized_provider_becomes_ended(self, client, db_session: Session):
        occupancy_id, _host, _renter, admin_cookies = self._active_occupancy(client, db_session, "end-ok")

        r = client.post(f"/api/occupancy/{occupancy_id}/end", cookies=admin_cookies)

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ENDED"
        assert body["moveOutDate"] == date.today().isoformat()

        occupancy = db_session.get(Occupancy, occupancy_id)
        assert occupancy.status == "ENDED"
        assert occupancy.move_out_date == date.today()
        assert occupancy.ended_at is not None

    def test_end_occupancy_notifies_renter_and_host(self, client, db_session: Session):
        occupancy_id, host, renter, admin_cookies = self._active_occupancy(client, db_session, "end-notify")

        r = client.post(f"/api/occupancy/{occupancy_id}/end", cookies=admin_cookies)
        assert r.status_code == 200, r.text

        assert db_session.scalar(
            select(Notification).where(
                Notification.recipient_user_id == renter.id, Notification.notification_type == "occupancy.ended",
            )
        ) is not None
        assert db_session.scalar(
            select(Notification).where(
                Notification.recipient_user_id == host.id, Notification.notification_type == "occupancy.ended_for_host",
            )
        ) is not None

    def test_unauthorized_admin_cannot_end_another_providers_occupancy(self, client, db_session: Session):
        occupancy_id, _host, _renter, _admin_cookies = self._active_occupancy(client, db_session, "end-authz")
        outsider = _make_admin(db_session, email="occ-end-outsider@test.com", role="admin")
        db_session.commit()

        r = client.post(f"/api/occupancy/{occupancy_id}/end", cookies=auth_admin_cookie(outsider))

        assert r.status_code == 403, r.text
        occupancy = db_session.get(Occupancy, occupancy_id)
        assert occupancy.status == "ACTIVE"
