"""ZR-ENG-CLR-011 Section 10/ZR-ENG-CLR-004 Section 4.3: confirming move-in
is a Host commercial action, not an admin-portal-only one -- previously
POST /api/occupancy/agreements/{id}/confirm-move-in was reachable ONLY
through the internal Zoiko admin console (api/routes/occupancy.py), with no
way for a real self-service Host to activate their own tenancy without
Zoiko staff doing it for them. Covers the new host-facing counterpart:
GET/POST /api/users/hosting/occupancies/{id}/move-in-eligibility,
confirm-move-in, plus the two handover steps (prepare/possession-delivered)
that gate it."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.crud import rental_payment as rp_crud
from app.crud.guest import get_guest_for_user
from app.models.leasing import Agreement
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.rental_payment import RentalPaymentObligation
from tests.conftest import _make_user, auth_user_cookie
from tests.test_host_offer_agreement_decisions import (
    _host_deliver_all_disclosures,
    _make_agreement_eligible,
    _submit_and_approve_application,
)
from tests.test_self_listing_restriction import _make_host_and_listing, _make_verified_renter


def _drive_agreement_to_signed(client, db_session: Session, *, host_email: str, renter_email: str):
    """Runs the full self-service pipeline (same steps as
    test_host_offer_agreement_decisions.py's own end-to-end test) all the
    way past both signatures, then pays through the new non-custodial rail
    (same as test_rental_payment_legacy_bridge.py's own _confirm_via_new_rail)
    to reach SIGNED with a PENDING_MOVE_IN occupancy -- the real precondition
    confirm-move-in needs, driven the same way a real host+renter pair would
    reach it. Returns (host, renter, host_cookies, renter_cookies,
    occupancy_id)."""
    host, listing_id = _make_host_and_listing(db_session, email=host_email)
    renter = _make_verified_renter(db_session, email=renter_email)
    host_cookies = auth_user_cookie(host)
    renter_cookies = auth_user_cookie(renter)

    application_id = _submit_and_approve_application(client, host, renter, listing_id)
    r = client.post(f"/api/users/hosting/applications/{application_id}/offers", cookies=host_cookies)
    offer_id = r.json()["id"]
    r = client.post(
        f"/api/users/hosting/offers/{offer_id}/terms",
        # Lease start_date is TODAY, not a future date like most of this
        # suite's own fixtures use -- confirm_move_in's own eligibility
        # check (is_agreement_effective) requires the lease term to have
        # actually started, which this test needs to reach ACTIVE.
        json={"monthlyRent": 500, "depositAmount": 500, "startDate": date.today().isoformat(), "termMonths": 6},
        cookies=host_cookies,
    )
    assert r.status_code == 200, r.text
    assert client.post(f"/api/users/hosting/offers/{offer_id}/send", cookies=host_cookies).status_code == 200
    assert client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=renter_cookies).status_code == 200

    _make_agreement_eligible(db_session, listing_id)
    r = client.post(f"/api/users/hosting/offers/{offer_id}/agreement", cookies=host_cookies)
    agreement_id = r.json()["id"]
    assert client.post(f"/api/users/hosting/agreements/{agreement_id}/send", cookies=host_cookies).status_code == 200
    _host_deliver_all_disclosures(client, host_cookies, agreement_id)
    assert client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=renter_cookies).status_code == 200
    r = client.post(f"/api/users/hosting/agreements/{agreement_id}/sign", cookies=host_cookies)
    assert r.json()["status"] == "PAYMENT_IN_PROGRESS"

    guest = get_guest_for_user(db_session, renter)
    rp_obligations = (
        db_session.query(RentalPaymentObligation).filter(RentalPaymentObligation.agreement_id == agreement_id).all()
    )
    recipient = db_session.get(Party, rp_obligations[0].recipient_party_id)
    for obligation in rp_obligations:
        record = rp_crud.mark_paid(
            db_session, guest, obligation, amount=float(obligation.amount), currency=obligation.currency,
            declared_date=date.today(), payment_method_category="BANK_TRANSFER",
        )
        rp_crud.confirm_receipt(db_session, recipient, record)

    agreement = db_session.get(Agreement, agreement_id)
    occupancy_id = db_session.query(Occupancy).filter(Occupancy.offer_id == agreement.offer_id).one().id

    # The activation gate (evaluate_activation_gate) requires all three
    # handover events before ACTIVATE -- two host-side (now self-service
    # too, see post_prepare_handover_as_host) and one renter-side (already
    # self-service via /api/users/rentals/occupancies/.../handover/receipt).
    assert client.post(
        f"/api/users/hosting/occupancies/{occupancy_id}/handover/prepare", json={}, cookies=host_cookies,
    ).status_code == 200
    assert client.post(
        f"/api/users/hosting/occupancies/{occupancy_id}/handover/possession-delivered", json={}, cookies=host_cookies,
    ).status_code == 200
    assert client.post(
        f"/api/users/rentals/occupancies/{occupancy_id}/handover/receipt", json={}, cookies=renter_cookies,
    ).status_code == 200

    return host, renter, host_cookies, renter_cookies, occupancy_id


class TestHostSelfServiceMoveInConfirmation:
    def test_the_owning_host_can_confirm_move_in_themselves(self, client, db_session: Session):
        host, _renter, host_cookies, _renter_cookies, occupancy_id = _drive_agreement_to_signed(
            client, db_session, host_email="movein-host1@test.com", renter_email="movein-renter1@test.com",
        )

        r = client.get(f"/api/users/hosting/occupancies/{occupancy_id}/move-in-eligibility", cookies=host_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["eligible"] is True

        r = client.post(f"/api/users/hosting/occupancies/{occupancy_id}/confirm-move-in", cookies=host_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ACTIVE"

    def test_a_different_hosts_account_cannot_confirm_move_in(self, client, db_session: Session):
        _host, _renter, _host_cookies, _renter_cookies, occupancy_id = _drive_agreement_to_signed(
            client, db_session, host_email="movein-host2@test.com", renter_email="movein-renter2@test.com",
        )
        outsider_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(outsider_party)
        db_session.flush()
        outsider_host = _make_user(db_session, email="movein-host2-outsider@test.com")
        outsider_host.party_id = outsider_party.id
        db_session.commit()
        outsider_cookies = auth_user_cookie(outsider_host)

        r = client.get(f"/api/users/hosting/occupancies/{occupancy_id}/move-in-eligibility", cookies=outsider_cookies)
        assert r.status_code == 403, r.text

        r = client.post(f"/api/users/hosting/occupancies/{occupancy_id}/confirm-move-in", cookies=outsider_cookies)
        assert r.status_code == 403, r.text

    def test_the_renter_cannot_confirm_their_own_move_in(self, client, db_session: Session):
        _host, _renter, _host_cookies, renter_cookies, occupancy_id = _drive_agreement_to_signed(
            client, db_session, host_email="movein-host3@test.com", renter_email="movein-renter3@test.com",
        )

        r = client.post(f"/api/users/hosting/occupancies/{occupancy_id}/confirm-move-in", cookies=renter_cookies)
        assert r.status_code == 403, r.text

    def test_confirming_twice_is_rejected(self, client, db_session: Session):
        _host, _renter, host_cookies, _renter_cookies, occupancy_id = _drive_agreement_to_signed(
            client, db_session, host_email="movein-host4@test.com", renter_email="movein-renter4@test.com",
        )
        assert client.post(f"/api/users/hosting/occupancies/{occupancy_id}/confirm-move-in", cookies=host_cookies).status_code == 200
        r = client.post(f"/api/users/hosting/occupancies/{occupancy_id}/confirm-move-in", cookies=host_cookies)
        assert r.status_code == 409, r.text

    def test_a_different_hosts_account_cannot_record_handover_events_either(self, client, db_session: Session):
        """The two host-side handover steps (prepare/possession-delivered)
        need the same ownership check as confirm-move-in itself -- without
        it, any host could plant fake evidence on someone else's tenancy."""
        _host, _renter, _host_cookies, _renter_cookies, occupancy_id = _drive_agreement_to_signed(
            client, db_session, host_email="movein-host5@test.com", renter_email="movein-renter5@test.com",
        )

        outsider_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(outsider_party)
        db_session.flush()
        outsider_host = _make_user(db_session, email="movein-host5-outsider@test.com")
        outsider_host.party_id = outsider_party.id
        db_session.commit()
        outsider_cookies = auth_user_cookie(outsider_host)

        r = client.post(f"/api/users/hosting/occupancies/{occupancy_id}/handover/prepare", json={}, cookies=outsider_cookies)
        assert r.status_code == 403, r.text
        r = client.post(f"/api/users/hosting/occupancies/{occupancy_id}/handover/possession-delivered", json={}, cookies=outsider_cookies)
        assert r.status_code == 403, r.text


class TestRenterHandoverReceipt:
    def test_renter_cannot_confirm_receipt_before_host_delivers_possession(self, client, db_session: Session):
        from app.models.occupancy_activation import OccupancyHandoverEvent

        _host, _renter, _host_cookies, renter_cookies, occupancy_id = _drive_agreement_to_signed(
            client, db_session, host_email="receipt-host1@test.com", renter_email="receipt-renter1@test.com",
        )
        # Rewind to "host prepared the handover but hasn't delivered yet".
        db_session.query(OccupancyHandoverEvent).filter(
            OccupancyHandoverEvent.occupancy_id == occupancy_id,
            OccupancyHandoverEvent.event_type.in_(("POSSESSION_DELIVERED", "RENTER_RECEIPT")),
        ).delete(synchronize_session=False)
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy_id}/handover/receipt", json={}, cookies=renter_cookies,
        )
        assert r.status_code == 409, r.text

    def test_renter_occupancy_lists_recorded_handover_steps(self, client, db_session: Session):
        _host, _renter, _host_cookies, renter_cookies, occupancy_id = _drive_agreement_to_signed(
            client, db_session, host_email="receipt-host2@test.com", renter_email="receipt-renter2@test.com",
        )

        r = client.get("/api/users/rentals/occupancies", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        occupancy = next(o for o in r.json() if o["id"] == occupancy_id)
        assert occupancy["handoverEventTypes"] == ["HANDOVER_READY", "POSSESSION_DELIVERED", "RENTER_RECEIPT"]


class TestRenterPaymentRecipientReview:
    def test_renter_review_is_recorded_once_and_returned_on_their_occupancy(self, client, db_session: Session):
        _host, _renter, _host_cookies, renter_cookies, occupancy_id = _drive_agreement_to_signed(
            client, db_session, host_email="recip-host1@test.com", renter_email="recip-renter1@test.com",
        )
        url = f"/api/users/rentals/occupancies/{occupancy_id}/payment-recipient/review"

        r = client.post(url, cookies=renter_cookies)
        assert r.status_code == 200, r.text
        reviewed_at = r.json()["paymentRecipientReviewedAt"]
        assert reviewed_at is not None

        r = client.post(url, cookies=renter_cookies)
        assert r.json()["paymentRecipientReviewedAt"] == reviewed_at

        r = client.get("/api/users/rentals/occupancies", cookies=renter_cookies)
        occupancy = next(o for o in r.json() if o["id"] == occupancy_id)
        assert occupancy["paymentRecipientReviewedAt"] == reviewed_at

    def test_host_cannot_record_the_renters_review(self, client, db_session: Session):
        _host, _renter, host_cookies, _renter_cookies, occupancy_id = _drive_agreement_to_signed(
            client, db_session, host_email="recip-host2@test.com", renter_email="recip-renter2@test.com",
        )
        r = client.post(f"/api/users/rentals/occupancies/{occupancy_id}/payment-recipient/review", cookies=host_cookies)
        assert r.status_code == 403, r.text


class TestFullHandoverAgreementVersion:
    def test_full_handover_reissues_the_agreement_in_the_new_tenants_name(self, client, db_session: Session):
        """A full sublet handover adds a new version of the same agreement naming
        the incoming tenant, frozen as a PDF on approval -- no new signing and no
        new payments. The previous version stays as the original renter's record."""
        from app.crud import sublet as sublet_crud
        from app.models.finance import Obligation

        host, renter, host_cookies, _renter_cookies, occupancy_id = _drive_agreement_to_signed(
            client, db_session, host_email="handover-host@test.com", renter_email="handover-renter@test.com",
        )
        assert client.post(
            f"/api/users/hosting/occupancies/{occupancy_id}/confirm-move-in", cookies=host_cookies,
        ).status_code == 200
        new_tenant = _make_verified_renter(db_session, email="handover-newtenant@test.com")
        db_session.commit()

        occupancy = db_session.get(Occupancy, occupancy_id)
        agreement_id = occupancy.offer.agreement.id
        obligations_before = db_session.query(Obligation).filter(Obligation.agreement_id == agreement_id).count()

        sublet_request = sublet_crud.submit_sublet_request(db_session, renter, occupancy_id, new_tenant.party_id, "ASSIGNMENT_FULL")
        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve",
            json={"stepUpPassword": "password123", "authorityConfirmed": True}, cookies=host_cookies,
        )
        assert r.status_code == 200, r.text

        db_session.expire_all()
        agreement = db_session.get(Agreement, agreement_id)
        assert [v.version_no for v in agreement.versions] == [1, 2]
        original, reissued = agreement.versions
        assert original.snapshot["renter_email"] == "handover-renter@test.com"
        assert reissued.snapshot["renter_email"] == "handover-newtenant@test.com"
        assert reissued.snapshot["document"]["renter"]["email"] == "handover-newtenant@test.com"
        assert reissued.status == "EXECUTED_IMMUTABLE" and reissued.artifact is not None
        assert agreement.status == "SIGNED"
        assert db_session.query(Obligation).filter(Obligation.agreement_id == agreement_id).count() == obligations_before

        r = client.get(f"/api/users/rentals/agreements/{agreement_id}/pdf", cookies=auth_user_cookie(new_tenant))
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
