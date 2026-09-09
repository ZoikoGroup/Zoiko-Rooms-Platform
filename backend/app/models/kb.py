from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Controlled taxonomies (ZR-AI-KB-006 / ZR-AI-RAG-001).
KB_MARKETS = ("GLOBAL", "ENGLAND")
KB_ACCESS_CLASSES = ("K0_PUBLIC", "K1_CUSTOMER", "K2_STAFF", "K3_RESTRICTED", "K4_PRIVATE")
KB_DOMAINS = (
    "general",
    "listing",
    "application",
    "tenancy",
    "payment_explanation",
    "host_compliance",
)
KB_DOCUMENT_STATUS = ("QUARANTINED", "DRAFT", "REVIEW", "ACTIVE", "REVOKED")
KB_RELEASE_STATUS = ("DRAFT", "REVIEW", "ACTIVE", "REVOKED")


class KbRelease(Base):
    """A governance release that makes documents eligible for retrieval.

    Only an ACTIVE release grants retrieval eligibility. Versions are immutable
    once released; superseding/revoking is a separate release state (RAG §5, §14).
    """

    __tablename__ = "kb_releases"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    market: Mapped[str] = mapped_column(String(20), nullable=False, default="GLOBAL")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    documents: Mapped[list["KbDocument"]] = relationship(back_populates="release")


class KbDocument(Base):
    """An approved knowledge source. Retrievable only when it is in an ACTIVE
    release and itself ACTIVE, within its effective/expiry window, and when its
    access class and market match the querying principal (RAG §5 eligibility)."""

    __tablename__ = "kb_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), default="KNOWLEDGE")
    author: Mapped[str] = mapped_column(String(120), default="")
    owner: Mapped[str] = mapped_column(String(120), default="")
    market: Mapped[str] = mapped_column(String(20), nullable=False, default="GLOBAL")
    jurisdiction: Mapped[str] = mapped_column(String(50), default="")
    domain: Mapped[str] = mapped_column(String(40), default="general", index=True)
    access_class: Mapped[str] = mapped_column(String(20), default="K0_PUBLIC", index=True)
    trust_tier: Mapped[int] = mapped_column(Integer, default=1)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", index=True)
    release_id: Mapped[int | None] = mapped_column(ForeignKey("kb_releases.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    release: Mapped[KbRelease | None] = relationship(back_populates="documents")
    chunks: Mapped[list["KbChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="KbChunk.chunk_index",
    )


class KbChunk(Base):
    """A structure-preserving chunk of a knowledge document, carrying the
    denormalized eligibility fields so retrieval can filter without joining
    through release state for every candidate (RAG §8)."""

    __tablename__ = "kb_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str] = mapped_column(String(300), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    content_search: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    document: Mapped[KbDocument] = relationship(back_populates="chunks")
