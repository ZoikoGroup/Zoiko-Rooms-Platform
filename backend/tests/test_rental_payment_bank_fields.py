"""ZR-PAY-LINK-003 Wireframe D: jurisdiction-aware structured bank fields
for direct payment instructions (GB sort code + account number, an IBAN
country, US routing + account number, and the generic fallback), plus the
Fernet encryption at rest (app/core/field_encryption.py) and the mandatory
"I confirm these instructions belong to the authorized recipient" checkbox.
Covers services/bank_field_schemas.py directly, crud/rental_payment.py:
submit_rental_payment_instruction's new signature, and the reveal-scoping
in api/routes/rental_payments.py:_to_instruction_read (only an authorized
viewer -- the tenant with a due obligation, the recipient themselves, or
restricted super-admin review -- ever gets the decrypted bank_details)."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.field_encryption import decrypt_json
from app.crud import rental_payment as rp_crud
from app.services.bank_field_schemas import resolve_bank_field_schema, validate_bank_details
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie
from tests.test_rental_payment_records import _make_guest, _make_party


class TestBankFieldSchemas:
    def test_gb_requires_sort_code_and_account_number(self):
        validate_bank_details("GB", {"sort_code": "12-34-56", "account_number": "12345678"})
        with pytest.raises(ValueError, match="Sort code"):
            validate_bank_details("GB", {"account_number": "12345678"})
        with pytest.raises(ValueError, match="not in a valid format"):
            validate_bank_details("GB", {"sort_code": "not-a-code", "account_number": "12345678"})

    def test_us_requires_routing_and_account_number(self):
        validate_bank_details("US", {"routing_number": "123456789", "account_number": "12345678"})
        with pytest.raises(ValueError, match="Routing number"):
            validate_bank_details("US", {"account_number": "12345678"})

    def test_iban_country_requires_iban(self):
        validate_bank_details("DE", {"iban": "DE89370400440532013000"})
        with pytest.raises(ValueError, match="IBAN"):
            validate_bank_details("DE", {"iban": "not-an-iban"})

    def test_unrecognized_country_falls_back_to_generic_field_not_an_error(self):
        """Fails open, not closed -- an unconfigured country must never
        brick submission (unlike MarketPolicyPack/ListingFeePolicy, this is
        a UX aid, not a legal gate)."""
        schema = resolve_bank_field_schema("ZZ")
        assert schema.primary_field_key == "account_identifier"
        validate_bank_details("ZZ", {"account_identifier": "0011223344"})
        with pytest.raises(ValueError):
            validate_bank_details("ZZ", {"account_identifier": "abc"})  # too short

    def test_non_bank_transfer_methods_always_get_the_generic_fallback(self):
        """A UK sort code makes no sense for a CASH/CARD/OTHER instruction
        -- country-specific structured fields only apply to BANK_TRANSFER,
        regardless of what country_code happens to be set."""
        for method in ("CASH", "CARD", "OTHER"):
            schema = resolve_bank_field_schema("GB", method)
            assert schema.primary_field_key == "account_identifier"
            validate_bank_details("GB", {"account_identifier": "cash-payment-ref-1"}, method)
            with pytest.raises(ValueError):
                # A real GB sort code/account number pair is NOT what a
                # CASH instruction should require -- the generic field is
                # required instead, so submitting only bank-shaped fields
                # without the generic one is rejected.
                validate_bank_details("GB", {"sort_code": "12-34-56", "account_number": "12345678"}, method)


def _make_obligation(db: Session):
    tenant = _make_guest(db, guest_id="G-BANKFIELDS-1")
    recipient = _make_party(db)
    obligation = rp_crud.create_obligation(
        db, obligation_type="RENT", tenant_guest_id=tenant.id, recipient_party_id=recipient.id,
        amount=850, currency="GBP", due_date=date.today(),
    )
    return obligation, tenant, recipient


class TestSubmitWithStructuredBankDetails:
    def test_gb_details_are_encrypted_and_last4_derived_from_account_number(self, db_session: Session):
        _obligation, _tenant, recipient = _make_obligation(db_session)
        instruction, _code = rp_crud.submit_rental_payment_instruction(
            db_session, recipient, method="BANK_TRANSFER", recipient_name="Example Property Ltd",
            country_code="GB", bank_details={"sort_code": "12-34-56", "account_number": "87654321"},
            authorized_recipient_confirmed=True,
        )
        assert instruction.account_identifier_last4 == "4321"
        assert instruction.country_code == "GB"
        # The real values are never stored in the clear anywhere on the row.
        assert "87654321" not in instruction.encrypted_bank_details
        assert instruction.encrypted_bank_details != ""
        decrypted = decrypt_json(instruction.encrypted_bank_details)
        assert decrypted == {"sort_code": "12-34-56", "account_number": "87654321"}

    def test_cash_instruction_uses_the_generic_field_and_clears_country(self, db_session: Session):
        _obligation, _tenant, recipient = _make_obligation(db_session)
        instruction, _code = rp_crud.submit_rental_payment_instruction(
            db_session, recipient, method="CASH", recipient_name="Example Property Ltd",
            country_code="GB", bank_details={"account_identifier": "cash-handoff-ref-1"},
            authorized_recipient_confirmed=True,
        )
        # country_code is only meaningful alongside real bank routing
        # fields -- persisting whatever the form happened to have selected
        # for a CASH instruction would be a confusing leftover.
        assert instruction.country_code == ""
        assert instruction.account_identifier_last4 == "cash-handoff-ref-1"[-4:]
        decrypted = decrypt_json(instruction.encrypted_bank_details)
        assert decrypted == {"account_identifier": "cash-handoff-ref-1"}

    def test_cash_instruction_rejects_bank_shaped_fields_without_the_generic_one(self, db_session: Session):
        """A GB sort code + account number pair alone doesn't satisfy a
        CASH instruction -- the generic account_identifier field is what's
        actually required for a non-bank-transfer method."""
        _obligation, _tenant, recipient = _make_obligation(db_session)
        with pytest.raises(HTTPException) as exc:
            rp_crud.submit_rental_payment_instruction(
                db_session, recipient, method="CASH", recipient_name="Example Property Ltd",
                country_code="GB", bank_details={"sort_code": "12-34-56", "account_number": "87654321"},
                authorized_recipient_confirmed=True,
            )
        assert exc.value.status_code == 400

    def test_rejects_when_authorized_recipient_confirmed_is_false(self, db_session: Session):
        _obligation, _tenant, recipient = _make_obligation(db_session)
        with pytest.raises(HTTPException) as exc:
            rp_crud.submit_rental_payment_instruction(
                db_session, recipient, method="BANK_TRANSFER", recipient_name="Example Property Ltd",
                country_code="GB", bank_details={"sort_code": "12-34-56", "account_number": "87654321"},
                authorized_recipient_confirmed=False,
            )
        assert exc.value.status_code == 400
        assert "confirm" in exc.value.detail.lower()

    def test_rejects_invalid_field_format(self, db_session: Session):
        _obligation, _tenant, recipient = _make_obligation(db_session)
        with pytest.raises(HTTPException) as exc:
            rp_crud.submit_rental_payment_instruction(
                db_session, recipient, method="BANK_TRANSFER", recipient_name="Example Property Ltd",
                country_code="US", bank_details={"routing_number": "123", "account_number": "87654321"},
                authorized_recipient_confirmed=True,
            )
        assert exc.value.status_code == 400

    def test_destination_novelty_risk_signal_still_works_on_structured_data(self, db_session: Session):
        """A change to just the sort code (account number unchanged) must
        still count as a novel destination -- the fingerprint covers the
        whole dict, not only the primary field."""
        _obligation, _tenant, recipient = _make_obligation(db_session)
        first, first_code = rp_crud.submit_rental_payment_instruction(
            db_session, recipient, method="BANK_TRANSFER", recipient_name="Example Property Ltd",
            country_code="GB", bank_details={"sort_code": "12-34-56", "account_number": "87654321"},
            authorized_recipient_confirmed=True,
        )
        rp_crud.confirm_rental_payment_instruction(db_session, first, first_code)

        second, _second_code = rp_crud.submit_rental_payment_instruction(
            db_session, recipient, method="BANK_TRANSFER", recipient_name="Example Property Ltd",
            country_code="GB", bank_details={"sort_code": "99-99-99", "account_number": "87654321"},
            authorized_recipient_confirmed=True,
        )
        assert second.is_high_risk is True
        assert "not been used" in second.high_risk_reason


class TestBankDetailsRevealScoping:
    def _setup(self, db: Session):
        tenant_user = _make_user(db, email="bf-tenant@test.com")
        recipient_user = _make_user(db, email="bf-recipient@test.com")
        party = _make_party(db)
        recipient_user.party_id = party.id
        guest = _make_guest(db, guest_id="G-BANKFIELDS-HTTP")
        guest.user_account_id = tenant_user.id
        db.flush()
        obligation = rp_crud.create_obligation(
            db, obligation_type="RENT", tenant_guest_id=guest.id, recipient_party_id=party.id,
            amount=850, currency="GBP", due_date=date.today(),
        )
        instruction, code = rp_crud.submit_rental_payment_instruction(
            db, party, method="BANK_TRANSFER", recipient_name="Example Property Ltd",
            country_code="GB", bank_details={"sort_code": "12-34-56", "account_number": "87654321"},
            authorized_recipient_confirmed=True,
        )
        rp_crud.confirm_rental_payment_instruction(db, instruction, code)
        return tenant_user, recipient_user, party, obligation

    def test_the_tenant_with_a_due_obligation_sees_the_real_details(self, client, db_session: Session):
        tenant_user, _recipient_user, _party, obligation = self._setup(db_session)
        r = client.get(f"/api/users/rental-payments/obligations/{obligation.id}/instructions", cookies=auth_user_cookie(tenant_user))
        assert r.status_code == 200, r.text
        assert r.json()["bankDetails"] == {"sort_code": "12-34-56", "account_number": "87654321"}

    def test_the_recipient_sees_their_own_real_details(self, client, db_session: Session):
        _tenant_user, recipient_user, _party, _obligation = self._setup(db_session)
        r = client.get("/api/users/rental-payments/recipient/instructions", cookies=auth_user_cookie(recipient_user))
        assert r.status_code == 200, r.text
        assert r.json()[0]["bankDetails"] == {"sort_code": "12-34-56", "account_number": "87654321"}

    def test_super_admin_review_sees_the_real_details(self, client, db_session: Session):
        _tenant_user, _recipient_user, party, _obligation = self._setup(db_session)
        admin = _make_admin(db_session, email="bf-admin@test.com", role="super_admin")
        r = client.get(f"/api/finance/rental-payments/instructions/{party.id}", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert r.json()[0]["bankDetails"] == {"sort_code": "12-34-56", "account_number": "87654321"}

    def test_a_tenant_with_no_relationship_to_this_obligation_is_rejected(self, client, db_session: Session):
        """Not merely masked -- a tenant with no relationship to this
        obligation must not be able to reach the instruction row at all
        (assert_tenant_owns_obligation rejects before _to_instruction_read
        ever runs)."""
        _tenant_user, _recipient_user, _party, obligation = self._setup(db_session)
        outsider = _make_user(db_session, email="bf-outsider@test.com")
        outsider_guest = _make_guest(db_session, guest_id="G-BANKFIELDS-OUTSIDER")
        outsider_guest.user_account_id = outsider.id
        db_session.flush()
        r = client.get(
            f"/api/users/rental-payments/obligations/{obligation.id}/instructions", cookies=auth_user_cookie(outsider),
        )
        assert r.status_code == 403, r.text
