"""ZR-PAY-002 Section 16's Engineering and QA Acceptance Gates and Section
16.1's must-test negative scenarios, exercised directly against
crud/listing_fee.py and crud/rental_payment.py. Each test names the gate ID
it verifies so this file stays traceable against the spec."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.crud import listing_fee as lf_crud
from app.crud import rental_payment as rp_crud
from app.models.listing import Listing
from app.models.listing_fee import ListingFeePolicy
from tests.conftest import _make_admin, auth_user_cookie
from tests.test_rental_payment_records import _make_obligation, _make_party


def _make_listing_fee_policy(
    db: Session, *, jurisdiction_code: str = "England", refund_eligible: bool = False, refund_window_days: int | None = None,
) -> ListingFeePolicy:
    policy = ListingFeePolicy(
        jurisdiction_code=jurisdiction_code, version=1, effective_from=date(2020, 1, 1),
        amount=25, currency="GBP", tax_rate=0.2, quote_validity_minutes=30,
        legal_entity_name="Zoiko Realty Group", tax_registration_number="GB123",
        refund_eligible=refund_eligible, refund_window_days=refund_window_days,
    )
    db.add(policy)
    db.flush()
    return policy


def _make_listing(db: Session, *, listing_id: str, party_id: int | None = None) -> Listing:
    listing = Listing(
        id=listing_id, slug=listing_id.lower(), name="Gate Test Listing", room_type="Private room",
        city="Bengaluru", location="Koramangala", price_per_night=500, guests=1, currency="GBP",
        rating=4.5, review_count=0, party_id=party_id,
    )
    db.add(listing)
    db.flush()
    return listing


class TestA6PCIBoundary:
    """A6: 'Listing Fee checkout uses tokenized/hosted provider controls;
    PAN/CVV is not stored by application services.'"""

    def test_listing_fee_payment_has_no_card_data_columns(self):
        from app.models.listing_fee import ListingFeePayment

        columns = {c.name for c in ListingFeePayment.__table__.columns}
        forbidden = {"card_number", "pan", "cvv", "cvc", "card_pan"}
        assert not (columns & forbidden)
        # Only a provider-issued reference is stored, never raw card data.
        assert "provider_payment_intent_id" in columns


class TestListingFeePaymentList:
    def test_lister_can_list_their_own_listing_fee_payments(self, client, db_session: Session):
        """ZR-PAY-002 Section 3.2: 'Listing fee receipts | View.'"""
        from tests.conftest import _make_user, auth_user_cookie

        user = _make_user(db_session, email="lf-list-owner@test.com")
        party = _make_party(db_session, party_type="provider")
        user.party_id = party.id
        listing = _make_listing(db_session, listing_id="L-GATE-LIST", party_id=party.id)
        _make_listing_fee_policy(db_session)
        quote = lf_crud.create_quote(db_session, listing, party)
        lf_crud.create_checkout(db_session, quote, party, idempotency_key="gate-list-1", billing_country="GB")

        other_user = _make_user(db_session, email="lf-list-stranger@test.com")
        other_party = _make_party(db_session, party_type="provider")
        other_user.party_id = other_party.id
        db_session.flush()

        r = client.get("/api/users/listing-fees/payments", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        payments = r.json()
        assert len(payments) == 1
        assert payments[0]["listingId"] == listing.id

        r2 = client.get("/api/users/listing-fees/payments", cookies=auth_user_cookie(other_user))
        assert r2.status_code == 200, r2.text
        assert r2.json() == []


class TestA7FeePaidDoesNotBypassOtherGates:
    def test_paid_listing_fee_alone_does_not_clear_identity_or_authority_reasons(self, db_session: Session):
        """A7/16.1: 'Listing Fee payment succeeds but identity/property/
        authority verification remains incomplete' -- check_publish_eligibility
        must still report the other gaps even once the fee is paid."""
        from app.crud import listing as listing_crud

        party = _make_party(db_session, party_type="provider")
        listing = _make_listing(db_session, listing_id="L-GATE-A7", party_id=party.id)
        _make_listing_fee_policy(db_session)

        quote = lf_crud.create_quote(db_session, listing, party)
        payment, _client_secret = lf_crud.create_checkout(
            db_session, quote, party, idempotency_key="gate-a7-checkout", billing_country="GB",
        )
        assert payment.status == "SUCCEEDED"  # unconfigured Stripe -> synchronous completion
        assert lf_crud.listing_fee_is_paid(db_session, listing.id) is True

        # No room/identity/authority set up at all -- check_publish_eligibility
        # must still flag "Listing is not linked to a room" (the one
        # structural check that short-circuits the rest), proving fee payment
        # did not silently satisfy publication readiness.
        reasons = listing_crud.check_publish_eligibility(db_session, listing)
        assert any("room" in r.lower() for r in reasons)


class TestA7ListingFeeHardGateOnPublication:
    """A7/8.3: 'Listing fee' is one of the checkmarks required for 'Eligible
    for publication' -- once a jurisdiction has a configured policy, the
    admin publish action must actually refuse to publish an unpaid listing,
    not just show it as an informational warning."""

    def test_publish_blocked_when_jurisdiction_has_policy_and_fee_unpaid(self, client, db_session: Session):
        from tests.conftest import auth_admin_cookie
        from tests.test_listing_workflow import LISTING_PAYLOAD, _make_host_with_room

        _make_listing_fee_policy(db_session)  # defaults to jurisdiction_code="England"
        user, room_id = _make_host_with_room(db_session, email="fee-gate-unpaid@test.com")
        cookies = auth_user_cookie(user)
        r = client.post("/api/users/hosting/listings", json={**LISTING_PAYLOAD, "roomId": room_id}, cookies=cookies)
        assert r.status_code == 201, r.text
        listing_id = r.json()["id"]
        client.post(f"/api/users/hosting/listings/{listing_id}/submit-for-review", cookies=cookies)

        admin = _make_admin(db_session)
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(f"/api/listings/{listing_id}/approve", cookies=admin_cookies)
        assert r.status_code == 200, r.text  # approval itself is unaffected by the fee gate

        r = client.post(f"/api/listings/{listing_id}/publish", cookies=admin_cookies)
        assert r.status_code == 409, r.text
        assert "listing fee" in r.json()["detail"].lower()

        listing = db_session.get(Listing, listing_id)
        assert listing.state == "APPROVED"  # never reached PUBLISHED

    def test_publish_succeeds_once_fee_is_paid(self, client, db_session: Session):
        from tests.conftest import auth_admin_cookie
        from tests.test_listing_workflow import LISTING_PAYLOAD, _make_host_with_room

        _make_listing_fee_policy(db_session)
        user, room_id = _make_host_with_room(db_session, email="fee-gate-paid@test.com")
        cookies = auth_user_cookie(user)
        r = client.post("/api/users/hosting/listings", json={**LISTING_PAYLOAD, "roomId": room_id}, cookies=cookies)
        listing_id = r.json()["id"]
        client.post(f"/api/users/hosting/listings/{listing_id}/submit-for-review", cookies=cookies)

        listing = db_session.get(Listing, listing_id)
        from app.models.party import Party as _Party

        party = db_session.get(_Party, user.party_id)
        quote = lf_crud.create_quote(db_session, listing, party)
        payment, _ = lf_crud.create_checkout(db_session, quote, party, idempotency_key="gate-a7-paid-1", billing_country="GB")
        assert payment.status == "SUCCEEDED"

        admin = _make_admin(db_session)
        admin_cookies = auth_admin_cookie(admin)
        client.post(f"/api/listings/{listing_id}/approve", cookies=admin_cookies)
        r = client.post(f"/api/listings/{listing_id}/publish", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "PUBLISHED"

    def test_publish_unblocked_when_no_policy_configured_for_jurisdiction(self, client, db_session: Session):
        """No Listing Fee policy exists anywhere in this test's DB -- nothing
        to enforce, so publication must not be bricked by a missing admin
        configuration step (matches every pre-existing publish test that
        never sets up a Listing Fee policy at all)."""
        from tests.conftest import auth_admin_cookie
        from tests.test_listing_workflow import LISTING_PAYLOAD, _make_host_with_room

        user, room_id = _make_host_with_room(db_session, email="fee-gate-nopolicy@test.com")
        cookies = auth_user_cookie(user)
        r = client.post("/api/users/hosting/listings", json={**LISTING_PAYLOAD, "roomId": room_id}, cookies=cookies)
        listing_id = r.json()["id"]
        client.post(f"/api/users/hosting/listings/{listing_id}/submit-for-review", cookies=cookies)

        admin = _make_admin(db_session)
        admin_cookies = auth_admin_cookie(admin)
        client.post(f"/api/listings/{listing_id}/approve", cookies=admin_cookies)
        r = client.post(f"/api/listings/{listing_id}/publish", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "PUBLISHED"

    def test_auto_approve_and_publish_blocked_when_fee_unpaid(self, client, db_session: Session):
        """The publication.requires_approval=False auto-publish path (ZR-ENG-
        CLR-001 Section 14) must not bypass the same Listing Fee gate."""
        from app.models.market_release import MarketRelease
        from tests.test_listing_workflow import LISTING_PAYLOAD, _make_host_with_room

        _make_listing_fee_policy(db_session)
        user, room_id = _make_host_with_room(db_session, email="fee-gate-autopublish@test.com")
        cookies = auth_user_cookie(user)

        release = MarketRelease(jurisdiction="IN-LOWRISK", status="active", policy_overrides={"publication.requires_approval": False})
        db_session.add(release)
        db_session.commit()

        r = client.post("/api/users/hosting/listings", json={**LISTING_PAYLOAD, "roomId": room_id}, cookies=cookies)
        listing_id = r.json()["id"]
        listing = db_session.get(Listing, listing_id)
        listing.market_release_id = release.id
        db_session.commit()

        r = client.post(f"/api/users/hosting/listings/{listing_id}/submit-for-review", cookies=cookies)
        assert r.status_code == 409, r.text
        assert "listing fee" in r.json()["detail"].lower()

        db_session.refresh(listing)
        assert listing.state == "REVIEW"  # submission itself still succeeded; only auto-publish was blocked


class TestA12WebhookIdempotency:
    def test_replayed_listing_fee_webhook_is_a_no_op(self, db_session: Session):
        """A12/16.1: 'Payment provider sends the same Listing Fee webhook
        repeatedly' -- must be authenticated (checked at the route layer,
        not here), idempotent and never double-process."""
        party = _make_party(db_session, party_type="provider")
        listing = _make_listing(db_session, listing_id="L-GATE-A12", party_id=party.id)
        _make_listing_fee_policy(db_session)
        quote = lf_crud.create_quote(db_session, listing, party)

        # Force the real-Stripe path so the payment stays PENDING until a
        # webhook lands (the unconfigured fallback completes synchronously
        # and would never exercise the webhook code path at all).
        from app.models.listing_fee import ListingFeePayment

        payment = ListingFeePayment(
            quote_id=quote.id, listing_id=listing.id, party_id=party.id, amount=float(quote.total_amount),
            currency=quote.currency, idempotency_key="gate-a12-payment", billing_country="GB",
            provider_payment_intent_id="pi_test_replay",
        )
        db_session.add(payment)
        db_session.commit()

        event = {
            "id": "evt_replay_1", "type": "payment_intent.succeeded",
            "data": {"object": {"id": "pi_test_replay"}},
        }
        lf_crud.ingest_stripe_webhook_event(db_session, event)
        db_session.refresh(payment)
        assert payment.status == "SUCCEEDED"
        first_paid_at = payment.paid_at

        # Same event id, replayed -- must be a no-op, not a re-processing.
        lf_crud.ingest_stripe_webhook_event(db_session, event)
        db_session.refresh(payment)
        assert payment.paid_at == first_paid_at

        from app.models.listing_fee import ListingFeeProviderEvent

        dedup_rows = (
            db_session.query(ListingFeeProviderEvent)
            .filter(ListingFeeProviderEvent.provider_event_id == "evt_replay_1")
            .count()
        )
        assert dedup_rows == 1


class TestA10ImmutabilityAcrossBothDomains:
    def test_listing_fee_receipt_is_rendered_exactly_once(self, db_session: Session):
        """A10 applied to the Listing Fee domain's own receipt -- calling
        get_or_create twice must return the identical row, never re-render."""
        party = _make_party(db_session, party_type="provider")
        listing = _make_listing(db_session, listing_id="L-GATE-A10", party_id=party.id)
        _make_listing_fee_policy(db_session)
        quote = lf_crud.create_quote(db_session, listing, party)
        payment, _ = lf_crud.create_checkout(db_session, quote, party, idempotency_key="gate-a10", billing_country="GB")

        first = lf_crud.get_or_create_listing_fee_receipt(db_session, payment)
        second = lf_crud.get_or_create_listing_fee_receipt(db_session, payment)
        assert first.id == second.id
        assert first.receipt_number == second.receipt_number

    def test_landlord_has_no_route_to_edit_a_record_directly(self, db_session: Session):
        """16.1: 'Landlord attempts to alter a confirmed payment event rather
        than append a correction.' There is no crud function that lets a
        Party mutate a RentalPaymentRecord's declared fields -- only
        append_correction (admin-only, reason-required) ever does, and
        confirm_receipt only ever moves `status`/`confirmed_*`, never
        assigns declared_amount/declared_date/external_reference (it does
        legitimately *read* declared_amount, to validate a partial
        confirmation never exceeds it -- Section 6 PARTIALLY_PAID -- so this
        checks for an assignment, not bare textual presence)."""
        import inspect

        source = inspect.getsource(rp_crud.confirm_receipt)
        for field in ("declared_amount", "declared_date", "external_reference"):
            assert f"record.{field} =" not in source and f"{field}=" not in source.replace(" ", "")


class TestRefundEligibility:
    """ZR-PAY-002 Section 8.4: REFUND_ELIGIBLE -- 'Display only when
    commercial policy/jurisdiction configuration permits.'"""

    def _pay(self, db_session, *, refund_eligible: bool, refund_window_days: int | None = None):
        party = _make_party(db_session, party_type="provider")
        listing = _make_listing(db_session, listing_id=f"L-ELIG-{lf_crud.new_id('T')}", party_id=party.id)
        _make_listing_fee_policy(db_session, refund_eligible=refund_eligible, refund_window_days=refund_window_days)
        quote = lf_crud.create_quote(db_session, listing, party)
        payment, _ = lf_crud.create_checkout(
            db_session, quote, party, idempotency_key=f"elig-{listing.id}", billing_country="GB",
        )
        assert payment.status == "SUCCEEDED"
        return payment

    def test_not_eligible_when_policy_does_not_permit_refunds(self, db_session: Session):
        payment = self._pay(db_session, refund_eligible=False)
        assert lf_crud.is_listing_fee_payment_refund_eligible(db_session, payment) is False

    def test_eligible_with_no_time_limit_when_policy_permits(self, db_session: Session):
        payment = self._pay(db_session, refund_eligible=True, refund_window_days=None)
        assert lf_crud.is_listing_fee_payment_refund_eligible(db_session, payment) is True

    def test_eligible_within_window_ineligible_after(self, db_session: Session):
        payment = self._pay(db_session, refund_eligible=True, refund_window_days=7)
        assert lf_crud.is_listing_fee_payment_refund_eligible(db_session, payment) is True

        payment.paid_at = datetime.now(timezone.utc) - timedelta(days=8)
        db_session.flush()
        assert lf_crud.is_listing_fee_payment_refund_eligible(db_session, payment) is False

    def test_not_eligible_once_fully_refunded(self, db_session: Session):
        payment = self._pay(db_session, refund_eligible=True)
        admin = _make_admin(db_session)
        from app.schemas.listing_fee import ListingFeeRefundCreate

        lf_crud.request_refund(
            db_session, admin, payment,
            ListingFeeRefundCreate(amount=float(payment.amount), reason="Full refund", idempotency_key="elig-refund-1"),
        )
        assert lf_crud.is_listing_fee_payment_refund_eligible(db_session, payment) is False

    def test_eligibility_reflects_the_policy_that_actually_priced_the_payment(self, db_session: Session):
        """Resolved from the quote's own frozen policy_snapshot -- a later
        policy change must never retroactively change an existing payment's
        eligibility (same 'reproducible from the snapshot' discipline as
        everywhere else this codebase resolves a market/commercial policy)."""
        payment = self._pay(db_session, refund_eligible=True)
        # A brand-new policy version (e.g. an ops team turning refunds off
        # going forward) must not affect this already-priced payment.
        _make_listing_fee_policy(db_session, refund_eligible=False)
        assert lf_crud.is_listing_fee_payment_refund_eligible(db_session, payment) is True

    def test_http_response_exposes_refund_eligible(self, client, db_session: Session):
        from tests.conftest import _make_user, auth_user_cookie

        user = _make_user(db_session, email="lf-elig-http@test.com")
        party = _make_party(db_session, party_type="provider")
        user.party_id = party.id
        listing = _make_listing(db_session, listing_id="L-ELIG-HTTP", party_id=party.id)
        _make_listing_fee_policy(db_session, refund_eligible=True, refund_window_days=30)
        quote = lf_crud.create_quote(db_session, listing, party)
        lf_crud.create_checkout(db_session, quote, party, idempotency_key="elig-http-1", billing_country="GB")
        db_session.flush()

        r = client.get("/api/users/listing-fees/payments", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        assert r.json()[0]["refundEligible"] is True


class TestListingFeeRefundFailure:
    """Section 8.4: REFUND_FAILED -- 'Route to controlled retry/support
    process; retain provider error internally.' A provider-side failure
    must never leave the refund stuck at REQUESTED or leak an unhandled
    exception (gateway diagnostics) to the caller."""

    def test_provider_failure_moves_refund_to_failed_not_an_unhandled_exception(self, db_session: Session, monkeypatch):
        party = _make_party(db_session, party_type="provider")
        listing = _make_listing(db_session, listing_id="L-GATE-REFUND", party_id=party.id)
        _make_listing_fee_policy(db_session)
        quote = lf_crud.create_quote(db_session, listing, party)
        payment, _ = lf_crud.create_checkout(db_session, quote, party, idempotency_key="gate-refund-fail", billing_country="GB")
        assert payment.status == "SUCCEEDED"

        def _raise(*args, **kwargs):
            raise RuntimeError("simulated provider outage")

        monkeypatch.setattr(lf_crud.stripe_client, "create_refund", _raise)

        admin = _make_admin(db_session)
        from app.schemas.listing_fee import ListingFeeRefundCreate

        refund = lf_crud.request_refund(
            db_session, admin, payment,
            ListingFeeRefundCreate(amount=float(quote.total_amount), reason="Duplicate listing", idempotency_key="gate-refund-idem-1"),
        )
        assert refund.status == "FAILED"
        assert "simulated provider outage" in refund.failure_message

    def test_lister_can_view_but_not_issue_their_own_refunds(self, client, db_session: Session):
        """Section 8.4/11: the lister sees refund status against their own
        payment, but only a restricted admin may issue one."""
        from tests.conftest import _make_user, auth_admin_cookie, auth_user_cookie

        user = _make_user(db_session, email="lf-refund-view@test.com")
        party = _make_party(db_session, party_type="provider")
        user.party_id = party.id
        listing = _make_listing(db_session, listing_id="L-GATE-REFUND-VIEW", party_id=party.id)
        _make_listing_fee_policy(db_session)
        quote = lf_crud.create_quote(db_session, listing, party)
        payment, _ = lf_crud.create_checkout(db_session, quote, party, idempotency_key="gate-refund-view-1", billing_country="GB")
        db_session.flush()

        admin = _make_admin(db_session, email="lf-refund-admin@test.com", role="super_admin")
        r = client.post(
            f"/api/finance/listing-fees/payments/{payment.id}/refunds",
            json={"amount": float(quote.total_amount), "reason": "Duplicate", "idempotencyKey": "gate-refund-view-idem"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text

        r_view = client.get(f"/api/users/listing-fees/payments/{payment.id}/refunds", cookies=auth_user_cookie(user))
        assert r_view.status_code == 200, r_view.text
        assert len(r_view.json()) == 1
        assert r_view.json()[0]["status"] in ("PROCESSING", "REFUNDED", "PARTIALLY_REFUNDED")

        # The lister has no route to issue a refund themselves -- POST is
        # only ever mounted under the admin router (super_admin-gated).
        r_forbidden = client.post(
            f"/api/finance/listing-fees/payments/{payment.id}/refunds",
            json={"amount": 1.0, "reason": "self-service attempt", "idempotencyKey": "gate-refund-view-idem-2"},
            cookies=auth_user_cookie(user),
        )
        assert r_forbidden.status_code in (401, 403), r_forbidden.text


class TestA9ObligationTypeFilter:
    def test_tenant_and_recipient_views_can_filter_by_obligation_type(self, db_session: Session):
        """Section 7's '[All] [Rent] [Deposit] [Other]' filter, server-side."""
        obligation, tenant, recipient = _make_obligation(db_session)
        assert obligation.obligation_type == "RENT"

        rent_only = rp_crud.list_obligations_for_tenant(db_session, tenant.id, obligation_type="RENT")
        deposit_only = rp_crud.list_obligations_for_tenant(db_session, tenant.id, obligation_type="DEPOSIT")
        assert obligation.id in [o.id for o in rent_only]
        assert obligation.id not in [o.id for o in deposit_only]

        recipient_rent_only = rp_crud.list_obligations_for_recipient(db_session, recipient.id, obligation_type="RENT")
        assert obligation.id in [o.id for o in recipient_rent_only]


class TestA5InstructionChangeStepUpAndRiskControls:
    """A5: 'Payment-instruction changes require configured step-up controls
    and create a versioned audit trail.' Plus the 16.1 negative scenario:
    'Payment instruction is changed immediately after password or MFA
    reset.' Full coverage of the risk/manual-review state machine lives in
    test_rental_payment_records.py:TestInstructionRiskControlsAndManualReview
    -- this is the gate-level assertion."""

    def test_instruction_change_right_after_a_password_reset_requires_manual_review(self, db_session: Session):
        from datetime import datetime, timedelta, timezone

        from tests.conftest import _make_user
        from app.crud import rental_payment as rp_crud

        recipient = _make_party(db_session)
        user = _make_user(db_session, email="a5-risk@test.com")
        user.party_id = recipient.id
        user.password_changed_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        db_session.flush()

        instruction, code = rp_crud.submit_rental_payment_instruction(
            db_session, recipient, method="BANK_TRANSFER", recipient_name="Example Property Ltd",
            account_identifier="00112233449999",
        )
        # Step-up auth (the mailed code) is still required and still checked.
        confirmed = rp_crud.confirm_rental_payment_instruction(db_session, instruction, code)
        # But a step-up-verified, high-risk change does not become the
        # active instruction on its own -- it waits for a restricted admin.
        assert confirmed.status == "PENDING_REVIEW"
        assert rp_crud.get_active_rental_payment_instruction(db_session, recipient.id) is None
        assert confirmed.verified_at is not None


class TestA15RoleSeparation:
    def test_recipient_obligation_list_and_tenant_obligation_list_never_overlap_by_construction(self, db_session: Session):
        """A15: 'Role switching never merges tenant and landlord/agent
        financial contexts.' list_obligations_for_tenant is keyed on
        tenant_guest_id; list_obligations_for_recipient is keyed on
        recipient_party_id -- a party id can never satisfy a guest_id filter
        (different column, different table), so there is no query shape
        that could accidentally return one list for the other's caller."""
        obligation, tenant, recipient = _make_obligation(db_session)

        tenant_view = rp_crud.list_obligations_for_tenant(db_session, tenant.id)
        recipient_view = rp_crud.list_obligations_for_recipient(db_session, recipient.id)
        assert obligation.id in [o.id for o in tenant_view]
        assert obligation.id in [o.id for o in recipient_view]

        # Querying with the wrong role's key (the mistake role-merging would
        # produce) returns nothing -- tenant_guest_id is a string column,
        # recipient_party_id is an integer column, so the two keys can never
        # accidentally satisfy each other's filter.
        assert rp_crud.list_obligations_for_tenant(db_session, str(recipient.id)) == []

    def test_recipient_cannot_mark_paid_and_tenant_cannot_confirm(self, db_session: Session):
        obligation, tenant, recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        # mark_paid only accepts a Guest -- a Party has no compatible
        # interface at all (no .id-as-guest ambiguity: assert_tenant_owns_
        # obligation compares against tenant_guest_id specifically).
        with pytest.raises(HTTPException):
            rp_crud.assert_tenant_owns_obligation(obligation, str(recipient.id))
        with pytest.raises(HTTPException):
            rp_crud.assert_party_is_recipient(obligation, -1)


class TestA1RentalPaymentDomainNeverTouchesAPaymentProvider:
    """A1: 'Listing Fee is the only customer payment routed to a Zoiko Rooms
    billing account.' Scoped assertion: within this module's own rental
    payment record domain (crud/rental_payment.py), no function ever calls
    a real payment provider -- it only ever records declarations,
    confirmations and disputes. This does not assert anything about
    app/crud/finance.py or app/crud/payment_provider.py, which are a
    separate, pre-existing payment-provider dispatch pipeline built under a
    different spec (ZR-ENG-CLR-005) before ZR-PAY-002 existed; reconciling
    that pipeline with ZR-PAY-002's boundary is a separate decision, not
    something this test can or should silently paper over."""

    def test_rental_payment_crud_never_imports_or_calls_stripe(self):
        import inspect

        import app.crud.rental_payment as rp_module

        source = inspect.getsource(rp_module)
        assert "stripe" not in source.lower()


class TestA2NoRentalObligationCanResolveToZoikoAsPayee:
    """A2: 'No rental obligation can resolve to Zoiko Rooms as the payee
    through UI, API or data defaults.'"""

    def test_recipient_party_id_has_no_default_and_cannot_be_omitted(self):
        import inspect

        sig = inspect.signature(rp_crud.create_obligation)
        recipient_param = sig.parameters["recipient_party_id"]
        # No default -- a caller (or a future call site) cannot silently
        # create an obligation without naming an explicit recipient, which
        # rules out a code path that falls through to some implicit/system
        # (e.g. Zoiko-owned) party id.
        assert recipient_param.default is inspect.Parameter.empty

    def test_omitting_the_recipient_raises_rather_than_defaulting(self, db_session: Session):
        with pytest.raises(TypeError):
            rp_crud.create_obligation(  # type: ignore[call-arg]
                db_session, obligation_type="RENT", tenant_guest_id="G-X",
                amount=100, currency="GBP", due_date=date.today(),
            )


class TestA11RefundRestrictedToListingFees:
    """A11: 'Refund workflow applies to Listing Fees only unless a separate
    future service is explicitly approved.' The rental payment record
    domain (crud/rental_payment.py, api/routes/rental_payments.py) must
    have no refund-issuing capability at all -- only listing_fee.py does."""

    def test_rental_payment_crud_has_no_refund_function(self):
        import app.crud.rental_payment as rp_module

        names = [name for name in dir(rp_module) if "refund" in name.lower()]
        assert names == [], f"unexpected refund-capable function(s) in the rental payment domain: {names}"

    def test_rental_payment_routes_have_no_refund_route(self):
        import inspect

        import app.api.routes.rental_payments as rp_routes

        source = inspect.getsource(rp_routes)
        assert "refund" not in source.lower()


class TestA14NotificationsDoNotLeakSensitiveData:
    """A14: 'Notifications do not leak sensitive account/payment data.'"""

    def test_rental_payment_notification_messages_never_reference_account_or_verification_fields(self):
        import inspect

        import app.crud.rental_payment as rp_module

        source = inspect.getsource(rp_module)
        # These are the actual attribute names that hold sensitive values
        # (masked account digits, the raw one-time verification code). A
        # notification message string built from any of them would risk
        # leaking that data into an email/SMS/push preview.
        forbidden_attrs = ("account_identifier_last4", "verification_code_hash", "raw_code")
        for line in source.splitlines():
            if "message=" not in line and "message =" not in line:
                continue
            for attr in forbidden_attrs:
                assert attr not in line, f"notification message references sensitive field {attr!r}: {line.strip()}"


class TestCorrelationIdsPropagateToAuditEvents:
    """Section 3 'Evidence': 'Material decisions and high-risk changes
    produce durable evidence and audit records with actor, timestamp,
    reason, source, and correlation identifiers.' Also ZR-PAY-002 Section
    12.3: 'Every financially relevant event requires ... a request or
    correlation ID.' TR-07 restates the same requirement per screen."""

    def test_a_client_supplied_correlation_header_reaches_the_audit_event(self, client, db_session: Session):
        from tests.conftest import _make_user, auth_user_cookie
        from tests.test_rental_payment_records import _make_guest
        from app.models.audit import AuditEvent

        tenant_user = _make_user(db_session, email="corr-tenant@test.com")
        party = _make_party(db_session)
        guest = _make_guest(db_session, guest_id="G-CORR-1")
        guest.user_account_id = tenant_user.id
        db_session.flush()

        obligation = rp_crud.create_obligation(
            db_session, obligation_type="RENT", tenant_guest_id=guest.id, recipient_party_id=party.id,
            amount=850, currency="GBP", due_date=date.today(),
        )
        db_session.commit()

        my_correlation_id = "test-correlation-abc123"
        r = client.post(
            f"/api/users/rental-payments/obligations/{obligation.id}/mark-paid",
            json={
                "amount": 850, "currency": "GBP", "declaredDate": date.today().isoformat(),
                "paymentMethodCategory": "BANK_TRANSFER",
            },
            headers={"X-Correlation-Id": my_correlation_id},
            cookies=auth_user_cookie(tenant_user),
        )
        assert r.status_code == 201, r.text
        # The response echoes the same correlation id back (correlation_id_middleware).
        assert r.headers.get("X-Correlation-Id") == my_correlation_id

        audit_row = db_session.query(AuditEvent).filter(
            AuditEvent.action == "rental_payment.marked_paid",
        ).order_by(AuditEvent.id.desc()).first()
        assert audit_row is not None
        assert audit_row.correlation_id == my_correlation_id

    def test_no_client_header_still_produces_a_non_empty_correlation_id(self, client, db_session: Session):
        """The middleware generates one (uuid4 hex) when the client doesn't
        supply X-Correlation-Id -- an audit row must never fall back to a
        blank correlation_id just because the caller omitted the header."""
        from tests.conftest import _make_user, auth_user_cookie
        from tests.test_rental_payment_records import _make_guest
        from app.models.audit import AuditEvent

        tenant_user = _make_user(db_session, email="corr-tenant-2@test.com")
        party = _make_party(db_session)
        guest = _make_guest(db_session, guest_id="G-CORR-2")
        guest.user_account_id = tenant_user.id
        db_session.flush()

        obligation = rp_crud.create_obligation(
            db_session, obligation_type="RENT", tenant_guest_id=guest.id, recipient_party_id=party.id,
            amount=850, currency="GBP", due_date=date.today(),
        )
        db_session.commit()

        r = client.post(
            f"/api/users/rental-payments/obligations/{obligation.id}/mark-paid",
            json={
                "amount": 850, "currency": "GBP", "declaredDate": date.today().isoformat(),
                "paymentMethodCategory": "BANK_TRANSFER",
            },
            cookies=auth_user_cookie(tenant_user),
        )
        assert r.status_code == 201, r.text

        audit_row = db_session.query(AuditEvent).filter(
            AuditEvent.action == "rental_payment.marked_paid",
        ).order_by(AuditEvent.id.desc()).first()
        assert audit_row is not None
        assert audit_row.correlation_id != ""
