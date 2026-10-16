from app.schemas.common import CamelModel


class PaymentMethodFieldRulesRead(CamelModel):
    """ZR-PAY-METHODS-GLOBAL-001 Section 5: the reference/evidence semantics
    for one category (or category + rail) choice."""

    reference_label: str
    reference_helper: str
    reference_policy: str  # OPTIONAL | RECOMMENDED | REQUIRED
    evidence_policy: str  # OPTIONAL | CONDITIONAL | REQUIRED
    evidence_helper: str
    requires_description: bool


class PaymentMethodSubtypeRead(PaymentMethodFieldRulesRead):
    code: str
    label: str
    # Where the rail is used ("UK", "EU/EEA", ...); "" when global.
    region: str = ""


class PaymentMethodCategoryRead(PaymentMethodFieldRulesRead):
    code: str
    label: str
    # Guidance text for the side panel.
    description: str = ""
    # The conditional second control's label; "" = no second control.
    subtype_label: str
    subtype_required: bool
    subtypes: list[PaymentMethodSubtypeRead]


class PaymentMethodCatalogRead(CamelModel):
    version: str
    country_code: str
    context: str
    categories: list[PaymentMethodCategoryRead]
