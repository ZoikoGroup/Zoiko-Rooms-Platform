"""Knowledge Base ingestion, chunking and security quarantine (Phase 5).

Implements a Level-1 subset of ZR-AI-KB-006:
  * structure-preserving chunking (RAG §8),
  * quarantine-on-ingestion for secrets / prompt-injection indicators (RAG §13),
  * document status control (QUARANTINED -> DRAFT -> ... -> ACTIVE) that gates
    retrieval eligibility together with release state.

Deterministic and model-outside: nothing here calls the LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.kb import (
    KB_DOCUMENT_STATUS,
    KB_DOMAINS,
    KB_MARKETS,
    KB_ACCESS_CLASSES,
    KbChunk,
    KbDocument,
)

# Security-scan markers (RAG §13 indirect prompt injection / secrets defence).
_QUARANTINE_MARKERS = (
    "ignore previous instructions",
    "ignore all previous",
    "system prompt:",
    "you are now",
    "disregard prior",
    "jailbreak",
    "password",
    "passwd,",
    "api_key",
    "apikey",
    "secret",
)

# Default chunk target (approx words) plus heading delimiter for splitting.
_MAX_CHUNK_WORDS = 180
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)
_SENSITIVE_HEADING_RE = re.compile(r"\b(password|secret|credential|token|api[- ]?key)\b", re.IGNORECASE)

# K0_PUBLIC is the only access class currently exposed to chatbot consumers; the
# higher classes are reserved for authorized staff contexts (deferred).
PUBLIC_ACCESS_CLASSES = ("K0_PUBLIC",)


def allowed_access_classes(actor: object) -> tuple[str, ...]:
    """Return the KB access classes ``actor`` may request from retrieval.

    Single source of truth for the actor -> access-class mapping so a new
    retrieval call site can't accidentally request a staff-only tier just by
    forgetting to pass ``access_classes``, or by copying an unvetted value.
    K1_CUSTOMER+ are modeled (KB_ACCESS_CLASSES) but no staff-access feature
    has shipped yet, so every actor -- user or admin -- is public-only for now.
    """
    return PUBLIC_ACCESS_CLASSES


class KnowledgeError(Exception):
    pass


@dataclass
class IngestionResult:
    document_id: int
    status: str
    chunk_count: int
    quarantine_reasons: list[str] = field(default_factory=list)


def validate_document_meta(
    *,
    market: str,
    domain: str,
    access_class: str,
) -> None:
    if market not in KB_MARKETS:
        raise KnowledgeError(f"invalid market: {market}")
    if domain not in KB_DOMAINS:
        raise KnowledgeError(f"invalid domain: {domain}")
    if access_class not in KB_ACCESS_CLASSES:
        raise KnowledgeError(f"invalid access_class: {access_class}")


def _frame(text: str) -> list[str]:
    """Split source into structural frames by Markdown headings, falling back to
    paragraph splits so a headingless document still chunks sensibly."""
    if not text:
        return []
    lines = text.splitlines()
    frames: list[str] = []
    current: list[str] = []
    current_heading = ""

    def flush():
        nonlocal current
        body = "\n".join(current).strip()
        if body:
            frames.append(f"{current_heading}\n{body}".strip() if current_heading else body)
        current = []

    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            flush()
            current_heading = line.strip()
        elif line.strip() == "":
            continue
        else:
            current.append(line.strip())
    flush()
    if not frames:
        frames = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return frames


def chunk_document(title: str, text: str) -> list[tuple[str, str]]:
    """Return a list of (section, chunk_text) preserving heading structure."""
    results: list[tuple[str, str]] = []
    for frame in _frame(text):
        heading = ""
        lines = frame.splitlines()
        if lines and _HEADING_RE.match(lines[0]):
            heading = lines[0].strip()
            lines = lines[1:]
        headings = [p for p in "\n".join(lines).split("\n\n") if p.strip()]
        # Further split any single paragraph that exceeds the word budget.
        paragraphs: list[str] = []
        for h in headings:
            h_words = h.split()
            if len(h_words) <= _MAX_CHUNK_WORDS:
                paragraphs.append(h)
                continue
            for i in range(0, len(h_words), _MAX_CHUNK_WORDS):
                paragraphs.append(" ".join(h_words[i : i + _MAX_CHUNK_WORDS]))
        # Merge paragraphs into bounded chunks under the same heading.
        buf: list[str] = []
        words = 0
        for para in paragraphs:
            para_words = len(para.split())
            if buf and words + para_words > _MAX_CHUNK_WORDS:
                results.append((heading, "\n\n".join(buf)))
                buf = []
                words = 0
            buf.append(para)
            words += para_words
        if buf:
            results.append((heading, "\n\n".join(buf)))
    # Never return zero chunks for non-empty text.
    if not results and text.strip():
        results.append(("", text.strip()))
    return results


def _quarantine_reasons(text: str) -> list[str]:
    lowered = text.lower()
    reasons = []
    for marker in _QUARANTINE_MARKERS:
        if marker in lowered:
            reasons.append(marker)
    return reasons


def ingest_document(
    db: Session,
    *,
    slug: str,
    title: str,
    content: str,
    market: str = "GLOBAL",
    jurisdiction: str = "",
    domain: str = "general",
    access_class: str = "K0_PUBLIC",
    trust_tier: int = 1,
    effective_date: date | None = None,
    expiry_date: date | None = None,
    author: str = "",
    owner: str = "",
) -> IngestionResult:
    """Register, chunk and store a document. Returns a quarantine if the content
    trips the security scan; otherwise the document is DRAFT."""
    validate_document_meta(market=market, domain=domain, access_class=access_class)

    quarantine_reasons = _quarantine_reasons(content)
    status = "QUARANTINED" if quarantine_reasons else "DRAFT"

    document = KbDocument(
        slug=slug,
        title=title,
        market=market,
        jurisdiction=jurisdiction,
        domain=domain,
        access_class=access_class,
        trust_tier=trust_tier,
        effective_date=effective_date,
        expiry_date=expiry_date,
        status=status,
        author=author,
        owner=owner,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(document)
    db.flush()

    for idx, (section, chunk_text) in enumerate(chunk_document(title, content)):
        db.add(
            KbChunk(
                document_id=document.id,
                chunk_index=idx,
                section=section,
                content=chunk_text,
                content_search=chunk_text.lower(),
                created_at=datetime.now(timezone.utc),
            )
        )
    db.flush()

    return IngestionResult(
        document_id=document.id,
        status=status,
        chunk_count=len(list(_iter_chunks(db, document.id))),
        quarantine_reasons=quarantine_reasons,
    )


def _iter_chunks(db: Session, document_id: int) -> Iterable[KbChunk]:
    return db.query(KbChunk).filter(KbChunk.document_id == document_id).all()


def make_active(db: Session, document_id: int) -> KbDocument:
    """Mark a document ACTIVE (post-approval). Must have a valid effective window
    for regulated domains; guarded by the caller/release flow."""
    document = db.get(KbDocument, document_id)
    if document is None:
        raise KnowledgeError("document not found")
    if document.status == "QUARANTINED":
        raise KnowledgeError("quarantined documents cannot be released")
    document.status = "ACTIVE"
    document.updated_at = datetime.now(timezone.utc)
    db.flush()
    return document


def revoke_document(db: Session, document_id: int) -> KbDocument:
    document = db.get(KbDocument, document_id)
    if document is None:
        raise KnowledgeError("document not found")
    document.status = "REVOKED"
    document.updated_at = datetime.now(timezone.utc)
    db.flush()
    return document
