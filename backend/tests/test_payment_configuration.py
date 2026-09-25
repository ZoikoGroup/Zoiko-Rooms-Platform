"""ZR-PAY-CFG-001: Zoiko Rooms collects its own Listing Fee only. Covers the
QA acceptance gates PAY-CFG-01..12 that aren't already covered elsewhere:
the money-movement boundary, production boot guard, Price Book lifecycle,
fail-closed pricing, tax behavior, quote TTL, billing entity snapshot,
environment isolation and PAYMENT_RECEIPT authority."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.crud import listing_fee as listing_fee_crud
from app.crud import rental_payment as rp_crud
from app.crud.guest import get_guest_for_user
from app.models.billing_entity import BillingEntity
from app.models.listing import Listing
from app.models.listing_fee import LISTING_FEE_QUOTE_TTL_SECONDS, ListingFeePolicy
from app.models.market_release import MarketRelease
from app.models.party import Party
from app.models.payment_recipient_authority import PaymentRecipientAuthority
from app.models.property import Property
from app.models.room import Room
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie
from tests.test_rental_payment_legacy_bridge import _create_signed_agreement, _rental_payment_obligations_for

boundary = pytest.mark.payment_boundary


# ---------------------------------------------------------------- helpers

def _market(db: Session, code: str = "England") -> MarketRelease:
    release = db.query(MarketRelease).filter_by(jurisdiction=code).first()
    if release is None:
        release = MarketRelease(jurisdiction=code, status="active", min_stay_nights=30)
        db.add(release)
        db.flush()
    return release


def _entity(db: Session, *, markets=("England",), currencies=("GBP",), code: str = "ZRG_INC") -> BillingEntity:
    entity = BillingEntity(
        code=code, legal_name="Zoiko Realty Group Inc.", trading_name="Zoiko Rooms",
        registered_address="1 Example Way, London", company_registration_number="12345678",
        tax_registration_type="VAT", tax_registration_number="GB123456789",
        supported_markets=list(markets), supported_currencies=list(currencies), effective_from=date(2026, 1, 1),
    )
    db.add(entity)
    db.flush()
    return entity


def _price(db: Session, admin, *, amount: float = 50.0, tax_rate: float = 0.20, tax_behavior: str = "EXCLUSIVE",
           entity: BillingEntity | None = None, tax_rule_reference: str = "UK-VAT-STANDARD-2026", code: str = "England"):
    _market(db, code)
    return listing_fee_crud.create_listing_fee_policy(db, admin, {
        "jurisdiction_code": code, "effective_from": date(2026, 1, 1), "amount": amount, "currency": "GBP",
        "tax_rate": tax_rate, "tax_behavior": tax_behavior, "tax_rule_reference": tax_rule_reference,
        "billing_entity_id": entity.id if entity else None,
    })


def _listing_in_england(db: Session, *, suffix: str) -> tuple[Listing, Party, object]:
    party = Party(party_type="provider", status="active", jurisdiction="England")
    db.add(party)
    db.flush()
    user = _make_user(db, email=f"fee-host-{suffix}@test.com")
    user.party_id = party.id
    prop = Property(owner_party_id=party.id, address="1 Fee St", city="London", jurisdiction_code="England")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=False, status="active")
    db.add(room)
    db.flush()
    listing = Listing(
        id=f"L-FEE-{suffix}", slug=f"fee-{suffix}", name="Fee listing", room_type="Private room", city="London",
        location="Soho", price_per_night=40, guests=1, party_id=party.id, room_id=room.id, state="APPROVED",
        currency="GBP",
    )
    db.add(listing)
    db.commit()
    return listing, party, user


# ---------------------------------------------------------------- PAY-CFG-05: no rental money movement

@boundary
class TestNoRentalMoneyMovement:
    @pytest.mark.parametrize("method,path,body", [
        ("post", "/api/finance/payments", {"guestId": "G-X", "amount": 10, "currency": "GBP", "idempotencyKey": "k"}),
        ("post", "/api/finance/payouts/run", {"partyId": 1, "periodKey": "2026-09"}),
        ("post", "/api/finance/payout-beneficiaries", {}),
        ("post", "/api/finance/host-stripe-accounts", {}),
        ("post", "/api/finance/deposits/1/release", {}),
        ("post", "/api/finance/refunds", {}),
        ("post", "/api/occupancy/refund-entitlements/1/execute", {}),
    ])
    def test_admin_money_movement_routes_are_refused(self, client, db_session: Session, method, path, body):
        admin = _make_admin(db_session, email="boundary-admin@test.com", role="super_admin")
        db_session.commit()
        r = getattr(client, method)(path, json=body, cookies=auth_admin_cookie(admin))
        assert r.status_code == 403, r.text
        assert "Zoiko Rooms" in r.json()["detail"]

    def test_renter_cannot_pay_rent_through_zoiko(self, client, db_session: Session):
        user = _make_user(db_session, email="boundary-renter@test.com")
        db_session.commit()
        r = client.post("/api/users/payments/obligations/1/pay", json={"methodClass": "CARD"}, cookies=auth_user_cookie(user))
        assert r.status_code == 403, r.text
        assert "does not collect rent" in r.json()["detail"]

    def test_capabilities_report_no_money_movement_or_commission(self, client, db_session: Session):
        user = _make_user(db_session, email="boundary-caps@test.com")
        db_session.commit()
        r = client.get("/api/users/payments/capabilities", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        caps = r.json()
        assert caps["rental_money_movement_via_zoiko"] is False
        assert caps["rent_collection_enabled"] is False
        assert caps["host_payouts_enabled"] is False
        assert caps["platform_fee_rate"] is None
        assert caps["listing_fee_enabled"] is True


class TestProductionBootGuard:
    def _prod(self, **overrides):
        from cryptography.fernet import Fernet

        base = dict(
            environment="production", jwt_secret="a" * 48, seed_admin_password="a-secure-password-123",
            cookie_secure=True, field_encryption_key=Fernet.generate_key().decode(), database_url="sqlite://",
        )
        base.update(overrides)
        return Settings(**base)

    def test_production_boots_with_the_baseline(self):
        assert self._prod().rent_collection_enabled is False

    @pytest.mark.parametrize("flag", [
        "rent_collection_enabled", "deposit_collection_enabled", "host_payouts_enabled",
        "escrow_enabled", "wallet_enabled", "split_settlement_enabled",
    ])
    def test_production_refuses_any_rental_money_movement(self, flag):
        with pytest.raises(ValidationError) as exc:
            self._prod(**{flag: True})
        assert flag.upper() in str(exc.value)

    def test_production_refuses_turning_off_fail_closed_rules(self):
        with pytest.raises(ValidationError):
            self._prod(listing_fee_fail_closed=False)
        with pytest.raises(ValidationError):
            self._prod(payment_receipt_authority_required=False)


# ---------------------------------------------------------------- Price Book

@boundary
class TestPriceBookLifecycle:
    def test_new_price_is_a_draft_and_not_chargeable(self, db_session: Session):
        admin = _make_admin(db_session, email="pb1@test.com", role="super_admin")
        price = _price(db_session, admin, entity=_entity(db_session))
        assert price.status == "DRAFT"
        assert price.amount_minor == 5000
        with pytest.raises(HTTPException) as exc:
            listing_fee_crud.resolve_listing_fee_policy(db_session, "England")
        assert exc.value.detail == listing_fee_crud.LISTING_FEE_UNAVAILABLE_MESSAGE

    def test_approval_requires_billing_entity_and_tax_rule(self, db_session: Session):
        admin = _make_admin(db_session, email="pb2@test.com", role="super_admin")
        price = _price(db_session, admin, entity=None, tax_rule_reference="")
        with pytest.raises(HTTPException) as exc:
            listing_fee_crud.approve_listing_fee_policy(db_session, admin, price)
        reasons = exc.value.detail["reasons"]
        assert any("billing entity" in r for r in reasons)
        assert any("tax rule" in r for r in reasons)

    def test_entity_must_support_the_market_and_currency(self, db_session: Session):
        admin = _make_admin(db_session, email="pb3@test.com", role="super_admin")
        price = _price(db_session, admin, entity=_entity(db_session, currencies=("USD",)))
        with pytest.raises(HTTPException) as exc:
            listing_fee_crud.approve_listing_fee_policy(db_session, admin, price)
        assert any("not approved to bill GBP" in r for r in exc.value.detail["reasons"])

    def test_approving_a_new_version_retires_the_previous_one(self, db_session: Session):
        admin = _make_admin(db_session, email="pb4@test.com", role="super_admin")
        entity = _entity(db_session)
        first = listing_fee_crud.approve_listing_fee_policy(db_session, admin, _price(db_session, admin, entity=entity))
        second = listing_fee_crud.approve_listing_fee_policy(
            db_session, admin, _price(db_session, admin, amount=60.0, entity=entity),
        )
        db_session.refresh(first)
        assert first.status == "RETIRED"
        assert second.status == "ACTIVE" and second.version == 2
        assert listing_fee_crud.resolve_listing_fee_policy(db_session, "England").id == second.id

    def test_approved_price_cannot_be_edited(self, db_session: Session):
        admin = _make_admin(db_session, email="pb5@test.com", role="super_admin")
        price = listing_fee_crud.approve_listing_fee_policy(
            db_session, admin, _price(db_session, admin, entity=_entity(db_session)),
        )
        with pytest.raises(HTTPException) as exc:
            listing_fee_crud.update_listing_fee_policy(db_session, admin, price, {"amount": 99.0})
        assert exc.value.status_code == 409

    def test_price_needs_a_configured_market(self, db_session: Session):
        admin = _make_admin(db_session, email="pb6@test.com", role="super_admin")
        with pytest.raises(HTTPException) as exc:
            listing_fee_crud.create_listing_fee_policy(db_session, admin, {
                "jurisdiction_code": "Engalnd", "effective_from": date(2026, 1, 1), "amount": 50.0, "currency": "GBP",
            })
        assert exc.value.status_code == 400

    def test_environment_isolation(self, db_session: Session, monkeypatch):
        """PAY-CFG-12: a price created in staging never resolves anywhere else."""
        admin = _make_admin(db_session, email="pb7@test.com", role="super_admin")
        entity = _entity(db_session)
        monkeypatch.setattr(settings, "environment", "staging")
        staging_price = listing_fee_crud.approve_listing_fee_policy(
            db_session, admin, _price(db_session, admin, entity=entity),
        )
        assert staging_price.environment == "staging"
        assert listing_fee_crud.resolve_listing_fee_policy(db_session, "England").id == staging_price.id
        monkeypatch.setattr(settings, "environment", "development")
        with pytest.raises(HTTPException):
            listing_fee_crud.resolve_listing_fee_policy(db_session, "England")


# ---------------------------------------------------------------- quote, tax, TTL, billing entity snapshot

@boundary
class TestQuote:
    def test_exclusive_tax_is_added_and_snapshot_is_complete(self, db_session: Session):
        admin = _make_admin(db_session, email="q1@test.com", role="super_admin")
        listing_fee_crud.approve_listing_fee_policy(
            db_session, admin, _price(db_session, admin, amount=50.0, tax_rate=0.20, entity=_entity(db_session)),
        )
        listing, party, _user = _listing_in_england(db_session, suffix="q1")
        quote = listing_fee_crud.create_quote(db_session, listing, party)
        assert (quote.amount_minor, quote.tax_amount_minor, quote.total_amount_minor) == (5000, 1000, 6000)
        assert quote.market == "England" and quote.currency == "GBP"
        assert quote.tax_behavior == "EXCLUSIVE" and quote.price_book_version == 1
        assert quote.billing_entity_id == "ZRG_INC"
        assert quote.billing_entity_name == "Zoiko Rooms — a trading name of Zoiko Realty Group Inc."
        assert quote.policy_snapshot["billing_entity"]["company_registration_number"] == "12345678"
        expires_in = (quote.expires_at - quote.created_at).total_seconds()
        assert abs(expires_in - LISTING_FEE_QUOTE_TTL_SECONDS) < 5

    def test_inclusive_tax_is_identified_not_added(self, db_session: Session):
        admin = _make_admin(db_session, email="q2@test.com", role="super_admin")
        listing_fee_crud.approve_listing_fee_policy(
            db_session, admin,
            _price(db_session, admin, amount=60.0, tax_rate=0.20, tax_behavior="INCLUSIVE", entity=_entity(db_session)),
        )
        listing, party, _user = _listing_in_england(db_session, suffix="q2")
        quote = listing_fee_crud.create_quote(db_session, listing, party)
        assert (quote.amount_minor, quote.tax_amount_minor, quote.total_amount_minor) == (5000, 1000, 6000)

    def test_quote_api_returns_the_contract_fields(self, client, db_session: Session):
        admin = _make_admin(db_session, email="q3@test.com", role="super_admin")
        listing_fee_crud.approve_listing_fee_policy(
            db_session, admin, _price(db_session, admin, entity=_entity(db_session)),
        )
        listing, _party, user = _listing_in_england(db_session, suffix="q3")
        r = client.post(f"/api/users/listing-fees/listings/{listing.id}/quotes", cookies=auth_user_cookie(user))
        assert r.status_code == 201, r.text
        body = r.json()
        for key in ("feeAmountMinor", "taxAmountMinor", "totalAmountMinor", "taxBehavior", "priceBookVersion",
                    "billingEntityId", "market", "expiresAt"):
            assert body[key] is not None, key

    def test_no_price_means_no_quote_and_no_publication(self, client, db_session: Session):
        """PAY-CFG-01: fail closed, never a default price or a free publish."""
        listing, party, user = _listing_in_england(db_session, suffix="q4")
        _market(db_session)
        db_session.commit()
        with pytest.raises(HTTPException) as exc:
            listing_fee_crud.create_quote(db_session, listing, party)
        assert exc.value.detail == "Listing fee currently unavailable in this market."

        from app.crud.listing import check_publish_eligibility, publish_listing

        assert "Listing fee currently unavailable in this market." in check_publish_eligibility(db_session, listing)
        admin = _make_admin(db_session, email="q4@test.com", role="super_admin")
        with pytest.raises(HTTPException) as exc:
            publish_listing(db_session, listing, admin)
        assert exc.value.status_code == 409

    def test_expired_quote_is_rejected(self, db_session: Session):
        """PAY-CFG-09."""
        admin = _make_admin(db_session, email="q5@test.com", role="super_admin")
        listing_fee_crud.approve_listing_fee_policy(
            db_session, admin, _price(db_session, admin, entity=_entity(db_session)),
        )
        listing, party, _user = _listing_in_england(db_session, suffix="q5")
        quote = listing_fee_crud.create_quote(db_session, listing, party)
        quote.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db_session.commit()
        with pytest.raises(HTTPException) as exc:
            listing_fee_crud.create_checkout(db_session, quote, party, idempotency_key="exp-1", billing_country="GB")
        assert "expired" in exc.value.detail


# ---------------------------------------------------------------- billing entity API

@boundary
class TestBillingEntityApi:
    def test_super_admin_can_register_an_entity(self, client, db_session: Session):
        admin = _make_admin(db_session, email="be1@test.com", role="super_admin")
        db_session.commit()
        r = client.post(
            "/api/finance/listing-fees/billing-entities",
            json={
                "code": "zrg_inc", "legalName": "Zoiko Realty Group Inc.", "effectiveFrom": "2026-01-01",
                "supportedMarkets": ["England"], "supportedCurrencies": ["gbp"],
            },
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        assert r.json()["code"] == "ZRG_INC"
        assert r.json()["supportedCurrencies"] == ["GBP"]


# ---------------------------------------------------------------- PAY-CFG-06/07: PAYMENT_RECEIPT authority

class TestPaymentReceiptAuthority:
    """Uses the legacy fixture for the agreement setup, then switches the
    authority rule on for the assertion."""

    def _signed_agreement_obligation(self, client, db_session: Session, suffix: str):
        user, _cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix=suffix)
        rent = _rental_payment_obligations_for(db_session, agreement_id)["RENT"]
        return user, rent

    def test_instructions_hidden_without_verified_authority(self, client, db_session: Session, monkeypatch):
        user, rent = self._signed_agreement_obligation(client, db_session, "auth1")
        monkeypatch.setattr(settings, "payment_receipt_authority_required", True)
        r = client.get(
            f"/api/users/rental-payments/obligations/{rent.id}/instructions", cookies=auth_user_cookie(user),
        )
        assert r.status_code == 409, r.text
        assert "hasn't been verified" in r.json()["detail"]

    def test_recipient_cannot_confirm_receipt_without_authority(self, client, db_session: Session, monkeypatch):
        user, rent = self._signed_agreement_obligation(client, db_session, "auth2")
        guest = get_guest_for_user(db_session, user)
        recipient = db_session.get(Party, rent.recipient_party_id)
        record = rp_crud.mark_paid(
            db_session, guest, rent, amount=float(rent.amount), currency=rent.currency,
            declared_date=date.today(), payment_method_category="BANK_TRANSFER",
        )
        monkeypatch.setattr(settings, "payment_receipt_authority_required", True)
        with pytest.raises(HTTPException) as exc:
            rp_crud.confirm_receipt(db_session, recipient, record)
        assert exc.value.status_code == 409

        db_session.add(PaymentRecipientAuthority(
            party_id=recipient.id, room_id=rent.room.id, relationship_type="OWNER", status="verified",
        ))
        db_session.commit()
        confirmed = rp_crud.confirm_receipt(db_session, recipient, record)
        assert confirmed.status == "CONFIRMED"


# ---------------------------------------------------------------- external handoff is market-approved only

class TestExternalHandoffMarketApproval:
    def test_handoff_refused_unless_the_market_approved_it(self, client, db_session: Session, monkeypatch):
        from app.crud import external_payment_session as session_crud
        from app.services import policy

        user, _cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix="handoff1")
        rent = _rental_payment_obligations_for(db_session, agreement_id)["RENT"]
        guest = get_guest_for_user(db_session, user)
        monkeypatch.setitem(policy._DEFAULTS, "payment.external_handoff_approved", lambda: False)
        with pytest.raises(HTTPException) as exc:
            session_crud.create_session(db_session, guest, rent, success_url="http://x", cancel_url="http://x")
        assert "isn't available in this market" in exc.value.detail
