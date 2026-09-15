"""ZR-ENG-CLR-005 Section 13.1: create_agreement and
generate_next_rent_obligation each generate an immutable RentInvoice
best-effort as soon as a RENT obligation exists -- the host's request for
payment, not proof one was made (PaymentReceipt is that counterpart). The
admin and renter download routes both serve the same rendered PDF, generating
on demand (idempotent) if the best-effort step somehow didn't run; the
renter route is ownership-checked; a non-RENT obligation has no invoice."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import occupancy as occupancy_crud
from app.models.finance import Obligation, RentInvoice
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie
from tests.test_application_workflow import _make_verified_renter_with_published_listing
from tests.test_deposit_claims import _make_agreement_eligible
from tests.test_ledger import _make_provider_rent_obligation
from tests.test_payment_schedule import _make_signed_agreement_with_schedule


class TestCreateAgreementGeneratesARentInvoice:
    def test_agreement_creation_creates_a_rent_invoice_for_the_initial_obligation(self, client, db_session: Session):
        user, listing_id = _make_verified_renter_with_published_listing(db_session, email="rentinv-renter@test.com")
        user_cookies = auth_user_cookie(user)
        admin = _make_admin(db_session, email="rentinv-admin@test.com", role="super_admin")
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
        r = client.post(
            f"/api/leasing/offers/{offer_id}/terms",
            json={
                "monthlyRent": 3000.0, "depositAmount": 3000.0,
                "startDate": (date.today() + timedelta(days=5)).isoformat(), "termMonths": 6,
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

        rent_obligation = db_session.scalar(
            select(Obligation).where(Obligation.agreement_id == agreement_id, Obligation.obligation_type == "RENT")
        )
        invoice = db_session.scalar(select(RentInvoice).where(RentInvoice.obligation_id == rent_obligation.id))
        assert invoice is not None
        assert invoice.invoice_number == f"RINV-{rent_obligation.id:08d}"


class TestGenerateNextRentObligationGeneratesARentInvoice:
    def test_the_recurring_obligation_also_gets_its_own_invoice(self, db_session: Session):
        admin = _make_admin(db_session, email="rentinv-recur-admin@test.com", role="admin")
        agreement, offer, listing, room, guest, schedule = _make_signed_agreement_with_schedule(
            db_session, admin=admin, monthly_rent=1200.0, listing_id="L-RENTINVRECUR", guest_id="G-RENTINVRECUR",
        )
        occupancy = occupancy_crud.confirm_move_in(db_session, agreement, admin)

        obligation = occupancy_crud.generate_next_rent_obligation(db_session, occupancy, admin)
        assert obligation is not None

        invoice = db_session.scalar(select(RentInvoice).where(RentInvoice.obligation_id == obligation.id))
        assert invoice is not None
        assert invoice.invoice_number == f"RINV-{obligation.id:08d}"


class TestAdminRentInvoiceDownload:
    def test_admin_can_download_the_invoice_pdf(self, client, db_session: Session):
        obligation, admin, _guest, _party_id = _make_provider_rent_obligation(db_session, suffix="rentinv-admin1")
        admin_cookies = auth_admin_cookie(admin)

        r = client.get(f"/api/finance/obligations/{obligation.id}/rent-invoice", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/pdf"
        assert r.content.startswith(b"%PDF")
        assert "attachment" in r.headers["content-disposition"]

    def test_calling_it_twice_is_idempotent(self, client, db_session: Session):
        obligation, admin, _guest, _party_id = _make_provider_rent_obligation(db_session, suffix="rentinv-idem1")
        admin_cookies = auth_admin_cookie(admin)

        r1 = client.get(f"/api/finance/obligations/{obligation.id}/rent-invoice", cookies=admin_cookies)
        r2 = client.get(f"/api/finance/obligations/{obligation.id}/rent-invoice", cookies=admin_cookies)
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.content == r2.content

        invoices = db_session.scalars(select(RentInvoice).where(RentInvoice.obligation_id == obligation.id)).all()
        assert len(invoices) == 1

    def test_it_never_requires_the_obligation_to_be_paid(self, client, db_session: Session):
        obligation, admin, _guest, _party_id = _make_provider_rent_obligation(db_session, suffix="rentinv-unpaid1")
        admin_cookies = auth_admin_cookie(admin)
        assert obligation.status == "PENDING"

        r = client.get(f"/api/finance/obligations/{obligation.id}/rent-invoice", cookies=admin_cookies)
        assert r.status_code == 200, r.text

    def test_other_providers_admin_cannot_download_it(self, client, db_session: Session):
        obligation, _admin, _guest, _party_id = _make_provider_rent_obligation(db_session, suffix="rentinv-otherprov1")
        outsider = _make_admin(db_session, email="rentinv-outsider@test.com", role="admin")

        r = client.get(f"/api/finance/obligations/{obligation.id}/rent-invoice", cookies=auth_admin_cookie(outsider))
        assert r.status_code == 403, r.text

    def test_no_invoice_for_a_non_rent_obligation(self, client, db_session: Session):
        obligation, admin, _guest, _party_id = _make_provider_rent_obligation(
            db_session, suffix="rentinv-deposit1", obligation_type="DEPOSIT",
        )
        admin_cookies = auth_admin_cookie(admin)

        r = client.get(f"/api/finance/obligations/{obligation.id}/rent-invoice", cookies=admin_cookies)
        assert r.status_code == 409, r.text


class TestUserRentInvoiceDownload:
    def test_owning_renter_can_download_their_invoice(self, client, db_session: Session):
        obligation, _admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="rentinv-user1")
        renter = _make_user(db_session, email="rentinv-renter-dl@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.get(f"/api/users/payments/obligations/{obligation.id}/rent-invoice", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        assert r.content.startswith(b"%PDF")

    def test_a_different_renter_cannot_download_it(self, client, db_session: Session):
        obligation, _admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="rentinv-user2")
        owning_renter = _make_user(db_session, email="rentinv-owner@test.com")
        guest.user_account_id = owning_renter.id
        db_session.commit()

        other_renter = _make_user(db_session, email="rentinv-stranger@test.com")
        r = client.get(
            f"/api/users/payments/obligations/{obligation.id}/rent-invoice", cookies=auth_user_cookie(other_renter),
        )
        assert r.status_code == 403, r.text
