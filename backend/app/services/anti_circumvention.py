"""Anti-circumvention output sanitizer (ZR-AI-SEARCH-001 Section 8).

Strips links (with or without a scheme), emails, phone numbers, social
handles, exact addresses and postcodes from any text released to users,
providers or the assistant's own replies.
"""

from __future__ import annotations

import re
from typing import Any


# Top-level domains recognised in scheme-less links ("www.agent.co.uk/rooms/9",
# "wa.me/447700900123"). A fixed list keeps ordinary words like "e.g." or
# "St.Paul" from being treated as links.
_TLDS = (
    "co.uk|org.uk|ac.uk|com.au|co.in|co.za|com|org|net|uk|us|in|io|co|me|ly|ae|au|ca|de|fr|es|nl|jp|"
    "info|biz|app|site|online|link|page|homes|house|properties|rent|estate"
)
# Zoiko Rooms' own domains are not a way around Zoiko Rooms.
_OWN_DOMAINS = ("zoikorooms.com",)


class AntiCircumvention:
    """Section 8 output sanitizer: removes direct-contact artifacts (links,
    emails, phone numbers, social handles, exact addresses, postcodes) from
    any text released to users or providers. ``last_masks`` reports what the
    most recent call removed, for circumvention-attempt metrics."""

    def __init__(self) -> None:
        self._email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
        self._url = re.compile(r"https?://[^\s)\]\"']+", re.IGNORECASE)
        # Bounded so a match can never reach further back than the
        # StreamSanitizer holdback window.
        self._domain = re.compile(
            r"(?<![@\w.-])(?:www\.)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.){1,4}(?:" + _TLDS + r")\b"
            r"(?:/[^\s)\]\"']{0,200})?",
            re.IGNORECASE,
        )
        self._handle = re.compile(r"(?<![\w@.])@[A-Za-z0-9_](?:[A-Za-z0-9_.]{1,29})\b")
        self._phone = re.compile(
            r"(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?)\d{3,4}[-.\s]?\d{3,4}"
        )
        # Indian 10-digit mobiles written 5+5 ("98765 43210").
        self._phone_in = re.compile(r"(?<![\d,.])(?:\+?91[-\s]?)?[6-9]\d{4}[-\s]\d{5}(?![\d,])")
        self._addr = re.compile(
            r"\b\d{1,5}[\s]+[A-Za-z0-9.\-\s]{1,60}"
            r"(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Block|Sector|Phase|Housing)\b",
            re.IGNORECASE,
        )
        # UK postcodes ("BS1 5QA", "SW1A 1AA").
        self._uk_postcode = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.IGNORECASE)
        self._pin = re.compile(r"\b\d{6}\b")
        self.last_masks: dict[str, int] = {}
        self._domains_masked = 0

    def _mask_domain(self, match: re.Match) -> str:
        host = match.group(0).lower().split("/")[0]
        host = host[4:] if host.startswith("www.") else host
        if any(host == d or host.endswith("." + d) for d in _OWN_DOMAINS):
            return match.group(0)
        self._domains_masked += 1
        return "[url masked]"

    def sanitize_text(self, text: str) -> str:
        self.last_masks = {}
        if not text:
            return text
        t = text
        for name, pattern, repl in (
            ("email", self._email, "[email masked]"),
            ("url", self._url, "[url masked]"),
            ("url", self._domain, self._mask_domain),
            ("handle", self._handle, "[handle masked]"),
            ("address", self._addr, "[address masked]"),
            ("postcode", self._uk_postcode, "[postcode masked]"),
            ("phone", self._phone_in, "[phone masked]"),
            ("phone", self._phone, "[phone masked]"),
            ("postcode", self._pin, "[postcode masked]"),
        ):
            self._domains_masked = 0
            t, n = pattern.subn(repl, t)
            if pattern is self._domain:
                n = self._domains_masked  # own-domain matches are kept, not masked
            if n:
                self.last_masks[name] = self.last_masks.get(name, 0) + n
        return t

    def sanitize_card(self, card: dict[str, Any]) -> dict[str, Any]:
        c = dict(card)
        for key in (
            "title",
            "availability_text",
            "room_type",
            "location_city",
            "location_region",
            "location_country",
            "occupancy",
        ):
            if c.get(key):
                c[key] = self.sanitize_text(str(c[key]))

        if isinstance(c.get("amenities"), list):
            c["amenities"] = [self.sanitize_text(str(a)) for a in c["amenities"]]

        # Masked cards never carry unlockable direct contact fields.
        c["has_exact_address"] = False
        c["has_phone"] = False
        c["has_email"] = False
        c["has_url"] = False
        c["is_unlocked"] = False
        return c


sanitizer = AntiCircumvention()


class StreamSanitizer:
    """Sanitize model text that is streamed to the client chunk by chunk.

    A URL, email or phone number can be split across chunks, so chunks are
    never sanitized in isolation. Instead the whole text received so far is
    re-sanitized and only a prefix ending at whitespace at least HOLDBACK
    characters before the end is released -- by then any contact pattern
    crossing that point has fully arrived. A prefix that would contradict
    text already released is withheld; the route's final "done" content is
    sanitized in full regardless.
    """

    HOLDBACK = 128

    def __init__(self, scrubber: AntiCircumvention = sanitizer) -> None:
        self._scrubber = scrubber
        self._raw = ""
        self._emitted = ""

    def feed(self, chunk: str) -> str:
        self._raw += chunk
        limit = len(self._raw) - self.HOLDBACK
        if limit <= 0:
            return ""
        cut = max(self._raw.rfind(" ", 0, limit), self._raw.rfind("\n", 0, limit))
        if cut <= 0:
            return ""
        prefix = self._scrubber.sanitize_text(self._raw[:cut])
        if not self._scrubber.sanitize_text(self._raw).startswith(prefix):
            # A pattern straddles the cut; wait for the next chunk.
            return ""
        return self._release(prefix)

    def flush(self) -> str:
        return self._release(self._scrubber.sanitize_text(self._raw))

    def _release(self, clean: str) -> str:
        if not clean.startswith(self._emitted):
            return ""
        delta = clean[len(self._emitted):]
        self._emitted = clean
        return delta