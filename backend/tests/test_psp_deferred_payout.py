"""ZR-ENG-CLR-005 Section 9.1 + ZR-ENG-CLR-012 Section 7: PSP_DEFERRED_PAYOUT
is now a real, executable funds-flow profile (app/models/market_policy.py's
SUPPORTED_FUNDS_FLOW_PROFILES), not just a name in the taxonomy that fails
closed. A market pack resolving to it makes run_payout (app/crud/finance.py)
do two things the DIRECT_SETTLEMENT path never had to:

1. Gate on a completed real Stripe Connect account (payouts_enabled) instead
   of the legacy bank-details PayoutBeneficiary -- this IS the
   PAYMENT_ONBOARDING requirement type the docs name and no code ever wired.
2. Defer any obligation whose occupancy hasn't reached ACTIVE (move-in
   confirmed) yet -- money stays parked rather than reaching the host before
   the thing it's paying for has actually happened.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.models.authority_record import AuthorityRecord
from app.models.leasing import Agreement, Application, Offer
from app.models.listing import Listing
from app.models.market_policy import MarketPolicyPack
from app.models.finance import Obligation
from app.models.guest import Guest
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from tests.conftest import _make_admin, auth_admin_cookie
from tests.test_ledger import _make_provider_rent_obligation


def _set_india_profile_psp_deferred(db: Session) -> None:
    policy = db.query(MarketPolicyPack).filter_by(jurisdiction_code="IN").one()
    policy.funds_flow_profile = "PSP_DEFERRED_PAYOUT"
    db.commit()


def _make_pending_move_in_rent_obligation(db: Session, *, suffix: str, amount: float = 1000.0):
    """Same shape as test_ledger._make_provider_rent_obligation, except the
    Occupancy is left PENDING_MOVE_IN (move-in never confirmed) instead of
    ACTIVE -- to exercise PSP_DEFERRED_PAYOUT's deferral gate specifically."""
    admin = _make_admin(db, email=f"psp-admin-{suffix}@test.com", role="super_admin")
    owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(owner_party)
    db.flush()

    prop = Property(owner_party_id=owner_party.id, address=f"{suffix} PSP St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()
    db.add(AuthorityRecord(party_id=owner_party.id, room_id=room.id, authority_type="lease_agreement", status="verified"))

    guest = Guest(id=f"G-PSP-{suffix}", name="Renter", email=f"psp-renter-{suffix}@test.com", joined_at=date.today())
    db.add(guest)
    db.flush()

    listing = Listing(
        id=f"L-PSP-{suffix}", slug=f"psp-{suffix}", name="PSP Test Listing", room_type="Private room",
        city="Bengaluru", location="Koramangala", price_per_night=500, guests=1,
        rating=4.5, review_count=0, party_id=owner_party.id, owner_id=None, room_id=room.id, state="PUBLISHED",
    )
    db.add(listing)
    db.flush()

    application = Application(listing_id=listing.id, guest_id=guest.id, status="DECIDED")
    db.add(application)
    db.flush()
    offer = Offer(application_id=application.id, listing_id=listing.id, guest_id=guest.id, status="ACCEPTED")
    db.add(offer)
    db.flush()
    agreement = Agreement(offer_id=offer.id, status="SIGNED")
    db.add(agreement)
    db.flush()

    occupancy = Occupancy(offer_id=offer.id, listing_id=listing.id, room_id=room.id, guest_id=guest.id, status="PENDING_MOVE_IN")
    db.add(occupancy)
    db.flush()

    obligation = Obligation(
        obligation_type="RENT", money_plane="OCCUPANCY", amount=amount, currency="INR",
        due_date=date.today(), status="PENDING", occupancy_id=occupancy.id,
    )
    db.add(obligation)
    db.commit()

    return obligation, admin, guest, owner_party.id, occupancy.id


class TestPaymentOnboardingGate:
    def test_held_with_no_stripe_account(self, client, db_session: Session):
        _set_india_profile_psp_deferred(db_session)
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="psp-onb1", amount=1000.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 1000.0, "currency": "INR", "idempotencyKey": "psp-onb1"},
            cookies=admin_cookies,
        )
        payment_id = r.json()["id"]
        client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 1000.0}]},
            cookies=admin_cookies,
        )

        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "HELD"
        assert "PAYMENT_ONBOARDING" in body["holdReason"]
        assert body["stripeTransferId"] is None

    def test_paid_once_stripe_connect_onboarding_is_complete(self, client, db_session: Session):
        _set_india_profile_psp_deferred(db_session)
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="psp-onb2", amount=1000.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/host-stripe-accounts",
            json={"partyId": party_id, "country": "GB", "email": "host@example.com"},
            cookies=admin_cookies,
        )
        account_id = r.json()["id"]
        client.post(f"/api/finance/host-stripe-accounts/{account_id}/simulate-onboarding-complete", cookies=admin_cookies)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 1000.0, "currency": "INR", "idempotencyKey": "psp-onb2"},
            cookies=admin_cookies,
        )
        payment_id = r.json()["id"]
        client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 1000.0}]},
            cookies=admin_cookies,
        )

        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "PAID"
        assert body["stripeTransferId"]
        assert body["stripeTransferId"].startswith("TR-")


class TestDeferredUntilMoveIn:
    def test_held_and_deferred_while_occupancy_is_still_pending_move_in(self, client, db_session: Session):
        _set_india_profile_psp_deferred(db_session)
        obligation, admin, guest, party_id, occupancy_id = _make_pending_move_in_rent_obligation(
            db_session, suffix="psp-def1", amount=1000.0,
        )
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/host-stripe-accounts",
            json={"partyId": party_id, "country": "GB", "email": "host@example.com"},
            cookies=admin_cookies,
        )
        account_id = r.json()["id"]
        client.post(f"/api/finance/host-stripe-accounts/{account_id}/simulate-onboarding-complete", cookies=admin_cookies)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 1000.0, "currency": "INR", "idempotencyKey": "psp-def1"},
            cookies=admin_cookies,
        )
        payment_id = r.json()["id"]
        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 1000.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        # Even with Stripe onboarding complete, a run right now must defer --
        # the occupancy hasn't moved in yet, so the money should not reach
        # the host.
        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "HELD"
        assert "move-in" in body["holdReason"].lower()
        assert body["stripeTransferId"] is None
        db_session.refresh(obligation)
        assert obligation.payout_id is None

        # Once move-in is confirmed (occupancy reaches ACTIVE), the same
        # obligation -- untouched by the HELD run above -- is eligible again.
        # A HELD run permanently consumes its period_key (existing, pre-existing
        # run_payout behavior unrelated to this feature), so this uses a fresh
        # period_key to prove the obligation itself is no longer deferred, not
        # to re-resolve the earlier HELD row.
        occupancy = db_session.get(Occupancy, occupancy_id)
        occupancy.status = "ACTIVE"
        occupancy.move_in_date = date.today()
        db_session.commit()

        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-10"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "PAID"
        assert body["stripeTransferId"]
