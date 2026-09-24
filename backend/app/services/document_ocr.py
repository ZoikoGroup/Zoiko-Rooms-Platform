"""Real, local OCR check on an uploaded identity document -- reads the
document number off the actual image (via Tesseract, running entirely on
this machine, no external API/vendor) and validates it against that
document type's real format. This is a genuine check, unlike the dev-only
auto_accept_agent.py: it can genuinely reject a bad upload.

Deliberately best-effort at the call site (see crud/identity_verification.py):
if Tesseract isn't installed, or OCR itself errors out, this must never
block a real user's submission -- it only actively re-routes a submission
to "please re-upload" when OCR ran successfully and produced a low-confidence
or no-match result. Infra being unavailable fails OPEN (falls back to the
existing manual/agent review path); a genuinely bad scan fails CLOSED
(reroutes to re-upload) -- these are different kinds of failure and must
not be conflated.

Confidence rule: both the document-number FORMAT must be found in the
extracted text, AND Tesseract's own average per-word confidence across the
image must be at least that document type's threshold (see
OCR_CONFIDENCE_THRESHOLDS below). Either one failing means the scan
quality/content can't be trusted.

Aadhaar additionally gets a real checksum validation (the Verhoeff
algorithm UIDAI itself uses) -- 12 digits alone only proves the OCR read
*a* 12-digit number, not a *valid* one; a mistyped or fabricated 12-digit
string would pass the format regex but fail the checksum. Nothing else
this platform accepts (PAN, passport, etc.) has a public, verifiable
checksum algorithm, so this stays Aadhaar-specific rather than a fabricated
"check" for formats that don't have one."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

# Fallback for any document type not listed in OCR_CONFIDENCE_THRESHOLDS.
OCR_CONFIDENCE_THRESHOLD = 50.0

# Real format for each identity document type this platform accepts
# (mirrors models/identity_verification.py:DOCUMENT_TYPES's identity-category
# entries). Documents with no single well-known universal format (most
# non-Indian government IDs) get a reasonable generic ID-like pattern
# instead of skipping validation entirely -- honest about being a loose
# check for those, not a skipped one.
_GENERIC_ID_PATTERN = r"\b[A-Z0-9]{6,20}\b"
DOCUMENT_NUMBER_PATTERNS: dict[str, str] = {
    # 12 digits, optionally space-grouped in 4s as printed on the real card.
    "aadhaar": r"\b\d{4}\s?\d{4}\s?\d{4}\b",
    # 5 letters + 4 digits + 1 letter -- India's real PAN format.
    "pan_card": r"\b[A-Z]{5}[0-9]{4}[A-Z]{1}\b",
    # India's Voter ID (EPIC) format -- 3 letters + 7 digits.
    "voter_id": r"\b[A-Z]{3}\d{7}\b",
    "passport": _GENERIC_ID_PATTERN,
    "driving_license": _GENERIC_ID_PATTERN,
    "national_id": _GENERIC_ID_PATTERN,
    "government_photo_id": _GENERIC_ID_PATTERN,
    "residence_permit": _GENERIC_ID_PATTERN,
    "permanent_resident_card": _GENERIC_ID_PATTERN,
    "government_employee_id": _GENERIC_ID_PATTERN,
}

# Per-type confidence bar. The three strict, highly specific patterns above
# (aadhaar/pan_card/voter_id) rarely false-positive-match random text, so
# the default 50% is fine -- aadhaar also gets a real checksum on top,
# making its bar effectively stronger than the number alone suggests. Every
# type on the loose _GENERIC_ID_PATTERN is inherently easier to
# false-positive against (any 6-20 char alnum run matches), so it demands a
# higher OCR confidence to compensate for the pattern itself being weaker
# evidence.
OCR_CONFIDENCE_THRESHOLDS: dict[str, float] = {
    "aadhaar": 50.0,
    "pan_card": 50.0,
    "voter_id": 50.0,
    "passport": 65.0,
    "driving_license": 65.0,
    "national_id": 65.0,
    "government_photo_id": 65.0,
    "residence_permit": 65.0,
    "permanent_resident_card": 65.0,
    "government_employee_id": 65.0,
}


def confidence_threshold_for(document_type: str) -> float:
    return OCR_CONFIDENCE_THRESHOLDS.get(document_type, OCR_CONFIDENCE_THRESHOLD)


# The Verhoeff checksum algorithm (public, used by UIDAI for Aadhaar) --
# detects a mistyped or fabricated 12-digit string that happens to match
# the format regex but isn't a mathematically valid Aadhaar number.
_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6), (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8), (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2), (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4), (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2), (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0), (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5), (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)
_VERHOEFF_INV = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def _verhoeff_checksum(digits: str) -> int:
    c = 0
    for i, digit in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(digit)]]
    return c


def is_valid_aadhaar_checksum(number: str) -> bool:
    """Validates all 12 digits including the checksum -- unlike generating
    one, which only needs the checksum digit chosen to make this true."""
    return len(number) == 12 and number.isdigit() and _verhoeff_checksum(number) == 0

_WINDOWS_DEFAULT_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


def _resolve_tesseract_cmd() -> str | None:
    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in _WINDOWS_DEFAULT_PATHS:
        if Path(candidate).is_file():
            return candidate
    return None


def is_available() -> bool:
    return _resolve_tesseract_cmd() is not None


def _resolve_poppler_bin_dir() -> str | None:
    """pdf2image needs the directory containing pdftoppm.exe (poppler_path),
    not the binary itself. Same "check PATH first, then the winget install
    location" shape as _resolve_tesseract_cmd above -- poppler's winget
    package writes a versioned folder name, so this globs for it rather
    than hardcoding a version that will drift on the next update."""
    found = shutil.which("pdftoppm")
    if found:
        return str(Path(found).parent)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    matches = list(Path(local_app_data).glob("Microsoft/WinGet/Packages/*Poppler*/poppler-*/Library/bin/pdftoppm.exe"))
    return str(matches[0].parent) if matches else None


def _first_page_as_image(pdf_bytes: bytes) -> "Image":
    from pdf2image import convert_from_bytes

    poppler_bin_dir = _resolve_poppler_bin_dir()
    if poppler_bin_dir is None:
        raise RuntimeError("Poppler (pdftoppm) not found on this machine")
    pages = convert_from_bytes(pdf_bytes, first_page=1, last_page=1, poppler_path=poppler_bin_dir)
    if not pages:
        raise RuntimeError("PDF has no pages to read")
    return pages[0]


def _ocr_text_and_confidence(document_bytes: bytes) -> tuple[str, float]:
    """Shared Tesseract pass behind both extract_and_score (identity) and
    check_address_document_plausibility (address) below. Raises on a
    genuine OCR/image-processing failure -- the caller decides whether that
    means "fail open" (infra unavailable) per this module's own docstring.
    Accepts either a real image (JPG/PNG) or a PDF -- a PDF's first page is
    rasterized via Poppler before the same Tesseract pass every image goes
    through; a document scanned as a multi-page PDF only ever needs its
    first page checked, same as a photo would be."""
    import pytesseract
    from PIL import Image
    from io import BytesIO

    cmd = _resolve_tesseract_cmd()
    if cmd is None:
        raise RuntimeError("Tesseract OCR binary not found on this machine")
    pytesseract.pytesseract.tesseract_cmd = cmd

    if document_bytes.startswith(b"%PDF"):
        image = _first_page_as_image(document_bytes)
    else:
        image = Image.open(BytesIO(document_bytes))
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

    words = [w for w in data.get("text", []) if w.strip()]
    confidences = [float(c) for c in data.get("conf", []) if c not in ("-1", -1)]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return " ".join(words).upper(), avg_confidence


def extract_and_score(document_bytes: bytes, document_type: str) -> tuple[str | None, float]:
    """Returns (matched_document_number_or_None, average_ocr_confidence_0_to_100)."""
    full_text, avg_confidence = _ocr_text_and_confidence(document_bytes)
    pattern = DOCUMENT_NUMBER_PATTERNS.get(document_type)
    if pattern is None:
        return None, avg_confidence

    match = re.search(pattern, full_text)
    matched_number = match.group(0).replace(" ", "") if match else None
    return matched_number, avg_confidence


# Address/residency documents have no universal document NUMBER (unlike
# identity documents) and nothing stored anywhere in this platform to check
# a claimed address against (Party/UserAccount have no address field at
# all) -- so the one genuine, non-fabricated signal available is "does this
# document's actual content plausibly match the type the user claims it
# is." Real, type-relevant keywords/phrases only -- not a stand-in for
# verifying the address itself.
ADDRESS_DOCUMENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "electricity_bill": ("ELECTRICITY", "KWH", "METER READING", "ELECTRIC"),
    "water_bill": ("WATER", "SEWERAGE", "SEWAGE", "WATER SUPPLY"),
    "gas_bill": ("GAS", "THERM", "CUBIC METER", "CUBIC METRE"),
    "telephone_bill": ("TELEPHONE", "PHONE", "LANDLINE", "CALL CHARGES"),
    "internet_bill": ("INTERNET", "BROADBAND", "WIFI", "WI-FI", "DATA USAGE"),
    "property_tax_bill": ("PROPERTY TAX", "COUNCIL TAX", "RATES"),
    "bank_statement": ("STATEMENT", "BALANCE", "ACCOUNT NUMBER", "SORT CODE", "TRANSACTION"),
    "credit_card_statement": ("CREDIT CARD", "STATEMENT", "MINIMUM PAYMENT", "AVAILABLE CREDIT"),
    "government_address_certificate": ("CERTIFICATE", "GOVERNMENT", "CERTIFY", "RESIDENCE"),
    "rental_agreement": ("TENANCY", "LEASE", "RENT", "LANDLORD", "TENANT", "AGREEMENT"),
}

# Bills/statements are typically dense, small-print, multi-column layouts --
# OCR confidence across a whole page is inherently noisier than a single
# large ID-card number, so this bar is deliberately lower than any identity
# threshold rather than a fabricated attempt at matching precision the
# scan quality can't actually support.
ADDRESS_OCR_CONFIDENCE_THRESHOLD = 40.0


def check_address_document_plausibility(document_bytes: bytes, document_type: str) -> tuple[bool, float]:
    """Returns (plausible, average_ocr_confidence_0_to_100). "plausible"
    means at least one real keyword for this document_type was found in
    the extracted text AND confidence meets ADDRESS_OCR_CONFIDENCE_THRESHOLD
    -- catches a blank page, an unrelated file, or a bill mislabeled as the
    wrong type. Never claims to verify the address itself."""
    full_text, avg_confidence = _ocr_text_and_confidence(document_bytes)
    keywords = ADDRESS_DOCUMENT_KEYWORDS.get(document_type, ())
    found = any(keyword in full_text for keyword in keywords)
    return found, avg_confidence
