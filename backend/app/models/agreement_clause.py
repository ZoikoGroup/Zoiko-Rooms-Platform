"""ZR-ENG-CLR-004 Section 7.2: the clause registry -- the one place approved
agreement content lives, so an executable agreement is always built from
`clause_id` references (see AgreementVersion.snapshot in models/leasing.py),
never free-form legal text.

LEGAL CONTENT WARNING: no legal counsel has reviewed the rows this codebase
seeds by default (see services/agreement_profile.py:ensure_default_clause_registry).
They exist so the registry's precedence/mandatory-level/fail-closed machinery
is real and testable, not to represent production-ready legal wording --
every default row's `approval_note` says so explicitly. A real market launch
requires replacing them with counsel-approved content before end users rely
on this platform's binding agreement text (see this document's own "LEGAL
CONTROL" doctrine).
"""

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

CLAUSE_MANDATORY_LEVELS = ("MANDATORY", "OPTIONAL", "PROHIBITED")
CLAUSE_STATUSES = ("DRAFT", "APPROVED", "RETIRED")


class ClauseDefinition(Base):
    """ZR-ENG-CLR-004 AC-25/Section 7.2: clause_id is deliberately NOT unique
    alone -- (clause_id, version) is, so a clause can have real history:
    create_clause_draft adds a new DRAFT row at version+1 without touching
    the currently-APPROVED row; approve_clause_version activates it
    (effective_from) and retires whatever was previously active for that
    clause_id (effective_to); rollback_clause reverses that by reactivating
    an older version and retiring whatever is currently active. See
    crud/agreement_clauses.py."""

    __tablename__ = "agreement_clause_definitions"
    __table_args__ = (UniqueConstraint("clause_id", "version", name="uq_agreement_clause_definitions_clause_id_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    clause_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    jurisdiction_scope: Mapped[str] = mapped_column(String(50), nullable=False)
    agreement_class: Mapped[str] = mapped_column(String(100), nullable=False)
    mandatory_level: Mapped[str] = mapped_column(String(20), default="OPTIONAL")
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    version: Mapped[int] = mapped_column(Integer, default=1)
    # AC-25 'effective-dated': a row only counts toward resolve_agreement_profile
    # while APPROVED and today falls in [effective_from, effective_to). Both
    # None means "never activated" (still DRAFT). effective_to None with
    # status APPROVED means "currently active, no successor yet".
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Short plain-language label rendered in the PDF section header -- the
    # actual prose is still built by generate_agreement_pdf's existing
    # formatting today (see crud/leasing.py), not stored here as free text;
    # this registry's job for now is to gate WHICH sections may render, per
    # AgreementProfile.clause_ids, not to hold the final wording itself.
    title: Mapped[str] = mapped_column(String(200), default="")
    # Never a legal sign-off -- see module docstring.
    approval_note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
