"""ZR-PAY-LINK-003 Wireframe D: "Account details [ secure jurisdiction-
specific fields ]" -- per-country structured bank-detail field sets (UK
sort code + account number, EU IBAN, US routing + account number), same
shape and posture as app/services/bank_identifiers.py's own per-jurisdiction
format registry: public banking-standard technical fact (like a postal
code's shape), not legal/regulatory content, so adding a new country here
doesn't need counsel review the way a MarketPolicyPack would.

Deliberately fails OPEN, not closed, for an unrecognized country --
resolve_bank_field_schema always returns a usable schema (the generic
fallback) rather than raising. This is a UX/data-quality aid, not a legal
gate like the Listing Fee/MarketPolicyPack checks; it must never brick a
host's ability to submit payment instructions just because nobody's added
their country's field set yet."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class BankField:
    key: str
    label: str
    pattern: re.Pattern[str]
    hint: str = ""


@dataclass(frozen=True)
class BankFieldSchema:
    fields: tuple[BankField, ...]
    # Which field's value backs account_identifier_last4/masked display --
    # always the one a tenant would recognize as "the account number".
    primary_field_key: str


# UK sort code: 6 digits, conventionally written in 2-2-2 groups either bare
# or dash-separated (e.g. "123456" or "12-34-56") -- same regex as
# bank_identifiers.py's own UK sort-code format.
_UK_SORT_CODE = re.compile(r"^\d{2}-?\d{2}-?\d{2}$")
_UK_ACCOUNT_NUMBER = re.compile(r"^\d{8}$")
_US_ROUTING_NUMBER = re.compile(r"^\d{9}$")
_US_ACCOUNT_NUMBER = re.compile(r"^\d{4,17}$")
# IBAN: 2-letter country code + 2 check digits + up to 30 alphanumeric BBAN
# characters -- the general ISO 13616 shape, not a per-country checksum
# validator (that's a heavier lift this build doesn't need for a UX-level
# field-shape check).
_IBAN = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{1,30}$")
# India: IFSC is 4 letters, a literal 0, then 6 letters/digits; account
# numbers run 9-18 digits depending on the bank.
_IN_IFSC = re.compile(r"^[A-Za-z]{4}0[A-Za-z0-9]{6}$")
_IN_ACCOUNT_NUMBER = re.compile(r"^\d{9,18}$")
# A UPI ID (virtual payment address): handle@provider, e.g. name@okhdfcbank.
_UPI_ID = re.compile(r"^[A-Za-z0-9.\-_]{2,256}@[A-Za-z][A-Za-z0-9]{1,63}$")
# A rail alias (PayID, Pix key, PayNow/DuitNow/PromptPay ID, Interac email,
# ...) or a wallet / mobile-money handle (email, phone or username).
_PAYMENT_ALIAS = re.compile(r"^.{3,128}$")
_GENERIC_IDENTIFIER = re.compile(r"^.{4,64}$")

_GB_SCHEMA = BankFieldSchema(
    fields=(
        BankField("sort_code", "Sort code", _UK_SORT_CODE, "6 digits, e.g. 12-34-56"),
        BankField("account_number", "Account number", _UK_ACCOUNT_NUMBER, "8 digits"),
    ),
    primary_field_key="account_number",
)
_US_SCHEMA = BankFieldSchema(
    fields=(
        BankField("routing_number", "Routing number", _US_ROUTING_NUMBER, "9 digits"),
        BankField("account_number", "Account number", _US_ACCOUNT_NUMBER, "4-17 digits"),
    ),
    primary_field_key="account_number",
)
_IBAN_SCHEMA = BankFieldSchema(
    fields=(BankField("iban", "IBAN", _IBAN, "e.g. DE89370400440532013000"),),
    primary_field_key="iban",
)
_IN_SCHEMA = BankFieldSchema(
    fields=(
        BankField("ifsc", "IFSC", _IN_IFSC, "11 characters, e.g. HDFC0001234"),
        BankField("account_number", "Account number", _IN_ACCOUNT_NUMBER, "9-18 digits"),
    ),
    primary_field_key="account_number",
)
UPI_SCHEMA = BankFieldSchema(
    fields=(BankField("upi_id", "UPI ID", _UPI_ID, "e.g. name@okhdfcbank"),),
    primary_field_key="upi_id",
)
INSTANT_ALIAS_SCHEMA = BankFieldSchema(
    fields=(BankField(
        "payment_alias", "Payment ID / alias", _PAYMENT_ALIAS,
        "e.g. PayID, Pix key, PayNow ID, or the phone/email registered for instant payments",
    ),),
    primary_field_key="payment_alias",
)
WALLET_HANDLE_SCHEMA = BankFieldSchema(
    fields=(BankField(
        "account_handle", "Wallet / app account", _PAYMENT_ALIAS, "The email, phone number or username to pay",
    ),),
    primary_field_key="account_handle",
)
MOBILE_MONEY_SCHEMA = BankFieldSchema(
    fields=(BankField(
        "account_handle", "Mobile money number / account", _PAYMENT_ALIAS, "The number registered with the service",
    ),),
    primary_field_key="account_handle",
)
# Cash is paid in person -- there are no account details to collect; the
# host describes where/when in the instruction's additional_instructions.
CASH_SCHEMA = BankFieldSchema(fields=(), primary_field_key="")
# The pre-existing single-generic-field behavior, preserved as the fallback
# for any country not explicitly listed below.
_FALLBACK_SCHEMA = BankFieldSchema(
    fields=(BankField("account_identifier", "Account / payment ID", _GENERIC_IDENTIFIER, "At least 4 characters"),),
    primary_field_key="account_identifier",
)

# A small, explicitly extensible starting set -- same "add a country, don't
# invent a format" posture as bank_identifiers.py. Not exhaustive of every
# IBAN-using country; unlisted ones fall through to the generic schema
# below rather than being silently blocked.
_IBAN_COUNTRIES = frozenset({"DE", "FR", "ES", "IT", "NL", "IE", "PT", "BE"})

BANK_FIELD_SCHEMAS: dict[str, BankFieldSchema] = {
    "GB": _GB_SCHEMA,
    "US": _US_SCHEMA,
    "IN": _IN_SCHEMA,
    **{code: _IBAN_SCHEMA for code in _IBAN_COUNTRIES},
}


def resolve_bank_field_schema(country_code: str, method: str = "BANK_TRANSFER", subtype: str = "") -> BankFieldSchema:
    """Country-specific structured fields (sort code, IBAN, routing number)
    only ever make sense for BANK_TRANSFER -- a CASH/CARD/OTHER instruction
    has no bank routing details at all, forcing a UK sort code onto a cash
    payment would be nonsensical, not just unhelpful. Every non-bank-
    transfer method always gets the generic single-field fallback,
    regardless of country_code."""
    # ZR-PAY-METHODS-GLOBAL-001: methods are canonical categories; UPI is the
    # INSTANT_BANK_PAYMENT rail (the bare "UPI" value is the legacy form).
    if method == "UPI" or (method == "INSTANT_BANK_PAYMENT" and subtype == "UPI"):
        return UPI_SCHEMA
    if method == "INSTANT_BANK_PAYMENT":
        return INSTANT_ALIAS_SCHEMA
    if method == "DIGITAL_WALLET_P2P":
        return WALLET_HANDLE_SCHEMA
    if method == "MOBILE_MONEY":
        return MOBILE_MONEY_SCHEMA
    if method == "CASH":
        return CASH_SCHEMA
    if method != "BANK_TRANSFER":
        return _FALLBACK_SCHEMA
    return BANK_FIELD_SCHEMAS.get(country_code.strip().upper(), _FALLBACK_SCHEMA)


def validate_bank_details(
    country_code: str, bank_details: dict[str, str], method: str = "BANK_TRANSFER", subtype: str = "",
) -> None:
    """Raises ValueError (caller converts to HTTPException 400) on a missing
    or malformed field. Extra keys not in the schema are ignored rather
    than rejected -- lets the frontend submit a superset without this
    validator needing to change in lockstep."""
    schema = resolve_bank_field_schema(country_code, method, subtype)
    for field in schema.fields:
        value = (bank_details.get(field.key) or "").strip()
        if not value:
            raise ValueError(f"{field.label} is required")
        if not field.pattern.match(value):
            raise ValueError(f"{field.label} is not in a valid format ({field.hint})" if field.hint else f"{field.label} is not in a valid format")
