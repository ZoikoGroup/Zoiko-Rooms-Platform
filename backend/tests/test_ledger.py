"""Tests for the ZR-ENG-CLR-005 ledger foundation (app/services/ledger.py) --
confirms crud/finance.py's wiring in confirm_payment, run_payout,
release_deposit, forfeit_deposit and decide_refund actually posts correct,
balanced double-entry LedgerEntry rows, without changing any of those
functions' existing behavior (see the other test_finance_*.py files, which
must keep passing unmodified alongside these)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import payment_provider as payment_provider_crud
from app.models.authority_record import AuthorityRecord
from app.models.finance import LedgerAccount, LedgerEntry, Obligation, PayoutBeneficiary, RefundRequest
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
    type, plus a verified AuthorityRecord for the room and a VERIFIED
    PayoutBeneficiary for the party (so run_payout tests against it are
    eligible for PAID, not HELD -- AC-30's beneficiary gate would otherwise
    shadow whatever other gate a given test is targeting). Returns
    (obligation, admin, guest, owner_party_id). Pass an existing
    owner_party_id to create a second, independent obligation for the SAME
    provider party (e.g. to test something that depends on that party
    already having other state, like an existing ledger hold) -- the
    beneficiary is only created once per party, not duplicated."""
    admin = _make_admin(db, email=f"ledger-admin-{suffix}@test.com", role="super_admin")

    if owner_party_id is not None:
        owner_party = db.get(Party, owner_party_id)
    else:
        owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db.add(owner_party)
        db.flush()
        db.add(PayoutBeneficiary(
            party_id=owner_party.id, account_holder_name="Test Landlord", bank_name="Test Bank",
            account_number_last4="1234", bank_identifier_code="TEST0123456", status="VERIFIED",
            verified_at=datetime.now(timezone.utc),
        ))
    # Explicitly "IN" (not this platform's default jurisdiction, "England")
    # -- these tests exercise the "IN" market policy pack's own fields.
    prop = Property(
        owner_party_id=owner_party.id, address=f"{suffix} Ledger St", city="Bengaluru", status="active",
        jurisdiction_code="IN",
    )
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


class TestExternalPaymentEvidence:
    """Section 5 gap: an off-platform cash/cheque confirm previously had no
    field at all to attach a receipt/reference to -- now captured (but not
    hard-required, to avoid breaking the many existing fixtures across this
    suite that confirm an EXTERNAL payment purely as setup)."""

    def test_evidence_ref_is_stored_when_given(self, client, db_session: Session):
        obligation, admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="evid1")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "evid-payment-1"},
            cookies=admin_cookies,
        )
        payment_id = r.json()["id"]

        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}], "evidenceRef": "cheque #4471"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["evidenceRef"] == "cheque #4471"

    def test_evidence_ref_defaults_to_blank_when_omitted(self, client, db_session: Session):
        obligation, admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="evid2")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "evid-payment-2"},
            cookies=admin_cookies,
        )
        payment_id = r.json()["id"]

        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["evidenceRef"] == ""


class TestRunPayoutPostsNetEntryOnly:
    """ZR-PAY-CFG-001 Decision 3: no commission, so a payout posts only the
    net-to-host entry -- never a PLATFORM_FEE_REVENUE entry."""

    def test_payout_posts_a_single_net_entry_for_the_full_gross(self, client, db_session: Session):
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
        assert len(entries) == 1
        [net_entry] = entries
        assert net_entry.description == "Payout paid to host"
        assert float(net_entry.amount) == 1000.0

        net_debit = db_session.get(LedgerAccount, net_entry.debit_account_id)
        net_credit = db_session.get(LedgerAccount, net_entry.credit_account_id)
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


class TestDecideRefundReversesRealPspTransaction:
    """Section 5 gap: decide_refund previously only ever reversed Zoiko's
    own ledger -- it never actually pulled the money back out of Stripe.
    Stripe isn't configured in tests, so stripe_client.create_refund falls
    back to its simulated id the same way create_payment_intent already
    does -- what's under test is that decide_refund calls it at all, and
    only when there's a real ProcessorTransaction to reverse."""

    def test_refunding_a_psp_dispatched_payment_sets_a_psp_refund_id(self, client, db_session: Session):
        from app.core.config import settings

        obligation, admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="pspref1", amount=500.0)
        admin_cookies = auth_admin_cookie(admin)
        # renter_pay_obligation attributes the synthetic-callback step to the
        # platform's seeded system admin (get_system_admin) -- give tests one.
        _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")

        payment = payment_provider_crud.renter_pay_obligation(db_session, guest, obligation, method_class="CARD")
        assert payment.status == "SUCCEEDED"

        r = client.post(
            "/api/finance/refunds",
            json={
                "paymentId": payment.id, "obligationId": obligation.id, "amount": 500.0, "reason": "test",
                "idempotencyKey": "psp-refund-request-1",
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        refund_id = r.json()["id"]

        r = client.post(f"/api/finance/refunds/{refund_id}/decide", json={"approve": True}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["pspRefundId"] != ""

        refund = db_session.get(RefundRequest, refund_id)
        assert refund.psp_refund_id.startswith("RE-")

    def test_refunding_an_external_cash_payment_leaves_psp_refund_id_blank(self, client, db_session: Session):
        obligation, admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="pspref2", amount=500.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "ext-refund-1"},
            cookies=admin_cookies,
        )
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
                "idempotencyKey": "ext-refund-request-1",
            },
            cookies=admin_cookies,
        )
        refund_id = r.json()["id"]
        r = client.post(f"/api/finance/refunds/{refund_id}/decide", json={"approve": True}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["pspRefundId"] == ""


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
