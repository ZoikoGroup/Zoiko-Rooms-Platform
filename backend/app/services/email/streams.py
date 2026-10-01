"""ZR-COMMS-EMAIL-001 Section 1.2 -- sending streams. Each stream has its own
From identity so security, transaction, money and trust reputation stay
separate.

The spec's example addresses (security@account.zoikorooms.com, ...) need
their own authenticated domains (SPF/DKIM/DMARC, Section 3.2). Until those
exist, every stream sends from the address in settings.email_from -- the one
the SMTP account is authorized to send as -- with the stream's own display
name. Set EMAIL_FROM_<STREAM> (e.g. EMAIL_FROM_SECURITY) to give a stream its
own address once its domain is authenticated, and EMAIL_REPLY_TO_<STREAM> for
its monitored reply route."""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import formataddr, parseaddr

from app.core.config import settings


@dataclass(frozen=True)
class Stream:
    key: str
    display_name: str
    label: str  # shown in the email header strip


SECURITY = Stream("security", "Zoiko Rooms Security", "Account security")
TRANSACTIONS = Stream("transactions", "Zoiko Rooms", "Rental updates")
MONEY = Stream("money", "Zoiko Rooms Payments", "Payments")
TRUST = Stream("trust", "Zoiko Rooms Trust & Safety", "Trust & Safety")
ORGANIZATIONS = Stream("organizations", "Zoiko Rooms for Organizations", "Organizations")
MARKETING = Stream("marketing", "Zoiko Rooms Updates", "Updates")

STREAMS: dict[str, Stream] = {s.key: s for s in (SECURITY, TRANSACTIONS, MONEY, TRUST, ORGANIZATIONS, MARKETING)}


def sender_for(stream: Stream) -> str:
    override = (getattr(settings, f"email_from_{stream.key}", "") or "").strip()
    if override:
        return override
    _, address = parseaddr(settings.email_from)
    return formataddr((stream.display_name, address or "no-reply@zoikorooms.com"))


def reply_to_for(stream: Stream) -> str:
    return (getattr(settings, f"email_reply_to_{stream.key}", "") or "").strip()
