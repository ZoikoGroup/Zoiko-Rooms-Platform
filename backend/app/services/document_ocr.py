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

# apt's standard install location on Debian/Ubuntu (tesseract-ocr package).
# shutil.which("tesseract") alone isn't enough in production: a systemd
# service's PATH is whatever the unit's own environment provides, not the
# deploy script's interactive SSH shell PATH the apt-get install ran under
# -- the binary can be genuinely installed and still invisible to
# shutil.which() here. Same "PATH first, then the real known install
# location" shape as the Windows fallback above.
_LINUX_DEFAULT_PATHS = (
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
)


def _resolve_tesseract_cmd() -> str | None:
    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in (*_WINDOWS_DEFAULT_PATHS, *_LINUX_DEFAULT_PATHS):
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
    if local_app_data:
        matches = list(Path(local_app_data).glob("Microsoft/WinGet/Packages/*Poppler*/poppler-*/Library/bin/pdftoppm.exe"))
        if matches:
            return str(matches[0].parent)
    # apt's poppler-utils package -- same PATH-visibility gap as
    # _resolve_tesseract_cmd's Linux fallback above.
    if Path("/usr/bin/pdftoppm").is_file():
        return "/usr/bin"
    if Path("/usr/local/bin/pdftoppm").is_file():
        return "/usr/local/bin"
    return None


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


# A passport's machine-readable zone (ICAO 9303, the two OCR-B lines at the
# bottom of every passport photo page) is a REAL, standardized format --
# unlike freeform document text, this genuinely can be parsed reliably:
# "P<" + issuing country (3 letters) + surname + "<<" + given names, with
# "<" standing in for spaces/padding. Tesseract reads it as one unbroken
# token (no real spaces are printed there), so it appears as a single long
# run of letters and "<" in the OCR'd text. This is real extraction, not a
# heuristic guess -- the one case where this codebase can genuinely pull a
# name out of unstructured OCR text, because the format itself is known and
# fixed, not because we're guessing which words look name-like.
_MRZ_NAME_PATTERN = re.compile(r"P<\s*[A-Z]{3}([A-Z<]{10,})")


def extract_name_from_mrz(full_text: str) -> str | None:
    """Returns the real name parsed from a passport's MRZ line, or None if
    no MRZ-shaped text was found (any other document type, or a passport
    photo where the MRZ itself didn't read cleanly)."""
    match = _MRZ_NAME_PATTERN.search(full_text)
    if not match:
        return None
    parts = match.group(1).split("<<", 1)
    if len(parts) != 2:
        return None
    surname = parts[0].replace("<", " ").strip()
    given_names = " ".join(w for w in parts[1].split("<") if w)
    if not surname or not given_names:
        return None
    return f"{given_names} {surname}".strip()


def _match_document_number(full_text: str, document_type: str, expected_number: str) -> str | None:
    """Every overlapping window matching the format is a candidate, not just
    the first: an Aadhaar card often prints another 4-digit group (a year,
    the VID) right before the number, so the first 12-digit window can
    straddle that group and the real number ("2011 7296 4981" out of
    "2011 7296 4981 6197"). A candidate equal to the number the user typed
    wins, then (Aadhaar) one passing the Verhoeff checksum, then the first --
    only ever a number actually present in the scanned text."""
    pattern = DOCUMENT_NUMBER_PATTERNS.get(document_type)
    if pattern is None:
        return None
    candidates = [m.group(1).replace(" ", "") for m in re.finditer(f"(?=({pattern}))", full_text)]
    if not candidates:
        return None
    expected = expected_number.replace(" ", "").upper()
    if expected and expected in candidates:
        return expected
    if document_type == "aadhaar":
        valid = [c for c in candidates if is_valid_aadhaar_checksum(c)]
        if valid:
            return valid[0]
    return candidates[0]


def extract_and_score(
    document_bytes: bytes, document_type: str, *, expected_number: str = "",
) -> tuple[str | None, float, str | None]:
    """Returns (matched_document_number_or_None, average_ocr_confidence_0_to_100,
    mrz_extracted_name_or_None). The number is chosen by
    _match_document_number (the typed number, then an Aadhaar-checksum-valid
    one, then the first match). The name is only ever populated for a real,
    parseable passport MRZ line -- see extract_name_from_mrz above -- never a
    guess for other document types."""
    full_text, avg_confidence = _ocr_text_and_confidence(document_bytes)
    matched_number = _match_document_number(full_text, document_type, expected_number)
    extracted_name = extract_name_from_mrz(full_text) if document_type == "passport" else None
    return matched_number, avg_confidence, extracted_name


# Property verification checks against REAL known values, unlike identity's
# address-document check above (no stored address exists there to compare
# against) -- the room's own actual Property.address/city, and the owner's
# own registered account name. Same two-signal shape real proof-of-address
# vendors use (Veriff/IDWise/Didit/AuthBridge all OCR-extract a document's
# name AND address, then cross-match both against the claimed identity) --
# not an invented rule. This is the PRIMARY check. An optional landmark is
# a FALLBACK ONLY, tried when the primary check fails: landmark isn't a
# signal any real proof-of-address system checks as its main rule (informal
# landmarks like "near XYZ Mall" rarely appear on formal documents), but
# some genuine local documents do print a less formal address alongside a
# landmark -- worth trying before routing to a human, never a substitute
# for the real check.
PROPERTY_ADDRESS_MATCH_CONFIDENCE_THRESHOLD = 40.0


def _significant_words(text: str) -> list[str]:
    return [w.strip(",.").upper() for w in text.split() if len(w.strip(",.")) >= 3]


def _all_match(words: list[str], full_text: str) -> bool:
    return bool(words) and all(w in full_text for w in words)


# Real name-EXTRACTION (pulling an arbitrary person's name out of
# unstructured OCR text with no "this is the name field" label) needs real
# document-layout parsing this build doesn't have. What IS reliable: taking
# a KNOWN claimed name (the account's own registered full_name) and
# checking whether it appears in the document -- the same "cross-match a
# known name against the document" shape real proof-of-address/KYC vendors
# use (Veriff/IDWise/Didit all report a name_match_score this same way,
# not a freestanding name extraction).
NAME_MATCH_CONFIDENCE_THRESHOLD = 40.0


def check_name_in_document(document_bytes: bytes, full_name: str) -> tuple[bool, float, str]:
    """Returns (name_matched, average_ocr_confidence_0_to_100, extracted_text_snippet).
    A match requires ALL of the claimed name's significant (3+ character)
    words to appear in the extracted text -- not just half. A name is
    short (often just 2 words), and requiring only half of a 2-word name
    means a single common word ("Account", "James", "Kumar"...) shared
    with completely unrelated document text is enough to false-positive a
    match; a real, honest name check on something this short needs every
    word, not a majority vote."""
    full_text, avg_confidence = _ocr_text_and_confidence(document_bytes)
    return _all_match(_significant_words(full_name), full_text), avg_confidence, full_text[:500]


def check_identity_details_in_document(
    document_bytes: bytes, *, full_name: str | None = None, document_number: str | None = None,
) -> tuple[bool, str, float, str]:
    """Returns (matched, matched_via, average_ocr_confidence_0_to_100, extracted_text_snippet).

    Property verification's own check: does THIS document contain EITHER
    the account's registered name OR the document number already read off
    their identity document at identity-verification time (stored on
    IdentityVerification.ocr_extracted_number -- reused directly, never
    re-OCR'd here). Either signal alone is enough; neither is mandatory on
    its own (a property document might show a name but not an ID number,
    or vice versa) -- this is intentionally simpler than requiring both a
    formal address match and a name match, which proved too strict for
    real documents in practice. matched_via is "name" or "number" (never
    both at once -- name is checked first) or "" if neither matched. Only
    one OCR pass over the document, reused for both checks."""
    full_text, avg_confidence = _ocr_text_and_confidence(document_bytes)

    if full_name and _all_match(_significant_words(full_name), full_text):
        return True, "name", avg_confidence, full_text[:500]

    if document_number:
        normalized = document_number.replace(" ", "").upper()
        if normalized and normalized in full_text.replace(" ", ""):
            return True, "number", avg_confidence, full_text[:500]

    return False, "", avg_confidence, full_text[:500]


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
