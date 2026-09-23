"""ZR-PAY-LINK-003 <-> legacy models/finance.py bridge:
crud/rental_payment.py:_sync_legacy_obligation_from_confirmation. Covers:
confirming both the RENT and DEPOSIT RentalPaymentObligation created
alongside an agreement's own legacy Obligation pair (crud/leasing.py:
create_agreement) bridges both legacy Obligations to PAID and (via the same
Booking Orchestrator boundary crud/finance.py:confirm_payment itself uses)
brings the agreement all the way to SIGNED with a DepositRecord created;
confirming only one leaves the agreement not yet signed; the bridge never
touches a deposit top-up's own separate legacy Obligation (no
RentalPaymentObligation counterpart -- must keep using the legacy rail);
a recurring (occupancy-scoped) RentalPaymentObligation with no matching
legacy counterpart is a safe no-op; and one WITH a matching legacy
recurring Obligation bridges it to PAID and generates the next period's
obligation in both domains (crud/occupancy.py:generate_next_rent_obligation,
called with admin=None)."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import rental_payment as rp_crud
from app.crud.guest import get_guest_for_user
from app.models.finance import OBLIGATION_TYPE_TO_PLANE, Obligation
from app.models.leasing import Agreement
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.rental_payment import RentalPaymentObligation
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie, deliver_all_disclosures
from tests.test_application_workflow import _make_verified_renter_with_published_listing
from tests.test_deposit_claims import _make_agreement_eligible


def _create_signed_agreement(client, db_session: Session, *, email_suffix: str, monthly_rent: float = 3000.0):
    """Drives the real application -> offer -> agreement -> BOTH SIGNATURES
    flow (same steps as tests/test_deposit_claims.py:_fund_a_deposit up to
    its own two /sign calls) so the agreement reaches PAYMENT_IN_PROGRESS/
    PAYMENT_PENDING -- crud/leasing.py:confirm_agreement_payment (and so the
    bridge under test here) is a no-op for any other agreement status.
    Deliberately stops there, before that fixture's own legacy
    /api/finance/payments confirm calls -- this suite pays through the new
    rail (the bridge) instead."""
    user, listing_id = _make_verified_renter_with_published_listing(db_session, email=f"bridge-renter-{email_suffix}@test.com")
    user_cookies = auth_user_cookie(user)
    admin = _make_admin(db_session, email=f"bridge-admin-{email_suffix}@test.com", role="super_admin")
    admin_cookies = auth_admin_cookie(admin)

    r = client.post(
        "/api/users/rentals/applications",
        json={"listingId": listing_id, "message": "hi", "desiredMoveIn": None},
        cookies=user_cookies,
    )
    assert r.status_code == 201, r.text
    application_id = r.json()["id"]
    assert client.post(
        f"/api/leasing/applications/{application_id}/decide", json={"decision": "APPROVED"}, cookies=admin_cookies,
    ).status_code == 200
    r = client.post(f"/api/leasing/applications/{application_id}/offers", cookies=admin_cookies)
    offer_id = r.json()["id"]
    start_date = date.today() + timedelta(days=5)
    r = client.post(
        f"/api/leasing/offers/{offer_id}/terms",
        json={
            "monthlyRent": monthly_rent, "depositAmount": monthly_rent,
            "startDate": start_date.isoformat(), "termMonths": 6,
        },
        cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text
    assert client.post(f"/api/leasing/offers/{offer_id}/send", cookies=admin_cookies).status_code == 200
    assert client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=user_cookies).status_code == 200

    _make_agreement_eligible(db_session, listing_id)
    r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    agreement_id = r.json()["id"]

    assert client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies).status_code == 200
    deliver_all_disclosures(client, admin_cookies, agreement_id)
    assert client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=user_cookies).status_code == 200
    assert client.post(
        f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies,
    ).status_code == 200

    return user, user_cookies, agreement_id, start_date


def _rental_payment_obligations_for(db: Session, agreement_id: int) -> dict[str, RentalPaymentObligation]:
    obligations = list(db.scalars(select(RentalPaymentObligation).where(RentalPaymentObligation.agreement_id == agreement_id)))
    return {o.obligation_type: o for o in obligations}


def _legacy_obligations_for(db: Session, agreement_id: int) -> list[Obligation]:
    return list(db.scalars(select(Obligation).where(Obligation.agreement_id == agreement_id).order_by(Obligation.id)))


def _confirm_via_new_rail(db: Session, obligation: RentalPaymentObligation, guest, recipient: Party) -> None:
    record = rp_crud.mark_paid(
        db, guest, obligation, amount=float(obligation.amount), currency=obligation.currency,
        declared_date=date.today(), payment_method_category="BANK_TRANSFER",
    )
    rp_crud.confirm_receipt(db, recipient, record)


class TestBridgeReachesSignedAgreement:
    def test_confirming_both_rent_and_deposit_signs_the_agreement(self, client, db_session: Session):
        user, _user_cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix="bridge1")
        guest = get_guest_for_user(db_session, user)

        rp_obligations = _rental_payment_obligations_for(db_session, agreement_id)
        assert set(rp_obligations) == {"RENT", "DEPOSIT"}
        recipient = db_session.get(Party, rp_obligations["RENT"].recipient_party_id)

        _confirm_via_new_rail(db_session, rp_obligations["RENT"], guest, recipient)
        _confirm_via_new_rail(db_session, rp_obligations["DEPOSIT"], guest, recipient)

        legacy = _legacy_obligations_for(db_session, agreement_id)
        assert {o.obligation_type: o.status for o in legacy} == {"RENT": "PAID", "DEPOSIT": "PAID"}

        deposit_obligation = next(o for o in legacy if o.obligation_type == "DEPOSIT")
        assert deposit_obligation.deposit_record is not None
        assert float(deposit_obligation.deposit_record.held_amount) == float(deposit_obligation.amount)

        agreement = db_session.get(Agreement, agreement_id)
        db_session.refresh(agreement)
        assert agreement.status == "SIGNED"
        occupancy = db_session.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id))
        assert occupancy is not None

    def test_confirming_only_one_leaves_the_agreement_unsigned(self, client, db_session: Session):
        user, _user_cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix="bridge2")
        guest = get_guest_for_user(db_session, user)

        rp_obligations = _rental_payment_obligations_for(db_session, agreement_id)
        recipient = db_session.get(Party, rp_obligations["RENT"].recipient_party_id)
        _confirm_via_new_rail(db_session, rp_obligations["RENT"], guest, recipient)

        legacy = _legacy_obligations_for(db_session, agreement_id)
        statuses = {o.obligation_type: o.status for o in legacy}
        assert statuses["RENT"] == "PAID"
        assert statuses["DEPOSIT"] != "PAID"

        agreement = db_session.get(Agreement, agreement_id)
        db_session.refresh(agreement)
        assert agreement.status != "SIGNED"

    def test_bridge_is_idempotent(self, client, db_session: Session):
        user, _user_cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix="bridge3")
        guest = get_guest_for_user(db_session, user)

        rp_obligations = _rental_payment_obligations_for(db_session, agreement_id)
        recipient = db_session.get(Party, rp_obligations["DEPOSIT"].recipient_party_id)
        _confirm_via_new_rail(db_session, rp_obligations["DEPOSIT"], guest, recipient)

        deposit_obligation = next(o for o in _legacy_obligations_for(db_session, agreement_id) if o.obligation_type == "DEPOSIT")
        assert deposit_obligation.status == "PAID"
        deposit_record_id = deposit_obligation.deposit_record.id

        # Recomputing again (e.g. a retried webhook/self-heal read) must not
        # raise or create a second DepositRecord for the same obligation.
        rp_crud.recompute_obligation_status(db_session, rp_obligations["DEPOSIT"])
        db_session.commit()
        db_session.refresh(deposit_obligation)
        assert deposit_obligation.status == "PAID"
        assert deposit_obligation.deposit_record.id == deposit_record_id

    def test_a_checkout_expiry_reset_to_sent_still_reaches_signed_once_paid(self, client, db_session: Session):
        """The exact bug found live in production data: services/
        booking_expiry.py's sweep resets an unpaid PAYMENT_IN_PROGRESS
        agreement back to SENT after its 30-minute custodial-checkout
        deadline -- a timeout that doesn't fit the non-custodial rail,
        where payment can legitimately clear well after 30 minutes.
        Without crud/leasing.py:confirm_agreement_payment's own SENT+both-
        signed carve-out, the bridge would silently no-op forever here,
        leaving a fully-paid agreement stuck at SENT and no Occupancy ever
        created -- which is exactly what left a renter's own paid-for room
        not showing on their own Rentals page."""
        user, _user_cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix="bridge5")
        guest = get_guest_for_user(db_session, user)

        agreement = db_session.get(Agreement, agreement_id)
        assert agreement.status == "PAYMENT_IN_PROGRESS"
        # Simulate the expiry sweep firing before payment ever completes.
        agreement.status = "SENT"
        agreement.payment_session_expires_at = None
        db_session.commit()

        rp_obligations = _rental_payment_obligations_for(db_session, agreement_id)
        recipient = db_session.get(Party, rp_obligations["RENT"].recipient_party_id)
        _confirm_via_new_rail(db_session, rp_obligations["RENT"], guest, recipient)
        _confirm_via_new_rail(db_session, rp_obligations["DEPOSIT"], guest, recipient)

        db_session.refresh(agreement)
        assert agreement.status == "SIGNED"
        occupancy = db_session.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id))
        assert occupancy is not None

    def test_a_sent_agreement_that_never_signed_both_parties_is_not_touched(self, db_session: Session, client):
        """The SENT carve-out must never fire for an ordinary not-yet-fully-
        signed agreement just because obligations happen to exist --
        requiring both signatures already present is the guard."""
        from app.crud.leasing import confirm_agreement_payment

        user, _user_cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix="bridge6")
        agreement = db_session.get(Agreement, agreement_id)
        agreement.status = "SENT"
        agreement.signed_by_renter_at = None
        db_session.commit()

        confirm_agreement_payment(db_session, agreement)
        db_session.refresh(agreement)
        assert agreement.status == "SENT"


class TestBridgeNeverTouchesUnrelatedObligations:
    def test_a_deposit_topup_obligation_is_never_touched(self, client, db_session: Session):
        """A deposit-increase amendment (crud/leasing.py:
        _generate_deposit_topup_obligation) adds a SECOND legacy DEPOSIT
        Obligation to the same agreement with no RentalPaymentObligation
        counterpart -- simulated directly here rather than driving the full
        amendment-approval flow, since only the bridge's own matching
        behavior is under test."""
        user, _user_cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix="bridge4")
        guest = get_guest_for_user(db_session, user)
        agreement = db_session.get(Agreement, agreement_id)

        topup = Obligation(
            obligation_type="DEPOSIT", money_plane=OBLIGATION_TYPE_TO_PLANE["DEPOSIT"], amount=500,
            currency=agreement.offer.listing.currency, due_date=date.today(), agreement_id=agreement_id,
        )
        db_session.add(topup)
        db_session.commit()

        rp_obligations = _rental_payment_obligations_for(db_session, agreement_id)
        recipient = db_session.get(Party, rp_obligations["DEPOSIT"].recipient_party_id)
        _confirm_via_new_rail(db_session, rp_obligations["DEPOSIT"], guest, recipient)

        legacy = _legacy_obligations_for(db_session, agreement_id)
        original_deposit = next(o for o in legacy if o.obligation_type == "DEPOSIT" and o.id != topup.id)
        db_session.refresh(topup)
        assert original_deposit.status == "PAID"
        assert topup.status != "PAID"

    def test_a_recurring_obligation_with_no_matching_legacy_row_is_a_safe_no_op(self, client, db_session: Session):
        user, _user_cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix="bridge5")
        guest = get_guest_for_user(db_session, user)
        agreement = db_session.get(Agreement, agreement_id)

        rp_obligations = _rental_payment_obligations_for(db_session, agreement_id)
        recipient = db_session.get(Party, rp_obligations["RENT"].recipient_party_id)
        _confirm_via_new_rail(db_session, rp_obligations["RENT"], guest, recipient)
        _confirm_via_new_rail(db_session, rp_obligations["DEPOSIT"], guest, recipient)
        db_session.refresh(agreement)
        occupancy = db_session.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id))
        assert occupancy is not None

        before = {o.id: o.status for o in _legacy_obligations_for(db_session, agreement_id)}

        recurring = rp_crud.create_obligation(
            db_session, obligation_type="RENT", tenant_guest_id=guest.id,
            recipient_party_id=rp_obligations["RENT"].recipient_party_id,
            amount=float(rp_obligations["RENT"].amount), currency=rp_obligations["RENT"].currency,
            due_date=date.today() + timedelta(days=30), occupancy_id=occupancy.id,
        )
        assert recurring.agreement_id is None
        _confirm_via_new_rail(db_session, recurring, guest, recipient)

        after = {o.id: o.status for o in _legacy_obligations_for(db_session, agreement_id)}
        assert before == after

    def test_a_recurring_obligation_with_a_matching_legacy_row_generates_the_next_period(
        self, client, db_session: Session,
    ):
        """occupancy.status must be ACTIVE for
        crud/occupancy.py:generate_next_rent_obligation to run at all --
        set directly here (the handover/activation-gate path to a real
        ACTIVE occupancy is already covered by
        tests/test_occupancy_workflow.py; this test's own concern is only
        the bridge's behavior once that precondition holds)."""
        user, _user_cookies, agreement_id, start = _create_signed_agreement(client, db_session, email_suffix="bridge6")
        guest = get_guest_for_user(db_session, user)
        agreement = db_session.get(Agreement, agreement_id)

        rp_obligations = _rental_payment_obligations_for(db_session, agreement_id)
        recipient = db_session.get(Party, rp_obligations["RENT"].recipient_party_id)
        _confirm_via_new_rail(db_session, rp_obligations["RENT"], guest, recipient)
        _confirm_via_new_rail(db_session, rp_obligations["DEPOSIT"], guest, recipient)
        db_session.refresh(agreement)
        occupancy = db_session.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id))
        occupancy.status = "ACTIVE"
        occupancy.expected_end_date = start + timedelta(days=365)
        db_session.commit()

        legacy_recurring = Obligation(
            obligation_type="RENT", money_plane=OBLIGATION_TYPE_TO_PLANE["RENT"],
            amount=float(rp_obligations["RENT"].amount), currency=rp_obligations["RENT"].currency,
            due_date=start + timedelta(days=30), occupancy_id=occupancy.id,
        )
        db_session.add(legacy_recurring)
        db_session.commit()
        recurring = rp_crud.create_obligation(
            db_session, obligation_type="RENT", tenant_guest_id=guest.id,
            recipient_party_id=rp_obligations["RENT"].recipient_party_id,
            amount=float(rp_obligations["RENT"].amount), currency=rp_obligations["RENT"].currency,
            due_date=legacy_recurring.due_date, occupancy_id=occupancy.id,
        )

        _confirm_via_new_rail(db_session, recurring, guest, recipient)

        db_session.refresh(legacy_recurring)
        assert legacy_recurring.status == "PAID"

        legacy_next_period = db_session.scalar(
            select(Obligation).where(
                Obligation.occupancy_id == occupancy.id, Obligation.obligation_type == "RENT",
                Obligation.id != legacy_recurring.id,
            )
        )
        assert legacy_next_period is not None
        assert legacy_next_period.due_date > legacy_recurring.due_date

        new_next_period = db_session.scalar(
            select(RentalPaymentObligation).where(
                RentalPaymentObligation.occupancy_id == occupancy.id, RentalPaymentObligation.obligation_type == "RENT",
                RentalPaymentObligation.id != recurring.id,
            )
        )
        assert new_next_period is not None
        assert new_next_period.due_date == legacy_next_period.due_date
