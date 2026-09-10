"""Regression coverage for the notification gaps found while auditing the
application -> offer -> agreement -> occupancy -> finance -> sublet lifecycle.

Each test exercises the actual crud function (directly, or via the real API
where no eligibility plumbing gets in the way) and asserts on the resulting
`Notification` rows: correct recipient, correct notification_type (so the
frontend's resolveNotificationHref routes it to the right page), and that
sensitive internal detail (e.g. an admin's rejection reason_code) never leaks
into a notification meant for the other side.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import finance as finance_crud
from app.crud import leasing as leasing_crud
from app.crud import notification as notification_module
from app.crud import occupancy as occupancy_crud
from app.crud import sublet as sublet_crud
from app.models.finance import DepositRecord, Obligation, OBLIGATION_TYPE_TO_PLANE, RefundRequest, SimulatedPayment
from app.models.guest import Guest
from app.models.leasing import Agreement, AgreementVersion, Application, Offer, OfferTerms
from app.models.listing import Listing
from app.models.notification import Notification
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.sublet_request import SubletRequest
from app.schemas.finance import DepositRelease, DisputeCreate, DisputeResolve, RefundDecide, SimulatedPaymentCreate
from app.schemas.leasing import ApplicationDecide
from tests.conftest import _make_admin, _make_user, auth_user_cookie
from tests.test_self_listing_restriction import _make_host_and_listing, _make_verified_renter


def _notification(db: Session, *, notification_type: str, recipient_user_id: int | None = None) -> Notification | None:
    query = select(Notification).where(Notification.notification_type == notification_type)
    if recipient_user_id is not None:
        query = query.where(Notification.recipient_user_id == recipient_user_id)
    return db.scalar(query)


def _application_for(db: Session, listing_id: str, guest: Guest) -> Application:
    application = Application(listing_id=listing_id, guest_id=guest.id)
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def _linked_guest_and_renter(db: Session, suffix: str) -> tuple[Guest, "object"]:
    """A Guest whose user_account_id is already linked -- notify_user_by_guest
    only reaches a real recipient once this link exists, so every test builds
    it before calling the crud function under test, not after."""
    guest = Guest(id=f"G-{suffix}", name=f"Renter {suffix}", email=f"renter-{suffix}@test.com", joined_at=date.today())
    db.add(guest)
    db.commit()
    renter_user = _make_user(db, email=f"renter-{suffix}@test.com")
    guest.user_account_id = renter_user.id
    db.commit()
    return guest, renter_user


class TestApplicationNotifications:
    """Requirement #1: renter is told their own application was submitted;
    the host is told a new one arrived; both are told when it's decided."""

    def test_submission_notifies_both_renter_and_host(self, client, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="app-host@test.com")
        renter = _make_verified_renter(db_session, email="app-renter@test.com")

        r = client.post(
            "/api/users/rentals/applications",
            json={"listingId": listing_id, "message": "hi"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text

        renter_notice = _notification(db_session, notification_type="application.confirmation", recipient_user_id=renter.id)
        assert renter_notice is not None
        assert "submitted" in renter_notice.message.lower()

        host_notice = _notification(db_session, notification_type="application.received", recipient_user_id=host.id)
        assert host_notice is not None

    def test_public_submission_also_notifies_the_host(self, client, db_session: Session):
        """The unauthenticated /api/public/applications path (a separate renter-
        facing site submitting on a renter's behalf) previously notified nobody
        at all -- confirm the host now hears about it too."""
        host, listing_id = _make_host_and_listing(db_session, email="public-app-host@test.com")

        r = client.post(
            "/api/public/applications",
            json={
                "listingId": listing_id,
                "newGuest": {"name": "Walk-in Renter", "email": "walkin@test.com", "phone": "", "location": ""},
            },
        )
        assert r.status_code == 201, r.text

        host_notice = _notification(db_session, notification_type="application.received", recipient_user_id=host.id)
        assert host_notice is not None

    def test_decision_notifies_renter_and_host_without_leaking_internal_reason_code(self, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="decide-host@test.com")
        guest, renter_user = _linked_guest_and_renter(db_session, "DECIDE1")
        application = _application_for(db_session, listing_id, guest)

        admin = _make_admin(db_session, email="decide-admin@test.com", role="super_admin")
        leasing_crud.decide_application(
            db_session, application, admin,
            ApplicationDecide(decision="REJECTED", reason_code="fraud_suspected", note=""),
        )

        # Renter's copy: no reason_code (internal trust & safety detail) leaked.
        renter_notice = _notification(db_session, notification_type="application.rejected", recipient_user_id=renter_user.id)
        assert renter_notice is not None
        assert "fraud_suspected" not in renter_notice.message

        host_notice = _notification(db_session, notification_type="application.decided", recipient_user_id=host.id)
        assert host_notice is not None
        assert "fraud_suspected" not in host_notice.message
        assert "fraud_suspected" not in host_notice.title


class TestOfferNotifications:
    """Requirement #2: renter/host are told once an offer reaches an
    authoritative state -- never before the backend actually commits it."""

    def _offer(self, db: Session, suffix: str) -> tuple[Offer, "object", "object"]:
        host, listing_id = _make_host_and_listing(db, email=f"offer-host-{suffix}@test.com")
        guest, renter_user = _linked_guest_and_renter(db, f"OFFER-{suffix}")
        application = _application_for(db, listing_id, guest)
        offer = Offer(application_id=application.id, listing_id=listing_id, guest_id=guest.id)
        db.add(offer)
        db.commit()
        db.refresh(offer)
        return offer, host, renter_user

    def test_offer_sent_notifies_renter_only(self, db_session: Session):
        offer, host, renter_user = self._offer(db_session, "sent")
        admin = _make_admin(db_session, email="offer-sent-admin@test.com", role="super_admin")

        leasing_crud.set_offer_status(db_session, offer, admin, "SENT")

        assert _notification(db_session, notification_type="offer.sent", recipient_user_id=renter_user.id) is not None
        assert _notification(db_session, notification_type="offer.sent", recipient_user_id=host.id) is None

    def test_offer_accepted_notifies_renter_and_host(self, db_session: Session):
        """Updated for origin/main's _assert_renter_has_no_account: a renter who
        has a real Zoiko account can no longer be accepted-on-behalf-of by an
        admin -- confirm that's actually enforced (409), then confirm the
        notification/email behavior itself (set_offer_status's ACCEPTED branch)
        still fires correctly for the case the admin path remains legitimate
        for: a walk-in guest with no Zoiko account. (The new self-service
        leasing_crud.user_accept_offer path, for a renter who does have an
        account, does not itself send a notification yet -- a separate,
        pre-existing gap in that new code, not something to paper over here.)"""
        offer, host, _renter_user = self._offer(db_session, "accepted")
        admin = _make_admin(db_session, email="offer-acc-admin@test.com", role="super_admin")
        with pytest.raises(HTTPException) as excinfo:
            leasing_crud.set_offer_status(db_session, offer, admin, "ACCEPTED")
        assert excinfo.value.status_code == status.HTTP_409_CONFLICT

        # Same host/listing, a second (walk-in, no Zoiko account) applicant --
        # _make_host_and_listing hardcodes its listing id, so a second host must
        # not be created in the same test; reusing this one's listing is fine
        # since it's the guest's account status that the rule keys off of, not
        # the listing.
        guest = Guest(id="G-OFFER-walkin", name="Walk-in Renter", email="offer-walkin@test.com", joined_at=date.today())
        db_session.add(guest)
        db_session.commit()
        application = _application_for(db_session, offer.listing_id, guest)
        walkin_offer = Offer(application_id=application.id, listing_id=offer.listing_id, guest_id=guest.id)
        db_session.add(walkin_offer)
        db_session.commit()
        db_session.refresh(walkin_offer)

        leasing_crud.set_offer_status(db_session, walkin_offer, admin, "ACCEPTED")

        assert _notification(db_session, notification_type="offer.accepted_for_host", recipient_user_id=host.id) is not None


class TestAgreementNotifications:
    """Requirement #5: notify when the agreement requires action (sent), and
    only when it actually reaches the executed (SIGNED) state -- not on a
    single one-sided signature."""

    def _agreement(self, db: Session, suffix: str):
        host, listing_id = _make_host_and_listing(db, email=f"agr-host-{suffix}@test.com")
        guest, renter_user = _linked_guest_and_renter(db, f"AGR-{suffix}")

        application = _application_for(db, listing_id, guest)
        offer = Offer(application_id=application.id, listing_id=listing_id, guest_id=guest.id, status="ACCEPTED")
        db.add(offer)
        db.flush()
        terms = OfferTerms(offer_id=offer.id, version=1, monthly_rent=15000, deposit_amount=15000, start_date=date.today(), term_months=12)
        db.add(terms)
        offer.current_version = 1
        db.commit()
        db.refresh(offer)

        agreement = Agreement(offer_id=offer.id)
        db.add(agreement)
        db.flush()
        # ZR-ENG-CLR-004 requires every agreement to carry a version whose
        # snapshot resolves required_signers (see crud/leasing.py:_apply_signature) --
        # mirror what create_agreement builds, not a bare Agreement row.
        listing = offer.listing
        snapshot = {
            "listing_name": listing.name, "listing_location": listing.location, "listing_city": listing.city,
            "provider_name": host.full_name, "provider_email": host.email,
            "renter_name": guest.name, "renter_email": guest.email,
            "monthly_rent": float(terms.monthly_rent), "deposit_amount": float(terms.deposit_amount),
            "start_date": terms.start_date.isoformat(), "term_months": terms.term_months,
        }
        db.add(AgreementVersion(agreement_id=agreement.id, version_no=1, status="WORKING", snapshot=snapshot))
        # ZR-ENG-CLR-001 Rule 7: SIGNED is only reached once every initial
        # obligation has cleared -- waived here so the "both signatures"
        # test can assert SIGNED rather than the PAYMENT_IN_PROGRESS hold.
        db.add(Obligation(
            obligation_type="RENT", money_plane=OBLIGATION_TYPE_TO_PLANE["RENT"],
            amount=terms.monthly_rent, due_date=terms.start_date, status="WAIVED", agreement_id=agreement.id,
        ))
        db.commit()
        db.refresh(agreement)

        admin = _make_admin(db, email=f"agr-admin-{suffix}@test.com", role="super_admin")
        return agreement, host, renter_user, admin

    def test_agreement_sent_notifies_renter(self, db_session: Session):
        agreement, host, renter_user, admin = self._agreement(db_session, "sent")
        leasing_crud.send_agreement(db_session, agreement, admin)

        notice = _notification(db_session, notification_type="agreement.sent", recipient_user_id=renter_user.id)
        assert notice is not None
        assert "sign" in notice.title.lower()

    def test_single_signature_does_not_notify_executed(self, db_session: Session):
        agreement, host, renter_user, admin = self._agreement(db_session, "single")
        leasing_crud.sign_agreement(db_session, agreement, "provider", admin)

        assert agreement.status != "SIGNED"
        assert _notification(db_session, notification_type="agreement.signed", recipient_user_id=renter_user.id) is None

    def test_both_signatures_notify_renter_and_host_of_executed_agreement(self, db_session: Session):
        """Updated for origin/main's _assert_renter_has_no_account: an
        admin-attested signature "as renter" is now rejected once the renter has
        a real account -- confirm that's enforced (409), then confirm the
        provider's signature is still admin-attested as before, and the
        renter's own signature goes through the new self-service
        leasing_crud.user_sign_agreement instead. Both paths share
        _apply_signature, so the notification/email assertions are unchanged."""
        agreement, host, renter_user, admin = self._agreement(db_session, "both")

        with pytest.raises(HTTPException) as excinfo:
            leasing_crud.sign_agreement(db_session, agreement, "renter", admin)
        assert excinfo.value.status_code == status.HTTP_409_CONFLICT

        leasing_crud.sign_agreement(db_session, agreement, "provider", admin)
        agreement.status = "SENT"  # user_sign_agreement only accepts a sent agreement
        db_session.commit()

        leasing_crud.user_sign_agreement(db_session, renter_user, agreement)

        assert agreement.status == "SIGNED"
        assert _notification(db_session, notification_type="agreement.signed", recipient_user_id=renter_user.id) is not None
        assert _notification(db_session, notification_type="agreement.signed_for_host", recipient_user_id=host.id) is not None


class TestOccupancyNotifications:
    """Requirement #9: move-in/move-out are communicated as committed events,
    never as a state change the notification itself performs."""

    def _active_occupancy(self, db: Session, suffix: str) -> tuple[Occupancy, "object", "object"]:
        host, listing_id = _make_host_and_listing(db, email=f"occ-host-{suffix}@test.com")
        guest, renter_user = _linked_guest_and_renter(db, f"OCC-{suffix}")
        listing = db.get(Listing, listing_id)

        application = _application_for(db, listing_id, guest)
        offer = Offer(application_id=application.id, listing_id=listing_id, guest_id=guest.id, status="ACCEPTED")
        db.add(offer)
        db.commit()
        db.refresh(offer)

        occupancy = Occupancy(
            offer_id=offer.id, listing_id=listing_id, room_id=listing.room_id, guest_id=guest.id,
            status="ACTIVE", move_in_date=date.today(), expected_end_date=date.today() + timedelta(days=365),
        )
        db.add(occupancy)
        db.commit()
        db.refresh(occupancy)
        return occupancy, host, renter_user

    def test_end_occupancy_notifies_renter_and_host(self, db_session: Session):
        occupancy, host, renter_user = self._active_occupancy(db_session, "end")
        admin = _make_admin(db_session, email="occ-end-admin@test.com", role="super_admin")

        occupancy_crud.end_occupancy(db_session, occupancy, admin)

        assert occupancy.status == "ENDED"
        assert _notification(db_session, notification_type="occupancy.ended", recipient_user_id=renter_user.id) is not None
        assert _notification(db_session, notification_type="occupancy.ended_for_host", recipient_user_id=host.id) is not None


class TestSubletNotifications:
    """Requirement #8: the proposed occupant is told only once actually
    authorized; the host is told the tenant changed."""

    def test_approving_a_sublet_notifies_new_occupant_and_host(self, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="sublet-host@test.com")
        listing = db_session.get(Listing, listing_id)

        current_guest = Guest(id="G-SUBLET-CUR", name="Current Tenant", email="sublet-current@test.com", joined_at=date.today())
        db_session.add(current_guest)
        db_session.commit()

        application = _application_for(db_session, listing_id, current_guest)
        offer = Offer(application_id=application.id, listing_id=listing_id, guest_id=current_guest.id, status="ACCEPTED")
        db_session.add(offer)
        db_session.flush()
        agreement = Agreement(offer_id=offer.id, status="SIGNED")
        db_session.add(agreement)
        db_session.commit()

        occupancy = Occupancy(
            offer_id=offer.id, listing_id=listing_id, room_id=listing.room_id, guest_id=current_guest.id,
            status="ACTIVE", move_in_date=date.today(), expected_end_date=date.today() + timedelta(days=200),
        )
        db_session.add(occupancy)
        db_session.commit()
        db_session.refresh(occupancy)

        # Proposed new occupant: a verified renter with their own party/UserAccount.
        proposed_user = _make_verified_renter(db_session, email="sublet-proposed@test.com")

        sublet_request = SubletRequest(
            current_occupancy_id=occupancy.id, proposed_renter_party_id=proposed_user.party_id,
            status="pending_admin_review",
        )
        db_session.add(sublet_request)
        db_session.commit()
        db_session.refresh(sublet_request)

        admin = _make_admin(db_session, email="sublet-admin@test.com", role="super_admin")
        sublet_crud.approve_sublet_request(db_session, sublet_request, admin, notes="Looks good")

        assert _notification(db_session, notification_type="sublet_request.authorized", recipient_user_id=proposed_user.id) is not None
        assert _notification(db_session, notification_type="sublet_request.tenant_changed", recipient_user_id=host.id) is not None


class TestFinanceNotifications:
    """Requirements #3/#4: deposit holding-status changes and payout state are
    communicated only once the backend actually records them; refunds notify
    the renter once money actually moves."""

    def test_deposit_release_notifies_renter(self, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="deposit-host@test.com")
        guest, renter_user = _linked_guest_and_renter(db_session, "DEPOSIT1")

        application = _application_for(db_session, listing_id, guest)
        offer = Offer(application_id=application.id, listing_id=listing_id, guest_id=guest.id, status="ACCEPTED")
        db_session.add(offer)
        db_session.flush()
        agreement = Agreement(offer_id=offer.id, status="SIGNED")
        db_session.add(agreement)
        db_session.flush()
        obligation = Obligation(
            obligation_type="DEPOSIT", money_plane="SAFEGUARDED", amount=10000, due_date=date.today(),
            status="PAID", agreement_id=agreement.id,
        )
        db_session.add(obligation)
        db_session.flush()
        record = DepositRecord(obligation_id=obligation.id, held_amount=10000, status="HELD")
        db_session.add(record)
        db_session.commit()
        db_session.refresh(record)

        admin = _make_admin(db_session, email="deposit-admin@test.com", role="super_admin")
        finance_crud.release_deposit(db_session, record, admin, DepositRelease(amount=10000, notes="End of tenancy"))

        notice = _notification(db_session, notification_type="deposit.released", recipient_user_id=renter_user.id)
        assert notice is not None

    def test_payout_held_notifies_host_with_the_real_reason(self, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="payout-host@test.com")
        listing = db_session.get(Listing, listing_id)
        party = db_session.get(Party, listing.party_id)

        guest = Guest(id="G-PAYOUT1", name="Payout Renter", email="payout-renter@test.com", joined_at=date.today())
        db_session.add(guest)
        db_session.commit()

        application = _application_for(db_session, listing_id, guest)
        offer = Offer(application_id=application.id, listing_id=listing_id, guest_id=guest.id, status="ACCEPTED")
        db_session.add(offer)
        db_session.flush()
        occupancy = Occupancy(
            offer_id=offer.id, listing_id=listing_id, room_id=listing.room_id, guest_id=guest.id,
            status="ACTIVE", move_in_date=date.today(),
        )
        db_session.add(occupancy)
        db_session.flush()

        obligation = Obligation(
            obligation_type="RENT", money_plane="OCCUPANCY", amount=15000, due_date=date.today(), status="PAID",
            occupancy_id=occupancy.id,
        )
        db_session.add(obligation)
        db_session.commit()

        admin = _make_admin(db_session, email="payout-admin@test.com", role="super_admin")
        # No AuthorityRecord exists for this room -> held_reason is set -> HELD.
        payout = finance_crud.run_payout(db_session, party, admin, "2026-09")

        assert payout.status == "HELD"
        notice = _notification(db_session, notification_type="payout.held", recipient_user_id=host.id)
        assert notice is not None
        assert "authority" in notice.message.lower()

    def test_refund_completion_notifies_renter(self, db_session: Session):
        guest, renter_user = _linked_guest_and_renter(db_session, "REFUND1")
        admin = _make_admin(db_session, email="refund-admin@test.com", role="super_admin")

        obligation = Obligation(obligation_type="RENT", money_plane="OCCUPANCY", amount=5000, due_date=date.today(), status="PAID")
        db_session.add(obligation)
        db_session.flush()
        payment = SimulatedPayment(guest_id=guest.id, amount=5000, currency="INR", idempotency_key="refund-test-1", status="SUCCEEDED")
        db_session.add(payment)
        db_session.commit()
        db_session.refresh(payment)

        refund = RefundRequest(payment_id=payment.id, obligation_id=obligation.id, amount=2000, reason="Overcharged", requested_by_admin_id=admin.id)
        db_session.add(refund)
        db_session.commit()
        db_session.refresh(refund)

        finance_crud.decide_refund(db_session, refund, admin, RefundDecide(approve=True))

        assert refund.status == "COMPLETED"
        notice = _notification(db_session, notification_type="refund.completed", recipient_user_id=renter_user.id)
        assert notice is not None


class TestDisputeNotifications:
    """Requirement #10: relevant parties are told a dispute needs attention or
    has been resolved -- and no deadline is ever invented, since DisputeCase
    has no deadline field."""

    def test_open_dispute_notifies_renter_host_and_super_admins(self, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="dispute-host@test.com")
        listing = db_session.get(Listing, listing_id)
        guest, renter_user = _linked_guest_and_renter(db_session, "DISPUTE1")

        application = _application_for(db_session, listing_id, guest)
        offer = Offer(application_id=application.id, listing_id=listing_id, guest_id=guest.id, status="ACCEPTED")
        db_session.add(offer)
        db_session.flush()
        occupancy = Occupancy(
            offer_id=offer.id, listing_id=listing_id, room_id=listing.room_id, guest_id=guest.id,
            status="ACTIVE", move_in_date=date.today(),
        )
        db_session.add(occupancy)
        db_session.commit()
        db_session.refresh(occupancy)

        super_admin = _make_admin(db_session, email="dispute-super@test.com", role="super_admin")
        dispute = finance_crud.open_dispute(
            db_session, DisputeCreate(occupancy_id=occupancy.id, category="DAMAGE", description="Broken fixture"), super_admin,
        )

        renter_notice = _notification(db_session, notification_type="dispute.opened", recipient_user_id=renter_user.id)
        assert renter_notice is not None
        assert "deadline" not in renter_notice.message.lower()

        assert _notification(db_session, notification_type="dispute.opened_for_host", recipient_user_id=host.id) is not None
        admin_notice = _notification(db_session, notification_type="dispute.opened_admin")
        assert admin_notice is not None
        assert "deadline" not in admin_notice.message.lower()

        finance_crud.resolve_dispute(db_session, dispute, super_admin, DisputeResolve(status="RESOLVED", resolution_notes="Fixed and closed"))
        assert _notification(db_session, notification_type="dispute.resolved", recipient_user_id=renter_user.id) is not None
        assert _notification(db_session, notification_type="dispute.resolved_for_host", recipient_user_id=host.id) is not None


class TestEmailWiring:
    """Confirms the transactional email helpers are actually called from the
    crud layer, alongside (not instead of) the in-app notification -- not just
    that mailer.send_email works in isolation (see test_mailer.py)."""

    def test_application_decision_sends_email_to_renter(self, db_session: Session, monkeypatch):
        _host, listing_id = _make_host_and_listing(db_session, email="email-decide-host@test.com")
        guest, renter_user = _linked_guest_and_renter(db_session, "EMAILDECIDE")
        application = _application_for(db_session, listing_id, guest)
        admin = _make_admin(db_session, email="email-decide-admin@test.com", role="super_admin")

        calls = []
        monkeypatch.setattr(
            leasing_crud, "send_application_decided_email",
            lambda to_email, full_name, listing_name, approved: calls.append((to_email, approved)),
        )

        leasing_crud.decide_application(db_session, application, admin, ApplicationDecide(decision="APPROVED"))

        assert calls == [(renter_user.email, True)]

    def test_payment_confirmation_sends_receipt_email(self, db_session: Session, monkeypatch):
        guest, renter_user = _linked_guest_and_renter(db_session, "EMAILPAY")
        obligation = Obligation(obligation_type="RENT", money_plane="OCCUPANCY", amount=5000, due_date=date.today(), status="PENDING")
        db_session.add(obligation)
        db_session.flush()
        payment = SimulatedPayment(guest_id=guest.id, amount=5000, currency="INR", idempotency_key="email-pay-1")
        db_session.add(payment)
        db_session.commit()
        db_session.refresh(payment)
        admin = _make_admin(db_session, email="email-pay-admin@test.com", role="super_admin")

        calls = []
        monkeypatch.setattr(
            finance_crud, "send_payment_confirmed_email",
            lambda to_email, full_name, amount, currency: calls.append((to_email, amount, currency)),
        )

        from app.schemas.finance import PaymentAllocationInput, PaymentConfirm

        finance_crud.confirm_payment(
            db_session, payment, PaymentConfirm(allocations=[PaymentAllocationInput(obligation_id=obligation.id, amount=5000)]), admin,
        )

        assert calls == [(renter_user.email, 5000, "INR")]


class TestNoDuplicateNotifications:
    """The same business event, processed twice, must not create a second
    notification row -- the model's unique constraint on
    (type, entity, recipient) is the actual mechanism; this proves it holds
    for a real call path, not just at the model level."""

    def test_deciding_the_same_application_twice_does_not_duplicate_the_notification(self, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="dup-host@test.com")
        guest, renter_user = _linked_guest_and_renter(db_session, "DUP1")
        application = _application_for(db_session, listing_id, guest)
        admin = _make_admin(db_session, email="dup-admin@test.com", role="super_admin")

        leasing_crud.decide_application(db_session, application, admin, ApplicationDecide(decision="APPROVED"))
        leasing_crud.decide_application(db_session, application, admin, ApplicationDecide(decision="APPROVED"))

        renter_rows = db_session.scalars(
            select(Notification).where(
                Notification.notification_type == "application.approved",
                Notification.recipient_user_id == renter_user.id,
            )
        ).all()
        host_rows = db_session.scalars(
            select(Notification).where(
                Notification.notification_type == "application.decided",
                Notification.recipient_user_id == host.id,
            )
        ).all()
        assert len(renter_rows) == 1
        assert len(host_rows) == 1


class TestPendingPaymentDoesNotNotify:
    def test_creating_a_payment_intent_does_not_notify_success(self, db_session: Session):
        guest, renter_user = _linked_guest_and_renter(db_session, "PENDING1")
        obligation = Obligation(obligation_type="RENT", money_plane="OCCUPANCY", amount=3000, due_date=date.today(), status="PENDING")
        db_session.add(obligation)
        db_session.commit()

        payment = finance_crud.create_payment_intent(
            db_session, SimulatedPaymentCreate(guest_id=guest.id, amount=3000, currency="INR", idempotency_key="pending-test-1"),
        )
        assert payment.status == "PENDING"
        assert _notification(db_session, notification_type="payment.confirmed", recipient_user_id=renter_user.id) is None

        admin = _make_admin(db_session, email="pending-admin@test.com", role="super_admin")
        from app.schemas.finance import PaymentAllocationInput, PaymentConfirm

        finance_crud.confirm_payment(
            db_session, payment, PaymentConfirm(allocations=[PaymentAllocationInput(obligation_id=obligation.id, amount=3000)]), admin,
        )
        assert _notification(db_session, notification_type="payment.confirmed", recipient_user_id=renter_user.id) is not None


class TestNotificationFailureDoesNotRollBackBusinessTransaction:
    def test_a_broken_notification_insert_does_not_block_the_application_decision(self, db_session: Session, monkeypatch):
        _host, listing_id = _make_host_and_listing(db_session, email="failsafe-host@test.com")
        guest, _renter_user = _linked_guest_and_renter(db_session, "FAILSAFE1")
        application = _application_for(db_session, listing_id, guest)
        admin = _make_admin(db_session, email="failsafe-admin@test.com", role="super_admin")

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated notification backend failure")

        monkeypatch.setattr(notification_module, "_create", _boom)

        # Must not raise, despite every notify_* call failing internally.
        leasing_crud.decide_application(db_session, application, admin, ApplicationDecide(decision="APPROVED"))

        db_session.refresh(application)
        assert application.status == "DECIDED"
        assert db_session.scalar(
            select(Notification).where(Notification.related_entity_id == str(application.id))
        ) is None
