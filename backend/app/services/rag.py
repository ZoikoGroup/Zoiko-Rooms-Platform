"""RAG retrieval (Phase 5, Level-1).

Release-governed, filterable retrieval over the Knowledge Base using portable
keyword matching (works on both SQLite tests and Postgres). Returns ranked
chunks each carrying a citation object with provenance; a citation validator
rejects fabricated identifiers so the chat layer can never present an
unresolved source as grounded (RAG-FR-012).

Scope note: vector similarity is out of scope for Level-1; the retrieval
surface is intentionally provider-neutral so an embedding/reranker can be added
later without changing the contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterable

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.kb import KbChunk, KbDocument, KbRelease

MAX_RESULTS = 5
_MIN_SCORE = 0.0

_WORD_RE = re.compile(r"[a-z0-9]{2,}")


def _keywords(query: str) -> list[str]:
    return _WORD_RE.findall((query or "").lower())


@dataclass(frozen=True)
class Citation:
    source_type: str = "KNOWLEDGE"
    source_id: int | None = None
    source_version: str = ""
    section: str = ""
    chunk_ref: int | None = None
    release_id: int | None = None
    market: str = ""
    effective_at: str = ""

    def to_dict(self) -> dict:
        return {
            "citation_id": self.citation_id(),
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_version": self.source_version,
            "section": self.section,
            "chunk_ref": self.chunk_ref,
            "release_id": self.release_id,
            "market": self.market,
            "effective_at": self.effective_at,
        }

    def citation_id(self) -> str:
        return f"kb:{self.source_id}:{self.chunk_ref}"


@dataclass
class RetrievalHit:
    chunk: KbChunk
    document: KbDocument
    citation: Citation
    score: float = 0.0


def _eligible_filter(cursor_date: date | None = None, access_classes: Iterable[str] | None = None) -> list:
    cursor_date = cursor_date or date.today()
    allowed = list(access_classes) if access_classes is not None else ["K0_PUBLIC"]
    return [
        KbDocument.status == "ACTIVE",
        KbRelease.status == "ACTIVE",
        KbDocument.release_id.isnot(None),
        or_(KbDocument.effective_date.is_(None), KbDocument.effective_date <= cursor_date),
        or_(KbDocument.expiry_date.is_(None), KbDocument.expiry_date >= cursor_date),
        KbDocument.access_class.in_(allowed),
    ]


def _score_chunk(chunk: KbChunk, keywords: list[str]) -> float:
    haystack = chunk.content_search or ""
    words = set(_WORD_RE.findall(haystack))
    if not keywords:
        return 0.0
    hits = sum(1 for kw in keywords if kw in words)
    coverage = len(words) or 1
    return hits + (hits / coverage)


def retrieve(
    db: Session,
    query: str,
    *,
    market: str = "GLOBAL",
    domains: Iterable[str] | None = None,
    access_classes: Iterable[str] | None = None,
    max_results: int = MAX_RESULTS,
    cursor_date: date | None = None,
) -> list[RetrievalHit]:
    """Retrieve grounded chunks for ``query`` subject to eligibility filters."""
    keywords = _keywords(query)
    if not keywords:
        return []

    base = _eligible_filter(cursor_date=cursor_date, access_classes=access_classes)
    if market:
        base.append(KbDocument.market.in_(["GLOBAL", market]))
    if domains:
        base.append(KbDocument.domain.in_(list(domains)))

    rows = db.execute(
        select(KbChunk, KbDocument, KbRelease)
        .join(KbDocument, KbChunk.document_id == KbDocument.id)
        .join(KbRelease, KbDocument.release_id == KbRelease.id)
        .where(and_(*base))
    ).all()

    hits: list[RetrievalHit] = []
    for chunk, document, release in rows:
        score = _score_chunk(chunk, keywords)
        if score <= _MIN_SCORE:
            continue
        hits.append(
            RetrievalHit(
                chunk=chunk,
                document=document,
                citation=Citation(
                    source_id=document.id,
                    source_version=release.version,
                    section=chunk.section,
                    chunk_ref=chunk.id,
                    release_id=release.id,
                    market=document.market,
                    effective_at=document.effective_date.isoformat() if document.effective_date else "",
                ),
                score=score,
            )
        )

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:max_results]


def resolve_citation(db: Session, citation_id: str) -> Citation | None:
    """Validate a citation reference resolves to a real, currently-eligible chunk
    (RAG-FR-012: reject fabricated or unresolved identifiers)."""
    m = re.match(r"^kb:(\d+):(\d+)$", citation_id or "")
    if not m:
        return None
    chunk_id = int(m.group(2))
    chunk = db.get(KbChunk, chunk_id)
    if chunk is None:
        return None
    document = chunk.document
    row = (
        db.query(KbDocument, KbRelease)
        .join(KbRelease, KbDocument.release_id == KbRelease.id)
        .filter(
            KbDocument.id == document.id,
            *_eligible_filter(),
        )
        .first()
    )
    if row is None:
        return None
    matched_doc, matched_release = row
    return Citation(
        source_id=matched_doc.id,
        source_version=matched_release.version,
        section=chunk.section,
        chunk_ref=chunk.id,
        release_id=matched_release.id,
        market=matched_doc.market,
        effective_at=matched_doc.effective_date.isoformat() if matched_doc.effective_date else "",
    )


def hits_to_text(hits: list[RetrievalHit]) -> str:
    """Render retrieved chunks into a compact, citational evidence block."""
    if not hits:
        return ""
    parts = []
    for i, hit in enumerate(hits, start=1):
        parts.append(
            f"[{i}] {hit.document.title} (market={hit.citation.market}, "
            f"effective={hit.citation.effective_at or 'n/a'})\n"
            f"source: {hit.citation.citation_id()}\n"
            f"{hit.chunk.content}"
        )
    return "\n\n".join(parts)
