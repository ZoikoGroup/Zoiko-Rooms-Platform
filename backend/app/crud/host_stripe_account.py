"""ZR-ENG-CLR-005 Section 4.4/9.1: Stripe Connect onboarding for a host's
payout destination -- the real-Stripe counterpart to
app/crud/payout_beneficiary.py, for a market/provider using
PSP_DEFERRED_PAYOUT via Stripe Connect rather than manually-collected bank
details. create_connected_account/create_onboarding_link call the real
Stripe API when settings.stripe_secret_key is configured (see
app/services/stripe_client.py); without it they fall back to generated
placeholder ids/links, so this stays fully testable without real
credentials -- refresh_account_status and simulate_onboarding_complete are
how a test (or a dev environment with no real Stripe keys) can still drive
an account from ONBOARDING to COMPLETE."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.crud.party import assert_provider_access
from app.models.admin_user import AdminUser
from app.models.finance import HostStripeAccount
from app.models.party import Party
from app.services import stripe_client


def get_host_stripe_account_or_404(db: Session, account_id: int) -> HostStripeAccount:
    account = db.get(HostStripeAccount, account_id)
    if not account:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Host Stripe account not found")
    return account


def get_for_party(db: Session, party_id: int) -> HostStripeAccount | None:
    return db.query(HostStripeAccount).filter(HostStripeAccount.party_id == party_id).first()


def _sync_status(account: HostStripeAccount) -> None:
    if account.details_submitted and account.charges_enabled and account.payouts_enabled:
        account.status = "COMPLETE"
    elif account.status != "COMPLETE":
        account.status = "ONBOARDING"


def create_connected_account(db: Session, party: Party, admin: AdminUser, country: str, email: str) -> HostStripeAccount:
    assert_provider_access(db, admin, party.id, roles=("provider_finance", "provider_owner_admin"))
    if get_for_party(db, party.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This provider already has a Stripe Connect account")

    stripe_account_id = stripe_client.create_connected_account(
        country=country, email=email, metadata={"party_id": str(party.id)},
    )
    account = HostStripeAccount(party_id=party.id, stripe_account_id=stripe_account_id, status="ONBOARDING")
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def create_onboarding_link(db: Session, account: HostStripeAccount, admin: AdminUser) -> str:
    assert_provider_access(db, admin, account.party_id, roles=("provider_finance", "provider_owner_admin"))
    return stripe_client.create_account_onboarding_link(stripe_account_id=account.stripe_account_id)


def refresh_account_status(db: Session, account: HostStripeAccount, admin: AdminUser) -> HostStripeAccount:
    """Re-reads the account's status from Stripe (a no-op returning {} when
    Stripe isn't configured, per stripe_client.retrieve_account_status's own
    docstring -- see simulate_onboarding_complete for the no-real-Stripe
    equivalent)."""
    assert_provider_access(db, admin, account.party_id, roles=("provider_finance", "provider_owner_admin"))
    status_fields = stripe_client.retrieve_account_status(stripe_account_id=account.stripe_account_id)
    for field, value in status_fields.items():
        setattr(account, field, value)
    _sync_status(account)
    account.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(account)
    return account


def simulate_onboarding_complete(db: Session, account: HostStripeAccount, admin: AdminUser) -> HostStripeAccount:
    """super_admin-only test/dev path: marks an account as if Stripe's own
    hosted onboarding had fully completed, without needing a real Stripe
    account to actually walk through it. Refuses outright once real Stripe
    credentials are configured -- at that point the account's status must
    come from Stripe itself (refresh_account_status / the account.updated
    webhook), never be hand-set."""
    if stripe_client.is_configured():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Real Stripe credentials are configured -- account status must come from Stripe, not be simulated",
        )
    account.details_submitted = True
    account.charges_enabled = True
    account.payouts_enabled = True
    account.status = "COMPLETE"
    account.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(account)
    return account
