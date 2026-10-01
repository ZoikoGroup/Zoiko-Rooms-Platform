"""Rent payment disputes on the admin side: a read-only queue. Resolving one
is between the tenant and host (tests/test_payment_dispute_cases.py)."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.crud import rental_payment as rp_crud
from app.crud.guest import get_guest_for_user
from app.models.party import Party
from app.models.rental_payment import RentalPaymentDispute
from tests.conftest import _make_admin, auth_admin_cookie
from tests.test_rental_payment_legacy_bridge import _create_signed_agreement, _rental_payment_obligations_for


def _disputed_payment(client, db_session: Session, suffix: str):
    user, _cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix=suffix)
    rent = _rental_payment_obligations_for(db_session, agreement_id)["RENT"]
    guest = get_guest_for_user(db_session, user)
    record = rp_crud.mark_paid(
        db_session, guest, rent, amount=float(rent.amount), currency=rent.currency,
        declared_date=date.today(), payment_method_category="BANK_TRANSFER", external_reference="REF-1",
    )
    recipient = db_session.get(Party, rent.recipient_party_id)
    dispute = rp_crud.report_discrepancy(
        db_session, record=record, reason_code="NOT_ARRIVED", details="Nothing in my account",
        reported_by_party_id=recipient.id,
    )
    admin = _make_admin(db_session, email=f"dr-admin-{suffix}@test.com", role="super_admin")
    return rent, record, dispute, admin


class TestDisputeQueue:
    def test_open_disputes_are_listed_with_their_payment(self, client, db_session: Session):
        rent, record, dispute, admin = _disputed_payment(client, db_session, "dr-list")
        r = client.get("/api/finance/rental-payments/disputes", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        row = next(d for d in r.json() if d["id"] == dispute.id)
        assert row["recordId"] == record.id
        assert row["obligationId"] == rent.id
        assert row["reportedBy"] == "host"
        assert row["declaredAmount"] == float(record.declared_amount)
        assert row["externalReference"] == "REF-1"


class TestNoAdminDecision:
    def test_admins_cannot_resolve_a_payment_dispute(self, client, db_session: Session):
        """Resolved between tenant and host only -- see test_payment_dispute_cases.py."""
        _rent, _record, dispute, admin = _disputed_payment(client, db_session, "dr-noadmin")
        r = client.post(
            f"/api/finance/rental-payments/disputes/{dispute.id}/resolve",
            json={"resolutionNotes": "x", "outcome": "PAYMENT_STANDS"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code in (404, 405)
        db_session.expire_all()
        assert db_session.get(RentalPaymentDispute, dispute.id).status == "OPEN"


class TestSupportEvidenceAccess:
    def test_support_can_list_and_open_the_proof_on_a_payment(self, client, db_session: Session):
        from tests.conftest import auth_user_cookie
        from tests.test_rental_payment_records import _PDF_BYTES

        rent, record, _dispute, admin = _disputed_payment(client, db_session, "dr-proof")
        user = db_session.get(type(rent.tenant), rent.tenant_guest_id)
        from app.models.user_account import UserAccount

        renter = db_session.get(UserAccount, user.user_account_id)
        r = client.post(
            f"/api/users/rental-payments/records/{record.id}/evidence",
            files={"file": ("proof.pdf", _PDF_BYTES, "application/pdf")}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        artifact_id = r.json()["id"]

        listed = client.get(f"/api/finance/rental-payments/records/{record.id}/evidence", cookies=auth_admin_cookie(admin))
        assert listed.status_code == 200, listed.text
        assert [a["id"] for a in listed.json()] == [artifact_id]

        opened = client.get(
            f"/api/finance/rental-payments/records/{record.id}/evidence/{artifact_id}", cookies=auth_admin_cookie(admin),
        )
        assert opened.status_code == 200
        assert opened.content == _PDF_BYTES


class TestListingFeePaymentsForSupport:
    def test_super_admin_lists_listing_fee_payments(self, client, db_session: Session):
        from tests.test_listing_fee_checkout_session_webhook import _make_pending_checkout_session_payment

        payment, listing = _make_pending_checkout_session_payment(db_session, checkout_session_id="cs_admin_list")
        admin = _make_admin(db_session, email="lf-list-admin@test.com", role="super_admin")
        r = client.get("/api/finance/listing-fees/payments", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert payment.id in [p["id"] for p in r.json()]
        filtered = client.get(f"/api/finance/listing-fees/payments?listing_id={listing.id}", cookies=auth_admin_cookie(admin))
        assert [p["id"] for p in filtered.json()] == [payment.id]
