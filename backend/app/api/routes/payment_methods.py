"""ZR-PAY-METHODS-GLOBAL-001 Section 10.1: the configuration APIs the
payment-recording forms render their dropdowns from. Read-only -- the
recording endpoints (rental_payments.py, rental_payment_returns.py) re-check
every choice at write time through services/payment_method_catalog.py."""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_user
from app.schemas.payment_method import (
    PaymentMethodCatalogRead,
    PaymentMethodFieldRulesRead,
    PaymentMethodSubtypeRead,
)
from app.services import payment_method_catalog as catalog

router = APIRouter(prefix="/api/users/payment-methods", tags=["payment-methods"], dependencies=[Depends(get_current_user)])

# Path placeholder for a category with no rail/provider subtype.
NO_SUBTYPE = "NONE"


def _country(country: str) -> str:
    return catalog.country_for(country)


def _available_category(code: str, country: str, context: str) -> catalog.PaymentMethodCategory:
    entry = next((c for c in catalog.available_categories(country, context) if c.code == code.upper()), None)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That payment method isn't available here")
    return entry


@router.get("", response_model=PaymentMethodCatalogRead)
def get_payment_methods(
    country: str = Query(default="", max_length=10), context: str = Query(default="rent_record"),
):
    """GET /payment-methods?country={ISO2}&context=rent_record -- the
    categories (local ones first) with their rails and reference rules."""
    return catalog.catalog_payload(_country(country), context)


@router.get("/{category}/subtypes", response_model=list[PaymentMethodSubtypeRead])
def get_payment_method_subtypes(
    category: str, country: str = Query(default="", max_length=10), context: str = Query(default="rent_record"),
):
    code = _country(country)
    entry = _available_category(category, code, context)
    return [
        {"code": s.code, "label": s.label, "region": catalog.region_tag(s), **catalog.field_rules(entry, s)}
        for s in catalog.available_subtypes(entry, code, catalog.date.today())
    ]


@router.get("/{category}/{subtype}/field-rules", response_model=PaymentMethodFieldRulesRead)
def get_payment_method_field_rules(
    category: str, subtype: str, country: str = Query(default="", max_length=10),
    context: str = Query(default="rent_record"),
):
    code = _country(country)
    entry = _available_category(category, code, context)
    if subtype.upper() == NO_SUBTYPE:
        return catalog.field_rules(entry, None)
    chosen = next(
        (s for s in catalog.available_subtypes(entry, code, catalog.date.today()) if s.code == subtype.upper()), None,
    )
    if chosen is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"That option isn't available for {entry.label} here")
    return catalog.field_rules(entry, chosen)
