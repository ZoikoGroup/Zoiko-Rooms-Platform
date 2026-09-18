"""ZR-ENG-CLR-005 Section 6.1-E/10.5/15.1/16.1/AC-28/AC-29/QA-27 -- autopay
mandates were entirely unimplemented before this (no model, no routes,
zero hits anywhere in the codebase). create_autopay_mandate/revoke_
autopay_mandate/process_autopay_charge (app/crud/finance.py) are the real
consent/revoke/charge-execution primitives; there is still no background
scheduler anywhere in this stack (see crud/occupancy.py:generate_next_rent_
obligation's own docstring), so the actual pull is admin-triggered here --
see process_autopay_charge's own docstring for that honesty."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import AutopayMandate, Obligation, PaymentSchedule
from app.models.guest import Guest
from app.models.leasing import Agreement, Application, Offer, OfferTerms
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie


def _make_active_tenancy_with_schedule(db: Session, *, suffix: str, amount: float = 500.0):
    """A signed, active occupancy with a real ACTIVE PaymentSchedule and one
    PENDING rent obligation due today -- returns (tenant_user, guest,
    occupancy_id, obligation_id, admin)."""
    owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(owner_party)
    db.flush()
    prop = Property(owner_party_id=owner_party.id, address="1 Autopay St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()

    tenant_party = Party(party_type="renter", status="active", jurisdiction="IN")
    db.add(tenant_party)
    db.flush()
    tenant_user = _make_user(db, email=f"autopay-tenant-{suffix}@test.com")
    tenant_user.party_id = tenant_party.id
    tenant_guest = Guest(id=f"G-AUTOPAY-{suffix}", name="Tenant", email=f"autopay-tenant-{suffix}@test.com", joined_at=date.today())
    db.add(tenant_guest)
    db.flush()

    listing = Listing(
        id=f"L-AUTOPAY-{suffix}", slug=f"autopay-{suffix}", name="Autopay Test Listing", room_type="Private room",
        city="Bengaluru", location="Koramangala", price_per_night=amount, guests=1,
        rating=4.5, review_count=0, party_id=owner_party.id, owner_id=None, room_id=room.id, state="PUBLISHED",
    )
    db.add(listing)
    db.flush()

    application = Application(listing_id=listing.id, guest_id=tenant_guest.id, status="DECIDED")
    db.add(application)
    db.flush()
    offer = Offer(application_id=application.id, listing_id=listing.id, guest_id=tenant_guest.id, status="ACCEPTED")
    db.add(offer)
    db.flush()
    db.add(OfferTerms(offer_id=offer.id, version=1, monthly_rent=amount, deposit_amount=amount, start_date=date.today(), term_months=6))
    db.flush()
    agreement = Agreement(offer_id=offer.id, status="SIGNED")
    db.add(agreement)
    db.flush()
    schedule = PaymentSchedule(
        agreement_id=agreement.id, cadence="MONTHLY", currency="INR", amount=amount,
        first_due=date.today(), anchor_day=date.today().day, status="ACTIVE",
    )
    db.add(schedule)
    db.flush()
    occupancy = Occupancy(
        offer_id=offer.id, listing_id=listing.id, room_id=room.id, guest_id=tenant_guest.id,
        status="ACTIVE", expected_end_date=date.today() + timedelta(days=180),
    )
    db.add(occupancy)
    db.flush()
    obligation = Obligation(
        obligation_type="RENT", money_plane="OCCUPANCY", amount=amount, currency="INR",
        due_date=date.today(), status="PENDING", occupancy_id=occupancy.id, schedule_id=schedule.id,
    )
    db.add(obligation)
    admin = _make_admin(db, email=f"autopay-admin-{suffix}@test.com", role="super_admin")
    db.commit()

    return tenant_user, tenant_guest, occupancy.id, obligation.id, admin


class TestCreateAutopayMandate:
    def test_renter_can_set_up_autopay_for_their_own_tenancy(self, client, db_session: Session):
        tenant_user, _guest, occupancy_id, _obligation_id, _admin = _make_active_tenancy_with_schedule(db_session, suffix="create1")

        r = client.post(
            "/api/users/payments/mandates", json={"occupancyId": occupancy_id}, cookies=auth_user_cookie(tenant_user),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "ACTIVE"
        assert body["consentSnapshot"]["cadence"] == "MONTHLY"
        assert body["consentSnapshot"]["amount"] == 500.0
        assert body["providerRef"].startswith("SETI-")

    def test_cannot_set_up_autopay_for_someone_elses_tenancy(self, client, db_session: Session):
        _tenant_user, _guest, occupancy_id, _obligation_id, _admin = _make_active_tenancy_with_schedule(db_session, suffix="create2")
        outsider = _make_user(db_session, email="autopay-outsider@test.com")

        r = client.post(
            "/api/users/payments/mandates", json={"occupancyId": occupancy_id}, cookies=auth_user_cookie(outsider),
        )
        assert r.status_code == 403, r.text

    def test_a_second_active_mandate_for_the_same_tenancy_is_rejected(self, client, db_session: Session):
        tenant_user, _guest, occupancy_id, _obligation_id, _admin = _make_active_tenancy_with_schedule(db_session, suffix="create3")
        cookies = auth_user_cookie(tenant_user)

        r = client.post("/api/users/payments/mandates", json={"occupancyId": occupancy_id}, cookies=cookies)
        assert r.status_code == 201, r.text

        r = client.post("/api/users/payments/mandates", json={"occupancyId": occupancy_id}, cookies=cookies)
        assert r.status_code == 409, r.text


class TestRevokeAutopayMandate:
    def test_owner_can_revoke_their_own_mandate(self, client, db_session: Session):
        tenant_user, _guest, occupancy_id, _obligation_id, _admin = _make_active_tenancy_with_schedule(db_session, suffix="revoke1")
        cookies = auth_user_cookie(tenant_user)
        mandate_id = client.post("/api/users/payments/mandates", json={"occupancyId": occupancy_id}, cookies=cookies).json()["id"]

        r = client.delete(f"/api/users/payments/mandates/{mandate_id}", cookies=cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "REVOKED"
        assert body["revokedAt"] is not None

        r = client.get("/api/users/payments/mandates", cookies=cookies)
        assert r.json()[0]["status"] == "REVOKED"

    def test_revoking_does_not_touch_the_underlying_obligation(self, client, db_session: Session):
        """AC-29: revoking autopay does not cancel future rent obligations."""
        tenant_user, _guest, occupancy_id, obligation_id, _admin = _make_active_tenancy_with_schedule(db_session, suffix="revoke2")
        cookies = auth_user_cookie(tenant_user)
        mandate_id = client.post("/api/users/payments/mandates", json={"occupancyId": occupancy_id}, cookies=cookies).json()["id"]
        client.delete(f"/api/users/payments/mandates/{mandate_id}", cookies=cookies)

        obligation = db_session.get(Obligation, obligation_id)
        assert obligation.status == "PENDING"  # untouched

    def test_cannot_revoke_someone_elses_mandate(self, client, db_session: Session):
        tenant_user, _guest, occupancy_id, _obligation_id, _admin = _make_active_tenancy_with_schedule(db_session, suffix="revoke3")
        mandate_id = client.post(
            "/api/users/payments/mandates", json={"occupancyId": occupancy_id}, cookies=auth_user_cookie(tenant_user),
        ).json()["id"]

        outsider = _make_user(db_session, email="autopay-revoke-outsider@test.com")
        r = client.delete(f"/api/users/payments/mandates/{mandate_id}", cookies=auth_user_cookie(outsider))
        assert r.status_code == 403, r.text

    def test_revoking_an_already_revoked_mandate_is_rejected(self, client, db_session: Session):
        tenant_user, _guest, occupancy_id, _obligation_id, _admin = _make_active_tenancy_with_schedule(db_session, suffix="revoke4")
        cookies = auth_user_cookie(tenant_user)
        mandate_id = client.post("/api/users/payments/mandates", json={"occupancyId": occupancy_id}, cookies=cookies).json()["id"]
        client.delete(f"/api/users/payments/mandates/{mandate_id}", cookies=cookies)

        r = client.delete(f"/api/users/payments/mandates/{mandate_id}", cookies=cookies)
        assert r.status_code == 409, r.text


class TestProcessAutopayCharge:
    def test_admin_triggered_charge_succeeds_with_an_active_mandate(self, client, db_session: Session):
        tenant_user, _guest, occupancy_id, obligation_id, admin = _make_active_tenancy_with_schedule(db_session, suffix="charge1", amount=750.0)
        client.post("/api/users/payments/mandates", json={"occupancyId": occupancy_id}, cookies=auth_user_cookie(tenant_user))

        r = client.post(f"/api/finance/obligations/{obligation_id}/autopay-charge", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "SUCCEEDED"

        obligation = db_session.get(Obligation, obligation_id)
        assert obligation.status == "PAID"

    def test_charge_is_rejected_with_no_active_mandate(self, client, db_session: Session):
        _tenant_user, _guest, _occupancy_id, obligation_id, admin = _make_active_tenancy_with_schedule(db_session, suffix="charge2")

        r = client.post(f"/api/finance/obligations/{obligation_id}/autopay-charge", cookies=auth_admin_cookie(admin))
        assert r.status_code == 409, r.text

    def test_charge_is_rejected_once_the_mandate_is_revoked(self, client, db_session: Session):
        tenant_user, _guest, occupancy_id, obligation_id, admin = _make_active_tenancy_with_schedule(db_session, suffix="charge3")
        cookies = auth_user_cookie(tenant_user)
        mandate_id = client.post("/api/users/payments/mandates", json={"occupancyId": occupancy_id}, cookies=cookies).json()["id"]
        client.delete(f"/api/users/payments/mandates/{mandate_id}", cookies=cookies)

        r = client.post(f"/api/finance/obligations/{obligation_id}/autopay-charge", cookies=auth_admin_cookie(admin))
        assert r.status_code == 409, r.text

        obligation = db_session.get(Obligation, obligation_id)
        assert obligation.status == "PENDING"  # still due -- revoking never cancels the obligation

    def test_charge_is_idempotent_on_a_second_call(self, client, db_session: Session):
        tenant_user, _guest, occupancy_id, obligation_id, admin = _make_active_tenancy_with_schedule(db_session, suffix="charge4")
        client.post("/api/users/payments/mandates", json={"occupancyId": occupancy_id}, cookies=auth_user_cookie(tenant_user))
        admin_cookies = auth_admin_cookie(admin)

        r1 = client.post(f"/api/finance/obligations/{obligation_id}/autopay-charge", cookies=admin_cookies)
        assert r1.status_code == 200, r1.text

        r = client.post(f"/api/finance/obligations/{obligation_id}/autopay-charge", cookies=admin_cookies)
        assert r.status_code == 409, r.text  # obligation is now PAID, not PENDING
