"""Seed the public (K0_PUBLIC) knowledge base used by /api/public/assistant.

All content below is drawn from Zoiko Rooms' real marketing copy (the
requirements/pricing and how-it-works pages of the website). Nothing here is
invented; platform prices and mechanics are the ones published on the site.

The script is idempotent: documents are keyed by slug, activated, attached to a
single GLOBAL release, and that release is activated. Re-running it will not
duplicate documents.

Run with:  python seed_public_kb.py
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.db.session import SessionLocal
from app.models.kb import KbDocument, KbRelease
from app.services.kb import KnowledgeError, ingest_document, make_active

RELEASE_VERSION = "2026.09-public"

ARTICLES: list[dict[str, str]] = [
    {
        "slug": "what-is-zoiko-rooms",
        "title": "What is Zoiko Rooms",
        "domain": "general",
        "content": """# What is Zoiko Rooms

Zoiko Rooms is a marketplace for verified private rooms for rent, designed for stays of 30 nights or more. Every step of the rental journey is evidenced and explained: facts about a room and its provider are labelled with how they were verified and when.

Room seekers can search, compare, inspect current facts, communicate, view, apply or reserve, review the agreement and costs, pay through the disclosed route, move in, and get support.

Providers can list and manage rooms through the same evidenced journey, from confirming identity and authority through to describing the room, disclosing costs, publishing, managing and support.""",
    },
    {
        "slug": "who-zoiko-rooms-is-for",
        "title": "Who Zoiko Rooms is for",
        "domain": "general",
        "content": """# Who Zoiko Rooms is for

Zoiko Rooms serves two main groups.

Room seekers: people looking for a private room for 30 nights or more, including students, professionals on work or healthcare placements, and people relocating internationally.

Providers: live-in providers, landlords, agents and managers, authorized sublets, and organizations and portfolios. Each provider path has its own requirements covering identity, listing authority, room and home facts, costs and terms, fair access and safety, and maintenance as things change.""",
    },
    {
        "slug": "how-renting-a-room-works",
        "title": "How renting a room works",
        "domain": "listing",
        "content": """# How renting a room works on Zoiko Rooms

The room seeker journey runs from search to move-in: search, compare, inspect current facts, communicate, view, apply or reserve, review the agreement and costs, pay through the disclosed route, move in, and get support.

Each stage shows its own evidence, status, source and dates so a seeker can see what is confirmed and what is not. Report, block and support routes are available at every stage, from the first search through to move-out.""",
    },
    {
        "slug": "how-listing-a-room-works",
        "title": "How listing a room works",
        "domain": "listing",
        "content": """# How listing a room works on Zoiko Rooms

The provider journey runs from list to support: choose a provider path, confirm identity and authority, describe the room, disclose availability and complete costs, review fairness and safety, publish, manage, and support.

Provider authority (confirmed identity and listing authority) is tracked separately from room facts. Every room fact is labelled by exactly how it was verified, and when.""",
    },
    {
        "slug": "zoiko-rooms-pricing-for-providers",
        "title": "Zoiko Rooms pricing for providers",
        "domain": "payment_explanation",
        "content": """# Zoiko Rooms pricing for providers

Provider plans are monthly and each includes a set number of active rooms:

- Live-in provider: $19 per month, includes 3 active rooms; $7 per month per extra room.
- Landlord: $29 per month, includes 5 active rooms; $7 per month per extra room.
- Agent / Manager: $49 per month, includes 10 active rooms; $6 per month per extra room.
- Authorized Sublet: $19 per month, includes 2 active rooms; $7 per month per extra room.
- Organization / Portfolio: pricing is tailored; talk to the Zoiko Rooms team.

Other charges: a one-time listing review charge of $25 is due when a listing is submitted for review, with tax calculated at checkout where applicable. Enhanced media review is an optional $12 per listing and does not guarantee approval or ranking. New individual providers may be eligible for a one-time $10 promotional credit, applied before tax.

Estimates show an estimated total due today, and the final amount is shown before payment. Price and requirement changes have a scope, effective date, notice, and grandfathering where applicable. Zoiko Rooms never silently updates a price you have already accepted.""",
    },
    {
        "slug": "what-a-room-costs-room-seeker-view",
        "title": "What a room costs: the room seeker view",
        "domain": "listing",
        "content": """# What a room costs: the room seeker view

A room seeker sees the full cost picture before committing. An example disclosure lists monthly rent of $1,200 per month, a security deposit of $1,200 due after agreement signing, a one-time required move-in fee of $75, and bills such as internet included, electricity estimated at $65 per month, and water estimated at $35 per month.

The known upfront total in that example is $2,475 plus any disclosed variable utility amount. Zoiko Rooms platform pricing is not included in that total and is shown separately.""",
    },
    {
        "slug": "payments-safety-and-support",
        "title": "Payments, safety and support",
        "domain": "payment_explanation",
        "content": """# Payments, safety and support

Payments are disclosed before they are made: the recipient, the route, the timing, the fees and the refund terms. Seekers pay through the disclosed route for the room.

Safety is built into every stage. You can report, block and reach support at any time, from first search through move-out. Dedicated payments, safety and support guidance covers before-paying checks, fraud and account recovery, refunds and disputes, safer communication, and the support and incident lifecycle.""",
    },
    {
        "slug": "verification-authority-and-room-passport",
        "title": "Verification, authority and the Room Passport",
        "domain": "host_compliance",
        "content": """# Verification, authority and the Room Passport

A verified badge does not mean everything about a room is confirmed. Verification is scoped: identity, authority, room evidence, availability and terms each show their own status, source and date, and what they do not prove.

The Room Passport records provider authority (confirmed identity and listing authority, tracked separately from room facts), room facts and evidence (every fact labelled by how it was verified and when), applications and agreements (human-owned decisions and versioned terms that are never silently changed), and payments (disclosed recipient, route, timing, fees and refund terms).""",
    },
    {
        "slug": "how-to-contact-zoiko-rooms",
        "title": "How to contact Zoiko Rooms",
        "domain": "general",
        "content": """# How to contact Zoiko Rooms

For help with a room, a listing, a payment or an account, contact the Zoiko Rooms support team at support@zoikorooms.com. The public assistant on the website can explain how the platform works but cannot access or change any individual person's records.

Organizations and providers can also talk to us about Zoiko Rooms Pro for portfolio and availability, compliance and verification, institutional distribution, and team requirements.""",
    },
]


def seed_public_kb() -> None:
    db = SessionLocal()
    try:
        release = db.query(KbRelease).filter(KbRelease.version == RELEASE_VERSION).first()
        if release is None:
            release = KbRelease(version=RELEASE_VERSION, market="GLOBAL", status="DRAFT")
            db.add(release)
            db.flush()

        created = 0
        activated = 0
        for article in ARTICLES:
            doc = db.query(KbDocument).filter(KbDocument.slug == article["slug"]).first()
            if doc is None:
                result = ingest_document(
                    db,
                    slug=article["slug"],
                    title=article["title"],
                    content=article["content"],
                    market="GLOBAL",
                    domain=article["domain"],
                    access_class="K0_PUBLIC",
                    author="Zoiko Rooms",
                    owner="marketing-site",
                )
                doc = db.get(KbDocument, result.document_id)
                created += 1
                if result.quarantine_reasons:
                    raise KnowledgeError(
                        f"document {article['slug']} quarantined: {', '.join(result.quarantine_reasons)}"
                    )

            if doc.status in ("DRAFT", "REVIEW"):
                make_active(db, doc.id)
                activated += 1
            if doc.release_id is None:
                doc.release_id = release.id

        release.status = "ACTIVE"
        release.activated_at = release.activated_at or datetime.now(timezone.utc)
        db.commit()

        total = db.query(KbDocument).filter(KbDocument.slug.in_([a["slug"] for a in ARTICLES])).count()
        print(
            f"Public KB seeded. release={RELEASE_VERSION} created={created} "
            f"activated={activated} total_public_docs={total}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    seed_public_kb()