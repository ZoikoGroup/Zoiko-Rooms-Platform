"""ZR-PROPERTY-VERIFY-001 Section 8 "Document evidence" -- reads an uploaded
property document and extracts the signals the confidence engine uses.

Text comes from the PDF's own text layer when it has one (portal
downloads), otherwise from OCR (Tesseract via services/document_ocr.py) for
photos and scanned PDFs. Regex/fuzzy matching then checks:
  - the confirmed address (line 1 words, OCR-tolerant) and postal code,
  - the unit / flat number,
  - the owner's name against the host's verified identity name (a signal
    for the reviewer -- ownership itself belongs to Authority Verification),
  - that the document looks like the type the host chose (keywords),
  - the most recent year on it (an outdated bill can't confirm the
    property exists today),
  - an assessment / property / account reference number.

Nothing here proves a document is genuine -- poor reads, mismatches and
tamper signals route to review (Section 14). The raw text is never stored.
Works the same on Windows and Linux: document_ocr finds Tesseract/Poppler on
PATH or the standard install locations.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field
from datetime import date

logger = logging.getLogger("uvicorn.error")

# Below this average Tesseract word confidence an OCR read is too unreliable
# to match against (blurred / angled / dark photos).
OCR_MIN_CONFIDENCE = 55.0
# A PDF text layer shorter than this is treated as a scan (image-only PDF).
MIN_TEXT_LAYER_CHARS = 30
# A document older than this many years can't confirm the property exists now.
MAX_DOCUMENT_AGE_YEARS = 3

# Words that show a document is the type the host chose. UK and US first
# (the main markets), then India.
TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "PROPERTY_TAX_RECORD": (
        # UK
        "COUNCIL TAX", "COUNCIL TAX BAND", "BILLING AUTHORITY", "VALUATION OFFICE", "BOROUGH COUNCIL",
        "DISTRICT COUNCIL", "CITY COUNCIL", "COUNTY COUNCIL", "BUSINESS RATES",
        # US
        "COUNTY ASSESSOR", "ASSESSOR", "TAX COLLECTOR", "TREASURER", "ASSESSED VALUE", "PROPERTY TAX STATEMENT",
        "SECURED PROPERTY TAX", "REAL ESTATE TAX", "AD VALOREM", "TAX YEAR", "PARCEL NUMBER", "APN",
        # general / India
        "PROPERTY TAX", "HOUSE TAX", "ASSESSMENT", "MUNICIPAL", "MUNICIPALITY", "CORPORATION", "GRAM PANCHAYAT",
        "PANCHAYAT", "TAX RECEIPT", "DEMAND NOTICE", "PTIN", "ASSESSEE", "TAX BILL"),
    "LAND_REGISTRY_RECORD": (
        # UK
        "HM LAND REGISTRY", "OFFICIAL COPY", "TITLE NUMBER", "PROPERTY REGISTER", "PROPRIETORSHIP REGISTER",
        "CHARGES REGISTER", "TITLE REGISTER", "REGISTERS OF SCOTLAND", "LAND AND PROPERTY SERVICES",
        # US
        "COUNTY RECORDER", "RECORDER OF DEEDS", "REGISTER OF DEEDS", "RECORDED", "PLAT", "LEGAL DESCRIPTION",
        "PARCEL",
        # general / India
        "LAND REGISTRY", "REGISTRY", "CADASTRAL", "SURVEY NO", "SURVEY NUMBER", "PATTA", "KHATA", "PAHANI",
        "ENCUMBRANCE", "MUTATION", "RECORD OF RIGHTS"),
    "BUILDING_UNIT_RECORD": (
        # UK
        "LEASEHOLD", "LEASE", "MANAGEMENT COMPANY", "MANAGING AGENT", "SERVICE CHARGE", "GROUND RENT",
        "BUILDING CONTROL", "COMPLETION CERTIFICATE",
        # US
        "HOA", "HOMEOWNERS ASSOCIATION", "CONDOMINIUM", "CONDO", "CERTIFICATE OF OCCUPANCY", "CO-OP",
        "COOPERATIVE",
        # general / India
        "BUILDING", "APARTMENT", "FLAT NO", "UNIT NO", "OCCUPANCY CERTIFICATE", "SOCIETY", "ASSOCIATION"),
    "TITLE_DEED": (
        # UK
        "TRANSFER OF WHOLE", "TR1", "TRANSFEROR", "TRANSFEREE", "CONVEYANCE", "FREEHOLD",
        # US
        "GRANT DEED", "WARRANTY DEED", "QUITCLAIM", "GRANTOR", "GRANTEE", "DEED OF TRUST",
        # general / India
        "SALE DEED", "DEED", "TITLE", "SUB-REGISTRAR", "SUB REGISTRAR", "REGISTRATION", "VENDOR", "VENDEE",
        "TRANSFER"),
    "MORTGAGE_INSURANCE_STATEMENT": (
        "MORTGAGE", "MORTGAGE STATEMENT", "ESCROW", "HOMEOWNERS INSURANCE", "BUILDINGS INSURANCE",
        "HOME INSURANCE", "HOME LOAN", "INSURANCE", "POLICY", "PREMIUM", "SUM INSURED", "DWELLING"),
    "UTILITY_BILL": (
        # UK
        "ENERGY", "BRITISH GAS", "EDF", "OCTOPUS", "THAMES WATER", "COUNCIL WATER", "MPAN", "MPRN",
        # US
        "UTILITY", "UTILITIES", "PG&E", "CON EDISON", "DUKE ENERGY", "SERVICE ADDRESS", "ACCOUNT NUMBER",
        # general / India
        "ELECTRICITY", "WATER", "GAS", "BILL", "CONSUMER", "METER", "UNITS CONSUMED", "SERVICE NO", "DISCOM",
        "TSSPDCL", "APSPDCL", "BESCOM", "MSEDCL"),
}
# Reference numbers the reviewer needs, most specific first: a UK Land
# Registry title number (e.g. MX123456, TGL12345), a US parcel number / APN,
# then any labelled account / assessment / reference number.
_REFERENCE_PATTERNS = (
    re.compile(r"TITLE\s*(?:NUMBER|NO)\s*[.:#-]?\s*([A-Z]{1,3}\s?\d{1,7})\b"),
    re.compile(r"(?:APN|PARCEL\s*(?:NUMBER|NO|ID)|ASSESSOR'?S\s*PARCEL\s*(?:NUMBER|NO)|PIN)\s*[.:#-]?\s*"
               r"([0-9][0-9A-Z\-.]{4,24}[0-9A-Z])"),
    re.compile(r"(?:COUNCIL\s*TAX\s*)?(?:ACCOUNT|REFERENCE|REF)\s*(?:NUMBER|NO)?\s*[.:#-]?\s*([A-Z0-9][A-Z0-9/\-]{4,24})"),
    re.compile(r"(?:ASSESSMENT|PTIN|PROPERTY|HOUSE|DOOR|CONSUMER|SERVICE|SURVEY|KHATA|PATTA|DOCUMENT|REG(?:ISTRATION)?)"
               r"\s*(?:NO|NUMBER|ID|#)?\s*[.:#-]?\s*([A-Z0-9][A-Z0-9/\-]{3,24})"),
)
_COUNCIL_TAX_BAND = re.compile(r"\bBAND\s*[:\-]?\s*([A-I])\b")
_YEAR = re.compile(r"(?<!\d)(19[89]\d|20\d\d)(?!\d)")


@dataclass
class DocumentAnalysis:
    text_source: str = "NONE"           # PDF_TEXT | OCR | NONE
    ocr_confidence: float | None = None
    quality: str = "UNREADABLE"          # GOOD | POOR | UNREADABLE | OCR_UNAVAILABLE
    readable: bool = False
    address_matched: bool | None = None
    postal_matched: bool | None = None
    unit_matched: bool | None = None
    owner_name_matched: bool | None = None
    document_type_matched: bool | None = None
    document_year: int | None = None
    reference_number: str = ""
    council_tax_band: str = ""          # UK council tax bills only
    signals: list[str] = field(default_factory=list)


def _norm(value: str | None) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (value or "").upper()).split())


def _pdf_text(content: bytes) -> str:
    try:
        from app.services import document_regex

        return document_regex.pdf_text(content) or ""
    except Exception:
        return ""


def _ocr(content: bytes) -> tuple[str, float] | None:
    """(text, confidence), or None when OCR isn't installed / failed (the
    caller routes the document to review rather than guessing)."""
    try:
        from app.services import document_ocr

        if not document_ocr.is_available():
            return None
        return document_ocr._ocr_text_and_confidence(content)
    except Exception:
        logger.info("property document OCR failed", exc_info=True)
        return None


def _token_present(token: str, words: set[str], tolerant: bool) -> bool:
    if token in words:
        return True
    if not tolerant or len(token) < 4:
        return False
    return any(difflib.SequenceMatcher(None, token, w).ratio() >= 0.85 for w in words if abs(len(w) - len(token)) <= 2)


def _phrase_in(text: str, phrase: str, tolerant: bool) -> bool:
    """All significant words of `phrase` appear (OCR-tolerant for scans)."""
    words = set(text.split())
    tokens = [t for t in _norm(phrase).split() if len(t) > 1 or t.isdigit()]
    if not tokens:
        return False
    hits = sum(1 for t in tokens if _token_present(t, words, tolerant))
    return hits / len(tokens) >= 0.75


# Words that describe where a property is relative to something else. Hosts
# write them in the address ("2-599, near Muthyalamma temple"); official
# documents almost never carry them, so they're not required to match.
LANDMARK_WORDS = {
    "near", "beside", "besides", "behind", "opp", "opposite", "next", "adjacent", "adj", "front", "back", "side",
    "temple", "mandir", "church", "mosque", "masjid", "gurudwara", "school", "college", "hospital", "bank", "office",
    "apartment", "apartments", "appartment", "complex", "tower", "towers", "building", "block", "lane", "road",
    "street", "cross", "main", "colony", "nagar", "layout", "village", "post", "landmark", "the", "and", "of",
}


def identity_matches(text: str, address: dict, tolerant: bool = False) -> bool:
    """The document identifies the property when it carries what makes the
    address unique -- not every word the host typed:

    - the house / door number (2-599 = 2599 = "2 - 599"), when one was given;
    - at least one place name -- street, colony, village or city -- allowing
      spelling variants ("Madupalli" / "Madupally") on OCR'd text.
    Landmark words ("near ... temple", "beside ... apartment") are ignored.
    The postal code is checked separately by the caller."""
    from app.services.location import CanonicalAddress, _number_tokens, house_numbers

    entered = CanonicalAddress.from_dict(address)
    numbers = house_numbers(entered)
    if numbers and not numbers & _number_tokens(text):
        return False
    words = set(_norm(text).split())
    names = []
    for field in ("address_line_1", "address_line_2", "locality"):
        for token in _norm(address.get(field) or "").split():
            lowered = token.lower()
            if len(token) >= 4 and token.isalpha() and lowered not in LANDMARK_WORDS:
                names.append(token)
    if not names:
        return bool(numbers)
    hits = [t for t in dict.fromkeys(names) if _token_present(t, words, True if len(t) >= 6 else tolerant)]
    return len(hits) >= (1 if numbers else min(2, len(set(names))))


def analyze(content: bytes, content_type: str, *, evidence_type: str, canonical_address: dict, unit: str = "",
            owner_name: str = "") -> DocumentAnalysis:
    result = DocumentAnalysis()
    text = ""
    if content_type == "application/pdf":
        text = _pdf_text(content)
        if len(text.strip()) >= MIN_TEXT_LAYER_CHARS:
            result.text_source, result.quality = "PDF_TEXT", "GOOD"
        else:
            text = ""
    if not text:
        ocr = _ocr(content)
        if ocr is None:
            result.quality = "OCR_UNAVAILABLE"
            result.signals.append("OCR_UNAVAILABLE")
            return result
        text, confidence = ocr
        result.text_source, result.ocr_confidence = "OCR", round(confidence, 1)
        if not text.strip():
            result.quality = "UNREADABLE"
            return result
        result.quality = "GOOD" if confidence >= OCR_MIN_CONFIDENCE else "POOR"

    body = _norm(text)
    tolerant = result.text_source == "OCR"
    result.readable = result.quality == "GOOD"

    line1 = canonical_address.get("address_line_1") or ""
    postal = re.sub(r"\s", "", canonical_address.get("postal_code") or "").upper()
    compact = re.sub(r"\s", "", body)
    result.postal_matched = (postal in compact) if postal else None
    line_ok = (_phrase_in(body, line1, tolerant) if line1 else False) or \
        identity_matches(text, canonical_address, tolerant)
    result.address_matched = bool(line_ok and (result.postal_matched is not False))
    if unit:
        u = _norm(unit)
        result.unit_matched = u in body.split() or u in body
    if owner_name:
        result.owner_name_matched = _phrase_in(body, owner_name, tolerant)

    keywords = TYPE_KEYWORDS.get(evidence_type, ())
    if keywords:
        result.document_type_matched = any(k in body for k in keywords)
    years = [int(y) for y in _YEAR.findall(body) if int(y) <= date.today().year + 1]
    result.document_year = max(years) if years else None
    raw = " ".join(text.upper().split())  # raw text: keeps "/" and "-" in numbers
    for pattern in _REFERENCE_PATTERNS:
        ref = pattern.search(raw)
        if ref:
            result.reference_number = ref.group(1).strip()[:40]
            break
    band = _COUNCIL_TAX_BAND.search(raw) if "COUNCIL TAX" in raw else None
    if band:
        result.council_tax_band = band.group(1)

    if result.quality == "POOR":
        result.signals.append("EVIDENCE_POOR_QUALITY")
    if result.document_type_matched is False:
        result.signals.append("EVIDENCE_TYPE_UNCLEAR")
    if result.document_year and result.document_year < date.today().year - MAX_DOCUMENT_AGE_YEARS:
        result.signals.append("EVIDENCE_OUTDATED")
    if result.owner_name_matched is False:
        result.signals.append("OWNER_NAME_NOT_ON_DOCUMENT")
    return result
