"""ZR-PAY-002 Section 4-7: the rental payment record domain (obligations,
tenant declarations, recipient confirmations, disputes, corrections) is
pure evidence/workflow -- these tests exercise crud/rental_payment.py
directly, the same level test_eligibility_service.py exercises crud/listing.py at."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.crud import rental_payment as rp_crud
from app.models.evidence_artifact import EvidenceArtifact
from app.models.guest import Guest
from app.models.party import Party
from app.services.rental_payment_due_soon import sweep_rental_payment_due_soon
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie


def _make_party(db: Session, *, party_type: str = "provider") -> Party:
    party = Party(party_type=party_type, status="active", jurisdiction="England")
    db.add(party)
    db.flush()
    return party


def _make_guest(db: Session, *, guest_id: str = "G-RP-1") -> Guest:
    guest = Guest(id=guest_id, name="Rental Tenant", email=f"{guest_id}@test.com", joined_at=date.today())
    db.add(guest)
    db.flush()
    return guest


_guest_counter = 0


def _make_obligation(db: Session, *, due_date: date | None = None):
    global _guest_counter
    _guest_counter += 1
    tenant = _make_guest(db, guest_id=f"G-RP-{_guest_counter}")
    recipient = _make_party(db)
    obligation = rp_crud.create_obligation(
        db, obligation_type="RENT", tenant_guest_id=tenant.id, recipient_party_id=recipient.id,
        amount=850, currency="GBP", due_date=due_date or date.today(),
    )
    return obligation, tenant, recipient


class TestMarkPaid:
    def test_tenant_marks_paid_creates_declaration_and_awaits_confirmation(self, db_session: Session):
        obligation, tenant, _recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        assert record.status == "TENANT_MARKED_PAID"
        assert record.provenance == "TENANT_DECLARATION"
        db_session.refresh(obligation)
        assert obligation.status == "AWAITING_CONFIRMATION"

    def test_other_tenant_cannot_mark_paid(self, db_session: Session):
        obligation, _tenant, _recipient = _make_obligation(db_session)
        other_guest = _make_guest(db_session, guest_id="G-RP-OTHER")
        with pytest.raises(HTTPException) as exc:
            rp_crud.mark_paid(
                db_session, other_guest, obligation, amount=850, currency="GBP", declared_date=date.today(),
                payment_method_category="BANK_TRANSFER",
            )
        assert exc.value.status_code == 403

    def test_invalid_payment_method_category_rejected(self, db_session: Session):
        obligation, tenant, _recipient = _make_obligation(db_session)
        with pytest.raises(HTTPException) as exc:
            rp_crud.mark_paid(
                db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
                payment_method_category="CRYPTO",
            )
        assert exc.value.status_code == 400


class TestConfirmReceipt:
    def test_authorized_recipient_confirms_receipt(self, db_session: Session):
        obligation, tenant, recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        confirmed = rp_crud.confirm_receipt(db_session, recipient, record)
        assert confirmed.status == "CONFIRMED_BY_RECIPIENT"
        assert confirmed.provenance == "RECIPIENT_CONFIRMATION"
        db_session.refresh(obligation)
        assert obligation.status == "CONFIRMED_BY_RECIPIENT"

    def test_tenant_cannot_confirm_their_own_receipt(self, db_session: Session):
        """A15/must-test negative scenario: 'Tenant attempts to confirm their
        own receipt.' confirm_receipt only ever accepts a Party (the
        recipient) -- there is no code path for a Guest to call it, so this
        asserts the recipient-only party check rejects an unrelated party
        standing in for 'not the authorized recipient'."""
        obligation, tenant, _recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        unrelated_party = _make_party(db_session)
        with pytest.raises(HTTPException) as exc:
            rp_crud.confirm_receipt(db_session, unrelated_party, record)
        assert exc.value.status_code == 403

    def test_cannot_confirm_an_already_confirmed_record(self, db_session: Session):
        obligation, tenant, recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        rp_crud.confirm_receipt(db_session, recipient, record)
        with pytest.raises(HTTPException) as exc:
            rp_crud.confirm_receipt(db_session, recipient, record)
        assert exc.value.status_code == 409

    def test_confirming_less_than_declared_produces_partially_paid(self, db_session: Session):
        """ZR-PAY-002 Section 6: 'PARTIALLY_PAID -- Confirmed amount is less
        than obligation.'"""
        obligation, tenant, recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        confirmed = rp_crud.confirm_receipt(db_session, recipient, record, amount=600)
        assert confirmed.status == "PARTIALLY_PAID"
        assert confirmed.provenance == "RECIPIENT_CONFIRMATION"
        assert float(confirmed.confirmed_amount) == 600.0
        db_session.refresh(obligation)
        assert obligation.status == "PARTIALLY_PAID"

    def test_confirming_the_full_declared_amount_explicitly_still_fully_confirms(self, db_session: Session):
        obligation, tenant, recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        confirmed = rp_crud.confirm_receipt(db_session, recipient, record, amount=850)
        assert confirmed.status == "CONFIRMED_BY_RECIPIENT"
        assert float(confirmed.confirmed_amount) == 850.0

    def test_confirmed_amount_cannot_exceed_declared_amount(self, db_session: Session):
        obligation, tenant, recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        with pytest.raises(HTTPException) as exc:
            rp_crud.confirm_receipt(db_session, recipient, record, amount=900)
        assert exc.value.status_code == 400


class TestProviderConfirmation:
    """ZR-PAY-002 Section 6: CONFIRMED_BY_PROVIDER -- 'Verified provider
    event,' restricted to an admin recording an authoritative confirmation
    obtained from a verified external source (no live provider integration
    exists to call this automatically -- see
    crud/rental_payment.py:confirm_receipt_as_provider's own docstring)."""

    def test_admin_can_confirm_as_provider_with_reference_and_reason(self, db_session: Session):
        obligation, tenant, _recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        admin = _make_admin(db_session)
        confirmed = rp_crud.confirm_receipt_as_provider(
            db_session, admin, record, provider_reference="TXN-EXT-99123", reason="Matched against provider's own settlement report",
        )
        assert confirmed.status == "CONFIRMED_BY_PROVIDER"
        assert confirmed.provenance == "PROVIDER_CONFIRMATION"
        assert confirmed.provider_reference == "TXN-EXT-99123"
        assert float(confirmed.confirmed_amount) == 850.0
        db_session.refresh(obligation)
        assert obligation.status == "CONFIRMED_BY_PROVIDER"

    def test_requires_both_reference_and_reason(self, db_session: Session):
        obligation, tenant, _recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        admin = _make_admin(db_session)
        with pytest.raises(HTTPException) as exc:
            rp_crud.confirm_receipt_as_provider(db_session, admin, record, provider_reference="", reason="some reason")
        assert exc.value.status_code == 400
        with pytest.raises(HTTPException) as exc:
            rp_crud.confirm_receipt_as_provider(db_session, admin, record, provider_reference="TXN-1", reason="")
        assert exc.value.status_code == 400

    def test_cannot_provider_confirm_an_already_confirmed_record(self, db_session: Session):
        obligation, tenant, recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        rp_crud.confirm_receipt(db_session, recipient, record)
        admin = _make_admin(db_session)
        with pytest.raises(HTTPException) as exc:
            rp_crud.confirm_receipt_as_provider(db_session, admin, record, provider_reference="TXN-1", reason="reason")
        assert exc.value.status_code == 409

    def test_provider_and_recipient_confirmation_remain_distinguishable(self, db_session: Session):
        """A4: 'Recipient and provider confirmations remain distinguishable
        in UI and data.'"""
        obligation, tenant, _recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        admin = _make_admin(db_session)
        confirmed = rp_crud.confirm_receipt_as_provider(db_session, admin, record, provider_reference="TXN-1", reason="reason")
        assert confirmed.status != "CONFIRMED_BY_RECIPIENT"
        assert confirmed.provenance != "RECIPIENT_CONFIRMATION"
        assert confirmed.provenance != "ADMIN_CORRECTION"


class TestDiscrepancyAndCorrection:
    def test_report_discrepancy_moves_record_and_obligation_to_disputed(self, db_session: Session):
        obligation, tenant, recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        dispute = rp_crud.report_discrepancy(
            db_session, record=record, reason_code="NOT_ARRIVED", reported_by_party_id=recipient.id,
        )
        assert dispute.status == "OPEN"
        db_session.refresh(record)
        db_session.refresh(obligation)
        assert record.status == "DISPUTED"
        assert obligation.status == "DISPUTED"

    def test_resolve_dispute_does_not_change_record_status(self, db_session: Session):
        """Section 5.1: resolving a dispute 'does not satisfy a disputed
        obligation automatically' -- it stays DISPUTED until a separate
        correction/new declaration changes it."""
        obligation, tenant, recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        dispute = rp_crud.report_discrepancy(
            db_session, record=record, reason_code="NOT_ARRIVED", reported_by_party_id=recipient.id,
        )
        admin = _make_admin(db_session)
        resolved = rp_crud.resolve_dispute(db_session, admin, dispute, resolution_notes="Matched to a late reference")
        assert resolved.status == "RESOLVED"
        db_session.refresh(record)
        assert record.status == "DISPUTED"

    def test_append_correction_is_additive_not_destructive(self, db_session: Session):
        """A10: 'Landlord attempts to alter a confirmed payment event rather
        than append a correction' -- append_correction is the only path that
        changes a field, and it always leaves a RentalPaymentCorrection row
        behind recording the previous value."""
        obligation, tenant, recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        rp_crud.confirm_receipt(db_session, recipient, record)
        admin = _make_admin(db_session)

        correction = rp_crud.append_correction(
            db_session, admin, record, field_name="declared_amount", new_value="900.00", reason="Tenant underreported the amount",
        )
        assert correction.previous_value == "850.00" or float(correction.previous_value) == 850.0
        assert correction.new_value == "900.00"
        db_session.refresh(record)
        assert float(record.declared_amount) == 900.0
        assert len(record.corrections) == 1

    def test_reverse_record_requires_a_previously_confirmed_record(self, db_session: Session):
        obligation, tenant, recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        admin = _make_admin(db_session)
        with pytest.raises(HTTPException) as exc:
            rp_crud.reverse_record(db_session, admin, record, reason="not yet confirmed")
        assert exc.value.status_code == 409

        rp_crud.confirm_receipt(db_session, recipient, record)
        reversed_record = rp_crud.reverse_record(db_session, admin, record, reason="Bank reported the transfer bounced")
        assert reversed_record.status == "REVERSED"
        assert reversed_record.provenance == "ADMIN_CORRECTION"
        db_session.refresh(obligation)
        assert obligation.status == "REVERSED"


class TestWaiveAndCancel:
    def test_waive_requires_a_reason(self, db_session: Session):
        obligation, _tenant, _recipient = _make_obligation(db_session)
        admin = _make_admin(db_session)
        with pytest.raises(HTTPException) as exc:
            rp_crud.waive_obligation(db_session, admin, obligation, reason="")
        assert exc.value.status_code == 400

    def test_waived_obligation_cannot_be_marked_paid(self, db_session: Session):
        obligation, tenant, _recipient = _make_obligation(db_session)
        admin = _make_admin(db_session)
        rp_crud.waive_obligation(db_session, admin, obligation, reason="Landlord agreed to waive this month's rent")
        db_session.refresh(obligation)
        assert obligation.status == "WAIVED"

        with pytest.raises(HTTPException) as exc:
            rp_crud.mark_paid(
                db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
                payment_method_category="BANK_TRANSFER",
            )
        assert exc.value.status_code == 409

    def test_cannot_waive_twice(self, db_session: Session):
        obligation, _tenant, _recipient = _make_obligation(db_session)
        admin = _make_admin(db_session)
        rp_crud.waive_obligation(db_session, admin, obligation, reason="First waiver")
        with pytest.raises(HTTPException) as exc:
            rp_crud.cancel_obligation(db_session, admin, obligation, reason="Second attempt")
        assert exc.value.status_code == 409


class TestDepositTerminology:
    def test_rent_obligation_label_is_plain_rent(self, db_session: Session):
        obligation, _tenant, _recipient = _make_obligation(db_session)
        assert obligation.obligation_type == "RENT"
        assert obligation.display_label == "rent"

    def test_deposit_obligation_with_no_resolvable_jurisdiction_falls_back_to_default(self, db_session: Session):
        """No agreement/occupancy is linked in this fixture, so
        jurisdiction_code resolves to None -- display_label must fall back
        to the safe default rather than raising or showing a blank label."""
        tenant = _make_guest(db_session, guest_id="G-RP-DEP-1")
        recipient = _make_party(db_session)
        obligation = rp_crud.create_obligation(
            db_session, obligation_type="DEPOSIT", tenant_guest_id=tenant.id, recipient_party_id=recipient.id,
            amount=850, currency="GBP", due_date=date.today(),
        )
        assert obligation.jurisdiction_code is None
        assert obligation.display_label == "security deposit"


class TestPaymentInstructions:
    def test_submit_creates_pending_verification_and_notifies_tenants(self, db_session: Session):
        obligation, tenant, recipient = _make_obligation(db_session)
        instruction, raw_code = rp_crud.submit_rental_payment_instruction(
            db_session, recipient, method="BANK_TRANSFER", recipient_name="Example Property Ltd",
            account_identifier="00112233445566", reference_format="ZR-{propertyCode}",
        )
        assert instruction.status == "PENDING_VERIFICATION"
        assert instruction.account_identifier_last4 == "5566"
        assert len(raw_code) == 6
        # No ACTIVE instruction exists yet -- the tenant-facing lookup must not
        # return an unverified one.
        assert rp_crud.get_active_rental_payment_instruction(db_session, recipient.id) is None

    def test_wrong_code_increments_attempts_and_rejects(self, db_session: Session):
        _obligation, _tenant, recipient = _make_obligation(db_session)
        instruction, _raw_code = rp_crud.submit_rental_payment_instruction(
            db_session, recipient, method="BANK_TRANSFER", recipient_name="Example Property Ltd",
            account_identifier="00112233445566",
        )
        with pytest.raises(HTTPException) as exc:
            rp_crud.confirm_rental_payment_instruction(db_session, instruction, "000000")
        assert exc.value.status_code == 400
        db_session.refresh(instruction)
        assert instruction.verification_attempts == 1

    def test_correct_code_activates_and_supersedes_previous(self, db_session: Session):
        _obligation, _tenant, recipient = _make_obligation(db_session)
        first, first_code = rp_crud.submit_rental_payment_instruction(
            db_session, recipient, method="BANK_TRANSFER", recipient_name="Example Property Ltd",
            account_identifier="00112233441111",
        )
        activated_first = rp_crud.confirm_rental_payment_instruction(db_session, first, first_code)
        assert activated_first.status == "ACTIVE"

        second, second_code = rp_crud.submit_rental_payment_instruction(
            db_session, recipient, method="BANK_TRANSFER", recipient_name="Example Property Ltd (new)",
            account_identifier="00112233442222",
        )
        rp_crud.confirm_rental_payment_instruction(db_session, second, second_code)
        db_session.refresh(first)
        assert first.status == "SUPERSEDED"

        active = rp_crud.get_active_rental_payment_instruction(db_session, recipient.id)
        assert active.id == second.id
        assert active.account_identifier_last4 == "2222"

    def test_tenant_sees_only_the_active_instruction_for_their_obligation(self, db_session: Session):
        obligation, tenant, recipient = _make_obligation(db_session)
        instruction, code = rp_crud.submit_rental_payment_instruction(
            db_session, recipient, method="BANK_TRANSFER", recipient_name="Example Property Ltd",
            account_identifier="00112233449999",
        )
        rp_crud.confirm_rental_payment_instruction(db_session, instruction, code)

        rp_crud.assert_tenant_owns_obligation(obligation, tenant.id)
        active = rp_crud.get_active_rental_payment_instruction(db_session, obligation.recipient_party_id)
        assert active is not None
        assert active.account_identifier_last4 == "9999"


class TestInstructionRiskControlsAndManualReview:
    """ZR-PAY-002 Section 9.1 steps 3/7 and the 16.1 negative scenario:
    'Payment instruction is changed immediately after password or MFA
    reset.'"""

    def _make_recipient_with_user(self, db: Session, *, password_changed_at=None):
        recipient = _make_party(db)
        user = _make_user(db, email=f"instr-risk-{recipient.id}@test.com")
        user.party_id = recipient.id
        user.password_changed_at = password_changed_at
        db.flush()
        return recipient, user

    def test_no_recent_password_change_activates_normally(self, db_session: Session):
        recipient, _user = self._make_recipient_with_user(db_session, password_changed_at=None)
        instruction, code = rp_crud.submit_rental_payment_instruction(
            db_session, recipient, method="BANK_TRANSFER", recipient_name="Example Property Ltd",
            account_identifier="00112233440001",
        )
        assert instruction.is_high_risk is False
        activated = rp_crud.confirm_rental_payment_instruction(db_session, instruction, code)
        assert activated.status == "ACTIVE"

    def test_password_changed_minutes_ago_is_routed_to_manual_review(self, db_session: Session):
        recent = datetime.now(timezone.utc) - timedelta(minutes=10)
        recipient, _user = self._make_recipient_with_user(db_session, password_changed_at=recent)
        instruction, code = rp_crud.submit_rental_payment_instruction(
            db_session, recipient, method="BANK_TRANSFER", recipient_name="Example Property Ltd",
            account_identifier="00112233440002",
        )
        assert instruction.is_high_risk is True
        assert instruction.high_risk_reason

        reviewed = rp_crud.confirm_rental_payment_instruction(db_session, instruction, code)
        assert reviewed.status == "PENDING_REVIEW"
        # Strong auth (the code) was still verified -- this isn't a rejection
        # of the code, only a second, admin-controlled gate on top of it.
        assert reviewed.verified_at is not None
        # A high-risk change must never become the tenant-facing active
        # instruction on its own.
        assert rp_crud.get_active_rental_payment_instruction(db_session, recipient.id) is None

    def test_password_changed_outside_the_window_activates_normally(self, db_session: Session):
        old = datetime.now(timezone.utc) - timedelta(hours=48)
        recipient, _user = self._make_recipient_with_user(db_session, password_changed_at=old)
        instruction, code = rp_crud.submit_rental_payment_instruction(
            db_session, recipient, method="BANK_TRANSFER", recipient_name="Example Property Ltd",
            account_identifier="00112233440003",
        )
        assert instruction.is_high_risk is False
        activated = rp_crud.confirm_rental_payment_instruction(db_session, instruction, code)
        assert activated.status == "ACTIVE"

    def test_admin_approves_pending_review_and_it_supersedes_the_previous_active(self, db_session: Session):
        recipient, _user = self._make_recipient_with_user(db_session, password_changed_at=None)
        first, first_code = rp_crud.submit_rental_payment_instruction(
            db_session, recipient, method="BANK_TRANSFER", recipient_name="Example Property Ltd",
            account_identifier="00112233440004",
        )
        rp_crud.confirm_rental_payment_instruction(db_session, first, first_code)

        # Simulate a password reset happening right before the next change.
        from app.crud.user import get_user_by_party_id
        host_user = get_user_by_party_id(db_session, recipient.id)
        host_user.password_changed_at = datetime.now(timezone.utc)
        db_session.flush()

        second, second_code = rp_crud.submit_rental_payment_instruction(
            db_session, recipient, method="BANK_TRANSFER", recipient_name="Example Property Ltd (new)",
            account_identifier="00112233440005",
        )
        pending = rp_crud.confirm_rental_payment_instruction(db_session, second, second_code)
        assert pending.status == "PENDING_REVIEW"
        db_session.refresh(first)
        assert first.status == "ACTIVE", "rejection/pending review must never touch the previously active instruction"

        admin = _make_admin(db_session)
        approved = rp_crud.approve_pending_review_instruction(db_session, admin, pending, reason="Verified with recipient by phone")
        assert approved.status == "ACTIVE"
        assert approved.reviewed_by_admin_id == admin.id
        db_session.refresh(first)
        assert first.status == "SUPERSEDED"

        active = rp_crud.get_active_rental_payment_instruction(db_session, recipient.id)
        assert active.id == second.id

    def test_admin_rejects_pending_review_and_previous_instruction_stays_active(self, db_session: Session):
        recent = datetime.now(timezone.utc) - timedelta(minutes=5)
        recipient, _user = self._make_recipient_with_user(db_session, password_changed_at=None)
        first, first_code = rp_crud.submit_rental_payment_instruction(
            db_session, recipient, method="BANK_TRANSFER", recipient_name="Example Property Ltd",
            account_identifier="00112233440006",
        )
        rp_crud.confirm_rental_payment_instruction(db_session, first, first_code)

        from app.crud.user import get_user_by_party_id
        host_user = get_user_by_party_id(db_session, recipient.id)
        host_user.password_changed_at = recent
        db_session.flush()

        second, second_code = rp_crud.submit_rental_payment_instruction(
            db_session, recipient, method="BANK_TRANSFER", recipient_name="Suspicious New Recipient",
            account_identifier="00112233440007",
        )
        pending = rp_crud.confirm_rental_payment_instruction(db_session, second, second_code)

        admin = _make_admin(db_session)
        rejected = rp_crud.reject_pending_review_instruction(db_session, admin, pending, reason="Could not verify with recipient")
        assert rejected.status == "REJECTED"

        active = rp_crud.get_active_rental_payment_instruction(db_session, recipient.id)
        assert active.id == first.id

    def test_cannot_approve_or_reject_an_instruction_that_is_not_pending_review(self, db_session: Session):
        recipient, _user = self._make_recipient_with_user(db_session, password_changed_at=None)
        instruction, code = rp_crud.submit_rental_payment_instruction(
            db_session, recipient, method="BANK_TRANSFER", recipient_name="Example Property Ltd",
            account_identifier="00112233440008",
        )
        activated = rp_crud.confirm_rental_payment_instruction(db_session, instruction, code)
        assert activated.status == "ACTIVE"

        admin = _make_admin(db_session)
        with pytest.raises(HTTPException) as exc:
            rp_crud.approve_pending_review_instruction(db_session, admin, activated)
        assert exc.value.status_code == 409

    def test_http_admin_review_queue_and_approve_route(self, client, db_session: Session):
        recent = datetime.now(timezone.utc) - timedelta(minutes=1)
        recipient, _user = self._make_recipient_with_user(db_session, password_changed_at=recent)
        instruction, code = rp_crud.submit_rental_payment_instruction(
            db_session, recipient, method="BANK_TRANSFER", recipient_name="Example Property Ltd",
            account_identifier="00112233440009",
        )
        rp_crud.confirm_rental_payment_instruction(db_session, instruction, code)
        db_session.commit()

        admin = _make_admin(db_session, email="instr-review-admin@test.com", role="super_admin")
        db_session.commit()

        r = client.get("/api/finance/rental-payments/instructions/pending-review", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        ids = [row["id"] for row in r.json()]
        assert instruction.id in ids

        r = client.post(
            f"/api/finance/rental-payments/instructions/{instruction.id}/approve",
            json={"reason": "Confirmed via phone"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ACTIVE"


class TestObligationStatusDerivation:
    def test_upcoming_vs_overdue_with_no_records(self, db_session: Session):
        future_obligation, _t, _r = _make_obligation(db_session, due_date=date.today() + timedelta(days=10))
        rp_crud.recompute_obligation_status(db_session, future_obligation)
        assert future_obligation.status == "UPCOMING"

        past_obligation, _t2, _r2 = _make_obligation(db_session, due_date=date.today() - timedelta(days=1))
        rp_crud.recompute_obligation_status(db_session, past_obligation)
        assert past_obligation.status == "OVERDUE"


_PDF_BYTES = b"%PDF-1.4 fake rental payment evidence content"


class TestRecipientEvidenceAndAdminOverrides:
    """HTTP-level tests for the four permission-table gaps closed after the
    initial pass: recipient evidence upload (Section 11 'Controlled'),
    evidence access history (Section 7.1), the admin correction-workflow
    confirmation (Section 11 'No, except explicit correction workflow'),
    and admin visibility into payment instructions (Section 11
    'Restricted')."""

    def _setup(self, db_session: Session):
        tenant_user = _make_user(db_session, email="rp-tenant@test.com")
        recipient_user = _make_user(db_session, email="rp-recipient@test.com")
        party = _make_party(db_session)
        recipient_user.party_id = party.id
        guest = _make_guest(db_session, guest_id="G-RP-HTTP-1")
        guest.user_account_id = tenant_user.id
        db_session.flush()

        obligation = rp_crud.create_obligation(
            db_session, obligation_type="RENT", tenant_guest_id=guest.id, recipient_party_id=party.id,
            amount=850, currency="GBP", due_date=date.today(),
        )
        return tenant_user, recipient_user, guest, party, obligation

    def test_recipient_can_upload_evidence_and_access_is_logged(self, client, db_session: Session):
        tenant_user, recipient_user, guest, _party, obligation = self._setup(db_session)
        record = rp_crud.mark_paid(
            db_session, guest, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )

        r = client.post(
            f"/api/users/rental-payments/recipient/records/{record.id}/evidence",
            files={"file": ("statement.pdf", _PDF_BYTES, "application/pdf")},
            cookies=auth_user_cookie(recipient_user),
        )
        assert r.status_code == 201, r.text
        artifact_id = r.json()["id"]

        r_list = client.get(f"/api/users/rental-payments/records/{record.id}/evidence", cookies=auth_user_cookie(tenant_user))
        assert r_list.status_code == 200, r_list.text
        assert [a["id"] for a in r_list.json()] == [artifact_id]

        before = db_session.query(EvidenceArtifact).count()
        r = client.get(
            f"/api/users/rental-payments/records/{record.id}/evidence/{artifact_id}", cookies=auth_user_cookie(tenant_user),
        )
        assert r.status_code == 200, r.text

        from app.models.audit import AuditEvent

        access_events = (
            db_session.query(AuditEvent)
            .filter(AuditEvent.action == "rental_payment.evidence_accessed", AuditEvent.resource_id == str(artifact_id))
            .count()
        )
        assert access_events == 1
        assert db_session.query(EvidenceArtifact).count() == before  # no mutation, just an audit row

    def test_unrelated_user_cannot_upload_or_download_evidence(self, client, db_session: Session):
        _tenant_user, _recipient_user, guest, _party, obligation = self._setup(db_session)
        record = rp_crud.mark_paid(
            db_session, guest, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        stranger = _make_user(db_session, email="rp-stranger@test.com")

        r = client.get(
            f"/api/users/rental-payments/records/{record.id}/evidence/999", cookies=auth_user_cookie(stranger),
        )
        assert r.status_code == 403, r.text

    def test_admin_confirms_via_correction_workflow_requires_reason(self, client, db_session: Session):
        tenant_user, _recipient_user, guest, _party, obligation = self._setup(db_session)
        record = rp_crud.mark_paid(
            db_session, guest, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        admin = _make_admin(db_session, email="rp-super@test.com", role="super_admin")

        r = client.post(
            f"/api/finance/rental-payments/records/{record.id}/confirm",
            json={"reason": ""}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 400, r.text

        r = client.post(
            f"/api/finance/rental-payments/records/{record.id}/confirm",
            json={"reason": "Recipient lost account access -- confirmed via phone verification"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "CONFIRMED_BY_RECIPIENT"
        assert r.json()["provenance"] == "ADMIN_CORRECTION"

    def test_admin_can_view_but_not_edit_payment_instructions(self, client, db_session: Session):
        _tenant_user, recipient_user, _guest, party, _obligation = self._setup(db_session)
        admin = _make_admin(db_session, email="rp-super2@test.com", role="super_admin")

        instruction, code = rp_crud.submit_rental_payment_instruction(
            db_session, party, method="BANK_TRANSFER", recipient_name="Example Property Ltd",
            account_identifier="00112233445566",
        )
        rp_crud.confirm_rental_payment_instruction(db_session, instruction, code)

        r = client.get(f"/api/finance/rental-payments/instructions/{party.id}", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert r.json()[0]["accountIdentifierMasked"].endswith("5566")

        # Non-super-admin is rejected -- "Restricted", not open to any admin.
        plain_admin = _make_admin(db_session, email="rp-plain@test.com", role="admin")
        r = client.get(f"/api/finance/rental-payments/instructions/{party.id}", cookies=auth_admin_cookie(plain_admin))
        assert r.status_code == 403, r.text

    def test_tenant_self_correct_route_serializes_a_non_admin_actor(self, client, db_session: Session):
        """Regression test: RentalPaymentCorrectionRead used to require
        actor_admin_id as a non-optional int, so this route crashed with a
        500 (FastAPI response_model validation error) on every real call --
        tenant_correct_own_record always leaves actor_admin_id unset. Only
        caught by going through the actual HTTP route, not by calling the
        crud function directly."""
        tenant_user, _recipient_user, guest, _party, obligation = self._setup(db_session)
        record = rp_crud.mark_paid(
            db_session, guest, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER", external_reference="ZR-TYPO",
        )

        r = client.post(
            f"/api/users/rental-payments/records/{record.id}/self-correct",
            # fieldName's *value* is the backend's own (snake_case) column
            # name -- used directly in getattr/setattr -- not a camelCase
            # JS-style field name, same convention as the admin correction
            # endpoint's CORRECTABLE_RECORD_FIELDS.
            json={"fieldName": "external_reference", "newValue": "ZR-FIXED", "reason": "Typo"},
            cookies=auth_user_cookie(tenant_user),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["newValue"] == "ZR-FIXED"
        assert body["actorGuestId"] == guest.id
        assert body["actorAdminId"] is None


class TestSelfServiceCorrections:
    def test_tenant_can_correct_own_record_before_recipient_acts(self, db_session: Session):
        obligation, tenant, _recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER", external_reference="ZR-WRONG-REF",
        )
        correction = rp_crud.tenant_correct_own_record(
            db_session, tenant, record, field_name="external_reference", new_value="ZR-CORRECT-REF",
            reason="Typo in the reference I sent",
        )
        assert correction.previous_value == "ZR-WRONG-REF"
        assert correction.new_value == "ZR-CORRECT-REF"
        assert correction.actor_guest_id == tenant.id
        assert correction.actor_admin_id is None
        db_session.refresh(record)
        assert record.external_reference == "ZR-CORRECT-REF"

    def test_tenant_cannot_correct_amount_or_date(self, db_session: Session):
        obligation, tenant, _recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        with pytest.raises(HTTPException) as exc:
            rp_crud.tenant_correct_own_record(db_session, tenant, record, field_name="declared_amount", new_value="9999")
        assert exc.value.status_code == 400

    def test_tenant_cannot_self_correct_once_recipient_has_confirmed(self, db_session: Session):
        obligation, tenant, recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        rp_crud.confirm_receipt(db_session, recipient, record)
        with pytest.raises(HTTPException) as exc:
            rp_crud.tenant_correct_own_record(db_session, tenant, record, field_name="external_reference", new_value="X")
        assert exc.value.status_code == 409

    def test_other_tenant_cannot_self_correct(self, db_session: Session):
        obligation, tenant, _recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        other = _make_guest(db_session, guest_id="G-RP-STRANGER")
        with pytest.raises(HTTPException) as exc:
            rp_crud.tenant_correct_own_record(db_session, other, record, field_name="external_reference", new_value="X")
        assert exc.value.status_code == 403

    def test_recipient_can_edit_own_open_dispute(self, db_session: Session):
        obligation, tenant, recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        dispute = rp_crud.report_discrepancy(
            db_session, record=record, reason_code="NOT_ARRIVED", reported_by_party_id=recipient.id,
        )
        updated = rp_crud.recipient_update_own_open_dispute(db_session, recipient, dispute, details="Still nothing after 3 days")
        assert updated.details == "Still nothing after 3 days"

    def test_recipient_cannot_edit_a_resolved_dispute(self, db_session: Session):
        obligation, tenant, recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        dispute = rp_crud.report_discrepancy(
            db_session, record=record, reason_code="NOT_ARRIVED", reported_by_party_id=recipient.id,
        )
        admin = _make_admin(db_session)
        rp_crud.resolve_dispute(db_session, admin, dispute, resolution_notes="Matched")
        with pytest.raises(HTTPException) as exc:
            rp_crud.recipient_update_own_open_dispute(db_session, recipient, dispute, details="too late")
        assert exc.value.status_code == 409

    def test_tenant_cannot_edit_the_recipients_dispute(self, db_session: Session):
        obligation, tenant, recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        dispute = rp_crud.report_discrepancy(
            db_session, record=record, reason_code="NOT_ARRIVED", reported_by_party_id=recipient.id,
        )
        other_party = _make_party(db_session)
        with pytest.raises(HTTPException) as exc:
            rp_crud.recipient_update_own_open_dispute(db_session, other_party, dispute, details="not mine")
        assert exc.value.status_code == 403


class TestEvidenceRetentionAndLegalHold:
    def _make_record_with_evidence(self, db_session: Session):
        obligation, tenant, _recipient = _make_obligation(db_session)
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        artifact = EvidenceArtifact(
            related_entity_type="rental_payment_record", related_entity_id=str(record.id),
            stored_filename="does-not-need-to-exist-on-disk.pdf", original_filename="proof.pdf",
            content_type="application/pdf", file_size=10, sha256_hash="0" * 64,
        )
        db_session.add(artifact)
        db_session.flush()
        return artifact

    def test_expired_unheld_evidence_is_swept(self, db_session: Session):
        from app.services.evidence_retention import sweep_expired_evidence

        artifact = self._make_record_with_evidence(db_session)
        artifact.created_at = datetime.now(timezone.utc) - timedelta(days=3000)  # past the 2555-day default
        db_session.flush()

        deleted = sweep_expired_evidence(db_session)
        assert artifact.id in [a.id for a in deleted]
        db_session.refresh(artifact)
        assert artifact.deleted_at is not None

    def test_legal_hold_blocks_the_sweep(self, db_session: Session):
        from app.services.evidence_retention import sweep_expired_evidence

        artifact = self._make_record_with_evidence(db_session)
        artifact.created_at = datetime.now(timezone.utc) - timedelta(days=3000)
        db_session.flush()
        admin = _make_admin(db_session)
        hold = rp_crud.place_evidence_legal_hold(db_session, admin, artifact, reason="Active dispute proceeding")

        deleted = sweep_expired_evidence(db_session)
        assert artifact.id not in [a.id for a in deleted]
        db_session.refresh(artifact)
        assert artifact.deleted_at is None

        rp_crud.release_evidence_legal_hold(db_session, admin, hold)
        deleted_after_release = sweep_expired_evidence(db_session)
        assert artifact.id in [a.id for a in deleted_after_release]

    def test_cannot_place_a_second_active_hold(self, db_session: Session):
        artifact = self._make_record_with_evidence(db_session)
        admin = _make_admin(db_session)
        rp_crud.place_evidence_legal_hold(db_session, admin, artifact, reason="First hold")
        with pytest.raises(HTTPException) as exc:
            rp_crud.place_evidence_legal_hold(db_session, admin, artifact, reason="Second hold")
        assert exc.value.status_code == 409

    def test_hold_requires_a_reason(self, db_session: Session):
        artifact = self._make_record_with_evidence(db_session)
        admin = _make_admin(db_session)
        with pytest.raises(HTTPException) as exc:
            rp_crud.place_evidence_legal_hold(db_session, admin, artifact, reason="")
        assert exc.value.status_code == 400

    def test_not_yet_expired_evidence_is_left_alone(self, db_session: Session):
        from app.services.evidence_retention import sweep_expired_evidence

        artifact = self._make_record_with_evidence(db_session)
        deleted = sweep_expired_evidence(db_session)
        assert artifact.id not in [a.id for a in deleted]


class TestDueSoonSweep:
    def test_notifies_once_for_obligation_due_within_the_lead_window(self, db_session: Session):
        obligation, _tenant, _recipient = _make_obligation(db_session, due_date=date.today() + timedelta(days=2))
        obligation.status = "DUE"
        db_session.flush()

        notified = sweep_rental_payment_due_soon(db_session)
        assert obligation.id in [o.id for o in notified]
        db_session.refresh(obligation)
        assert obligation.due_soon_notified_at is not None

        # Idempotent -- running again must not re-notify the same obligation.
        notified_again = sweep_rental_payment_due_soon(db_session)
        assert obligation.id not in [o.id for o in notified_again]

    def test_does_not_notify_obligations_outside_the_lead_window(self, db_session: Session):
        obligation, _tenant, _recipient = _make_obligation(db_session, due_date=date.today() + timedelta(days=30))
        notified = sweep_rental_payment_due_soon(db_session)
        assert obligation.id not in [o.id for o in notified]

    def test_does_not_notify_once_tenant_has_already_declared_payment(self, db_session: Session):
        obligation, tenant, _recipient = _make_obligation(db_session, due_date=date.today() + timedelta(days=1))
        rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        notified = sweep_rental_payment_due_soon(db_session)
        assert obligation.id not in [o.id for o in notified]

    def test_does_not_notify_a_waived_obligation(self, db_session: Session):
        obligation, _tenant, _recipient = _make_obligation(db_session, due_date=date.today())
        admin = _make_admin(db_session)
        rp_crud.waive_obligation(db_session, admin, obligation, reason="Landlord waived this period")
        notified = sweep_rental_payment_due_soon(db_session)
        assert obligation.id not in [o.id for o in notified]
