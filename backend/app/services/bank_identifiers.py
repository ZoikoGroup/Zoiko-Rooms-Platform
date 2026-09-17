"""Country-specific secondary bank routing identifier formats -- e.g. India's
IFSC code, the UK's sort code. This is public banking-standard technical fact
(like knowing a postal code's shape), not legal/regulatory content, so unlike
app/services/agreement_profile.py or a MarketPolicyPack's deposit/sublet
terms, adding a new country's format here doesn't require counsel review.

ZR-ENG-CLR-005 Section 5's payout data model calls this a generic
"beneficiary/account token" -- routing detail belongs to per-country
configuration, not a single hard-coded format baked into the core schema
(crud/payout_beneficiary.py used to validate every submission against
India's IFSC pattern alone, silently making payouts impossible to register
for any other country). Resolution fails closed for an unconfigured
jurisdiction rather than falling back to some other country's format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class BankIdentifierFormat:
    label: str
    pattern: re.Pattern[str]


BANK_IDENTIFIER_FORMATS: dict[str, BankIdentifierFormat] = {
    "IN": BankIdentifierFormat("IFSC code", re.compile(r"[A-Z]{4}0[A-Z0-9]{6}")),
    # UK sort code: 6 digits, conventionally written in 2-2-2 groups either
    # bare or dash-separated (e.g. "123456" or "12-34-56").
    "England": BankIdentifierFormat("UK sort code", re.compile(r"\d{2}-?\d{2}-?\d{2}")),
}


def resolve_bank_identifier_format(jurisdiction: str) -> BankIdentifierFormat | None:
    return BANK_IDENTIFIER_FORMATS.get(jurisdiction)
