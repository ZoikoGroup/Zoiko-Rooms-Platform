"""ZR-PAY-METHODS-GLOBAL-001 -- the one global payment-method taxonomy every
payment-recording flow (renter mark-paid, host record-receipt, deposit/
cancellation returns) draws from.

Two levels, never flattened into one list (Section 1's "most important
correction"):

- a short, stable, globally understandable **category** (Section 2/6.2 --
  BANK_TRANSFER, INSTANT_BANK_PAYMENT, ... OTHER), and
- only where it adds reconciliation value, a conditional **subtype** -- the
  payment network / rail / provider (Section 3), e.g. INSTANT_BANK_PAYMENT ->
  UPI. Regional rails like UPI never appear at the top level.

The list a user sees is resolved server-side from the rental's country and
context (Section 6/7): only the categories enabled for that context, local
methods ordered first, subtypes filtered to the ones configured for that
country. The frontend renders whatever this module returns and submits
canonical codes; resolve_selection is the write-time authority (Section 10,
acceptance criteria 26/27).

Versioned and effective-dated (Section 6, criteria 23/24): an entry is
retired by setting effective_to, never deleted, so codes on historical
records always still resolve to a label; each record also snapshots the
label and CATALOG_VERSION in force when it was written.

Same posture as services/bank_field_schemas.py: banking-rail facts, not
legal content, and fails OPEN for an unknown country (every global category
and global rail is still offered) -- it must never stop someone recording a
payment just because their country has no tuned configuration yet.

Deliberately excluded (Section 7.2): crypto/stablecoins, gift cards/vouchers,
BNPL/consumer credit, barter/in-kind and anonymous methods. None has a
category here, so none can be recorded."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from fastapi import HTTPException, status

CATALOG_VERSION = "2026.10.1"
_LAUNCH = date(2026, 10, 15)

REFERENCE_POLICIES = ("OPTIONAL", "RECOMMENDED", "REQUIRED")
EVIDENCE_POLICIES = ("OPTIONAL", "CONDITIONAL", "REQUIRED")
# rent_record / deposit_record: money the renter paid the payee.
# return_record: money the payee sent back to the renter.
PAYMENT_METHOD_CONTEXTS = ("rent_record", "deposit_record", "return_record")

PAYMENT_METHOD_CATEGORIES = (
    "BANK_TRANSFER", "INSTANT_BANK_PAYMENT", "BANK_DEBIT", "STANDING_ORDER", "PAYMENT_CARD",
    "DIGITAL_WALLET_P2P", "MOBILE_MONEY", "REMITTANCE_SERVICE", "CASH", "CHEQUE_CHECK", "BANK_DRAFT",
    "MONEY_ORDER", "OTHER",
)

# Values written before this taxonomy existed, mapped onto it. Still accepted
# on input so older clients keep working, and used by the data migration.
LEGACY_METHOD_MAP: dict[str, tuple[str, str]] = {
    "UPI": ("INSTANT_BANK_PAYMENT", "UPI"),
    "CARD": ("PAYMENT_CARD", ""),
}

METHOD_DESCRIPTION_MAX_LENGTH = 200
REFERENCE_MAX_LENGTH = 255

_EEA = (
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU",
    "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "IS", "LI", "NO", "CH", "MC", "SM", "AD", "VA",
)
_MPESA = ("KE", "TZ", "CD", "MZ", "LS", "ET")
_MTN_MOMO = ("GH", "UG", "RW", "CM", "CI", "ZM", "BJ", "GN", "LR", "SZ", "CG", "ZA")
_AIRTEL_MONEY = ("KE", "UG", "TZ", "RW", "ZM", "MW", "CD", "MG", "NE", "TD", "GA", "CG", "SC")
_ORANGE_MONEY = ("CI", "SN", "CM", "ML", "BF", "GN", "MG", "CD", "SL", "LR", "BW", "CF")
_MOBILE_MONEY_MARKETS = tuple(sorted(set(_MPESA + _MTN_MOMO + _AIRTEL_MONEY + _ORANGE_MONEY)))

_CARD_REFERENCE_HELPER = (
    "Use the merchant or payment reference from the receipt. Never enter a full card number or the CVV -- "
    "the last 4 digits are enough."
)


@dataclass(frozen=True)
class PaymentMethodSubtype:
    code: str
    label: str
    # None = offered everywhere; otherwise the ISO 3166-1 alpha-2 countries
    # where this rail/provider is configured.
    countries: tuple[str, ...] | None = None
    reference_label: str = ""
    reference_helper: str = ""
    reference_policy: str = "OPTIONAL"
    effective_from: date = _LAUNCH
    effective_to: date | None = None

    def available(self, country: str, as_of: date) -> bool:
        if not _effective(self.effective_from, self.effective_to, as_of):
            return False
        # Unknown country: fail open and offer everything.
        return self.countries is None or not country or country in self.countries


@dataclass(frozen=True)
class PaymentMethodCategory:
    code: str
    label: str
    reference_label: str
    reference_helper: str
    reference_policy: str = "OPTIONAL"
    evidence_policy: str = "OPTIONAL"
    evidence_helper: str = ""
    # The second control's label (Section 3); "" = this category has no subtype.
    subtype_label: str = ""
    subtypes: tuple[PaymentMethodSubtype, ...] = ()
    # True where the rail/provider *is* the reconciliation evidence (which
    # instant rail, wallet, mobile-money service or remittance provider);
    # elsewhere the second control is offered but may be left blank.
    subtype_required: bool = False
    contexts: tuple[str, ...] = PAYMENT_METHOD_CONTEXTS
    countries: tuple[str, ...] | None = None
    requires_description: bool = False
    effective_from: date = _LAUNCH
    effective_to: date | None = None

    def available(self, country: str, context: str, as_of: date) -> bool:
        if not _effective(self.effective_from, self.effective_to, as_of) or context not in self.contexts:
            return False
        return self.countries is None or not country or country in self.countries


def _effective(start: date, end: date | None, as_of: date) -> bool:
    return start <= as_of and (end is None or as_of <= end)


def _other(reference_label: str = "", reference_helper: str = "") -> PaymentMethodSubtype:
    return PaymentMethodSubtype("OTHER", "Other", reference_label=reference_label, reference_helper=reference_helper)


# Section 2's exact first-level taxonomy, in its canonical order (Section 3/5
# supply each category's subtypes and reference semantics).
CATALOG: tuple[PaymentMethodCategory, ...] = (
    PaymentMethodCategory(
        "BANK_TRANSFER", "Bank Transfer",
        reference_label="Payment reference / bank transfer reference",
        reference_helper="The reference or transaction number on the bank statement or transfer confirmation.",
        subtype_label="Transfer type / rail",
        subtypes=(
            PaymentMethodSubtype("DOMESTIC_BANK_TRANSFER", "Domestic bank transfer"),
            PaymentMethodSubtype(
                "INTERNATIONAL_WIRE", "International wire",
                reference_label="Wire / SWIFT reference",
                reference_helper="The SWIFT or wire reference (for example the UETR) from the bank's confirmation.",
                reference_policy="RECOMMENDED",
            ),
            PaymentMethodSubtype(
                "FASTER_PAYMENTS", "Faster Payments", ("GB",),
                reference_label="Faster Payments reference", reference_policy="RECOMMENDED",
            ),
            PaymentMethodSubtype("BACS_CREDIT", "BACS Credit", ("GB",), reference_label="BACS reference"),
            PaymentMethodSubtype("CHAPS", "CHAPS", ("GB",), reference_label="CHAPS reference", reference_policy="RECOMMENDED"),
            PaymentMethodSubtype(
                "SEPA_CREDIT_TRANSFER", "SEPA Credit Transfer", _EEA,
                reference_label="End-to-end ID / bank reference", reference_policy="RECOMMENDED",
            ),
            PaymentMethodSubtype(
                "ACH", "ACH", ("US",),
                reference_label="ACH trace / reference number", reference_policy="RECOMMENDED",
            ),
            PaymentMethodSubtype("DOMESTIC_WIRE", "Domestic wire", ("US",), reference_label="Wire reference number"),
            PaymentMethodSubtype(
                "NEFT", "NEFT", ("IN",), reference_label="NEFT reference / UTR", reference_policy="RECOMMENDED",
            ),
            PaymentMethodSubtype(
                "RTGS", "RTGS", ("IN",), reference_label="RTGS reference / UTR", reference_policy="RECOMMENDED",
            ),
            PaymentMethodSubtype("BECS_TRANSFER", "BECS / local transfer", ("AU",), reference_label="Bank reference"),
            _other(),
        ),
    ),
    PaymentMethodCategory(
        "INSTANT_BANK_PAYMENT", "Instant Bank Payment / Pay by Bank",
        reference_label="Payment / transaction reference",
        reference_helper="The transaction reference shown in the banking app after the payment went through.",
        reference_policy="RECOMMENDED",
        subtype_label="Payment network / rail",
        subtype_required=True,
        subtypes=(
            PaymentMethodSubtype(
                "UPI", "UPI", ("IN",),
                reference_label="UPI transaction ID / UTR",
                reference_helper="Enter the UPI transaction ID / UTR if available.",
                reference_policy="RECOMMENDED",
            ),
            PaymentMethodSubtype(
                "IMPS", "IMPS", ("IN",), reference_label="IMPS reference / UTR", reference_policy="RECOMMENDED",
            ),
            PaymentMethodSubtype(
                "PIX", "Pix", ("BR",),
                reference_label="Pix transaction ID / E2E ID",
                reference_helper="The Pix end-to-end ID (E2E ID) from the payment receipt.",
                reference_policy="RECOMMENDED",
            ),
            PaymentMethodSubtype(
                "SEPA_INSTANT", "SEPA Instant", _EEA,
                reference_label="End-to-end ID / bank reference", reference_policy="RECOMMENDED",
            ),
            PaymentMethodSubtype("OPEN_BANKING_PAY_BY_BANK", "Open Banking / Pay by Bank", ("GB",)),
            PaymentMethodSubtype("FEDNOW", "FedNow", ("US",)),
            PaymentMethodSubtype("RTP", "RTP", ("US",)),
            PaymentMethodSubtype(
                "INTERAC_E_TRANSFER", "Interac e-Transfer", ("CA",), reference_label="Interac reference number",
            ),
            PaymentMethodSubtype("PAYNOW", "PayNow", ("SG",)),
            PaymentMethodSubtype("DUITNOW", "DuitNow", ("MY",)),
            PaymentMethodSubtype("PROMPTPAY", "PromptPay", ("TH",)),
            PaymentMethodSubtype("HK_FPS", "Hong Kong FPS", ("HK",), reference_label="FPS reference"),
            PaymentMethodSubtype("BI_FAST", "BI-FAST", ("ID",)),
            PaymentMethodSubtype("INSTAPAY", "InstaPay", ("PH",)),
            PaymentMethodSubtype("NPP_PAYID_OSKO", "NPP / PayID / Osko", ("AU",)),
            _other(),
        ),
    ),
    PaymentMethodCategory(
        "BANK_DEBIT", "Direct Debit / Bank Debit",
        reference_label="Mandate / debit reference",
        reference_helper="The debit or mandate reference on the bank statement.",
        subtype_label="Debit scheme",
        subtypes=(
            PaymentMethodSubtype("BACS_DIRECT_DEBIT", "Bacs Direct Debit", ("GB",)),
            PaymentMethodSubtype(
                "SEPA_DIRECT_DEBIT", "SEPA Direct Debit", _EEA, reference_label="Mandate reference / end-to-end ID",
            ),
            PaymentMethodSubtype("ACH_DEBIT", "ACH Debit", ("US",), reference_label="ACH trace / reference number"),
            PaymentMethodSubtype("BECS_DIRECT_DEBIT", "BECS Direct Debit", ("AU",)),
            PaymentMethodSubtype("LOCAL_BANK_DEBIT", "Local bank debit"),
            _other(),
        ),
        # A debit is pulled by the payee; it can't be how money is sent back.
        contexts=("rent_record", "deposit_record"),
    ),
    PaymentMethodCategory(
        "STANDING_ORDER", "Standing Order / Scheduled Bank Transfer",
        reference_label="Payment reference / bank transfer reference",
        reference_helper="The standing-order reference on the bank statement.",
        # A recurring transfer only makes sense for rent.
        contexts=("rent_record",),
    ),
    PaymentMethodCategory(
        "PAYMENT_CARD", "Payment Card",
        reference_label="Merchant / payment transaction reference",
        reference_helper=_CARD_REFERENCE_HELPER,
        subtype_label="Card type",
        subtypes=(
            PaymentMethodSubtype("DEBIT_CARD", "Debit card"),
            PaymentMethodSubtype("CREDIT_CARD", "Credit card"),
            PaymentMethodSubtype("PREPAID_CARD", "Prepaid card"),
        ),
    ),
    PaymentMethodCategory(
        "DIGITAL_WALLET_P2P", "Digital Wallet / P2P Payment App",
        reference_label="Wallet transaction ID / reference",
        reference_helper="The transaction ID shown in the app's payment history.",
        reference_policy="RECOMMENDED",
        subtype_label="Wallet / app",
        subtype_required=True,
        subtypes=(
            PaymentMethodSubtype("PAYPAL", "PayPal"),
            PaymentMethodSubtype("VENMO", "Venmo", ("US",)),
            PaymentMethodSubtype("CASH_APP", "Cash App", ("US", "GB")),
            PaymentMethodSubtype("ZELLE", "Zelle", ("US",)),
            PaymentMethodSubtype("APPLE_PAY", "Apple Pay"),
            PaymentMethodSubtype("GOOGLE_PAY", "Google Pay"),
            PaymentMethodSubtype("PAYTM", "Paytm", ("IN",)),
            PaymentMethodSubtype("ALIPAY", "Alipay"),
            PaymentMethodSubtype("WECHAT_PAY", "WeChat Pay"),
            PaymentMethodSubtype("GCASH", "GCash", ("PH",)),
            _other(),
        ),
    ),
    PaymentMethodCategory(
        "MOBILE_MONEY", "Mobile Money",
        reference_label="Mobile money transaction ID",
        reference_helper="The transaction ID from the confirmation SMS or app.",
        reference_policy="RECOMMENDED",
        subtype_label="Mobile money service",
        subtype_required=True,
        subtypes=(
            PaymentMethodSubtype("MPESA", "M-Pesa", _MPESA),
            PaymentMethodSubtype("MTN_MOMO", "MTN Mobile Money / MoMo", _MTN_MOMO),
            PaymentMethodSubtype("AIRTEL_MONEY", "Airtel Money", _AIRTEL_MONEY),
            PaymentMethodSubtype("ORANGE_MONEY", "Orange Money", _ORANGE_MONEY),
            _other(),
        ),
        countries=_MOBILE_MONEY_MARKETS,
    ),
    PaymentMethodCategory(
        "REMITTANCE_SERVICE", "Remittance / Money Transfer Service",
        reference_label="Transfer / tracking number",
        reference_helper="The provider's transfer or tracking number (for example a Western Union MTCN).",
        reference_policy="RECOMMENDED",
        subtype_label="Transfer provider",
        subtype_required=True,
        subtypes=(
            PaymentMethodSubtype("WISE", "Wise"),
            PaymentMethodSubtype("WESTERN_UNION", "Western Union", reference_label="MTCN / tracking number"),
            PaymentMethodSubtype("MONEYGRAM", "MoneyGram", reference_label="Reference number"),
            PaymentMethodSubtype("REMITLY", "Remitly"),
            _other(),
        ),
    ),
    PaymentMethodCategory(
        "CASH", "Cash",
        reference_label="Receipt / reference number",
        reference_helper="Leave this blank if there's no receipt number -- don't make one up.",
        evidence_helper="A photo of a signed receipt is strongly encouraged for cash.",
        subtype_label="Cash type",
        subtypes=(
            PaymentMethodSubtype(
                "CASH_HANDED_DIRECTLY", "Cash handed directly",
                reference_label="Receipt / reference number",
                reference_helper="Leave this blank if there's no receipt number -- don't make one up.",
            ),
            PaymentMethodSubtype(
                "CASH_BANK_DEPOSIT", "Cash deposit to recipient bank account",
                reference_label="Deposit slip / bank reference",
                reference_helper="The reference printed on the deposit slip.",
                reference_policy="RECOMMENDED",
            ),
        ),
    ),
    PaymentMethodCategory(
        "CHEQUE_CHECK", "Cheque / Check",
        reference_label="Cheque / check number", reference_helper="The number printed on the cheque.",
        reference_policy="RECOMMENDED",
        subtype_label="Cheque type",
        subtypes=(
            PaymentMethodSubtype("PERSONAL", "Personal"),
            PaymentMethodSubtype("BUSINESS", "Business"),
            _other(),
        ),
    ),
    PaymentMethodCategory(
        "BANK_DRAFT", "Bank Draft / Cashier's Check",
        reference_label="Draft / cashier's check number", reference_helper="The number printed on the draft.",
        reference_policy="RECOMMENDED",
    ),
    PaymentMethodCategory(
        "MONEY_ORDER", "Money Order",
        reference_label="Money order number", reference_helper="The serial number printed on the money order.",
        reference_policy="RECOMMENDED",
    ),
    PaymentMethodCategory(
        "OTHER", "Other",
        reference_label="Payment reference (if available)",
        reference_helper="Any reference that would help match this payment.",
        requires_description=True,
    ),
)

_BY_CODE: dict[str, PaymentMethodCategory] = {c.code: c for c in CATALOG}
assert tuple(_BY_CODE) == PAYMENT_METHOD_CATEGORIES

# Plain-language guidance shown beside the form for each category.
CATEGORY_GUIDANCE: dict[str, str] = {
    "BANK_TRANSFER": "A domestic, international or local transfer from your bank account (e.g. ACH, SEPA, Faster Payments).",
    "INSTANT_BANK_PAYMENT": "A real-time bank payment (e.g. UPI, Pix, PayNow, DuitNow, RTP, FedNow).",
    "BANK_DEBIT": "A recurring payment you authorised the recipient to take from your bank account.",
    "STANDING_ORDER": "A recurring bank transfer you set up yourself.",
    "PAYMENT_CARD": "A debit, credit or prepaid card used through an outside merchant or provider.",
    "DIGITAL_WALLET_P2P": "A wallet or peer-to-peer app (e.g. PayPal, Apple Pay, Google Pay, Zelle).",
    "MOBILE_MONEY": "A mobile-money transfer (e.g. M-Pesa, MTN Mobile Money, Airtel Money).",
    "REMITTANCE_SERVICE": "A money-transfer provider (e.g. Wise, Western Union, MoneyGram, Remitly).",
    "CASH": "Cash handed over directly, or deposited into the recipient's bank account.",
    "CHEQUE_CHECK": "A personal or business cheque.",
    "BANK_DRAFT": "A bank-issued draft or cashier's check.",
    "MONEY_ORDER": "A postal, bank or other authorised money order.",
    "OTHER": "Any other method not listed above -- you'll be asked to describe it.",
}

_COUNTRY_NAMES = {
    "GB": "UK", "US": "United States", "IN": "India", "CA": "Canada", "BR": "Brazil", "AU": "Australia",
    "SG": "Singapore", "MY": "Malaysia", "TH": "Thailand", "HK": "Hong Kong", "ID": "Indonesia", "PH": "Philippines",
}


def region_tag(subtype: PaymentMethodSubtype) -> str:
    """A short "where this rail is used" tag ("UK", "EU/EEA", ...); "" for
    a rail offered everywhere or across many unrelated markets."""
    if subtype.countries is None:
        return ""
    if subtype.countries == _EEA:
        return "EU/EEA"
    if len(subtype.countries) == 1:
        return _COUNTRY_NAMES.get(subtype.countries[0], subtype.countries[0])
    return ""


# Section 7 rule 3: common local methods first, canonical codes unchanged.
# Categories not named keep their canonical order after these.
_LOCAL_FIRST: dict[str, tuple[str, ...]] = {
    "GB": ("BANK_TRANSFER", "STANDING_ORDER", "BANK_DEBIT", "INSTANT_BANK_PAYMENT"),
    "IN": ("INSTANT_BANK_PAYMENT", "BANK_TRANSFER", "CASH", "DIGITAL_WALLET_P2P"),
    "US": ("BANK_TRANSFER", "DIGITAL_WALLET_P2P", "CHEQUE_CHECK", "INSTANT_BANK_PAYMENT"),
    "CA": ("INSTANT_BANK_PAYMENT", "BANK_TRANSFER", "CHEQUE_CHECK"),
    "BR": ("INSTANT_BANK_PAYMENT", "BANK_TRANSFER"),
    "AU": ("BANK_TRANSFER", "INSTANT_BANK_PAYMENT", "BANK_DEBIT"),
    "SG": ("INSTANT_BANK_PAYMENT", "BANK_TRANSFER"),
    "MY": ("INSTANT_BANK_PAYMENT", "BANK_TRANSFER"),
    "TH": ("INSTANT_BANK_PAYMENT", "BANK_TRANSFER"),
    "HK": ("INSTANT_BANK_PAYMENT", "BANK_TRANSFER"),
    **{country: ("BANK_TRANSFER", "INSTANT_BANK_PAYMENT", "BANK_DEBIT") for country in _EEA},
    **{country: ("MOBILE_MONEY", "BANK_TRANSFER", "CASH") for country in _MOBILE_MONEY_MARKETS},
}

# Property jurisdiction codes that aren't already an ISO country code.
_JURISDICTION_COUNTRY = {
    "ENGLAND": "GB", "WALES": "GB", "SCOTLAND": "GB", "NORTHERN IRELAND": "GB", "UK": "GB",
    "FRANCE": "FR", "INDIA": "IN", "GERMANY": "DE", "AUSTRALIA": "AU", "USA": "US",
}


def country_for(country_code: str | None, jurisdiction_code: str | None = None) -> str:
    """The ISO alpha-2 country a rental's methods resolve from: the
    property's own country_code, else one derived from its jurisdiction
    code. "" when neither says -- which fails open to the global list."""
    code = (country_code or "").strip().upper()
    if len(code) == 2 and code.isalpha():
        return code
    jurisdiction = (jurisdiction_code or "").strip().upper()
    if jurisdiction in _JURISDICTION_COUNTRY:
        return _JURISDICTION_COUNTRY[jurisdiction]
    return jurisdiction if len(jurisdiction) == 2 and jurisdiction.isalpha() else ""


def context_for_obligation_type(obligation_type: str) -> str:
    return "deposit_record" if obligation_type == "DEPOSIT" else "rent_record"


def _check_context(context: str) -> None:
    if context not in PAYMENT_METHOD_CONTEXTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"context must be one of {PAYMENT_METHOD_CONTEXTS}")


def available_subtypes(category: PaymentMethodCategory, country: str, as_of: date) -> list[PaymentMethodSubtype]:
    """Local rails first, then the ones offered everywhere, "Other" last."""
    subtypes = [s for s in category.subtypes if s.available(country, as_of)]
    return sorted(subtypes, key=lambda s: (s.code == "OTHER", s.countries is None))


def available_categories(country: str, context: str, *, as_of: date | None = None) -> list[PaymentMethodCategory]:
    _check_context(context)
    as_of = as_of or date.today()
    categories = [c for c in CATALOG if c.available(country, context, as_of)]
    # A category whose every rail is regional to somewhere else has nothing to offer here.
    categories = [c for c in categories if not c.subtypes or available_subtypes(c, country, as_of)]
    first = _LOCAL_FIRST.get(country, ())
    return sorted(categories, key=lambda c: (c.code not in first, first.index(c.code) if c.code in first else 0))


def get_category(code: str) -> PaymentMethodCategory | None:
    return _BY_CODE.get(code)


def _find_subtype(category: PaymentMethodCategory, code: str) -> PaymentMethodSubtype | None:
    return next((s for s in category.subtypes if s.code == code), None)


def field_rules(category: PaymentMethodCategory, subtype: PaymentMethodSubtype | None) -> dict:
    """Section 5: the reference label/helper/policy for this exact choice --
    a subtype's own wording wins over its category's."""
    return {
        "reference_label": (subtype.reference_label if subtype and subtype.reference_label else category.reference_label),
        "reference_helper": (subtype.reference_helper if subtype and subtype.reference_helper else category.reference_helper),
        "reference_policy": (subtype.reference_policy if subtype and subtype.reference_policy != "OPTIONAL"
                             else category.reference_policy),
        "evidence_policy": category.evidence_policy,
        "evidence_helper": category.evidence_helper,
        "requires_description": category.requires_description or (subtype is not None and subtype.code == "OTHER"),
    }


def method_label(category_code: str, subtype_code: str = "", description: str = "") -> str:
    """The human label for a stored method -- resolves retired codes too,
    since nothing is ever removed from CATALOG."""
    if category_code in LEGACY_METHOD_MAP and not subtype_code:
        category_code, subtype_code = LEGACY_METHOD_MAP[category_code]
    category = _BY_CODE.get(category_code)
    if category is None:
        return category_code.replace("_", " ").title()
    parts = [category.label]
    subtype = _find_subtype(category, subtype_code) if subtype_code else None
    if subtype and subtype.code != "OTHER":
        parts.append(subtype.label)
    if description:
        parts.append(description)
    return " · ".join(parts)


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


_PAN_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def looks_like_card_number(value: str) -> bool:
    """Section 8 / criterion 21: never store a full PAN. A 13-19 digit run
    that passes the Luhn check is treated as one."""
    for match in _PAN_CANDIDATE.finditer(value or ""):
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            return True
    return False


@dataclass(frozen=True)
class MethodSelection:
    category: str
    subtype: str
    description: str
    reference: str
    label: str
    config_version: str = field(default=CATALOG_VERSION)


def resolve_selection(
    *, category: str, subtype: str = "", description: str = "", reference: str = "",
    country: str = "", context: str, as_of: date | None = None,
) -> MethodSelection:
    """Write-time validation for a new payment record or return (Section 10
    "Method rail validation" / "Reference requirement"). Returns the
    normalized selection plus the label snapshot to store on the record."""
    _check_context(context)
    as_of = as_of or date.today()
    category = (category or "").strip().upper()
    subtype = (subtype or "").strip().upper()
    description = " ".join((description or "").split())
    reference = (reference or "").strip()
    if category in LEGACY_METHOD_MAP:
        legacy_category, legacy_subtype = LEGACY_METHOD_MAP[category]
        category, subtype = legacy_category, subtype or legacy_subtype

    entry = _BY_CODE.get(category)
    if entry is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a payment method from the list")
    if not entry.available(country, context, as_of):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{entry.label} isn't available for this payment")

    options = available_subtypes(entry, country, as_of)
    chosen: PaymentMethodSubtype | None = None
    if subtype:
        chosen = next((s for s in options if s.code == subtype), None)
        if chosen is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"That {entry.subtype_label.lower() or 'option'} isn't available for {entry.label} here",
            )
    elif options and entry.subtype_required:
        # Section 7 rule 5: a single valid option is preselected, not asked for.
        if len(options) == 1:
            chosen = options[0]
        else:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Choose the {entry.subtype_label.lower()}")

    rules = field_rules(entry, chosen)
    if rules["requires_description"]:
        if not description:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Describe how the payment was made")
        if len(description) > METHOD_DESCRIPTION_MAX_LENGTH:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Keep the method description under {METHOD_DESCRIPTION_MAX_LENGTH} characters",
            )
    else:
        description = ""

    if len(reference) > REFERENCE_MAX_LENGTH:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Keep the reference under {REFERENCE_MAX_LENGTH} characters")
    if rules["reference_policy"] == "REQUIRED" and not reference:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Enter the {rules['reference_label'].lower()}")
    if looks_like_card_number(reference) or looks_like_card_number(description):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That looks like a full card number -- never enter card numbers. Use the merchant reference "
            "or the last 4 digits instead.",
        )

    subtype_code = chosen.code if chosen else ""
    return MethodSelection(
        category=category, subtype=subtype_code, description=description, reference=reference,
        label=method_label(category, subtype_code, description),
    )


def normalize_stored_method(category: str, subtype: str = "") -> tuple[str, str]:
    """For corrections: map a legacy value onto the taxonomy and check the
    category/subtype pair exists at all (no country/context filter -- a
    correction fixes a historical record, it isn't a new payment)."""
    category = (category or "").strip().upper()
    subtype = (subtype or "").strip().upper()
    if category in LEGACY_METHOD_MAP:
        category, legacy_subtype = LEGACY_METHOD_MAP[category]
        subtype = subtype or legacy_subtype
    entry = _BY_CODE.get(category)
    if entry is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Payment method must be one of {PAYMENT_METHOD_CATEGORIES}")
    if subtype and _find_subtype(entry, subtype) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{subtype} isn't a {entry.label} option")
    return category, subtype


def catalog_payload(country: str, context: str, *, as_of: date | None = None) -> dict:
    """What GET /payment-methods returns: everything the form needs to
    render both dropdowns and the reference field for this rental."""
    as_of = as_of or date.today()
    categories = []
    for c in available_categories(country, context, as_of=as_of):
        subtypes = available_subtypes(c, country, as_of)
        categories.append({
            "code": c.code,
            "label": c.label,
            "description": CATEGORY_GUIDANCE.get(c.code, ""),
            "subtype_label": c.subtype_label if subtypes else "",
            "subtype_required": c.subtype_required and bool(subtypes),
            "subtypes": [
                {"code": s.code, "label": s.label, "region": region_tag(s), **field_rules(c, s)} for s in subtypes
            ],
            **field_rules(c, None),
        })
    return {"version": CATALOG_VERSION, "country_code": country, "context": context, "categories": categories}
