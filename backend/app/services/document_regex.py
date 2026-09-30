"""Regex-only detail extraction for uploaded identity documents -- no OCR.

The text searched is whatever already exists as text: the document number the
user typed into the form, plus the embedded text layer of a PDF upload (read
with pypdf). A photo (JPG/PNG) has no text layer, so for photos only the typed
number is available. Extraction never blocks verification -- it only fills in
the details stored on the IdentityVerification row.
"""

from __future__ import annotations

import io
import logging
import re

logger = logging.getLogger("uvicorn.error")

# Document number formats per document type. Anything not listed falls back to
# _GENERIC_NUMBER: 6-20 uppercase letters/digits/hyphens containing a digit.
_NUMBER_PATTERNS: dict[str, re.Pattern[str]] = {
    "aadhaar": re.compile(r"\b[2-9]\d{3}\s?\d{4}\s?\d{4}\b"),
    "pan_card": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    "passport": re.compile(r"\b[A-Z]{1,2}\d{6,8}\b"),
    "voter_id": re.compile(r"\b[A-Z]{3}\d{7}\b"),
    "driving_license": re.compile(r"\b[A-Z]{2}[-\s]?\d{2}[-\s]?\d{4}[-\s]?\d{7}\b"),
}
_GENERIC_NUMBER = re.compile(r"\b(?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{5,19}\b")

_NAME_PATTERN = re.compile(
    r"^\s*(?:full\s+name|holder'?s?\s+name|name)\s*[:\-]?\s*([A-Za-z][A-Za-z .'-]{1,80}?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def pdf_text(document_bytes: bytes) -> str:
    """The PDF's embedded text layer, or "" for a scanned/image-only PDF or
    anything pypdf can't open."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(document_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages[:5])
    except Exception:
        logger.info("document_regex: could not read PDF text layer", exc_info=True)
        return ""


def extract_number(document_type: str, *texts: str) -> str | None:
    """First match for this document type's number format, checking `texts`
    in order (typed number first, then document text). Spaces/hyphens are
    stripped from the result."""
    pattern = _NUMBER_PATTERNS.get(document_type, _GENERIC_NUMBER)
    for text in texts:
        match = pattern.search((text or "").upper())
        if match:
            return re.sub(r"[\s-]", "", match.group(0))
    return None


def extract_name(text: str) -> str | None:
    """A value printed after a "Name:" style label in the document text."""
    match = _NAME_PATTERN.search(text or "")
    return " ".join(match.group(1).split()) if match else None


def extract_details(document_type: str, document_bytes: bytes, content_type: str, typed_number: str) -> tuple[str | None, str | None]:
    """(document_number, name) -- either may be None."""
    text = pdf_text(document_bytes) if content_type == "application/pdf" else ""
    return extract_number(document_type, typed_number, text), extract_name(text)


# --- Property documents (title deed, sale deed, tax receipt, utility bill...) ---

_OWNER_PATTERN = re.compile(
    r"^\s*(?:owner(?:'?s)?\s+name|name\s+of\s+(?:the\s+)?owner|owner|landlord|lessor|"
    r"property\s+holder|consumer\s+name|customer\s+name|name)\s*[:\-]?\s*([A-Za-z][A-Za-z .'-]{1,80}?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_ADDRESS_PATTERN = re.compile(
    r"^\s*(?:property\s+address|address\s+of\s+(?:the\s+)?property|premises|site\s+address|"
    r"supply\s+address|service\s+address|address)\s*[:\-]?\s*(.{5,300}?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_PROPERTY_NUMBER_PATTERN = re.compile(
    r"(?:deed|survey|khata|patta|assessment|property\s+tax|consumer|account|registration|document)"
    r"\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Z0-9][A-Z0-9/\-]{2,40})",
    re.IGNORECASE,
)


def extract_property_details(document_bytes: bytes, content_type: str, typed_text: str) -> dict[str, str | None]:
    """owner_name / address / document_number read from a property document's
    PDF text layer and the host's typed evidence reference. Any may be None;
    has_text says whether there was anything to read at all."""
    document_text = pdf_text(document_bytes) if content_type == "application/pdf" else ""
    text = "\n".join(filter(None, [document_text, typed_text or ""]))
    owner = _OWNER_PATTERN.search(text)
    address = _ADDRESS_PATTERN.search(text)
    number = _PROPERTY_NUMBER_PATTERN.search(text)
    return {
        "owner_name": " ".join(owner.group(1).split()) if owner else None,
        "address": " ".join(address.group(1).split()) if address else None,
        "document_number": number.group(1).upper() if number else None,
        "text": text,
        # Only the PDF's own text, not the typed reference -- a short typed
        # label like "title deed" is not a document to match against.
        "document_text": document_text,
    }


def _words(value: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (value or "").lower()) if len(w) >= 3]


def name_matches(expected_name: str, text: str) -> bool | None:
    """True when every 3+ letter word of expected_name appears in text; None
    when there's no text or no usable name to compare."""
    wanted, haystack = _words(expected_name), set(_words(text))
    if not wanted or not haystack:
        return None
    return all(w in haystack for w in wanted)


def address_matches(address: str, city: str, text: str) -> bool | None:
    """True when the city appears in text AND at least half of the address's
    3+ letter words do (addresses get abbreviated/reordered, so an exact
    match is too strict). None when there's nothing to compare."""
    haystack = set(_words(text))
    address_words, city_words = _words(address), _words(city)
    if not haystack or not (address_words or city_words):
        return None
    city_ok = all(w in haystack for w in city_words) if city_words else True
    if not address_words:
        return city_ok
    hits = sum(1 for w in address_words if w in haystack)
    return city_ok and hits * 2 >= len(address_words)
