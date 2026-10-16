"""How closely a document's text matches what the host entered -- a name, an
address line, a postal code -- as a confidence from 0 to 1.

Property and authority verification accept a match at
settings.document_match_threshold (75%) or above, so a small difference
between the document and the entered details (an OCR slip, a joined or
missing middle name, a spacing difference) doesn't block a genuine document.

Guards keep a different person or property out:
- every word is scored as 1 - edits / length and must reach the threshold;
  a short word (3 letters or fewer) must match exactly ("Rao" is not "Roy");
- house / door numbers are compared exactly by the callers.
"""

from __future__ import annotations

import re

from app.core.config import settings



def threshold() -> float:
    return float(getattr(settings, "document_match_threshold", 0.75) or 0.75)


# Street-type words written either way ("Rd" / "Road") compare equal.
_ABBREVIATIONS = {
    "road": "rd", "street": "st", "avenue": "ave", "av": "ave", "lane": "ln", "drive": "dr", "close": "cl",
    "court": "ct", "crescent": "cres", "place": "pl", "terrace": "ter", "gardens": "gdns", "square": "sq",
    "boulevard": "blvd", "highway": "hwy", "apartment": "apt", "apartments": "apts", "building": "bldg",
    "house": "ho", "flat": "flt", "floor": "fl", "north": "n", "south": "s", "east": "e", "west": "w",
    "saint": "st", "mount": "mt",
}


def _norm(value: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).split()
    return " ".join(_ABBREVIATIONS.get(w, w) for w in words)


def _edits(a: str, b: str) -> int:
    """Single-letter edits between two words (wrong, missing or extra letter;
    two swapped neighbours count as one) -- optimal string alignment."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


def _weighted_share(tokens: list[str], text: str, *, joinable: bool = False) -> float:
    """Share of the tokens' letters that are found in the text."""
    if not tokens:
        return 0.0
    words = set(_norm(text).split())
    joined = []
    if joinable:
        # "ANIL KUMAR" written "ANILKUMAR": a word made only of the tokens.
        def composed(w: str, used: int = 0) -> bool:
            return used >= 2 if not w else any(w.startswith(t) and composed(w[len(t):], used + 1) for t in tokens)
        joined = [w for w in words if len(w) > 3 and composed(w)]
    total = sum(len(t) for t in tokens)
    found = sum(len(t) for t in tokens if _word_score(t, words, joined) >= threshold())
    return found / total


def _word_score(token: str, words: set[str], joined: list[str]) -> float:
    """1.0 when the word is there (or inside a joined name word); otherwise
    how close the nearest word is, as 1 - edits / length. A word of 3 letters
    or fewer, or a number, only ever scores 1 or 0."""
    if token in words or any(token in w for w in joined):
        return 1.0
    if len(token) <= 3 or token.isdigit():
        return 0.0
    near = [max(0.0, 1 - _edits(token, w) / len(token)) for w in words if abs(len(w) - len(token)) <= 2]
    return max(near, default=0.0)


def name_confidence(text: str, name: str) -> float:
    """The weakest word of the name decides: every word must be on the
    document at 75%+ ("NAKULUR1" for "NAKULURI" is 88%). A word may appear as
    its initial ("N. ANILKUMAR") if at least one word is written out in full.
    "SUNIL" for "ANIL" is 50% -- a different person."""
    parts = [p for p in _norm(name).split() if len(p) > 1]
    if not parts:
        return 0.0
    words = set(_norm(text).split())

    def composed(w: str, used: int = 0) -> bool:
        return used >= 2 if not w else any(w.startswith(t) and composed(w[len(t):], used + 1) for t in parts)

    joined = [w for w in words if len(w) > 3 and composed(w)]
    scores = [_word_score(p, words, joined) for p in parts]
    if max(scores) < threshold():
        return round(min(scores), 3)
    return round(min(1.0 if s < threshold() and p[0] in words else s for p, s in zip(parts, scores)), 3)


def phrase_confidence(text: str, phrase: str) -> float:
    """An address line's likeness. A house / flat number (any word with a
    digit) must be on the document exactly -- otherwise it's 0: a neighbour's
    bill at No. 14 must never match No. 12."""
    tokens = [t for t in _norm(phrase).split() if len(t) > 1 or t.isdigit()]
    words = set(_norm(text).split())
    compact = re.sub(r"[^a-z0-9]", "", (text or "").lower())
    for t in tokens:
        if any(c.isdigit() for c in t) and t not in words and re.sub(r"[^a-z0-9]", "", t) not in compact:
            return 0.0
    return round(_weighted_share(tokens, text), 3)


def postal_confidence(text: str, postal_code: str) -> float:
    """Best likeness of the postal code to any same-length run of the text's
    letters and digits (spaces ignored): one misread character in a UK or
    Indian code still scores about 0.83."""
    code = re.sub(r"[^a-z0-9]", "", (postal_code or "").lower())
    if not code:
        return 0.0
    body = re.sub(r"[^a-z0-9]", "", (text or "").lower())
    if code in body:
        return 1.0
    n = len(code)
    best = 0.0
    for i in range(max(0, len(body) - n + 1)):
        best = max(best, 1 - _edits(code, body[i:i + n]) / n)
        if best >= 0.99:
            break
    return round(best, 3)


def accepted(score: float | None) -> bool:
    return score is not None and score >= threshold()
