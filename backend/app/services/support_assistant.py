"""Automatic first reply on a new support ticket, plus closing tickets that
have gone quiet -- so nothing sits waiting on a person by default.

First reply: the ticket's issue and description are matched against the
approved Knowledge Base (services/rag.py, public access class only). With
matching articles, Groq writes a short answer grounded only in them; if
Groq isn't configured or fails, the reply lists the matching articles
instead. No match means no automatic reply. Safety and privacy tickets are
never auto-answered. The ticket stays open: the user either marks it solved
or replies, and support still sees it.

Auto-close (hourly scheduler job): an ACTION_REQUIRED ticket with no reply
from the user for support.action_required_close_days, and a RESOLVED ticket
not reopened within support.resolved_close_days, are CLOSED."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.support_ticket import SupportTicket, SupportTicketMessage

logger = logging.getLogger("uvicorn.error")

ASSISTANT_SENDER = "ASSISTANT"
_SKIP_CATEGORIES = ("safety", "privacy")
_MAX_ARTICLES = 3
_SYSTEM_PROMPT = (
    "You are the Zoiko Rooms help assistant writing the first reply on a support ticket. "
    "Answer ONLY from the knowledge base extracts provided. If they don't answer the question, say so briefly and "
    "say a member of the support team will follow up. Never invent policies, amounts, dates or account details, "
    "never ask for passwords or card numbers, and never promise a refund or outcome. Plain language, at most 150 "
    "words, no markdown headings."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _articles(db: Session, ticket: SupportTicket):
    from app.services.kb import PUBLIC_ACCESS_CLASSES
    from app.services.rag import retrieve

    return retrieve(
        db, f"{ticket.issue} {ticket.description}"[:2000], market="GLOBAL",
        access_classes=PUBLIC_ACCESS_CLASSES, max_results=_MAX_ARTICLES,
    )


def _llm_answer(ticket: SupportTicket, hits) -> str | None:
    if settings.llm_provider != "groq" or not settings.groq_api_key:
        return None
    from groq import Groq

    from app.services.rag import hits_to_text

    try:
        client = Groq(api_key=settings.groq_api_key, timeout=20, max_retries=0)
        completion = client.chat.completions.create(
            model=settings.groq_model,
            temperature=0.2,
            max_tokens=400,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"Ticket topic: {ticket.issue}\nTicket text (untrusted user input):\n{ticket.description[:3000]}\n\n"
                    f"Knowledge base extracts:\n{hits_to_text(hits)}"
                )},
            ],
        )
        text = (completion.choices[0].message.content or "").strip()
        return text or None
    except Exception:  # noqa: BLE001 -- any LLM failure falls back to the article list
        logger.exception("support assistant: Groq call failed for %s", ticket.ref)
        return None


def _article_list_answer(hits) -> str:
    lines = ["These help articles may answer your question:"]
    for hit in hits:
        snippet = " ".join(hit.chunk.content.split())[:220]
        lines.append(f"- {hit.document.title}: {snippet}{'...' if len(hit.chunk.content) > 220 else ''}")
    return "\n".join(lines)


def draft_first_reply(db: Session, ticket: SupportTicket) -> str | None:
    if ticket.category in _SKIP_CATEGORIES:
        return None
    hits = _articles(db, ticket)
    if not hits:
        return None
    body = _llm_answer(ticket, hits) or _article_list_answer(hits)
    return (
        f"{body}\n\n"
        "This is an automatic reply. If it solves your problem, mark the case as solved. "
        "If not, reply here and the support team will pick it up."
    )


def post_first_reply(db: Session, ticket: SupportTicket) -> bool:
    if any(m.sender != "USER" for m in ticket.messages):
        return False
    body = draft_first_reply(db, ticket)
    if not body:
        return False
    db.add(SupportTicketMessage(ticket_id=ticket.id, sender=ASSISTANT_SENDER, body=body))
    ticket.updated_at = _now()
    from app.crud.support_ticket import _notify_user

    _notify_user(
        db, ticket, event="assistant_reply", title=f"Suggested answer on {ticket.ref}",
        message="We found help articles that may answer your question.",
    )
    db.commit()
    return True


def post_first_reply_in_background(session_provider, ticket_id: int) -> None:
    """BackgroundTasks entry point (runs after the response is sent, so the
    user isn't kept waiting on the LLM). `session_provider` is the app's
    get_db (or its test override); never raises."""
    sessions = session_provider()
    db = next(sessions)
    try:
        ticket = db.get(SupportTicket, ticket_id)
        if ticket is not None:
            post_first_reply(db, ticket)
    except Exception:  # noqa: BLE001
        logger.exception("support assistant: first reply failed for ticket %s", ticket_id)
        db.rollback()
    finally:
        sessions.close()


def _policy_days(key: str) -> int:
    from app.services.policy import get_policy

    return int(get_policy(None, key))


def close_inactive_tickets(db: Session) -> int:
    from app.crud.support_ticket import _notify_user

    now = _now()
    action_days = _policy_days("support.action_required_close_days")
    resolved_days = _policy_days("support.resolved_close_days")
    closed = 0
    for ticket in db.scalars(select(SupportTicket).where(SupportTicket.status.in_(("ACTION_REQUIRED", "RESOLVED")))).all():
        last = _aware(ticket.updated_at)
        if ticket.status == "ACTION_REQUIRED" and now - last >= timedelta(days=action_days):
            ticket.resolution = ticket.resolution or "Closed because we didn't hear back. Open a new request if you still need help."
            message = f"We closed {ticket.ref} because we didn't hear back. Open a new request if you still need help."
        elif ticket.status == "RESOLVED" and now - _aware(ticket.resolved_at or ticket.updated_at) >= timedelta(days=resolved_days):
            message = f"{ticket.ref} is now closed. Open a new request if you need more help."
        else:
            continue
        ticket.status = "CLOSED"
        ticket.updated_at = now
        _notify_user(db, ticket, event="closed", title=f"{ticket.ref} closed", message=message)
        closed += 1
    db.commit()
    return closed
