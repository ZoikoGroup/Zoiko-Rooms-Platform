"""Tests for the ZR-ENG-CLR-005 ledger foundation (app/services/ledger.py) --
confirms crud/finance.py's wiring in confirm_payment, run_payout,
release_deposit, forfeit_deposit and decide_refund actually posts correct,
balanced double-entry LedgerEntry rows, without changing any of those
functions' existing behavior (see the other test_finance_*.py files, which
must keep passing unmodified alongside these)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.authority_record import AuthorityRecord
from app.models.finance import LedgerAccount, LedgerEntry, Obligation
from app.models.guest import Guest
from app.models.leasing import Agreement, Application, Offer
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from app.services import ledger as ledger_service
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_deposit_claims import _fund_a_deposit


def _make_provider_rent_obligation(
    db: Session, *, suffix: str, obligation_type: str = "RENT", money_plane: str = "OCCUPANCY", amount: float = 500.0,
    owner_party_id: int | None = None,
):
    """A provider-owned room/occupancy with a pending obligation of the given
    type, plus a verified AuthorityRecord for the room (so run_payout tests
    against it are eligible for PAID, not HELD). Returns
    (obligation, admin, guest, owner_party_id). Pass an existing
    owner_party_id to create a second, independent obligation for the SAME
    provider party (e.g. to test something that depends on that party
    already having other state, like an existing ledger hold)."""
    admin = _make_admin(db, email=f"ledger-admin-{suffix}@test.com", role="super_admin")

    if owner_party_id is not None:
        owner_party = db.get(Party, owner_party_id)
    else:
        owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db.add(owner_party)
        db.flush()
    prop = Property(owner_party_id=owner_party.id, address=f"{suffix} Ledger St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()
    db.add(AuthorityRecord(party_id=owner_party.id, room_id=room.id, authority_type="lease_agreement", status="verified"))

    guest = Guest(id=f"G-LEDGER-{suffix}", name="Renter", email=f"ledger-renter-{suffix}@test.com", joined_at=date.today())
    db.add(guest)
    db.flush()

    listing = Listing(
        id=f"L-LEDGER-{suffix}", slug=f"ledger-{suffix}", name="Ledger Test Listing", room_type="Private room",
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

    occupancy = Occupancy(offer_id=offer.id, listing_id=listing.id, room_id=room.id, guest_id=guest.id, status="ACTIVE")
    db.add(occupancy)
    db.flush()

    obligation = Obligation(
        obligation_type=obligation_type, money_plane=money_plane, amount=amount, currency="INR",
        due_date=date.today(), status="PENDING", occupancy_id=occupancy.id,
    )
    db.add(obligation)
    db.commit()

    return obligation, admin, guest, owner_party.id


class TestConfirmPaymentPostsLedgerEntries:
    def test_rent_payment_posts_platform_clearing_to_host_payable(self, client, db_session: Session):
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="rent1")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "ledger-rent-1"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        payment_id = r.json()["id"]

        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        entries = db_session.scalars(
            select(LedgerEntry).where(LedgerEntry.source_type == "payment_allocation", LedgerEntry.source_id == str(obligation.id))
        ).all()
        assert len(entries) == 1
        entry = entries[0]
        assert float(entry.amount) == 500.0
        debit = db_session.get(LedgerAccount, entry.debit_account_id)
        credit = db_session.get(LedgerAccount, entry.credit_account_id)
        assert debit.account_type == "PLATFORM_CLEARING"
        assert debit.party_id is None
        assert credit.account_type == "HOST_PAYABLE"
        assert credit.party_id == party_id

    def test_deposit_payment_posts_platform_clearing_to_deposit_custody_liability(self, client, db_session: Session):
        obligation, admin, guest, party_id = _make_provider_rent_obligation(
            db_session, suffix="dep1", obligation_type="DEPOSIT", money_plane="SAFEGUARDED", amount=1000.0,
        )
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 1000.0, "currency": "INR", "idempotencyKey": "ledger-dep-1"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        payment_id = r.json()["id"]

        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 1000.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        entry = db_session.scalar(
            select(LedgerEntry).where(LedgerEntry.source_type == "payment_allocation", LedgerEntry.source_id == str(obligation.id))
        )
        assert entry is not None
        assert float(entry.amount) == 1000.0
        debit = db_session.get(LedgerAccount, entry.debit_account_id)
        credit = db_session.get(LedgerAccount, entry.credit_account_id)
        assert debit.account_type == "PLATFORM_CLEARING"
        assert credit.account_type == "DEPOSIT_CUSTODY_LIABILITY"
        assert credit.party_id == party_id


class TestRunPayoutPostsFeeAndNetEntries:
    def test_payout_posts_fee_and_net_entries_summing_to_gross(self, client, db_session: Session):
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="payout1", amount=1000.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 1000.0, "currency": "INR", "idempotencyKey": "ledger-payout-1"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        payment_id = r.json()["id"]
        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 1000.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        payout = r.json()
        assert payout["status"] == "PAID"
        payout_id = payout["id"]

        entries = db_session.scalars(
            select(LedgerEntry).where(LedgerEntry.source_type == "payout_record", LedgerEntry.source_id == str(payout_id))
        ).all()
        assert len(entries) == 2

        fee_entry = next(e for e in entries if e.description == "Platform fee on payout")
        net_entry = next(e for e in entries if e.description == "Payout paid to host")

        gross = 1000.0
        expected_fee = round(gross * 0.10, 2)
        expected_net = round(gross - expected_fee, 2)
        assert float(fee_entry.amount) == expected_fee
        assert float(net_entry.amount) == expected_net
        assert round(float(fee_entry.amount) + float(net_entry.amount), 2) == gross

        fee_debit = db_session.get(LedgerAccount, fee_entry.debit_account_id)
        fee_credit = db_session.get(LedgerAccount, fee_entry.credit_account_id)
        net_debit = db_session.get(LedgerAccount, net_entry.debit_account_id)
        net_credit = db_session.get(LedgerAccount, net_entry.credit_account_id)
        assert fee_debit.account_type == "HOST_PAYABLE" and fee_debit.party_id == party_id
        assert fee_credit.account_type == "PLATFORM_FEE_REVENUE"
        assert net_debit.account_type == "HOST_PAYABLE" and net_debit.party_id == party_id
        assert net_credit.account_type == "PLATFORM_CLEARING"


class TestReleaseDepositPostsLedgerEntry:
    def test_release_posts_custody_liability_to_platform_clearing(self, client, db_session: Session):
        _user, deposit_id, _guest_id = _fund_a_deposit(client, db_session, email="ledger-release@test.com", deposit_amount=2000.0)
        super_admin = _make_admin(db_session, email="ledger-release-admin@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)

        r = client.post(f"/api/finance/deposits/{deposit_id}/release", json={"amount": 800.0}, cookies=admin_cookies)
        assert r.status_code == 200, r.text

        entry = db_session.scalar(
            select(LedgerEntry).where(LedgerEntry.source_type == "deposit_record", LedgerEntry.source_id == str(deposit_id))
        )
        assert entry is not None
        assert float(entry.amount) == 800.0
        debit = db_session.get(LedgerAccount, entry.debit_account_id)
        credit = db_session.get(LedgerAccount, entry.credit_account_id)
        assert debit.account_type == "DEPOSIT_CUSTODY_LIABILITY"
        assert credit.account_type == "PLATFORM_CLEARING"


class TestForfeitDepositPostsLedgerEntry:
    def test_forfeit_posts_custody_liability_to_host_payable(self, client, db_session: Session):
        user, deposit_id, _guest_id = _fund_a_deposit(client, db_session, email="ledger-forfeit@test.com", deposit_amount=3000.0)
        super_admin = _make_admin(db_session, email="ledger-forfeit-admin@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        user_cookies = auth_user_cookie(user)

        r = client.post(
            f"/api/finance/deposits/{deposit_id}/claims",
            json={"items": [{"categoryCode": "DAMAGE_BEYOND_NORMAL_WEAR", "amountRequested": 3000.0, "description": "Damage"}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        item_id = r.json()["items"][0]["id"]
        r = client.post(f"/api/users/rentals/deposit-claim-items/{item_id}/respond", json={"response": "ACCEPT"}, cookies=user_cookies)
        assert r.status_code == 200, r.text

        r = client.post(f"/api/finance/deposits/{deposit_id}/forfeit", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "FORFEITED"

        entry = db_session.scalar(
            select(LedgerEntry).where(LedgerEntry.source_type == "deposit_record", LedgerEntry.source_id == str(deposit_id))
        )
        assert entry is not None
        assert float(entry.amount) == 3000.0
        debit = db_session.get(LedgerAccount, entry.debit_account_id)
        credit = db_session.get(LedgerAccount, entry.credit_account_id)
        assert debit.account_type == "DEPOSIT_CUSTODY_LIABILITY"
        assert credit.account_type == "HOST_PAYABLE"


class TestDecideRefundPostsReversingEntry:
    def test_approved_refund_reverses_original_collection_entry(self, client, db_session: Session):
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="refund1", amount=500.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "ledger-refund-1"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        payment_id = r.json()["id"]
        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            "/api/finance/refunds",
            json={
                "paymentId": payment_id, "obligationId": obligation.id, "amount": 500.0, "reason": "test",
                "idempotencyKey": "ledger-refund-request-1",
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        refund_id = r.json()["id"]

        r = client.post(f"/api/finance/refunds/{refund_id}/decide", json={"approve": True}, cookies=admin_cookies)
        assert r.status_code == 200, r.text

        entry = db_session.scalar(
            select(LedgerEntry).where(LedgerEntry.source_type == "refund_request", LedgerEntry.source_id == str(refund_id))
        )
        assert entry is not None
        assert float(entry.amount) == 500.0
        debit = db_session.get(LedgerAccount, entry.debit_account_id)
        credit = db_session.get(LedgerAccount, entry.credit_account_id)
        assert debit.account_type == "HOST_PAYABLE"
        assert debit.party_id == party_id
        assert credit.account_type == "PLATFORM_CLEARING"


class TestGetBalanceHelper:
    def test_balance_nets_debits_and_credits(self, db_session: Session):
        owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(owner_party)
        db_session.commit()

        platform_clearing = ledger_service.get_platform_account(db_session, "PLATFORM_CLEARING", "INR")
        host_payable = ledger_service.get_party_account(db_session, "HOST_PAYABLE", owner_party.id, "INR")

        ledger_service.post_entry(
            db_session, debit_account=platform_clearing, credit_account=host_payable, amount=300.0,
            currency="INR", description="first", source_type="test", source_id="1",
        )
        ledger_service.post_entry(
            db_session, debit_account=host_payable, credit_account=platform_clearing, amount=120.0,
            currency="INR", description="second", source_type="test", source_id="2",
        )
        db_session.commit()

        # host_payable: credited 300, debited 120 -> net debit balance = 120 - 300 = -180
        assert ledger_service.get_balance(db_session, host_payable) == -180
        # platform_clearing: debited 300, credited 120 -> net debit balance = 300 - 120 = 180
        assert ledger_service.get_balance(db_session, platform_clearing) == 180

    def test_get_or_create_account_helpers_are_idempotent(self, db_session: Session):
        owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(owner_party)
        db_session.commit()

        a1 = ledger_service.get_party_account(db_session, "HOST_PAYABLE", owner_party.id, "INR")
        a2 = ledger_service.get_party_account(db_session, "HOST_PAYABLE", owner_party.id, "INR")
        assert a1.id == a2.id

        p1 = ledger_service.get_platform_account(db_session, "PLATFORM_CLEARING", "INR")
        p2 = ledger_service.get_platform_account(db_session, "PLATFORM_CLEARING", "INR")
        assert p1.id == p2.id
