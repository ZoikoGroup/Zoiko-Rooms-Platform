"""Authorization gap found during a full backend read-through: unlike every
sibling finance mutation (release_deposit/forfeit_deposit/run_payout/
request_refund/decide_refund, all of which call assert_provider_access or
check _owned_obligation_ids), crud.finance.confirm_payment never verified that
the acting admin actually owns the obligation being paid. A regular (non
super-admin) admin could confirm a payment against ANY provider's obligation,
marking it PAID and triggering deposit-record creation / next-rent generation
for occupancies they don't own."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.finance import Obligation
from app.models.guest import Guest
from app.models.leasing import Agreement, Application, Offer
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from tests.conftest import _make_admin, auth_admin_cookie


def _make_provider_a_obligation(db: Session) -> tuple[Obligation, "AdminUser", Guest]:  # noqa: F821
    """Admin A owns a listing (Listing.owner_id) with an active occupancy and a
    pending RENT obligation. Returns (obligation, admin_a, guest)."""
    admin_a = _make_admin(db, email="finance-provider-a@test.com", role="admin")

    owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(owner_party)
    db.flush()
    prop = Property(owner_party_id=owner_party.id, address="1 Provider A St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()

    guest = Guest(id="G-FINAUTHZ", name="Renter", email="finauthz-renter@test.com", joined_at=date.today())
    db.add(guest)
    db.flush()

    listing = Listing(
        id="L-FINAUTHZ", slug="finauthz", name="Provider A Listing", room_type="Private room",
        city="Bengaluru", location="Koramangala", price_per_night=500, guests=1,
        rating=4.5, review_count=0, owner_id=admin_a.id, room_id=room.id, state="PUBLISHED",
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

    occupancy = Occupancy(offer_id=offer.id, listing_id=listing.id, room_id=room.id, guest_id=guest.id, status="ACTIVE")
    db.add(occupancy)
    db.flush()

    obligation = Obligation(
        obligation_type="RENT", money_plane="OCCUPANCY", amount=500.0, currency="INR",
        due_date=date.today(), status="PENDING", occupancy_id=occupancy.id,
    )
    db.add(obligation)
    db.commit()

    return obligation, admin_a, guest


class TestConfirmPaymentIsProviderScoped:
    def test_plain_admin_cannot_confirm_payment_against_another_providers_obligation(
        self, client, db_session: Session
    ):
        obligation, admin_a, guest = _make_provider_a_obligation(db_session)
        # Admin B has no relationship at all to Provider A's listing/occupancy/obligation.
        admin_b = _make_admin(db_session, email="finance-provider-b@test.com", role="admin")
        admin_b_cookies = auth_admin_cookie(admin_b)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "authz-test-key-1"},
            cookies=admin_b_cookies,
        )
        assert r.status_code == 201, r.text
        payment_id = r.json()["id"]

        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_b_cookies,
        )
        assert r.status_code == 403, r.text

        db_session.refresh(obligation)
        assert obligation.status == "PENDING"  # never actually paid by the unauthorized confirm attempt

    def test_super_admin_can_confirm_payment_for_any_provider(self, client, db_session: Session):
        obligation, _admin_a, guest = _make_provider_a_obligation(db_session)
        super_admin = _make_admin(db_session, email="finance-super@test.com", role="super_admin")
        super_admin_cookies = auth_admin_cookie(super_admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "authz-test-key-2"},
            cookies=super_admin_cookies,
        )
        assert r.status_code == 201, r.text
        payment_id = r.json()["id"]

        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=super_admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "SUCCEEDED"

    def test_owning_admin_can_confirm_their_own_payment(self, client, db_session: Session):
        obligation, admin_a, guest = _make_provider_a_obligation(db_session)
        admin_a_cookies = auth_admin_cookie(admin_a)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "authz-test-key-3"},
            cookies=admin_a_cookies,
        )
        assert r.status_code == 201, r.text
        payment_id = r.json()["id"]

        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_a_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "SUCCEEDED"
