"""Anti-circumvention output sanitizer (ZR-AI-SEARCH-001 SS-6).

Strips URLs, phones, emails and exact addresses from any text destined for
external cards before they are released to clients.
"""

from __future__ import annotations

import re
from typing import Any


class AntiCircumvention:
    def __init__(self) -> None:
        self._email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
        self._url = re.compile(r"https?://[^\s)\]\"']+", re.IGNORECASE)
        self._phone = re.compile(
            r"(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?)\d{3,4}[-.\s]?\d{3,4}"
        )
        # Bounded span so a match can never reach further back than the
        # StreamSanitizer holdback window.
        self._addr = re.compile(
            r"\b\d{1,5}[\s]+[A-Za-z0-9.\-\s]{1,60}"
            r"(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Block|Sector|Phase|Housing)\b",
            re.IGNORECASE,
        )
        self._pin = re.compile(r"\b\d{6}\b")

    def sanitize_text(self, text: str) -> str:
        if not text:
            return text
        t = self._email.sub("[email masked]", text)
        t = self._url.sub("[url masked]", t)
        t = self._addr.sub("[address masked]", t)
        t = self._phone.sub("[phone masked]", t)
        t = self._pin.sub("[postcode masked]", t)
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