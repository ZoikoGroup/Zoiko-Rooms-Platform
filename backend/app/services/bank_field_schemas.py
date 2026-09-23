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
    **{code: _IBAN_SCHEMA for code in _IBAN_COUNTRIES},
}


def resolve_bank_field_schema(country_code: str, method: str = "BANK_TRANSFER") -> BankFieldSchema:
    """Country-specific structured fields (sort code, IBAN, routing number)
    only ever make sense for BANK_TRANSFER -- a CASH/CARD/OTHER instruction
    has no bank routing details at all, forcing a UK sort code onto a cash
    payment would be nonsensical, not just unhelpful. Every non-bank-
    transfer method always gets the generic single-field fallback,
    regardless of country_code."""
    if method != "BANK_TRANSFER":
        return _FALLBACK_SCHEMA
    return BANK_FIELD_SCHEMAS.get(country_code.strip().upper(), _FALLBACK_SCHEMA)


def validate_bank_details(country_code: str, bank_details: dict[str, str], method: str = "BANK_TRANSFER") -> None:
    """Raises ValueError (caller converts to HTTPException 400) on a missing
    or malformed field. Extra keys not in the schema are ignored rather
    than rejected -- lets the frontend submit a superset without this
    validator needing to change in lockstep."""
    schema = resolve_bank_field_schema(country_code, method)
    for field in schema.fields:
        value = (bank_details.get(field.key) or "").strip()
        if not value:
            raise ValueError(f"{field.label} is required")
        if not field.pattern.match(value):
            raise ValueError(f"{field.label} is not in a valid format ({field.hint})" if field.hint else f"{field.label} is not in a valid format")
