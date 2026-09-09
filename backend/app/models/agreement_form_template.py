"""ZR-ENG-CLR-004 Section 2.3/3.2/AC-04: the four non-native agreement
delivery modes. Governed exactly like the clause registry (models/
agreement_clause.py) -- versioned, effective-dated, super_admin-approved --
see crud/agreement_form_templates.py, which mirrors
crud/agreement_clauses.py's create-draft/approve/rollback shape.

FORM_MODES:
  B -- Prescribed government form: source_document is the official blank
       form (uploaded once); field_anchor_map places our structured values
       onto it at generation time (services/agreement_form_rendering.py).
  C -- Prescribed-content reconstruction: no external form to overlay --
       native rendering is used, but authoritative_content_text is the
       counsel-approved reference text generation is diff-checked against
       (AC-04 'without converting it into an uncontrolled generic
       template').
  D -- External approved document: source_document IS the whole agreement;
       generation appends a signature-wrapper page rather than overlaying
       fields.
  E is not a template row at all -- it's the absence of any approved B/C/D
  template combined with a market flagged manual-only (see
  services/agreement_profile.py:resolve_agreement_profile's own form_mode
  resolution)."""

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

FORM_MODES_REQUIRING_TEMPLATE = ("B", "C", "D")
FORM_TEMPLATE_STATUSES = ("DRAFT", "APPROVED", "RETIRED")


class AgreementFormTemplate(Base):
    __tablename__ = "agreement_form_templates"
    __table_args__ = (UniqueConstraint("jurisdiction_scope", "agreement_class", "form_mode", "version", name="uq_form_template_scope_mode_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    jurisdiction_scope: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    agreement_class: Mapped[str] = mapped_column(String(100), nullable=False)
    form_mode: Mapped[str] = mapped_column(String(1), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    # Mode B/D: the uploaded official/external document this template wraps.
    source_document_storage_ref: Mapped[str] = mapped_column(String(255), default="")
    source_document_content_hash: Mapped[str] = mapped_column(String(64), default="")
    # Mode B only: {"field_name": {"page": 0, "x": 72, "y": 700}, ...}
    field_anchor_map: Mapped[dict] = mapped_column(JSON, default=dict)
    # Mode C only: the counsel-approved reference text generation is
    # diff-checked against -- same placeholder-content status as every
    # other seeded legal text in this codebase (see
    # models/agreement_clause.py's own warning).
    authoritative_content_text: Mapped[str] = mapped_column(String(20000), default="")
    approval_note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
