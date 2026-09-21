"""Rental Transaction Record wireframe: crud/rental_transaction_record.py's
computed, read-only composite over an existing Occupancy, and the two routes
that expose it (user_rentals.py for the renter, user_hosting.py for the
host). Fixture shape mirrors test_occupancy_crud.py:_make_signed_agreement --
built directly via ORM rows rather than the full API flow, extended with a
UserAccount for both the renter and the host so both self-service
authorization paths (get_guest_for_user / room.property.owner_party_id) can
be exercised end-to-end."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.crud import occupancy as occupancy_crud
from app.models.authority_record import AuthorityRecord
from app.models.domain_event import DomainEvent
from app.models.finance import Obligation, PaymentAllocation, SimulatedPayment
from app.models.guest import Guest
from app.models.leasing import Agreement, Application, Offer, OfferTerms
from app.models.listing import Listing
from app.models.market_release import MarketRelease
from app.models.occupancy import Occupancy
from app.models.occupancy_classification import OccupancyClassification
from app.models.party import Party
from app.models.property import Property
from app.models.property_verification import PropertyVerification
from app.models.room import Room
from app.models.sublet_request import SubletRequest
from app.models.user_account import UserAccount
from tests.conftest import _make_admin, _make_user, auth_user_cookie


def _make_rental(
    db: Session, *, host_email: str, renter_email: str, monthly_rent: float = 1000.0, term_months: int = 12,
) -> dict:
    """Builds one full rental (Property -> Room -> Listing -> Application ->
    Offer -> Agreement -> Occupancy), with a self-service host UserAccount
    owning the property's party and a self-service renter UserAccount linked
    to the tenancy's Guest -- the two authorization chains the new routes
    actually check."""
    admin = _make_admin(db, email=f"admin-for-{host_email}", role="admin")

    host_party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(host_party)
    db.flush()
    host_user = _make_user(db, email=host_email)
    host_user.party_id = host_party.id

    renter_party = Party(party_type="renter", status="active", jurisdiction="IN")
    db.add(renter_party)
    db.flush()
    renter_user = _make_user(db, email=renter_email)
    renter_user.party_id = renter_party.id
    db.flush()

    prop = Property(owner_party_id=host_party.id, address="1 Transaction Record St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()

    market_release = MarketRelease(jurisdiction=host_party.jurisdiction, status="active", min_stay_nights=30)
    db.add(market_release)
    db.add(AuthorityRecord(party_id=host_party.id, room_id=room.id, authority_type="lease", status="verified"))
    db.add(PropertyVerification(party_id=host_party.id, room_id=room.id, evidence_ref="deed.pdf", status="verified"))
    db.add(OccupancyClassification(room_id=room.id, classification="long_term_residential", review_state="APPROVED"))
    db.flush()

    listing = Listing(
        id=f"L-RTR-{room.id}", slug=f"rtr-{room.id}", name="Transaction Record Test Listing", room_type="Private room",
        city="Bengaluru", location="Koramangala", price_per_night=500, guests=1,
        rating=4.5, review_count=0, owner_id=admin.id, room_id=room.id, state="PUBLISHED",
        market_release_id=market_release.id,
    )
    db.add(listing)
    db.flush()

    guest = Guest(id=f"G-RTR-{room.id}", name="Renter", email=renter_email, joined_at=date.today(), user_account_id=renter_user.id)
    db.add(guest)
    db.flush()

    application = Application(listing_id=listing.id, guest_id=guest.id, status="DECIDED")
    db.add(application)
    db.flush()
    offer = Offer(application_id=application.id, listing_id=listing.id, guest_id=guest.id, status="ACCEPTED")
    db.add(offer)
    db.flush()
    db.add(OfferTerms(
        offer_id=offer.id, version=1, monthly_rent=monthly_rent, deposit_amount=monthly_rent,
        start_date=date.today(), term_months=term_months,
    ))
    db.flush()
    agreement = Agreement(offer_id=offer.id, status="SIGNED")
    db.add(agreement)
    db.flush()

    rent_obligation = Obligation(
        obligation_type="RENT", money_plane="OCCUPANCY", amount=monthly_rent, currency="INR",
        due_date=date.today(), status="PAID", agreement_id=agreement.id,
    )
    deposit_obligation = Obligation(
        obligation_type="DEPOSIT", money_plane="SAFEGUARDED", amount=monthly_rent, currency="INR",
        due_date=date.today(), status="PAID", agreement_id=agreement.id,
    )
    db.add(rent_obligation)
    db.add(deposit_obligation)
    db.flush()

    occupancy = Occupancy(
        offer_id=offer.id, listing_id=listing.id, room_id=room.id, guest_id=guest.id,
        status="ACTIVE", move_in_date=date.today(), expected_end_date=occupancy_crud._add_months(date.today(), term_months),
    )
    db.add(occupancy)
    db.commit()
    db.refresh(occupancy)

    return {
        "host_user": host_user, "renter_user": renter_user, "host_party": host_party, "renter_party": renter_party,
        "property": prop, "room": room, "listing": listing, "guest": guest, "application": application,
        "offer": offer, "agreement": agreement, "occupancy": occupancy,
        "rent_obligation": rent_obligation, "deposit_obligation": deposit_obligation,
    }


class TestRenterAccess:
    def test_renter_can_retrieve_own_transaction_record(self, client, db_session: Session):
        rental = _make_rental(db_session, host_email="rtr-host-1@test.com", renter_email="rtr-renter-1@test.com")

        r = client.get(
            f"/api/users/rentals/occupancies/{rental['occupancy'].id}/transaction-record",
            cookies=auth_user_cookie(rental["renter_user"]),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["occupancy"]["id"] == rental["occupancy"].id
        assert body["occupancy"]["status"] == "ACTIVE"
        assert body["application"]["id"] == rental["application"].id
        assert body["application"]["offer"]["id"] == rental["offer"].id
        assert body["application"]["offer"]["agreement"]["id"] == rental["agreement"].id
        # Renter viewing their own record sees their own identity claim.
        assert body["identityVerification"] is not None
        assert body["identityVerification"]["status"] == "not_submitted"
        # Property/authority verification for the room they're renting is a
        # legitimate, non-sensitive fact for the renter to see.
        assert body["propertyVerification"]["status"] == "verified"
        assert body["authorityToList"]["status"] == "verified"

    def test_renter_cannot_retrieve_another_renters_occupancy(self, client, db_session: Session):
        rental = _make_rental(db_session, host_email="rtr-host-2@test.com", renter_email="rtr-renter-2@test.com")
        outsider = _make_user(db_session, email="rtr-outsider-2@test.com")
        db_session.commit()

        r = client.get(
            f"/api/users/rentals/occupancies/{rental['occupancy'].id}/transaction-record",
            cookies=auth_user_cookie(outsider),
        )
        assert r.status_code == 403, r.text

    def test_unauthenticated_request_is_rejected(self, client, db_session: Session):
        rental = _make_rental(db_session, host_email="rtr-host-3@test.com", renter_email="rtr-renter-3@test.com")
        r = client.get(f"/api/users/rentals/occupancies/{rental['occupancy'].id}/transaction-record")
        assert r.status_code == 401, r.text

    def test_unknown_occupancy_is_404(self, client, db_session: Session):
        rental = _make_rental(db_session, host_email="rtr-host-4@test.com", renter_email="rtr-renter-4@test.com")
        r = client.get(
            "/api/users/rentals/occupancies/999999/transaction-record", cookies=auth_user_cookie(rental["renter_user"]),
        )
        assert r.status_code == 404, r.text

    def test_sparse_rental_with_no_payment_sublet_amendment_data_does_not_fail(self, client, db_session: Session):
        """No SimulatedPayment/PaymentAllocation, no SubletRequest, no
        AgreementAmendment, no deposit record, no termination record --
        every list section must come back empty rather than erroring."""
        rental = _make_rental(db_session, host_email="rtr-host-5@test.com", renter_email="rtr-renter-5@test.com")

        r = client.get(
            f"/api/users/rentals/occupancies/{rental['occupancy'].id}/transaction-record",
            cookies=auth_user_cookie(rental["renter_user"]),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["payments"] == []
        assert body["subletRequests"] == []
        assert body["amendments"] == []
        assert body["deposit"] is None
        assert body["terminationRecord"] is None
        assert body["terminationCases"] == []
        assert body["handoverEvents"] == []
        assert body["activationDecisions"] == []
        # The two pre-occupancy, agreement-scoped obligations still show up.
        assert len(body["obligations"]) == 2


class TestHostAccess:
    def test_host_can_retrieve_transaction_record_for_own_room(self, client, db_session: Session):
        rental = _make_rental(db_session, host_email="rtr-host-6@test.com", renter_email="rtr-renter-6@test.com")

        r = client.get(
            f"/api/users/hosting/occupancies/{rental['occupancy'].id}/transaction-record",
            cookies=auth_user_cookie(rental["host_user"]),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["occupancy"]["id"] == rental["occupancy"].id
        assert len(body["obligations"]) == 2

    def test_host_cannot_retrieve_another_hosts_transaction_record(self, client, db_session: Session):
        rental = _make_rental(db_session, host_email="rtr-host-7@test.com", renter_email="rtr-renter-7@test.com")
        other_host_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(other_host_party)
        db_session.commit()
        outsider_host = _make_user(db_session, email="rtr-outsider-host-7@test.com")
        outsider_host.party_id = other_host_party.id
        db_session.commit()

        r = client.get(
            f"/api/users/hosting/occupancies/{rental['occupancy'].id}/transaction-record",
            cookies=auth_user_cookie(outsider_host),
        )
        assert r.status_code == 403, r.text

    def test_host_with_no_party_is_denied(self, client, db_session: Session):
        rental = _make_rental(db_session, host_email="rtr-host-8@test.com", renter_email="rtr-renter-8@test.com")
        no_party_user = _make_user(db_session, email="rtr-no-party-8@test.com")
        db_session.commit()

        r = client.get(
            f"/api/users/hosting/occupancies/{rental['occupancy'].id}/transaction-record",
            cookies=auth_user_cookie(no_party_user),
        )
        assert r.status_code == 403, r.text

    def test_host_view_never_includes_renter_identity_verification(self, client, db_session: Session):
        """Critical privacy boundary: even though the renter has identity
        verification state, the host-facing route must never surface it."""
        rental = _make_rental(db_session, host_email="rtr-host-9@test.com", renter_email="rtr-renter-9@test.com")

        r = client.get(
            f"/api/users/hosting/occupancies/{rental['occupancy'].id}/transaction-record",
            cookies=auth_user_cookie(rental["host_user"]),
        )
        assert r.status_code == 200, r.text
        assert r.json()["identityVerification"] is None
        # Property/authority verification (about the room, not the renter)
        # remains visible -- the host already knows and submitted this.
        assert r.json()["propertyVerification"]["status"] == "verified"

    def test_unauthenticated_request_is_rejected(self, client, db_session: Session):
        rental = _make_rental(db_session, host_email="rtr-host-15@test.com", renter_email="rtr-renter-15@test.com")
        r = client.get(f"/api/users/hosting/occupancies/{rental['occupancy'].id}/transaction-record")
        assert r.status_code == 401, r.text

    def test_unknown_occupancy_is_404(self, client, db_session: Session):
        rental = _make_rental(db_session, host_email="rtr-host-16@test.com", renter_email="rtr-renter-16@test.com")
        r = client.get(
            "/api/users/hosting/occupancies/999999/transaction-record", cookies=auth_user_cookie(rental["host_user"]),
        )
        assert r.status_code == 404, r.text

    def test_sparse_rental_works_for_host_viewer_too(self, client, db_session: Session):
        """Same sparse-data guarantee as the renter route -- no payments,
        sublets, amendments, deposit or termination records -- verified
        separately for the host endpoint since it builds the record with a
        different include_identity flag."""
        rental = _make_rental(db_session, host_email="rtr-host-17@test.com", renter_email="rtr-renter-17@test.com")

        r = client.get(
            f"/api/users/hosting/occupancies/{rental['occupancy'].id}/transaction-record",
            cookies=auth_user_cookie(rental["host_user"]),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["payments"] == []
        assert body["subletRequests"] == []
        assert body["amendments"] == []
        assert body["deposit"] is None
        assert body["terminationRecord"] is None
        assert body["identityVerification"] is None
        assert len(body["obligations"]) == 2


class TestHostOccupancyListing:
    """The host's actual entry point into the Rental Transaction Record --
    GET /api/users/hosting/rooms/{room_id}/occupancies -- lets a host
    discover which occupancy ids exist for a room they own, so the UI never
    has to be handed an occupancy id out of band."""

    def test_host_can_list_occupancies_for_own_room(self, client, db_session: Session):
        rental = _make_rental(db_session, host_email="rtr-host-18@test.com", renter_email="rtr-renter-18@test.com")

        r = client.get(
            f"/api/users/hosting/rooms/{rental['room'].id}/occupancies", cookies=auth_user_cookie(rental["host_user"]),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body) == 1
        assert body[0]["id"] == rental["occupancy"].id
        assert body[0]["status"] == "ACTIVE"
        assert body[0]["guestName"] == "Renter"

    def test_host_cannot_list_occupancies_for_another_hosts_room(self, client, db_session: Session):
        rental = _make_rental(db_session, host_email="rtr-host-19@test.com", renter_email="rtr-renter-19@test.com")
        other_host_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(other_host_party)
        db_session.commit()
        outsider_host = _make_user(db_session, email="rtr-outsider-host-19@test.com")
        outsider_host.party_id = other_host_party.id
        db_session.commit()

        r = client.get(
            f"/api/users/hosting/rooms/{rental['room'].id}/occupancies", cookies=auth_user_cookie(outsider_host),
        )
        assert r.status_code == 403, r.text

    def test_unauthenticated_request_is_rejected(self, client, db_session: Session):
        rental = _make_rental(db_session, host_email="rtr-host-20@test.com", renter_email="rtr-renter-20@test.com")
        r = client.get(f"/api/users/hosting/rooms/{rental['room'].id}/occupancies")
        assert r.status_code == 401, r.text

    def test_unknown_room_is_404(self, client, db_session: Session):
        rental = _make_rental(db_session, host_email="rtr-host-21@test.com", renter_email="rtr-renter-21@test.com")
        r = client.get("/api/users/hosting/rooms/999999/occupancies", cookies=auth_user_cookie(rental["host_user"]))
        assert r.status_code == 404, r.text


class TestDataCorrectness:
    def test_calling_endpoint_creates_no_new_rows(self, client, db_session: Session):
        rental = _make_rental(db_session, host_email="rtr-host-10@test.com", renter_email="rtr-renter-10@test.com")
        occupancy_count_before = db_session.query(Occupancy).count()
        obligation_count_before = db_session.query(Obligation).count()
        agreement_count_before = db_session.query(Agreement).count()

        for _ in range(2):
            r = client.get(
                f"/api/users/rentals/occupancies/{rental['occupancy'].id}/transaction-record",
                cookies=auth_user_cookie(rental["renter_user"]),
            )
            assert r.status_code == 200, r.text

        assert db_session.query(Occupancy).count() == occupancy_count_before
        assert db_session.query(Obligation).count() == obligation_count_before
        assert db_session.query(Agreement).count() == agreement_count_before

    def test_obligations_include_pre_occupancy_agreement_scoped_rows(self, db_session: Session):
        """The initial RENT+DEPOSIT obligations are created with agreement_id
        set (before Occupancy exists) rather than occupancy_id -- the read
        model must include them, not just occupancy_id-scoped rows."""
        from app.crud.rental_transaction_record import build_rental_transaction_record

        rental = _make_rental(db_session, host_email="rtr-host-11@test.com", renter_email="rtr-renter-11@test.com")
        assert rental["rent_obligation"].occupancy_id is None
        assert rental["rent_obligation"].agreement_id == rental["agreement"].id

        record = build_rental_transaction_record(db_session, rental["occupancy"])
        obligation_ids = {o.id for o in record.obligations}
        assert rental["rent_obligation"].id in obligation_ids
        assert rental["deposit_obligation"].id in obligation_ids

    def test_payment_is_linked_via_allocation(self, client, db_session: Session):
        rental = _make_rental(db_session, host_email="rtr-host-12@test.com", renter_email="rtr-renter-12@test.com")
        payment = SimulatedPayment(
            guest_id=rental["guest"].id, amount=1000.0, currency="INR",
            idempotency_key="rtr-test-payment-12", status="SUCCEEDED", confirmed_at=datetime.now(timezone.utc),
        )
        db_session.add(payment)
        db_session.flush()
        db_session.add(PaymentAllocation(
            payment_id=payment.id, obligation_id=rental["rent_obligation"].id, amount_allocated=1000.0,
        ))
        db_session.commit()

        r = client.get(
            f"/api/users/rentals/occupancies/{rental['occupancy'].id}/transaction-record",
            cookies=auth_user_cookie(rental["renter_user"]),
        )
        assert r.status_code == 200, r.text
        payment_ids = {p["id"] for p in r.json()["payments"]}
        assert payment.id in payment_ids

    def test_sublet_request_for_this_occupancy_is_included(self, client, db_session: Session):
        rental = _make_rental(db_session, host_email="rtr-host-13@test.com", renter_email="rtr-renter-13@test.com")
        proposed_party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(proposed_party)
        db_session.commit()
        db_session.add(SubletRequest(
            current_occupancy_id=rental["occupancy"].id, proposed_renter_party_id=proposed_party.id,
            status="pending_admin_review", requested_by_guest_id=rental["guest"].id,
        ))
        db_session.commit()

        r = client.get(
            f"/api/users/rentals/occupancies/{rental['occupancy'].id}/transaction-record",
            cookies=auth_user_cookie(rental["renter_user"]),
        )
        assert r.status_code == 200, r.text
        assert len(r.json()["subletRequests"]) == 1
        assert r.json()["subletRequests"][0]["currentOccupancyId"] == rental["occupancy"].id

    def test_timeline_entries_are_chronologically_ordered(self, client, db_session: Session):
        rental = _make_rental(db_session, host_email="rtr-host-14@test.com", renter_email="rtr-renter-14@test.com")
        now = datetime.now(timezone.utc)
        # Inserted out of chronological order on purpose.
        db_session.add(DomainEvent(
            event_type="occupancy.active", resource_type="occupancy", resource_id=str(rental["occupancy"].id),
            occurred_at=now, payload={},
        ))
        db_session.add(DomainEvent(
            event_type="agreement.created", resource_type="agreement", resource_id=str(rental["agreement"].id),
            occurred_at=now - timedelta(days=10), payload={},
        ))
        db_session.add(DomainEvent(
            event_type="offer.created", resource_type="offer", resource_id=str(rental["offer"].id),
            occurred_at=now - timedelta(days=15), payload={},
        ))
        db_session.commit()

        r = client.get(
            f"/api/users/rentals/occupancies/{rental['occupancy'].id}/transaction-record",
            cookies=auth_user_cookie(rental["renter_user"]),
        )
        assert r.status_code == 200, r.text
        timeline = r.json()["timeline"]
        event_types = [e["eventType"] for e in timeline]
        assert event_types.index("offer.created") < event_types.index("agreement.created") < event_types.index("occupancy.active")
        timestamps = [e["timestamp"] for e in timeline]
        assert timestamps == sorted(timestamps)
