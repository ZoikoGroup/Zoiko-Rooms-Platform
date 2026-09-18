"""ZR-ENG-CLR-005 AC-30/Section 9.2: a Party's payout destination is a
verified PayoutBeneficiary, not the Party row itself. Submitting one starts
PENDING_VERIFICATION; only confirming with the emailed one-time code makes it
VERIFIED (the strong-auth control) and supersedes whatever was VERIFIED
before. run_payout now refuses (HELD) any party with no currently VERIFIED
beneficiary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import payout_beneficiary as beneficiary_crud
from app.models.finance import PayoutBeneficiary
from app.models.party import Party
from app.schemas.finance import PayoutBeneficiarySubmit
from tests.conftest import _make_admin, auth_admin_cookie
from tests.test_ledger import _make_provider_rent_obligation


def _submit_payload(party_id: int, **overrides) -> dict:
    payload = {
        "partyId": party_id,
        "accountHolderName": "Jane Landlord",
        "bankName": "Test Bank of India",
        "accountNumber": "000123456789",
        "bankIdentifierCode": "test0123456",
    }
    payload.update(overrides)
    return payload


def _submit_payload_dict(party_id: int) -> dict:
    return {
        "party_id": party_id, "account_holder_name": "Jane Landlord", "bank_name": "Test Bank of India",
        "account_number": "000123456789", "bank_identifier_code": "TEST0123456",
    }


class TestSubmitPayoutBeneficiary:
    def test_submitting_creates_a_pending_verification_row_masking_the_account_number(self, client, db_session: Session):
        owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(owner_party)
        db_session.commit()
        admin = _make_admin(db_session, email="benef-submit1@test.com", role="super_admin")

        r = client.post(
            "/api/finance/payout-beneficiaries", json=_submit_payload(owner_party.id), cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "PENDING_VERIFICATION"
        assert body["accountNumberLast4"] == "6789"
        assert body["bankIdentifierCode"] == "TEST0123456"
        assert "accountNumber" not in body
        assert "code" not in body

        row = db_session.get(PayoutBeneficiary, body["id"])
        assert row.verification_code_hash is not None
        assert row.verification_code_hash != "000123456789"

    def test_rejects_a_malformed_account_number(self, client, db_session: Session):
        owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(owner_party)
        db_session.commit()
        admin = _make_admin(db_session, email="benef-badacct@test.com", role="super_admin")

        r = client.post(
            "/api/finance/payout-beneficiaries",
            json=_submit_payload(owner_party.id, accountNumber="12AB"),
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 400, r.text

    def test_rejects_a_malformed_bank_identifier_code(self, client, db_session: Session):
        owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(owner_party)
        db_session.commit()
        admin = _make_admin(db_session, email="benef-badifsc@test.com", role="super_admin")

        r = client.post(
            "/api/finance/payout-beneficiaries",
            json=_submit_payload(owner_party.id, bankIdentifierCode="NOTANIFSC"),
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 400, r.text

    def test_rejects_registration_for_a_jurisdiction_with_no_configured_bank_format(self, client, db_session: Session):
        owner_party = Party(party_type="provider", status="active", jurisdiction="Nowhereland")
        db_session.add(owner_party)
        db_session.commit()
        admin = _make_admin(db_session, email="benef-nojurisdiction@test.com", role="super_admin")

        r = client.post(
            "/api/finance/payout-beneficiaries", json=_submit_payload(owner_party.id), cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 409, r.text


class TestConfirmPayoutBeneficiary:
    def test_correct_code_verifies_and_supersedes_the_previous_one(self, client, db_session: Session):
        owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(owner_party)
        db_session.commit()
        admin = _make_admin(db_session, email="benef-confirm1@test.com", role="super_admin")

        first, first_code = beneficiary_crud.submit_payout_beneficiary(
            db_session, owner_party, admin,
            PayoutBeneficiarySubmit(**{
                "party_id": owner_party.id, "account_holder_name": "First Holder", "bank_name": "First Bank",
                "account_number": "111122223333", "bank_identifier_code": "TEST0111111",
            }),
        )
        confirmed_first = beneficiary_crud.confirm_payout_beneficiary(db_session, first, admin, first_code)
        assert confirmed_first.status == "VERIFIED"

        second, second_code = beneficiary_crud.submit_payout_beneficiary(
            db_session, owner_party, admin,
            PayoutBeneficiarySubmit(**{
                "party_id": owner_party.id, "account_holder_name": "Second Holder", "bank_name": "Second Bank",
                "account_number": "444455556666", "bank_identifier_code": "TEST0222222",
            }),
        )
        r = client.post(
            f"/api/finance/payout-beneficiaries/{second.id}/confirm", json={"code": second_code},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "VERIFIED"

        db_session.refresh(first)
        assert first.status == "SUPERSEDED"

        verified_rows = db_session.scalars(
            select(PayoutBeneficiary).where(PayoutBeneficiary.party_id == owner_party.id, PayoutBeneficiary.status == "VERIFIED")
        ).all()
        assert len(verified_rows) == 1
        assert verified_rows[0].id == second.id

    def test_incorrect_code_is_rejected_and_counted(self, client, db_session: Session):
        owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(owner_party)
        db_session.commit()
        admin = _make_admin(db_session, email="benef-wrongcode@test.com", role="super_admin")

        beneficiary, _code = beneficiary_crud.submit_payout_beneficiary(
            db_session, owner_party, admin,
            PayoutBeneficiarySubmit(**_submit_payload_dict(owner_party.id)),
        )
        r = client.post(
            f"/api/finance/payout-beneficiaries/{beneficiary.id}/confirm", json={"code": "000000"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 400, r.text

        db_session.refresh(beneficiary)
        assert beneficiary.status == "PENDING_VERIFICATION"
        assert beneficiary.verification_attempts == 1

    def test_too_many_incorrect_attempts_locks_out_further_tries(self, db_session: Session):
        owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(owner_party)
        db_session.commit()
        admin = _make_admin(db_session, email="benef-lockout@test.com", role="super_admin")

        beneficiary, code = beneficiary_crud.submit_payout_beneficiary(
            db_session, owner_party, admin, PayoutBeneficiarySubmit(**_submit_payload_dict(owner_party.id)),
        )
        for _ in range(beneficiary_crud.MAX_VERIFICATION_ATTEMPTS):
            try:
                beneficiary_crud.confirm_payout_beneficiary(db_session, beneficiary, admin, "000000")
            except Exception:
                pass

        with pytest.raises(HTTPException) as exc_info:
            beneficiary_crud.confirm_payout_beneficiary(db_session, beneficiary, admin, code)
        assert exc_info.value.status_code == 429

    def test_expired_code_is_rejected(self, db_session: Session):
        owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(owner_party)
        db_session.commit()
        admin = _make_admin(db_session, email="benef-expired@test.com", role="super_admin")

        beneficiary, code = beneficiary_crud.submit_payout_beneficiary(
            db_session, owner_party, admin, PayoutBeneficiarySubmit(**_submit_payload_dict(owner_party.id)),
        )
        beneficiary.verification_code_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            beneficiary_crud.confirm_payout_beneficiary(db_session, beneficiary, admin, code)
        assert exc_info.value.status_code == 400

    def test_resend_issues_a_new_code_and_resets_attempts(self, db_session: Session):
        owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(owner_party)
        db_session.commit()
        admin = _make_admin(db_session, email="benef-resend@test.com", role="super_admin")

        beneficiary, first_code = beneficiary_crud.submit_payout_beneficiary(
            db_session, owner_party, admin, PayoutBeneficiarySubmit(**_submit_payload_dict(owner_party.id)),
        )
        try:
            beneficiary_crud.confirm_payout_beneficiary(db_session, beneficiary, admin, "000000")
        except Exception:
            pass
        assert beneficiary.verification_attempts == 1

        beneficiary, second_code = beneficiary_crud.resend_payout_beneficiary_code(db_session, beneficiary, admin)
        assert beneficiary.verification_attempts == 0
        assert second_code != first_code

        confirmed = beneficiary_crud.confirm_payout_beneficiary(db_session, beneficiary, admin, second_code)
        assert confirmed.status == "VERIFIED"


class TestRunPayoutRequiresAVerifiedBeneficiary:
    def test_payout_is_held_with_no_verified_beneficiary_on_file(self, client, db_session: Session):
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="benefgate1", amount=1000.0)
        # _make_provider_rent_obligation auto-verifies a beneficiary -- remove it
        # to exercise the "never submitted/verified one" path.
        db_session.query(PayoutBeneficiary).filter_by(party_id=party_id).delete()
        db_session.commit()
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 1000.0, "currency": "INR", "idempotencyKey": "benef-gate-1"},
            cookies=admin_cookies,
        )
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
        assert payout["status"] == "HELD"
        assert "beneficiary" in payout["holdReason"].lower()

    def test_payout_pays_out_once_a_beneficiary_is_verified(self, client, db_session: Session):
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="benefgate2", amount=1000.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 1000.0, "currency": "INR", "idempotencyKey": "benef-gate-2"},
            cookies=admin_cookies,
        )
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
        assert r.json()["status"] == "PAID"


class TestListPayoutBeneficiaries:
    def test_lists_beneficiaries_for_a_party_masked(self, client, db_session: Session):
        owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(owner_party)
        db_session.commit()
        admin = _make_admin(db_session, email="benef-list1@test.com", role="super_admin")

        beneficiary_crud.submit_payout_beneficiary(
            db_session, owner_party, admin, PayoutBeneficiarySubmit(**_submit_payload_dict(owner_party.id)),
        )

        r = client.get(f"/api/finance/payout-beneficiaries?party_id={owner_party.id}", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["accountNumberLast4"] == "6789"
