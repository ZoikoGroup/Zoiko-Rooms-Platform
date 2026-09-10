"""ZR-ENG-CLR-004 Section 4 (Agreement/Lease Rules) -- the P0 agreement
engine foundation: a fail-closed jurisdiction resolver, immutable versioned
agreements with hashed document artifacts, signature evidence, and the
Executed-vs-Effective distinction.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.leasing import Agreement, AgreementVersion, DocumentArtifact, Offer, SignatureEvent
from app.models.market_release import MarketRelease
from app.services.agreement_effectiveness import is_agreement_effective
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie, deliver_all_disclosures
from tests.test_renter_offer_agreement_flow import _make_agreement_eligible
from tests.test_room_hold_atomicity import _apply_and_send_offer, _make_listing_with_room, _make_verified_renter


def _readiness(client, offer_id: int, admin_cookies: dict) -> dict:
    r = client.get(f"/api/leasing/offers/{offer_id}/agreement-readiness", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    return r.json()


def _apply_send_accept_add_terms(client, db_session: Session, admin_cookies: dict, renter, listing_id: str, *, start_date: date, term_months: int = 6):
    """Apply, send, accept an offer and add terms, WITHOUT creating the
    agreement -- returns (application_id, offer_id) so each test controls
    when/whether create_agreement runs."""
    _app_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)
    r = client.post(
        f"/api/leasing/offers/{offer_id}/terms",
        json={
            "monthlyRent": 500, "depositAmount": 500,
            "startDate": start_date.isoformat(), "termMonths": term_months,
        },
        cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(renter))
    assert r.status_code == 200, r.text
    return _app_id, offer_id


def _pay_off_agreement_obligations(client, db_session: Session, admin_cookies: dict, agreement_id: int) -> None:
    """Pays the RENT + DEPOSIT obligations in full -- confirm_agreement_payment
    only reaches SIGNED once every initial obligation clears (ZR-ENG-CLR-001
    Rule 7), so both signatures alone are never enough."""
    from app.models.finance import Obligation

    obligations = list(db_session.query(Obligation).filter(Obligation.agreement_id == agreement_id))
    total_due = sum(float(o.amount) for o in obligations)
    guest_id = db_session.get(Agreement, agreement_id).offer.guest_id

    r = client.post(
        "/api/finance/payments",
        json={"guestId": guest_id, "amount": total_due, "currency": "INR", "idempotencyKey": f"agreement-{agreement_id}-test-pay"},
        cookies=admin_cookies,
    )
    assert r.status_code == 201, r.text
    payment_id = r.json()["id"]

    r = client.post(
        f"/api/finance/payments/{payment_id}/confirm",
        json={"allocations": [{"obligationId": o.id, "amount": float(o.amount)} for o in obligations]},
        cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text


class TestFailClosedResolver:
    def test_agreement_creation_blocked_for_unsupported_jurisdiction(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="agreement-fc-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="agreement-fc-renter1@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )

        # Deliberately do NOT call _make_agreement_eligible -- no active
        # MarketRelease at all, so pre-existing eligibility gates (and the
        # new resolver) both have nothing to match. (The resolver's own
        # distinct rejection message is exercised by the "different
        # jurisdiction" test below, where every other gate passes.)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        assert r.status_code == 409, r.text

    def test_agreement_creation_blocked_for_a_different_jurisdiction(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="agreement-fc-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="agreement-fc-renter2@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )

        from app.models.listing import Listing
        listing = db_session.get(Listing, listing_id)
        release = MarketRelease(jurisdiction="United States", status="active")
        db_session.add(release)
        db_session.flush()
        listing.market_release_id = release.id
        db_session.commit()

        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        assert r.status_code == 409, r.text

    def test_agreement_creation_succeeds_for_the_supported_jurisdiction(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="agreement-fc-admin3@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="agreement-fc-renter3@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)

        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        assert r.status_code == 200, r.text


class TestVersionAndArtifactImmutability:
    def test_execution_freezes_exactly_one_version_and_artifact(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="agreement-immut-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="agreement-immut-renter1@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today(),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)

        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        agreement_id = r.json()["id"]

        agreement = db_session.get(Agreement, agreement_id)
        assert len(agreement.versions) == 1
        version = agreement.versions[0]
        assert version.status == "WORKING"
        assert version.content_hash is None
        assert version.artifact is None

        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)
        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PAYMENT_IN_PROGRESS"  # both signed, obligations still unpaid
        _pay_off_agreement_obligations(client, db_session, admin_cookies, agreement_id)

        db_session.refresh(agreement)
        version = agreement.versions[0]
        assert version.status == "EXECUTED_IMMUTABLE"
        assert version.content_hash is not None
        assert version.artifact is not None
        assert version.artifact.content_hash == version.content_hash

        artifacts = list(db_session.query(DocumentArtifact).filter(DocumentArtifact.agreement_version_id == version.id))
        assert len(artifacts) == 1

    def test_downloaded_pdf_hash_matches_stored_artifact_hash_and_is_stable(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="agreement-immut-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="agreement-immut-renter2@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today(),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]

        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)
        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)
        _pay_off_agreement_obligations(client, db_session, admin_cookies, agreement_id)

        r1 = client.get(f"/api/leasing/agreements/{agreement_id}/pdf", cookies=admin_cookies)
        r2 = client.get(f"/api/leasing/agreements/{agreement_id}/pdf", cookies=admin_cookies)
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.content == r2.content  # same persisted bytes, not re-rendered

        agreement = db_session.get(Agreement, agreement_id)
        stored_hash = agreement.versions[0].artifact.content_hash
        assert hashlib.sha256(r1.content).hexdigest() == stored_hash


class TestSignatureEvidence:
    def test_a_signature_event_is_recorded_per_signer(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="agreement-evidence-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="agreement-evidence-renter1@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]

        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)
        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))

        events = list(db_session.query(SignatureEvent).filter(SignatureEvent.agreement_id == agreement_id))
        assert len(events) == 1
        assert events[0].signer_role == "renter"
        assert events[0].method == "SIMPLE_ESIGN"
        assert events[0].signer_identifier == offer.guest_id

        client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)
        events = list(db_session.query(SignatureEvent).filter(SignatureEvent.agreement_id == agreement_id))
        assert len(events) == 2
        assert {e.signer_role for e in events} == {"renter", "provider"}


class TestExecutedVsEffective:
    def test_signed_agreement_with_future_start_date_is_not_yet_effective(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="agreement-eff-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="agreement-eff-renter1@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=10),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]

        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)
        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)
        _pay_off_agreement_obligations(client, db_session, admin_cookies, agreement_id)

        agreement = db_session.get(Agreement, agreement_id)
        assert agreement.status == "SIGNED"
        assert is_agreement_effective(agreement) is False
        assert is_agreement_effective(agreement, today=date.today() + timedelta(days=10)) is True

        r = client.post(f"/api/occupancy/agreements/{agreement_id}/confirm-move-in", cookies=admin_cookies)
        assert r.status_code == 409, r.text
        assert "not yet effective" in r.text

    def test_signed_agreement_with_start_date_today_is_effective(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="agreement-eff-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="agreement-eff-renter2@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today(),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]

        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)
        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)
        _pay_off_agreement_obligations(client, db_session, admin_cookies, agreement_id)

        agreement = db_session.get(Agreement, agreement_id)
        assert is_agreement_effective(agreement) is True


class TestAccessLoggingAndRenterDownload:
    def test_renter_can_download_their_own_agreement(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="agreement-access-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="agreement-access-renter1@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]

        r = client.get(f"/api/users/rentals/agreements/{agreement_id}/pdf", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/pdf"

    def test_a_different_renter_cannot_download_this_agreement(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="agreement-access-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="agreement-access-renter2@test.com")
        other_renter = _make_verified_renter(db_session, email="agreement-access-other2@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]

        r = client.get(f"/api/users/rentals/agreements/{agreement_id}/pdf", cookies=auth_user_cookie(other_renter))
        assert r.status_code == 403, r.text

    def test_admin_view_and_download_are_audit_logged(self, client, db_session: Session):
        from app.models.audit import AuditEvent

        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="agreement-access-admin3@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="agreement-access-renter3@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]

        client.get(f"/api/leasing/agreements/{agreement_id}", cookies=admin_cookies)
        client.get(f"/api/leasing/agreements/{agreement_id}/pdf", cookies=admin_cookies)

        actions = {
            e.action for e in db_session.query(AuditEvent).filter(
                AuditEvent.resource_type == "agreement", AuditEvent.resource_id == str(agreement_id),
            )
        }
        assert "agreement.view" in actions
        assert "agreement.download" in actions


class TestPartiallyExecuted:
    def test_one_signature_reaches_partially_executed_not_signed(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="pe-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="pe-renter1@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)

        r = client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PARTIALLY_EXECUTED"

        # The second signer can still sign from PARTIALLY_EXECUTED.
        r = client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PAYMENT_IN_PROGRESS"

    def test_unresolved_commitment_guard_treats_partially_executed_as_live(self, client, db_session: Session):
        """A listing whose room has a PARTIALLY_EXECUTED agreement must not
        be archivable (ZR-ENG-CLR-001 Section 8's unresolved-commitments
        guard, crud/listing.py:_has_unresolved_commitments)."""
        from app.models.listing import Listing

        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="pe-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="pe-renter2@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)
        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))

        listing = db_session.get(Listing, listing_id)
        listing.state = "SUSPENDED"
        db_session.commit()

        r = client.post(f"/api/listings/{listing_id}/archive", cookies=admin_cookies)
        assert r.status_code == 409, r.text


class TestSigningDeadlineExpiry:
    def test_unsigned_agreement_expires_with_the_offers_confirmation_window(self, client, db_session: Session):
        listing_id, room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="sd-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="sd-renter1@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)
        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))

        from datetime import datetime, timedelta as td, timezone as tz
        offer = db_session.get(Offer, offer_id)
        offer.confirmation_expires_at = datetime.now(tz.utc) - td(minutes=1)
        db_session.commit()

        r = client.get(f"/api/leasing/agreements/{agreement_id}", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "EXPIRED"
        # The renter's own signature is preserved as evidence, not cleared.
        assert r.json()["signedByRenterAt"] is not None

    def test_agreement_already_signed_is_not_touched_by_offer_expiry(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="sd-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="sd-renter2@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today(),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)
        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)
        _pay_off_agreement_obligations(client, db_session, admin_cookies, agreement_id)

        from datetime import datetime, timedelta as td, timezone as tz
        offer = db_session.get(Offer, offer_id)
        offer.confirmation_expires_at = datetime.now(tz.utc) - td(minutes=1)
        db_session.commit()

        r = client.get(f"/api/leasing/agreements/{agreement_id}", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "SIGNED"


class TestConcurrentFreezeIsIdempotent:
    def test_freezing_an_already_frozen_version_returns_the_existing_artifact(self, client, db_session: Session):
        """Simulates the losing side of a concurrent double-signature race:
        by the time this caller reaches freeze_agreement_version, another
        request already froze the same version. It must return the existing
        artifact, not raise or create a second one."""
        from app.crud.leasing import freeze_agreement_version

        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="race-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="race-renter1@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today(),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)
        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)
        _pay_off_agreement_obligations(client, db_session, admin_cookies, agreement_id)

        agreement = db_session.get(Agreement, agreement_id)
        first_artifact = agreement.versions[-1].artifact
        assert first_artifact is not None

        second_call_result = freeze_agreement_version(db_session, agreement)
        assert second_call_result.id == first_artifact.id
        assert second_call_result.content_hash == first_artifact.content_hash

        artifacts = list(
            db_session.query(DocumentArtifact).filter(
                DocumentArtifact.agreement_version_id == agreement.versions[-1].id,
            )
        )
        assert len(artifacts) == 1


class TestHostReadiness:
    """ZR-ENG-CLR-004 Section 5.3: /agreement-readiness must walk the full
    NOT_STARTED -> BLOCKED_POLICY -> NEEDS_VERIFICATION -> READY_TO_GENERATE
    -> GENERATED_FOR_REVIEW -> LOCKED_FOR_EXECUTION ladder, reusing the same
    gates create_agreement itself enforces."""

    def test_readiness_walks_every_state_across_the_lifecycle(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="ready-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="ready-renter1@test.com")

        _app_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)

        # No terms yet.
        assert _readiness(client, offer_id, admin_cookies)["state"] == "NOT_STARTED"

        r = client.post(
            f"/api/leasing/offers/{offer_id}/terms",
            json={
                "monthlyRent": 500, "depositAmount": 500,
                "startDate": (date.today() + timedelta(days=5)).isoformat(), "termMonths": 6,
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        # Terms exist, but no market release/jurisdiction has been set up at
        # all -- the resolver has nothing to match and fails closed.
        assert _readiness(client, offer_id, admin_cookies)["state"] == "BLOCKED_POLICY"

        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)

        # Profile now resolves (jurisdiction=England), but the offer itself
        # hasn't been accepted yet -- check_agreement_eligibility still
        # blocks, so this is NEEDS_VERIFICATION, not READY_TO_GENERATE.
        readiness = _readiness(client, offer_id, admin_cookies)
        assert readiness["state"] == "NEEDS_VERIFICATION"
        assert any("not been accepted" in reason for reason in readiness["missingFacts"])

        r = client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text

        assert _readiness(client, offer_id, admin_cookies)["state"] == "READY_TO_GENERATE"

        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        agreement_id = r.json()["id"]

        assert _readiness(client, offer_id, admin_cookies)["state"] == "GENERATED_FOR_REVIEW"

        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        deliver_all_disclosures(client, admin_cookies, agreement_id)
        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)
        _pay_off_agreement_obligations(client, db_session, admin_cookies, agreement_id)

        assert _readiness(client, offer_id, admin_cookies)["state"] == "LOCKED_FOR_EXECUTION"


class TestDisclosureDeliveryGate:
    """ZR-ENG-CLR-004 AC-15: execution is blocked while a required disclosure
    is still REQUIRED_MISSING, and unblocked once it's DELIVERED."""

    def test_signing_is_blocked_while_a_required_disclosure_is_undelivered(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="disc-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="disc-renter1@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)

        disclosures = client.get(f"/api/leasing/agreements/{agreement_id}/disclosures", cookies=admin_cookies).json()
        assert disclosures, "expected the profile to have seeded at least one required disclosure"
        assert all(d["status"] == "REQUIRED_MISSING" for d in disclosures)

        r = client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        assert r.status_code == 409, r.text
        assert "disclosures" in r.text.lower()

        r = client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)
        assert r.status_code == 409, r.text

    def test_signing_succeeds_once_every_required_disclosure_is_delivered(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="disc-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="disc-renter2@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)

        deliver_all_disclosures(client, admin_cookies, agreement_id)
        disclosures = client.get(f"/api/leasing/agreements/{agreement_id}/disclosures", cookies=admin_cookies).json()
        assert all(d["status"] == "DELIVERED" for d in disclosures)
        assert all(d["deliveredAt"] is not None for d in disclosures)

        r = client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text

    def test_delivering_a_disclosure_is_audit_logged(self, client, db_session: Session):
        from app.models.audit import AuditEvent

        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="disc-admin3@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="disc-renter3@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)

        disclosure_id = client.get(
            f"/api/leasing/agreements/{agreement_id}/disclosures", cookies=admin_cookies,
        ).json()[0]["id"]
        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/disclosures/{disclosure_id}/deliver", cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        events = list(
            db_session.query(AuditEvent).filter(
                AuditEvent.resource_type == "disclosure_requirement", AuditEvent.resource_id == str(disclosure_id),
            )
        )
        assert any(e.action == "disclosure.deliver" for e in events)


class TestSignerWhitelistIsProfileDriven:
    """ZR-ENG-CLR-004 AC-10: required_signers comes from the resolved
    profile's own snapshot, not a hardcoded literal -- proven by rejecting a
    party outside it, not just the two the API's own schema happens to
    allow today."""

    def test_apply_signature_rejects_a_party_outside_the_snapshots_required_signers(self, client, db_session: Session):
        from app.crud.leasing import _apply_signature

        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="signer-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="signer-renter1@test.com")

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]

        agreement = db_session.get(Agreement, agreement_id)
        assert agreement.versions[-1].snapshot["required_signers"] == ["provider", "renter"]

        import pytest
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _apply_signature(db_session, agreement, "guarantor")
        assert exc_info.value.status_code == 400
        assert "asParty must be one of" in exc_info.value.detail
