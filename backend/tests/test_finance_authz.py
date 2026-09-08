"""Coverage for a real cross-tenant authorization gap found while comparing
this codebase against a teammate's branch: a regular (non-super) admin could
request/decide refunds and open/resolve disputes against *any* provider's
obligations/occupancies, not just their own -- _owned_obligation_ids and
_owned_occupancy_ids were already computed elsewhere in this file but never
actually checked on these four actions. Also covers the accompanying
idempotency-key-collision fix for create_payment_intent.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.finance import OBLIGATION_TYPE_TO_PLANE, Obligation, SimulatedPayment
from app.models.guest import Guest
from app.models.leasing import Agreement, Application, Offer
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.room import Room
from app.models.party import Party
from app.models.property import Property
from tests.conftest import _make_admin, auth_admin_cookie


def _make_owned_obligation(db: Session, owner_admin_id: int):
    """A RENT obligation reachable through an admin-owned (owner_id, not
    party-based) listing's occupancy -- the shape _owned_obligation_ids
    actually scopes against. Returns (obligation, payment)."""
    party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()
    prop = Property(owner_party_id=party.id, address="1 Authz St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()

    guest = Guest(id="G-AUTHZ", name="Authz Guest", email="authz-guest@test.com", joined_at=date.today())
    db.add(guest)
    db.flush()

    listing = Listing(
        id="L-AUTHZTEST", slug="authztest", name="Authz Test Listing", room_type="Private room",
        city="Bengaluru", location="Indiranagar", price_per_night=500, guests=1,
        rating=0.0, review_count=0, party_id=None, owner_id=owner_admin_id, room_id=room.id,
        state="PUBLISHED",
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
        obligation_type="RENT", money_plane=OBLIGATION_TYPE_TO_PLANE["RENT"], amount=15000,
        due_date=date.today(), occupancy_id=occupancy.id,
    )
    db.add(obligation)
    db.flush()

    payment = SimulatedPayment(guest_id=guest.id, amount=15000, currency="INR", idempotency_key=f"authz-{obligation.id}", status="SUCCEEDED")
    db.add(payment)
    db.commit()
    return obligation, payment, occupancy


class TestRefundAuthorization:
    def test_owning_admin_can_request_refund(self, client, db_session: Session):
        owner = _make_admin(db_session, email="authz-owner1@test.com", role="admin")
        db_session.commit()
        obligation, payment, _occ = _make_owned_obligation(db_session, owner.id)

        resp = client.post(
            "/api/finance/refunds",
            json={"paymentId": payment.id, "obligationId": obligation.id, "amount": 100, "reason": "test"},
            cookies=auth_admin_cookie(owner),
        )
        assert resp.status_code == 201, resp.text

    def test_other_admin_cannot_request_refund(self, client, db_session: Session):
        owner = _make_admin(db_session, email="authz-owner2@test.com", role="admin")
        stranger = _make_admin(db_session, email="authz-stranger1@test.com", role="admin")
        db_session.commit()
        obligation, payment, _occ = _make_owned_obligation(db_session, owner.id)

        resp = client.post(
            "/api/finance/refunds",
            json={"paymentId": payment.id, "obligationId": obligation.id, "amount": 100, "reason": "test"},
            cookies=auth_admin_cookie(stranger),
        )
        assert resp.status_code == 403

    def test_super_admin_can_request_refund_for_anyone(self, client, db_session: Session):
        owner = _make_admin(db_session, email="authz-owner3@test.com", role="admin")
        super_admin = _make_admin(db_session, email="authz-super1@test.com", role="super_admin")
        db_session.commit()
        obligation, payment, _occ = _make_owned_obligation(db_session, owner.id)

        resp = client.post(
            "/api/finance/refunds",
            json={"paymentId": payment.id, "obligationId": obligation.id, "amount": 100, "reason": "test"},
            cookies=auth_admin_cookie(super_admin),
        )
        assert resp.status_code == 201

    def test_other_admin_cannot_decide_a_refund(self, client, db_session: Session):
        owner = _make_admin(db_session, email="authz-owner4@test.com", role="admin")
        stranger = _make_admin(db_session, email="authz-stranger2@test.com", role="admin")
        db_session.commit()
        obligation, payment, _occ = _make_owned_obligation(db_session, owner.id)

        created = client.post(
            "/api/finance/refunds",
            json={"paymentId": payment.id, "obligationId": obligation.id, "amount": 100, "reason": "test"},
            cookies=auth_admin_cookie(owner),
        ).json()

        resp = client.post(
            f"/api/finance/refunds/{created['id']}/decide",
            json={"approve": True},
            cookies=auth_admin_cookie(stranger),
        )
        assert resp.status_code == 403


class TestDisputeAuthorization:
    def test_other_admin_cannot_open_dispute_on_someone_elses_occupancy(self, client, db_session: Session):
        owner = _make_admin(db_session, email="authz-owner5@test.com", role="admin")
        stranger = _make_admin(db_session, email="authz-stranger3@test.com", role="admin")
        db_session.commit()
        _obligation, payment, occupancy = _make_owned_obligation(db_session, owner.id)

        resp = client.post(
            "/api/finance/disputes",
            json={"paymentId": payment.id, "occupancyId": occupancy.id, "category": "billing", "description": "test"},
            cookies=auth_admin_cookie(stranger),
        )
        assert resp.status_code == 403

    def test_owning_admin_can_open_dispute(self, client, db_session: Session):
        owner = _make_admin(db_session, email="authz-owner6@test.com", role="admin")
        db_session.commit()
        _obligation, payment, occupancy = _make_owned_obligation(db_session, owner.id)

        resp = client.post(
            "/api/finance/disputes",
            json={"paymentId": payment.id, "occupancyId": occupancy.id, "category": "billing", "description": "test"},
            cookies=auth_admin_cookie(owner),
        )
        assert resp.status_code == 201

    def test_other_admin_cannot_resolve_dispute(self, client, db_session: Session):
        owner = _make_admin(db_session, email="authz-owner7@test.com", role="admin")
        stranger = _make_admin(db_session, email="authz-stranger4@test.com", role="admin")
        db_session.commit()
        _obligation, payment, occupancy = _make_owned_obligation(db_session, owner.id)

        created = client.post(
            "/api/finance/disputes",
            json={"paymentId": payment.id, "occupancyId": occupancy.id, "category": "billing", "description": "test"},
            cookies=auth_admin_cookie(owner),
        ).json()

        resp = client.post(
            f"/api/finance/disputes/{created['id']}/resolve",
            json={"status": "RESOLVED", "resolutionNotes": "n/a"},
            cookies=auth_admin_cookie(stranger),
        )
        assert resp.status_code == 403


class TestIdempotencyKeyCollision:
    def test_reusing_key_for_a_different_request_is_rejected(self, client, db_session: Session):
        admin = _make_admin(db_session, email="authz-idempotency@test.com", role="super_admin")
        guest = Guest(id="G-IDEM", name="Idem Guest", email="idem@test.com", joined_at=date.today())
        db_session.add(guest)
        db_session.commit()

        first = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "shared-key"},
            cookies=auth_admin_cookie(admin),
        )
        assert first.status_code == 201

        second = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 999.0, "currency": "INR", "idempotencyKey": "shared-key"},
            cookies=auth_admin_cookie(admin),
        )
        assert second.status_code == 409

    def test_reusing_key_for_the_same_request_is_a_true_retry(self, client, db_session: Session):
        admin = _make_admin(db_session, email="authz-idempotency2@test.com", role="super_admin")
        guest = Guest(id="G-IDEM2", name="Idem Guest 2", email="idem2@test.com", joined_at=date.today())
        db_session.add(guest)
        db_session.commit()

        first = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "same-key"},
            cookies=auth_admin_cookie(admin),
        )
        assert first.status_code == 201

        second = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "same-key"},
            cookies=auth_admin_cookie(admin),
        )
        assert second.status_code == 201
        assert second.json()["id"] == first.json()["id"]
