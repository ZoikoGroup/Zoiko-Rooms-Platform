"""ZR-PAY-METHODS-GLOBAL-001 -- the global payment-method taxonomy:
canonical categories with conditional, country-configured rails; dynamic
reference rules; write-time validation; duplicate warnings; label
snapshots; and the configuration API."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.crud import rental_payment as rp_crud
from app.crud.guest import get_guest_for_user
from app.models.party import Party
from app.services import payment_method_catalog as catalog
from tests.conftest import auth_user_cookie
from tests.test_direct_rent_payment_model import _host_user_for
from tests.test_rental_payment_legacy_bridge import _create_signed_agreement, _rental_payment_obligations_for


def _codes(country: str, context: str = "rent_record") -> list[str]:
    return [c.code for c in catalog.available_categories(country, context)]


def _rails(category: str, country: str) -> list[str]:
    entry = catalog.get_category(category)
    return [s.code for s in catalog.available_subtypes(entry, country, date.today())]


class TestTaxonomy:
    def test_upi_is_never_a_top_level_category(self):
        assert "UPI" not in catalog.PAYMENT_METHOD_CATEGORIES
        for country in ("IN", "GB", "US", ""):
            assert "UPI" not in _codes(country)

    def test_the_canonical_categories_are_the_standard_list_in_order(self):
        assert catalog.PAYMENT_METHOD_CATEGORIES == (
            "BANK_TRANSFER", "INSTANT_BANK_PAYMENT", "BANK_DEBIT", "STANDING_ORDER", "PAYMENT_CARD",
            "DIGITAL_WALLET_P2P", "MOBILE_MONEY", "REMITTANCE_SERVICE", "CASH", "CHEQUE_CHECK", "BANK_DRAFT",
            "MONEY_ORDER", "OTHER",
        )

    def test_upi_is_an_instant_rail_offered_in_india_only(self):
        assert "UPI" in _rails("INSTANT_BANK_PAYMENT", "IN")
        assert "UPI" not in _rails("INSTANT_BANK_PAYMENT", "GB")
        assert "FASTER_PAYMENTS" in _rails("BANK_TRANSFER", "GB")
        assert "NEFT" not in _rails("BANK_TRANSFER", "GB")

    def test_local_methods_come_first(self):
        assert _codes("IN")[0] == "INSTANT_BANK_PAYMENT"
        assert _codes("KE")[0] == "MOBILE_MONEY"
        assert _codes("GB")[0] == "BANK_TRANSFER"

    def test_mobile_money_is_only_shown_where_configured(self):
        assert "MOBILE_MONEY" in _codes("KE")
        assert "MOBILE_MONEY" not in _codes("GB")

    def test_unknown_country_fails_open_to_the_global_list(self):
        assert set(_codes("")) == set(catalog.PAYMENT_METHOD_CATEGORIES)

    def test_context_filters_categories(self):
        assert "STANDING_ORDER" in _codes("GB", "rent_record")
        assert "STANDING_ORDER" not in _codes("GB", "deposit_record")
        assert "BANK_DEBIT" not in _codes("GB", "return_record")

    def test_excluded_methods_cannot_be_recorded(self):
        for method in ("CRYPTO", "GIFT_CARD", "BNPL"):
            with pytest.raises(HTTPException):
                catalog.resolve_selection(category=method, context="rent_record", country="GB")

    def test_retired_codes_still_have_labels(self):
        assert catalog.method_label("UPI") == "Instant Bank Payment / Pay by Bank · UPI"
        assert catalog.method_label("BANK_TRANSFER", "CHAPS") == "Bank Transfer · CHAPS"


class TestResolveSelection:
    def test_legacy_upi_maps_onto_the_instant_rail(self):
        sel = catalog.resolve_selection(category="UPI", context="rent_record", country="IN")
        assert (sel.category, sel.subtype) == ("INSTANT_BANK_PAYMENT", "UPI")
        assert sel.config_version == catalog.CATALOG_VERSION

    def test_a_rail_from_another_country_is_refused(self):
        with pytest.raises(HTTPException) as exc:
            catalog.resolve_selection(category="INSTANT_BANK_PAYMENT", subtype="UPI", context="rent_record", country="GB")
        assert exc.value.status_code == 400

    def test_an_instant_payment_needs_its_rail(self):
        with pytest.raises(HTTPException):
            catalog.resolve_selection(category="INSTANT_BANK_PAYMENT", context="rent_record", country="IN")

    def test_bank_transfer_rail_is_optional(self):
        sel = catalog.resolve_selection(category="BANK_TRANSFER", context="rent_record", country="GB")
        assert sel.subtype == "" and sel.label == "Bank Transfer"

    def test_other_needs_a_description(self):
        with pytest.raises(HTTPException):
            catalog.resolve_selection(category="OTHER", context="rent_record", country="GB")
        sel = catalog.resolve_selection(category="OTHER", description="Paid via employer", context="rent_record")
        assert sel.label == "Other · Paid via employer"

    def test_cash_needs_no_reference(self):
        sel = catalog.resolve_selection(category="CASH", subtype="CASH_HANDED_DIRECTLY", context="rent_record")
        assert sel.reference == ""

    def test_a_full_card_number_is_never_stored(self):
        with pytest.raises(HTTPException):
            catalog.resolve_selection(
                category="PAYMENT_CARD", subtype="DEBIT_CARD", reference="4111 1111 1111 1111", context="rent_record",
            )
        catalog.resolve_selection(category="PAYMENT_CARD", subtype="DEBIT_CARD", reference="1111", context="rent_record")

    def test_reference_wording_follows_the_rail(self):
        upi = next(s for s in catalog.get_category("INSTANT_BANK_PAYMENT").subtypes if s.code == "UPI")
        rules = catalog.field_rules(catalog.get_category("INSTANT_BANK_PAYMENT"), upi)
        assert rules["reference_label"] == "UPI transaction ID / UTR"
        assert "UTR" not in catalog.field_rules(catalog.get_category("CASH"), None)["reference_label"]


class TestRecording:
    def _setup(self, client, db_session: Session, suffix: str):
        user, _cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix=suffix)
        obligations = _rental_payment_obligations_for(db_session, agreement_id)
        recipient = db_session.get(Party, obligations["RENT"].recipient_party_id)
        return user, obligations, recipient

    def test_the_record_keeps_category_rail_and_label_separately(self, client, db_session: Session):
        _user, obligations, recipient = self._setup(client, db_session, "gpm-1")
        record = rp_crud.record_receipt_as_recipient(
            db_session, recipient, obligations["RENT"], amount=100, received_date=date.today(),
            payment_method_category="BANK_TRANSFER", payment_method_subtype="FASTER_PAYMENTS", external_reference="FP1",
        )
        assert record.payment_method_category == "BANK_TRANSFER"
        assert record.payment_method_subtype == "FASTER_PAYMENTS"
        assert record.payment_method_label == "Bank Transfer · Faster Payments"
        assert record.payment_method_config_version == catalog.CATALOG_VERSION

    def test_upi_on_an_indian_property_is_stored_as_an_instant_rail(self, client, db_session: Session):
        user, obligations, _recipient = self._setup(client, db_session, "gpm-2")
        rent = obligations["RENT"]
        rent.room.property.country_code = "IN"
        db_session.commit()
        guest = get_guest_for_user(db_session, user)
        record = rp_crud.mark_paid(
            db_session, guest, rent, amount=100, currency=rent.currency, declared_date=date.today(),
            payment_method_category="INSTANT_BANK_PAYMENT", payment_method_subtype="UPI", external_reference="UTR1",
            note="October rent",
        )
        assert (record.payment_method_category, record.payment_method_subtype) == ("INSTANT_BANK_PAYMENT", "UPI")
        assert record.note == "October rent"
        assert record.status == "PAYER_RECORDED"

    def test_a_possible_duplicate_is_warned_then_allowed_once_confirmed(self, client, db_session: Session):
        _user, obligations, recipient = self._setup(client, db_session, "gpm-3")
        rent = obligations["RENT"]
        kwargs = dict(amount=100, received_date=date.today(), payment_method_category="CASH")
        rp_crud.record_receipt_as_recipient(db_session, recipient, rent, **kwargs)
        db_session.refresh(rent)
        with pytest.raises(HTTPException) as exc:
            rp_crud.record_receipt_as_recipient(db_session, recipient, rent, **kwargs)
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == rp_crud.POSSIBLE_DUPLICATE_CODE
        rp_crud.record_receipt_as_recipient(db_session, recipient, rent, confirm_not_duplicate=True, **kwargs)

    def test_different_references_are_not_duplicates(self, client, db_session: Session):
        _user, obligations, recipient = self._setup(client, db_session, "gpm-4")
        rent = obligations["RENT"]
        for ref in ("A1", "B2"):
            rp_crud.record_receipt_as_recipient(
                db_session, recipient, rent, amount=100, received_date=date.today(),
                payment_method_category="BANK_TRANSFER", external_reference=ref,
            )
            db_session.refresh(rent)

    def test_a_method_correction_appends_and_refreshes_the_label(self, client, db_session: Session):
        _user, obligations, recipient = self._setup(client, db_session, "gpm-5")
        record = rp_crud.record_receipt_as_recipient(
            db_session, recipient, obligations["RENT"], amount=100, received_date=date.today(),
            payment_method_category="CASH",
        )
        from app.models.admin_user import AdminUser

        admin = db_session.query(AdminUser).first() or AdminUser(email="gpm-admin@test.com", password_hash="x")
        db_session.add(admin)
        db_session.commit()
        correction = rp_crud.append_correction(
            db_session, admin, record, field_name="payment_method_category",
            new_value="BANK_TRANSFER:CHAPS", reason="wrong method",
        )
        assert correction.previous_value == "CASH"
        assert record.payment_method_label == "Bank Transfer · CHAPS"


class TestApi:
    def test_the_catalog_endpoint_returns_local_rails(self, client, db_session: Session):
        from tests.conftest import _make_user

        user = _make_user(db_session, email="gpm-api@test.com")
        r = client.get("/api/users/payment-methods?country=IN&context=rent_record", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["categories"][0]["code"] == "INSTANT_BANK_PAYMENT"
        instant = body["categories"][0]
        assert instant["subtypeRequired"] is True
        assert "UPI" in [s["code"] for s in instant["subtypes"]]

        r = client.get(
            "/api/users/payment-methods/INSTANT_BANK_PAYMENT/UPI/field-rules?country=IN", cookies=auth_user_cookie(user),
        )
        assert r.json()["referenceLabel"] == "UPI transaction ID / UTR"

    def test_the_host_api_records_a_rail(self, client, db_session: Session):
        user, _cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix="gpm-api2")
        rent = _rental_payment_obligations_for(db_session, agreement_id)["RENT"]
        host_cookies = auth_user_cookie(_host_user_for(db_session, rent.recipient_party_id))
        body = {"receivedDate": date.today().isoformat(), "amount": 50, "paymentMethodCategory": "BANK_TRANSFER",
                "paymentMethodSubtype": "CHAPS"}
        url = f"/api/users/rental-payments/recipient/obligations/{rent.id}/record-receipt"
        r = client.post(url, json=body, cookies=host_cookies)
        assert r.status_code == 201, r.text
        record = r.json()["records"][-1]
        assert record["paymentMethodLabel"] == "Bank Transfer · CHAPS"
        assert r.json()["countryCode"] == "GB"

        dup = client.post(url, json=body, cookies=host_cookies)
        assert dup.status_code == 409


class TestOverpayment:
    """Section 10: an overpayment is never silently applied -- it needs an
    explicit acknowledgement, counts only what was owed, and the excess
    waits (REVIEW_REQUIRED) for the recipient to apply or return it."""

    def _setup(self, client, db_session: Session, suffix: str):
        user, _cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix=suffix)
        obligations = _rental_payment_obligations_for(db_session, agreement_id)
        recipient = db_session.get(Party, obligations["RENT"].recipient_party_id)
        return user, obligations, recipient

    def test_an_unacknowledged_overpayment_is_refused(self, client, db_session: Session):
        _user, obligations, recipient = self._setup(client, db_session, "gpm-op1")
        rent = obligations["RENT"]
        with pytest.raises(HTTPException) as exc:
            rp_crud.record_receipt_as_recipient(
                db_session, recipient, rent, amount=float(rent.amount) + 50, received_date=date.today(),
                payment_method_category="CASH",
            )
        assert exc.value.detail["code"] == rp_crud.OVERPAYMENT_CODE

    def test_host_applies_the_excess_to_another_payment(self, client, db_session: Session):
        _user, obligations, recipient = self._setup(client, db_session, "gpm-op2")
        rent, deposit = obligations["RENT"], obligations["DEPOSIT"]
        record = rp_crud.record_receipt_as_recipient(
            db_session, recipient, rent, amount=float(rent.amount) + 50, received_date=date.today(),
            payment_method_category="BANK_TRANSFER", acknowledge_overpayment=True,
        )
        assert float(record.confirmed_amount) == float(rent.amount)
        assert float(record.excess_amount) == 50
        assert record.excess_status == "REVIEW_REQUIRED"
        db_session.refresh(rent)
        assert rent.outstanding_amount == 0

        deposit_before = deposit.outstanding_amount
        allocation = rp_crud.apply_excess_to_obligation(db_session, recipient, record, target_obligation_id=deposit.id)
        assert allocation.action == "APPLY_TO_OBLIGATION" and float(allocation.amount) == 50
        db_session.refresh(deposit)
        db_session.refresh(record)
        assert deposit.outstanding_amount == round(deposit_before - 50, 2)
        assert record.excess_status == "RESOLVED" and record.excess_remaining == 0
        # The original payment is never edited -- the excess lives in its own records.
        assert float(record.declared_amount) == float(rent.amount) + 50

    def test_renter_overpays_and_the_host_sends_the_extra_back(self, client, db_session: Session):
        user, obligations, recipient = self._setup(client, db_session, "gpm-op3")
        rent = obligations["RENT"]
        guest = get_guest_for_user(db_session, user)
        record = rp_crud.mark_paid(
            db_session, guest, rent, amount=float(rent.amount) + 20, currency=rent.currency, declared_date=date.today(),
            payment_method_category="BANK_TRANSFER", acknowledge_overpayment=True,
        )
        assert record.excess_status == "REVIEW_REQUIRED"
        rp_crud.confirm_receipt(db_session, recipient, record)
        db_session.refresh(record)
        assert float(record.confirmed_amount) == float(rent.amount) and float(record.excess_amount) == 20

        with pytest.raises(HTTPException):
            rp_crud.return_excess_to_payer(
                db_session, recipient, record, amount=21, returned_date=date.today(), payment_method_category="CASH",
            )
        rp_crud.return_excess_to_payer(
            db_session, recipient, record, amount=5, returned_date=date.today(), payment_method_category="CASH",
        )
        db_session.refresh(record)
        assert record.excess_status == "REVIEW_REQUIRED" and record.excess_remaining == 15
        rp_crud.return_excess_to_payer(
            db_session, recipient, record, returned_date=date.today(), payment_method_category="BANK_TRANSFER",
            payment_method_subtype="FASTER_PAYMENTS", external_reference="RET-1",
        )
        db_session.refresh(record)
        assert record.excess_status == "RESOLVED"

    def test_confirming_less_than_owed_leaves_no_excess(self, client, db_session: Session):
        user, obligations, recipient = self._setup(client, db_session, "gpm-op4")
        rent = obligations["RENT"]
        guest = get_guest_for_user(db_session, user)
        record = rp_crud.mark_paid(
            db_session, guest, rent, amount=float(rent.amount) + 20, currency=rent.currency, declared_date=date.today(),
            payment_method_category="CASH", acknowledge_overpayment=True,
        )
        rp_crud.confirm_receipt(db_session, recipient, record, amount=100)
        db_session.refresh(record)
        assert float(record.confirmed_amount) == 100 and float(record.excess_amount) == 0
        assert record.excess_status == ""


class TestCanonicalInstructionsAndSublets:
    def test_instruction_rail_is_checked_against_its_country(self, db_session: Session):
        from tests.test_rental_payment_bank_fields import _make_obligation

        _obligation, _tenant, recipient = _make_obligation(db_session)
        with pytest.raises(HTTPException):
            rp_crud.submit_rental_payment_instruction(
                db_session, recipient, method="INSTANT_BANK_PAYMENT", method_subtype="UPI",
                recipient_name="Host", country_code="GB", bank_details={"upi_id": "a@okhdfc"},
                authorized_recipient_confirmed=True,
            )
        with pytest.raises(HTTPException):  # an instant payment must name its rail
            rp_crud.submit_rental_payment_instruction(
                db_session, recipient, method="INSTANT_BANK_PAYMENT", recipient_name="Host", country_code="AU",
                bank_details={"payment_alias": "host@example.com"}, authorized_recipient_confirmed=True,
            )
        instruction, _code = rp_crud.submit_rental_payment_instruction(
            db_session, recipient, method="INSTANT_BANK_PAYMENT", method_subtype="NPP_PAYID_OSKO",
            recipient_name="Host", country_code="AU", bank_details={"payment_alias": "host@example.com"},
            authorized_recipient_confirmed=True,
        )
        assert instruction.method_subtype == "NPP_PAYID_OSKO"
        assert instruction.account_identifier_last4 == ".com"

    def test_sublet_accepted_methods_are_canonical(self):
        from app.services.sublet_payments import normalize_methods

        assert normalize_methods(["BANK_TRANSFER", "UPI", "CASH", "CRYPTO"]) == [
            "BANK_TRANSFER", "INSTANT_BANK_PAYMENT", "CASH",
        ]
        assert normalize_methods(["UPI", "INSTANT_BANK_PAYMENT"]) == ["INSTANT_BANK_PAYMENT"]
