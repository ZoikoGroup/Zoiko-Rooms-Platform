"""Identity is verified only by Veriff: no upload / manual-review route

Country packs move to the Veriff provider with the document method only
(each as a new pack version, so the edit history is kept). Attempts still
open on the retired built-in / manual route are closed as METHOD_RETIRED and
the person's identity returns to NOT_STARTED, so they simply verify again
with Veriff. Verified identities are untouched.

Revision ID: 7c1e2f9a4b10
Revises: 4d26afb5d759
Create Date: 2026-10-05 10:00:00.000000

"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7c1e2f9a4b10"
down_revision: Union[str, None] = "4d26afb5d759"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RETIRED_PROVIDERS = ("zoiko_document_check", "zoiko_manual_review", "signed_webhook", "")
_OLD_CONSENT_TAIL = " You can choose another verification option instead."

packs = sa.table(
    "identity_regulatory_packs",
    sa.column("id", sa.Integer), sa.column("country_code", sa.String), sa.column("country_name", sa.String),
    sa.column("version", sa.Integer), sa.column("active", sa.Boolean),
    sa.column("accepted_document_types", sa.JSON), sa.column("available_methods", sa.JSON),
    sa.column("date_of_birth_required", sa.Boolean), sa.column("minimum_age", sa.Integer),
    sa.column("biometric_consent_text", sa.Text), sa.column("privacy_notice_text", sa.Text),
    sa.column("evidence_retention_days", sa.Integer), sa.column("reverification_interval_days", sa.Integer),
    sa.column("reverify_on_account_recovery", sa.Boolean), sa.column("document_provider_code", sa.String),
    sa.column("max_attempts_per_day", sa.Integer),
    sa.column("created_at", sa.DateTime(timezone=True)), sa.column("updated_at", sa.DateTime(timezone=True)),
)
sessions = sa.table(
    "identity_verifications",
    sa.column("id", sa.Integer), sa.column("party_id", sa.Integer), sa.column("session_state", sa.String),
    sa.column("status", sa.String), sa.column("provider_code", sa.String), sa.column("method_type", sa.String),
    sa.column("reason_codes", sa.JSON), sa.column("decided_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
profiles = sa.table(
    "identity_profiles",
    sa.column("party_id", sa.Integer), sa.column("state", sa.String), sa.column("reason_codes", sa.JSON),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

_COPIED = (
    "country_code", "country_name", "accepted_document_types", "date_of_birth_required", "minimum_age",
    "privacy_notice_text", "evidence_retention_days", "reverification_interval_days",
    "reverify_on_account_recovery", "max_attempts_per_day",
)


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)

    for pack in bind.execute(sa.select(packs).where(packs.c.active.is_(True))).mappings().all():
        if pack["document_provider_code"] == "veriff" and list(pack["available_methods"] or []) == ["DOCUMENT"]:
            continue
        consent = (pack["biometric_consent_text"] or "").replace(_OLD_CONSENT_TAIL, "")
        bind.execute(packs.update().where(packs.c.id == pack["id"]).values(active=False, updated_at=now))
        bind.execute(packs.insert().values(
            **{c: pack[c] for c in _COPIED}, version=pack["version"] + 1, active=True,
            available_methods=["DOCUMENT"], document_provider_code="veriff", biometric_consent_text=consent,
            created_at=now, updated_at=now,
        ))

    open_rows = bind.execute(sa.select(sessions.c.id, sessions.c.party_id, sessions.c.reason_codes).where(
        sessions.c.session_state.in_(("IN_PROGRESS", "PROCESSING", "PENDING_REVIEW", "ACTION_REQUIRED")),
        sa.or_(sessions.c.provider_code.in_(_RETIRED_PROVIDERS), sessions.c.method_type == "MANUAL"),
    )).mappings().all()
    parties = set()
    for row in open_rows:
        codes = [c for c in (row["reason_codes"] or []) if c != "METHOD_RETIRED"] + ["METHOD_RETIRED"]
        bind.execute(sessions.update().where(sessions.c.id == row["id"]).values(
            session_state="FAILED", status="rejected", reason_codes=codes, decided_at=now, updated_at=now,
        ))
        parties.add(row["party_id"])
    for party_id in parties:
        bind.execute(profiles.update().where(
            profiles.c.party_id == party_id,
            profiles.c.state.in_(("IN_PROGRESS", "PROCESSING", "PENDING_REVIEW", "ACTION_REQUIRED", "FAILED")),
        ).values(state="NOT_STARTED", reason_codes=["METHOD_RETIRED"], updated_at=now))


def downgrade() -> None:
    # Data-only migration: closed attempts and new pack versions are kept
    # (re-opening retired manual attempts would be wrong).
    pass
