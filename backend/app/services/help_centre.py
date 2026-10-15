"""Help & Support articles, served from the Knowledge Base.

The Help centre page used to carry its articles hardcoded in the frontend;
they now live in the KB (public access class), so the page, the chat
assistant and the automatic first reply on support tickets
(services/support_assistant.py) all read the same content, and staff edit it
in one place (Admin > Knowledge).

The help category and article type are carried in the slug,
`help--<category>--<type>--<title>`; any other ACTIVE public KB document is
listed too, its category taken from its KB domain.

seed_help_centre() puts the original articles into the KB once (idempotent;
run by the scheduler and by `python -m app.services.help_centre`)."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.kb import KbChunk, KbDocument, KbRelease

HELP_RELEASE_VERSION = "help-centre-1"
_PREFIX = "help--"

# category, type, title, summary, offer "report a security concern"
SEED_ARTICLES: tuple[tuple[str, str, str, str, bool], ...] = (
    ('payments', 'How-to', 'Review verified payment instructions',
     'Use the payment instructions shown in Zoiko Rooms for your rental. Do not rely on replacement details sent in chat or email.', False),
    ('payments', 'Troubleshooting', 'My payment is marked paid but not confirmed',
     'Understand renter declarations, recipient confirmation and payment mismatches.', False),
    ('payments', 'Security guidance', 'Payment instructions changed unexpectedly',
     'Review the verified recipient and report a security concern if the change is suspicious.', True),
    ('payments', 'Policy', 'What Zoiko Rooms does not hold or process',
     'Rent and deposits go to the verified recipient. Zoiko Rooms collects only the Listing Fee.', False),
    ('apps', 'How-to', 'How to review an offer',
     'Check the offer terms, expiry and what happens when you accept.', False),
    ('rentals', 'Workflow guide', 'How to give notice where the workflow is enabled',
     'Notices are sent from My Rentals, not through Support messages.', False),
    ('listings', 'Troubleshooting', 'Why a listing cannot publish',
     'See which publication gate is blocking — verification, review or the Listing Fee.', False),
    ('verification', 'Policy', 'What Property Verified means',
     'Property verification and listing authority are separate checks.', False),
    ('sublet', 'Workflow guide', 'How to request a sublet',
     'Requests go to your landlord or their authorized agent through Sublet Requests.', False),
    ('account', 'Security guidance', 'Suspicious sign-in or account recovery',
     'Secure your account first, then contact Support if needed.', False),
    ('safety', 'Escalation', 'When to contact Support, Trust & Safety, a tribunal or emergency services',
     'Know which route fits your situation — Zoiko Rooms isn’t an emergency service or court.', False),
    ('account', 'How-to', 'How to update notification preferences',
     'Manage email and push alerts in Settings. Essential notices are always sent.', False),
)

# Help category -> KB domain (for retrieval), and back for non-help documents.
_CATEGORY_DOMAIN = {
    "apps": "application", "rentals": "tenancy", "payments": "payment_explanation",
    "verification": "host_compliance", "listings": "listing", "sublet": "tenancy",
    "account": "general", "safety": "general",
}
_DOMAIN_CATEGORY = {
    "application": "apps", "tenancy": "rentals", "payment_explanation": "payments",
    "host_compliance": "verification", "listing": "listings", "general": "account",
}
ARTICLE_TYPES = ("How-to", "Troubleshooting", "Policy", "Workflow guide", "Security guidance", "Escalation")
_STEPS = (
    "1. Open the related record in Zoiko Rooms.\n"
    "2. Check the status shown there — it's the authoritative record.\n"
    "3. If something looks wrong, contact Support from that screen so the record is linked automatically."
)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def article_slug(category: str, article_type: str, title: str, report: bool = False) -> str:
    return f"{_PREFIX}{category}--{_slugify(article_type)}{'-report' if report else ''}--{_slugify(title)}"


def _content(category: str, summary: str) -> str:
    text = f"## Summary\n{summary}\n\n## What to do\n{_STEPS}"
    if category == "payments":
        text += "\n\n## Warning\nNever send rent using bank details received by message or email. Only use instructions on the verified screen."
    return text


def seed_help_centre(db: Session) -> int:
    """Adds any missing original article to the KB and makes sure they're all
    in an ACTIVE help-centre release. Returns how many were added."""
    from app.services.kb import ingest_document, make_active

    added = 0
    ids: list[int] = []
    for category, article_type, title, summary, report in SEED_ARTICLES:
        slug = article_slug(category, article_type, title, report)
        existing = db.scalar(select(KbDocument).where(KbDocument.slug == slug))
        if existing is None:
            result = ingest_document(
                db, slug=slug, title=title, content=_content(category, summary),
                domain=_CATEGORY_DOMAIN[category], access_class="K0_PUBLIC", author="Zoiko Rooms", owner="help-centre",
            )
            make_active(db, result.document_id)
            ids.append(result.document_id)
            added += 1
        elif existing.status == "ACTIVE" and existing.release_id is None:
            ids.append(existing.id)
    if ids:
        release = db.scalar(select(KbRelease).where(KbRelease.version == HELP_RELEASE_VERSION))
        if release is None:
            release = KbRelease(version=HELP_RELEASE_VERSION, market="GLOBAL", status="ACTIVE", activated_at=datetime.now(timezone.utc))
            db.add(release)
            db.flush()
        for document_id in ids:
            db.get(KbDocument, document_id).release_id = release.id
    db.commit()
    return added


def _parse_slug(document: KbDocument) -> tuple[str, str, bool]:
    if document.slug.startswith(_PREFIX):
        parts = document.slug[len(_PREFIX):].split("--")
        if len(parts) >= 3:
            category, type_part = parts[0], parts[1]
            report = type_part.endswith("-report")
            type_slug = type_part[: -len("-report")] if report else type_part
            article_type = next((t for t in ARTICLE_TYPES if _slugify(t) == type_slug), "How-to")
            return category, article_type, report
    return _DOMAIN_CATEGORY.get(document.domain, "account"), "How-to", False


def list_articles(db: Session) -> list[dict]:
    today = date.today()
    documents = db.scalars(
        select(KbDocument).join(KbRelease, KbDocument.release_id == KbRelease.id).where(and_(
            KbDocument.status == "ACTIVE", KbRelease.status == "ACTIVE", KbDocument.access_class == "K0_PUBLIC",
            KbDocument.market == "GLOBAL",
            or_(KbDocument.effective_date.is_(None), KbDocument.effective_date <= today),
            or_(KbDocument.expiry_date.is_(None), KbDocument.expiry_date >= today),
        )).order_by(KbDocument.id)
    ).all()
    articles = []
    for document in documents:
        category, article_type, report = _parse_slug(document)
        chunks = db.scalars(select(KbChunk).where(KbChunk.document_id == document.id).order_by(KbChunk.chunk_index)).all()
        body = "\n\n".join(c.content for c in chunks)
        summary_match = re.search(r"## Summary\s*\n(.+?)(?:\n\n|$)", body, re.S)
        summary = (summary_match.group(1) if summary_match else body).strip().split("\n")[0][:300]
        articles.append({
            "id": document.slug, "category": category, "type": article_type, "title": document.title,
            "summary": summary, "body": body, "report": report, "updatedAt": document.updated_at,
        })
    return articles


if __name__ == "__main__":  # python -m app.services.help_centre
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        print(f"help centre: {seed_help_centre(session)} article(s) added")
    finally:
        session.close()
