"""Partner feed parsers (ZR-AI-SEARCH-001 Section 6.1 Tier A).

Every parser turns one partner feed into the same normalized item shape the
partner feed adapter ingests (keys match ``partner_feeds.FIELD_MAP``):

    external_id, approx_location, price_minor, currency, price_period,
    room_type, provider_name, provider_contact, exact_address, source_url

Only rental listings that are currently available are returned. Free text
(descriptions, summaries, titles) is never carried over: it is untrusted,
can carry prompt-injection or contact details, and makes the source easy to
find (Sections 6.3 and 8). Formats:

* BLM  -- the UK portal feed letting agents already send to Rightmove/Zoopla;
* RESO -- the US MLS Web API (OData JSON, ``{"value": [...]}``);
* CSV / JSON -- Zoiko Rooms' own simple partner format (see ``parse_simple``).
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

FEED_FORMATS = ("JSON", "CSV", "BLM", "RESO")


class FeedFormatError(ValueError):
    """The feed could not be parsed in its declared format."""


def _minor(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(round(float(str(value).replace(",", "")) * 100))
    except ValueError:
        return None


def _clean(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def parse_feed(content: bytes | str, feed_format: str) -> list[dict[str, Any]]:
    fmt = (feed_format or "").upper()
    text = content.decode("utf-8-sig", errors="replace") if isinstance(content, bytes) else content
    if fmt == "BLM":
        return parse_blm(text)
    if fmt == "RESO":
        return parse_reso(text)
    if fmt in ("JSON", "CSV"):
        return parse_simple(text, fmt)
    raise FeedFormatError(f"unsupported feed format: {feed_format!r}")


# -- UK: BLM ------------------------------------------------------------------

# LET_RENT_FREQUENCY: 0 weekly, 1 monthly, 2 quarterly, 3 annual,
# 5 per person per week. Quarterly/annual are converted to monthly.
_BLM_FREQUENCY = {"0": ("WEEK", 1), "1": ("MONTH", 1), "2": ("MONTH", 1 / 3), "3": ("MONTH", 1 / 12), "5": ("WEEK", 1)}
_BLM_LETTINGS = "2"  # TRANS_TYPE_ID: 1 resale, 2 lettings
_BLM_AVAILABLE = ("", "0")  # STATUS_ID 0 = available


def _blm_delimiter(header: str, name: str, default: str) -> str:
    match = re.search(rf"^\s*{name}\s*:\s*'(.)'", header, re.MULTILINE | re.IGNORECASE)
    return match.group(1) if match else default


def parse_blm(text: str) -> list[dict[str, Any]]:
    try:
        header = text.split("#DEFINITION#", 1)[0]
        definition = text.split("#DEFINITION#", 1)[1].split("#DATA#", 1)[0]
        data = text.split("#DATA#", 1)[1].split("#END#", 1)[0]
    except IndexError as exc:
        raise FeedFormatError("BLM feed is missing #DEFINITION# or #DATA#") from exc
    eof = _blm_delimiter(header, "EOF", "^")
    eor = _blm_delimiter(header, "EOR", "~")
    columns = [c.strip() for c in definition.strip().split(eor)[0].split(eof)]
    columns = [c for c in columns if c]
    if "AGENT_REF" not in columns:
        raise FeedFormatError("BLM definition has no AGENT_REF column")

    items: list[dict[str, Any]] = []
    for record in data.split(eor):
        if not record.strip():
            continue
        values = record.strip("\r\n").split(eof)
        row = dict(zip(columns, (v.strip() for v in values)))
        if row.get("TRANS_TYPE_ID", _BLM_LETTINGS) != _BLM_LETTINGS:
            continue
        if row.get("STATUS_ID", "") not in _BLM_AVAILABLE:
            continue
        period, factor = _BLM_FREQUENCY.get(row.get("LET_RENT_FREQUENCY", "1"), ("MONTH", 1))
        price = _minor(row.get("PRICE"))
        address = ", ".join(
            p for p in (row.get("ADDRESS_1"), row.get("ADDRESS_2"), row.get("ADDRESS_3"), row.get("TOWN")) if p
        )
        postcode = " ".join(p for p in (row.get("POSTCODE1"), row.get("POSTCODE2")) if p)
        items.append(
            {
                "external_id": row["AGENT_REF"],
                # Town only: the full postcode would pinpoint the property.
                "approx_location": _clean(row.get("TOWN")),
                "price_minor": int(round(price * factor)) if price is not None else None,
                "currency": "GBP",
                "price_period": period,
                "room_type": None,
                "bedrooms": _clean(row.get("BEDROOMS")),
                "provider_name": None,
                "provider_contact": None,
                "exact_address": ", ".join(p for p in (address, postcode) if p) or None,
                "source_url": None,
            }
        )
    return items


# -- US: RESO Web API -----------------------------------------------------------

def parse_reso(text: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FeedFormatError("RESO feed is not valid JSON") from exc
    rows = payload.get("value") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise FeedFormatError("RESO feed has no 'value' array")
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("ListingKey"):
            continue
        if row.get("PropertyType") not in (None, "ResidentialLease"):
            continue
        if row.get("StandardStatus") not in (None, "Active"):
            continue
        items.append(
            {
                "external_id": str(row["ListingKey"]),
                "approx_location": _clean(row.get("City")),
                "price_minor": _minor(row.get("ListPrice")),
                "currency": "USD",
                "price_period": "MONTH",
                "room_type": _clean(row.get("PropertySubType")),
                "bedrooms": _clean(row.get("BedroomsTotal")),
                "provider_name": _clean(row.get("ListAgentFullName") or row.get("ListOfficeName")),
                "provider_contact": _clean(row.get("ListAgentEmail") or row.get("ListAgentDirectPhone")),
                "exact_address": _clean(row.get("UnparsedAddress")),
                "source_url": None,
            }
        )
    return items


# -- Zoiko Rooms simple format (CSV or JSON) ------------------------------------

SIMPLE_COLUMNS = (
    "external_id", "city", "price", "currency", "price_period", "room_type",
    "bedrooms", "provider_name", "provider_contact", "address", "listing_url",
)


def parse_simple(text: str, fmt: str) -> list[dict[str, Any]]:
    """CSV with a header row of SIMPLE_COLUMNS, or JSON: a list of objects
    with the same keys (or ``{"listings": [...]}``). ``price`` is in major
    units (e.g. 950.00); ``price_period`` is MONTH or WEEK."""
    if fmt == "CSV":
        rows = list(csv.DictReader(io.StringIO(text)))
    else:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FeedFormatError("JSON feed is not valid JSON") from exc
        rows = payload.get("listings") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise FeedFormatError("JSON feed must be a list or {'listings': [...]}")
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not _clean(row.get("external_id")):
            continue
        period = (_clean(row.get("price_period")) or "MONTH").upper()
        items.append(
            {
                "external_id": _clean(row.get("external_id")),
                "approx_location": _clean(row.get("city")),
                "price_minor": _minor(row.get("price")),
                "currency": (_clean(row.get("currency")) or "").upper() or None,
                "price_period": period if period in ("MONTH", "WEEK") else "MONTH",
                "room_type": _clean(row.get("room_type")),
                "bedrooms": _clean(row.get("bedrooms")),
                "provider_name": _clean(row.get("provider_name")),
                "provider_contact": _clean(row.get("provider_contact")),
                "exact_address": _clean(row.get("address")),
                "source_url": _clean(row.get("listing_url")),
            }
        )
    return items
