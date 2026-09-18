"""India-scope MVP of ZR-ENG-CLR-002 Section 2 -- Deposit Rules. Before this,
Admin had exactly two deposit actions: Release (any amount, no reason) and
Forfeit (the entire remaining balance, no reason, no evidence, no renter say
in it at all). These tests exercise the replacement: a canonical instrument
record auto-created alongside the deposit, an itemized claim workflow with
evidence, and a renter accept/dispute step that blind forfeiture can no
longer bypass."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.authority_record import AuthorityRecord
from app.models.finance import DepositRecord, Obligation
from app.models.guest import Guest
from app.models.listing import Listing
from app.models.market_release import MarketRelease
from app.models.occupancy_classification import OccupancyClassification
from app.models.room import Room
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie, deliver_all_disclosures
from tests.test_application_workflow import _make_verified_renter_with_published_listing


def _make_agreement_eligible(db: Session, listing_id: str) -> None:
    listing = db.get(Listing, listing_id)
    room = db.get(Room, listing.room_id)
    # "England" -- the one jurisdiction services/agreement_profile.py's
    # resolver currently supports (ZR-ENG-CLR-004 fail-closed resolver).
    release = MarketRelease(jurisdiction="England", status="active")
    db.add(release)
    db.flush()
    listing.market_release_id = release.id
    db.add(AuthorityRecord(party_id=listing.party_id or 1, room_id=room.id, authority_type="lease_agreement", status="verified"))
    db.add(OccupancyClassification(room_id=room.id, classification="shared_residential_room", review_state="APPROVED"))
    db.commit()


def _fund_a_deposit(client, db_session: Session, *, email: str, deposit_amount: float = 5000.0) -> tuple[object, int, str]:
    """Runs the real application -> offer -> agreement -> payment flow up to a
    FUNDED deposit obligation, exactly as a renter would. Returns
    (user, deposit_record_id, guest_id)."""
    user, listing_id = _make_verified_renter_with_published_listing(db_session, email=email)
    user_cookies = auth_user_cookie(user)
    super_admin = _make_admin(db_session, email=f"decider-{email}", role="super_admin")
    admin_cookies = auth_admin_cookie(super_admin)

    r = client.post(
        "/api/users/rentals/applications",
        json={"listingId": listing_id, "message": "hi", "desiredMoveIn": None},
        cookies=user_cookies,
    )
    assert r.status_code == 201, r.text
    application_id = r.json()["id"]

    r = client.post(f"/api/leasing/applications/{application_id}/decide", json={"decision": "APPROVED"}, cookies=admin_cookies)
    assert r.status_code == 200, r.text

    r = client.post(f"/api/leasing/applications/{application_id}/offers", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    offer_id = r.json()["id"]

    r = client.post(
        f"/api/leasing/offers/{offer_id}/terms",
        json={
            "monthlyRent": 2000,
            "depositAmount": deposit_amount,
            "startDate": (date.today() + timedelta(days=5)).isoformat(),
            "termMonths": 6,
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

    assert client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies).status_code == 200
    deliver_all_disclosures(client, admin_cookies, agreement_id)
    assert client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=user_cookies).status_code == 200
    assert client.post(
        f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies
    ).status_code == 200

    deposit_obligation = db_session.scalar(
        select(Obligation).where(Obligation.agreement_id == agreement_id, Obligation.obligation_type == "DEPOSIT")
    )
    rent_obligation = db_session.scalar(
        select(Obligation).where(Obligation.agreement_id == agreement_id, Obligation.obligation_type == "RENT")
    )
    guest = db_session.scalar(select(Guest).where(Guest.email == email))
    assert deposit_obligation is not None and guest is not None

    r = client.post(
        "/api/finance/payments",
        json={"guestId": guest.id, "amount": float(deposit_obligation.amount), "currency": "INR", "idempotencyKey": f"dep-{email}"},
        cookies=admin_cookies,
    )
    assert r.status_code == 201, r.text
    payment_id = r.json()["id"]

    r = client.post(
        f"/api/finance/payments/{payment_id}/confirm",
        json={"allocations": [{"obligationId": deposit_obligation.id, "amount": float(deposit_obligation.amount)}]},
        cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text

    db_session.refresh(deposit_obligation)
    record = db_session.scalar(select(DepositRecord).where(DepositRecord.obligation_id == deposit_obligation.id))
    assert record is not None, "DepositRecord should auto-create once the DEPOSIT obligation is PAID"

    return user, record.id, guest.id


class TestDepositInstrumentAutoCreation:
    def test_funding_a_deposit_creates_an_instrument_with_a_calculation_snapshot(self, client, db_session: Session):
        _user, deposit_id, _guest_id = _fund_a_deposit(client, db_session, email="instrument@test.com", deposit_amount=5000.0)
        super_admin = _make_admin(db_session, email="viewer1@test.com", role="super_admin")
        r = client.get("/api/finance/deposits", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        record = next(d for d in r.json() if d["id"] == deposit_id)
        assert record["status"] == "HELD"
        assert record["instrument"]["instrumentType"] == "SECURITY_DEPOSIT"
        assert record["instrument"]["custodyModel"] == "HOST_OR_AGENT"
        assert record["instrument"]["calculationSnapshot"]["amount"] == 5000.0


class TestBlindForfeitureIsNoLongerPossible:
    def test_forfeit_with_no_claim_at_all_is_rejected(self, client, db_session: Session):
        _user, deposit_id, _guest_id = _fund_a_deposit(client, db_session, email="blindforfeit@test.com")
        super_admin = _make_admin(db_session, email="forfeit-admin1@test.com", role="super_admin")
        r = client.post(f"/api/finance/deposits/{deposit_id}/forfeit", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 409, r.text
        assert "claim" in r.text.lower()

    def test_forfeit_succeeds_once_a_claim_covers_the_full_amount_and_renter_accepted(self, client, db_session: Session):
        _user, deposit_id, _guest_id = _fund_a_deposit(client, db_session, email="fullaccept@test.com", deposit_amount=5000.0)
        super_admin = _make_admin(db_session, email="forfeit-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        user_cookies = auth_user_cookie(_user)

        r = client.post(
            f"/api/finance/deposits/{deposit_id}/claims",
            json={"items": [{"categoryCode": "DAMAGE_BEYOND_NORMAL_WEAR", "amountRequested": 5000.0, "description": "Broken wardrobe door"}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        item_id = r.json()["items"][0]["id"]

        # Blind forfeit still blocked while the item is unresponded-to.
        r = client.post(f"/api/finance/deposits/{deposit_id}/forfeit", cookies=admin_cookies)
        assert r.status_code == 409, r.text

        r = client.post(f"/api/users/rentals/deposit-claim-items/{item_id}/respond", json={"response": "ACCEPT"}, cookies=user_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["finalAmount"] == 5000.0

        r = client.post(f"/api/finance/deposits/{deposit_id}/forfeit", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "FORFEITED"


class TestDisputeWorkflow:
    def test_disputed_item_freezes_the_record_and_blocks_release_of_the_disputed_portion(self, client, db_session: Session):
        _user, deposit_id, _guest_id = _fund_a_deposit(client, db_session, email="disputer@test.com", deposit_amount=5000.0)
        super_admin = _make_admin(db_session, email="dispute-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        user_cookies = auth_user_cookie(_user)

        r = client.post(
            f"/api/finance/deposits/{deposit_id}/claims",
            json={"items": [{"categoryCode": "UNPAID_RENT", "amountRequested": 2000.0, "description": "Last month unpaid"}]},
            cookies=admin_cookies,
        )
        item_id = r.json()["items"][0]["id"]

        r = client.post(f"/api/users/rentals/deposit-claim-items/{item_id}/respond", json={"response": "DISPUTE"}, cookies=user_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["finalAmount"] is None

        r = client.get("/api/finance/deposits", cookies=admin_cookies)
        record = next(d for d in r.json() if d["id"] == deposit_id)
        assert record["status"] == "FROZEN"

        # The other 3000 (never claimed) is still releasable; the disputed 2000 is not.
        r = client.post(f"/api/finance/deposits/{deposit_id}/release", json={"amount": 3000.0}, cookies=admin_cookies)
        assert r.status_code == 200, r.text

        r = client.post(f"/api/finance/deposits/{deposit_id}/release", json={"amount": 1.0}, cookies=admin_cookies)
        assert r.status_code == 400, r.text

        r = client.post(f"/api/finance/deposits/{deposit_id}/forfeit", cookies=admin_cookies)
        assert r.status_code == 409, r.text

    def test_releasing_the_full_undisputed_remainder_does_not_falsely_close_out_an_open_dispute(self, client, db_session: Session):
        """Regression: if release_deposit's status logic only checked
        released+committed >= held, releasing every releasable rupee while a
        dispute was still open would flip the record straight to RELEASED --
        conflating 'nothing left to release' with 'this deposit is fully and
        finally closed', even though a claim item is still awaiting resolution."""
        _user, deposit_id, _guest_id = _fund_a_deposit(client, db_session, email="fauxclose@test.com", deposit_amount=5000.0)
        super_admin = _make_admin(db_session, email="fauxclose-admin@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        user_cookies = auth_user_cookie(_user)

        r = client.post(
            f"/api/finance/deposits/{deposit_id}/claims",
            json={"items": [
                {"categoryCode": "DAMAGE_BEYOND_NORMAL_WEAR", "amountRequested": 2000.0, "description": "damage"},
                {"categoryCode": "MISSING_ITEMS_OR_KEYS", "amountRequested": 500.0, "description": "missing key"},
            ]},
            cookies=admin_cookies,
        )
        item_ids = [item["id"] for item in r.json()["items"]]

        client.post(f"/api/users/rentals/deposit-claim-items/{item_ids[0]}/respond", json={"response": "ACCEPT"}, cookies=user_cookies)
        client.post(f"/api/users/rentals/deposit-claim-items/{item_ids[1]}/respond", json={"response": "DISPUTE"}, cookies=user_cookies)

        # 5000 - 2000 accepted - 500 disputed(reserved) = 2500 truly releasable.
        r = client.post(f"/api/finance/deposits/{deposit_id}/release", json={"amount": 2500.0}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "FROZEN", "must stay FROZEN -- the 500 dispute is still unresolved"

        r = client.post(f"/api/finance/deposits/{deposit_id}/forfeit", cookies=admin_cookies)
        assert r.status_code == 409, r.text

    def test_admin_resolving_the_dispute_unfreezes_the_record(self, client, db_session: Session):
        _user, deposit_id, _guest_id = _fund_a_deposit(client, db_session, email="resolver@test.com", deposit_amount=5000.0)
        super_admin = _make_admin(db_session, email="dispute-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        user_cookies = auth_user_cookie(_user)

        r = client.post(
            f"/api/finance/deposits/{deposit_id}/claims",
            json={"items": [{"categoryCode": "MISSING_ITEMS_OR_KEYS", "amountRequested": 1000.0, "description": "Missing key fob"}]},
            cookies=admin_cookies,
        )
        claim_id = r.json()["id"]
        item_id = r.json()["items"][0]["id"]

        client.post(f"/api/users/rentals/deposit-claim-items/{item_id}/respond", json={"response": "DISPUTE"}, cookies=user_cookies)

        # Only super_admin/plain-admin-with-role can resolve; a non-DISPUTED claim can't be resolved.
        r = client.post(f"/api/finance/deposit-claims/{claim_id}/resolve", json={"itemFinalAmounts": [{"itemId": item_id, "finalAmount": 400.0}], "notes": "Key fob replacement cost only"}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "RESOLVED"
        assert r.json()["items"][0]["finalAmount"] == 400.0

        r = client.get("/api/finance/deposits", cookies=admin_cookies)
        record = next(d for d in r.json() if d["id"] == deposit_id)
        assert record["status"] != "FROZEN"

        # Remaining 4600 (5000 - 400 resolved) is now releasable.
        r = client.post(f"/api/finance/deposits/{deposit_id}/release", json={"amount": 4600.0}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "RELEASED"

        # Resolving twice is rejected -- there's nothing left to resolve.
        r = client.post(f"/api/finance/deposit-claims/{claim_id}/resolve", json={"itemFinalAmounts": [], "notes": ""}, cookies=admin_cookies)
        assert r.status_code == 409, r.text


class TestClaimValidation:
    def test_claim_amount_cannot_exceed_remaining_deposit_balance(self, client, db_session: Session):
        _user, deposit_id, _guest_id = _fund_a_deposit(client, db_session, email="overclaim@test.com", deposit_amount=1000.0)
        super_admin = _make_admin(db_session, email="overclaim-admin@test.com", role="super_admin")
        r = client.post(
            f"/api/finance/deposits/{deposit_id}/claims",
            json={"items": [{"categoryCode": "UNPAID_RENT", "amountRequested": 1500.0, "description": "too much"}]},
            cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 400, r.text
        assert "exceeds" in r.text.lower()

    def test_unsupported_category_is_rejected(self, client, db_session: Session):
        _user, deposit_id, _guest_id = _fund_a_deposit(client, db_session, email="badcategory@test.com")
        super_admin = _make_admin(db_session, email="badcat-admin@test.com", role="super_admin")
        r = client.post(
            f"/api/finance/deposits/{deposit_id}/claims",
            json={"items": [{"categoryCode": "NORMAL_WEAR_AND_TEAR", "amountRequested": 100.0, "description": "not allowed"}]},
            cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 400, r.text


class TestRenterOwnershipIsolation:
    def test_a_different_renter_cannot_respond_to_someone_elses_claim_item(self, client, db_session: Session):
        _owner, deposit_id, _guest_id = _fund_a_deposit(client, db_session, email="realowner@test.com", deposit_amount=2000.0)
        # A registered renter with no guest/occupancy of their own -- respond_to_deposit_claim_item
        # must reject them for having no ownership link at all, not just a mismatched one.
        stranger = _make_user(db_session, email="stranger@test.com")
        super_admin = _make_admin(db_session, email="isolation-admin@test.com", role="super_admin")

        r = client.post(
            f"/api/finance/deposits/{deposit_id}/claims",
            json={"items": [{"categoryCode": "UNPAID_RENT", "amountRequested": 500.0, "description": "x"}]},
            cookies=auth_admin_cookie(super_admin),
        )
        item_id = r.json()["items"][0]["id"]

        r = client.post(
            f"/api/users/rentals/deposit-claim-items/{item_id}/respond",
            json={"response": "ACCEPT"},
            cookies=auth_user_cookie(stranger),
        )
        assert r.status_code == 403, r.text

    def test_renter_can_list_their_own_claims_and_not_see_none_when_they_have_one(self, client, db_session: Session):
        user, deposit_id, _guest_id = _fund_a_deposit(client, db_session, email="listclaims@test.com", deposit_amount=1200.0)
        super_admin = _make_admin(db_session, email="listclaims-admin@test.com", role="super_admin")
        client.post(
            f"/api/finance/deposits/{deposit_id}/claims",
            json={"items": [{"categoryCode": "CLEANING_REMEDIATION", "amountRequested": 300.0, "description": "x"}]},
            cookies=auth_admin_cookie(super_admin),
        )

        r = client.get("/api/users/rentals/deposit-claims", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1
        assert r.json()[0]["items"][0]["categoryCode"] == "CLEANING_REMEDIATION"
