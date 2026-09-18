"""ZR-ENG-CLR-004 Section 7.2/AC-29: 'translations: Controlled legal
translations linked to source-language version... cannot drift
independently from approved source text.'

A translation is FK'd to one specific ClauseDefinition row -- i.e. one exact
(clause_id, version). When that clause gets a new version approved (see
crud/agreement_clauses.py:approve_clause_version), the old version's
translations stay attached to the now-RETIRED row; they do NOT silently
carry forward and apply to the new version. A translation for the new
version has to be created and approved on its own -- see
crud/agreement_clause_translations.py:missing_translations_for_effective_clauses,
which reports exactly this drift risk so it's visible, not silent."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

CLAUSE_TRANSLATION_STATUSES = ("DRAFT", "APPROVED")


class ClauseTranslation(Base):
    __tablename__ = "agreement_clause_translations"
    __table_args__ = (UniqueConstraint("clause_definition_id", "language_code", name="uq_clause_translation_definition_language"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    clause_definition_id: Mapped[int] = mapped_column(
        ForeignKey("agreement_clause_definitions.id", ondelete="CASCADE"), nullable=False,
    )
    language_code: Mapped[str] = mapped_column(String(10), nullable=False)
    translated_title: Mapped[str] = mapped_column(String(200), default="")
    translated_content: Mapped[str] = mapped_column(String(20000), default="")
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    approval_note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    clause_definition: Mapped["ClauseDefinition"] = relationship()
