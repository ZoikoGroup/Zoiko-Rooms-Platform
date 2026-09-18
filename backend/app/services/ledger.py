"""ZR-ENG-CLR-005 Section 15.2: the double-entry ledger foundation -- an
immutable, balanced journal underneath the existing Obligation/PaymentAllocation/
DepositRecord/PayoutRecord/RefundRequest model. Cash-basis only: entries are
posted when money actually moves (payment confirmed, payout paid, deposit
released/forfeited, refund completed), never when an Obligation is merely
created -- so there is no renter-receivable account here.

Amounts are coerced through Decimal in this module specifically so the ledger
doesn't compound the float-rounding imprecision that crud/finance.py's
_round2/float(...) calls already carry elsewhere in this codebase -- fixing
that repo-wide is a separately-tracked gap, not something this module expands
into.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Union

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import LedgerAccount, LedgerEntry

Amount = Union[int, float, Decimal, str]


def _to_decimal(amount: Amount) -> Decimal:
    return Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def get_platform_account(db: Session, account_type: str, currency: str = "INR") -> LedgerAccount:
    """Get-or-create a platform-wide account (party_id is null). Query-then-insert,
    the same idiom create_payment_intent uses for its idempotency key -- Postgres
    treats NULL as distinct, so the unique constraint alone would not stop a
    duplicate platform-wide row from being created."""
    account = db.scalar(
        select(LedgerAccount).where(
            LedgerAccount.account_type == account_type,
            LedgerAccount.party_id.is_(None),
            LedgerAccount.currency == currency,
        )
    )
    if account:
        return account
    account = LedgerAccount(account_type=account_type, party_id=None, currency=currency)
    db.add(account)
    db.flush()
    return account


def get_party_account(db: Session, account_type: str, party_id: int, currency: str = "INR") -> LedgerAccount:
    """Get-or-create a per-party account (e.g. HOST_PAYABLE, DEPOSIT_CUSTODY_LIABILITY
    for a given provider)."""
    account = db.scalar(
        select(LedgerAccount).where(
            LedgerAccount.account_type == account_type,
            LedgerAccount.party_id == party_id,
            LedgerAccount.currency == currency,
        )
    )
    if account:
        return account
    account = LedgerAccount(account_type=account_type, party_id=party_id, currency=currency)
    db.add(account)
    db.flush()
    return account


def post_entry(
    db: Session,
    *,
    debit_account: LedgerAccount,
    credit_account: LedgerAccount,
    amount: Amount,
    currency: str,
    description: str = "",
    source_type: str,
    source_id: str,
) -> LedgerEntry:
    """Adds one balanced journal row -- no db.commit() here, so the caller's
    existing transaction (and its own db.commit()) stays the atomicity boundary.
    amount is always positive; direction is encoded by which side (debit/credit)
    an account is on, unlike PaymentAllocation's signed amount_allocated
    convention. A correction/reversal is a new entry with the sides swapped,
    never a negative amount or an edit to an existing row."""
    decimal_amount = _to_decimal(amount)
    if decimal_amount <= 0:
        raise ValueError("Ledger entry amount must be positive")

    entry = LedgerEntry(
        debit_account_id=debit_account.id,
        credit_account_id=credit_account.id,
        amount=decimal_amount,
        currency=currency,
        description=description,
        source_type=source_type,
        source_id=source_id,
    )
    db.add(entry)
    return entry


def get_balance(db: Session, account: LedgerAccount) -> Decimal:
    """Net debit balance: sum(amounts where this account is the debit side) minus
    sum(amounts where this account is the credit side). This is a raw, unsigned-
    by-convention number -- for an asset-like account (PLATFORM_CLEARING) a
    positive result means "increased", while for a liability-like account
    (HOST_PAYABLE, DEPOSIT_CUSTODY_LIABILITY) a positive result means "paid down
    below zero owed" and a negative result means "amount still owed"; the caller
    is responsible for that interpretation, this helper does not flip the sign
    per account type."""
    debited = db.scalars(select(LedgerEntry.amount).where(LedgerEntry.debit_account_id == account.id)).all()
    credited = db.scalars(select(LedgerEntry.amount).where(LedgerEntry.credit_account_id == account.id)).all()
    total_debit = sum((_to_decimal(a) for a in debited), Decimal("0.00"))
    total_credit = sum((_to_decimal(a) for a in credited), Decimal("0.00"))
    return total_debit - total_credit
