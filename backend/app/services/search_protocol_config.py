"""ZR-AI-SEARCH-001 Appendix A -- minimum production configuration.

Each Appendix A key, its required value, and where the code enforces it.
Most are invariants built into the code paths (they cannot be switched off);
the few that are configurable are read from settings / flags / the Market
Legal Pack. ``assert_protocol_invariants`` runs at startup and refuses to boot
if a configurable value has been set to an unsafe one.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings

# key -> (required value, enforced by)
APPENDIX_A: dict[str, tuple[Any, str]] = {
    # Search precedence
    "internal_inventory_first": (True, "SearchOrchestrator.search: internal search always runs first"),
    "external_search_fallback_only": (True, "SearchOrchestrator.search: external only after zero qualifying matches"),
    "external_search_requires_internal_zero": (True, "qualifying_count > 0 returns INTERNAL_ONLY"),
    "external_results_when_internal_exists": (False, "INTERNAL_ONLY route never carries external cards"),
    # External display
    "external_default_verification_status": ("NOT_VERIFIED_BY_ZOIKO_ROOMS", "ExternalCard.verification_status default"),
    "external_direct_url_exposed_pre_unlock": (False, "card schema has no URL field; sanitizer masks links"),
    "external_contact_exposed_pre_unlock": (False, "card schema has no contact field; release needs both parties"),
    "external_exact_address_pre_unlock": (False, "approx_location only; exact address stored encrypted"),
    "external_images_default": ("PLACEHOLDER", "images_present=False; UI shows a placeholder"),
    "external_required_cta": ("REQUEST_ZOIKO_CONTACT", "ExternalCardResult.primary_cta"),
    # Source rights
    "unknown_source_rights": ("FAIL_CLOSED", "SourceRightsRegistry: unknown/unapproved sources are blocked"),
    "bypass_access_controls": (False, "ExternalBroker.fetch_allowed rejects auth/CAPTCHA/paywall bypass"),
    "raw_source_data_to_llm": (False, "tool rows carry masked cards only; source text discarded"),
    # Provider flow
    "provider_acceptance_required_for_intro": (True, "relay messaging requires provider acceptance"),
    "provider_acceptance_sets_verified": (False, "acceptance never changes verification_status"),
    "renter_consent_required_for_data_share": (True, "contact request needs consent_fields (Section 7.4)"),
    "relay_preferred": (True, "Zoiko-mediated thread before any direct contact release"),
    # Transactions / payments
    "unverified_external_payment_enabled": (False, "payment_block_reason guards payment instructions"),
    "rent_collection_enabled": (False, "settings.rent_collection_enabled"),
    "deposit_collection_enabled": (False, "settings.deposit_collection_enabled"),
    "rent_percentage_commission": (0, "commercial policy rejects RENT fee basis"),
    "external_referral_billing_enabled": (False, "flag external.referral_billing + Legal Pack + amendment"),
    # Commercial default
    "external_provider_default_offer": ("CLAIM_AND_LIST", "provider response page default option"),
    "external_tenant_introduction_fee": (0, "no renter fee path exists"),
    # Governance
    "market_legal_pack_required": (True, "market_legal_pack gates search, fetch, outreach, release, fees"),
    "source_rights_registry_required": (True, "every external source resolves through the registry"),
    "audit_required": (True, "every route, rights decision, consent, outreach and unlock is audited"),
}


def effective_protocol_config() -> dict[str, Any]:
    return {
        key: {"value": required, "enforced_by": where}
        for key, (required, where) in APPENDIX_A.items()
    } | {
        "rent_collection_enabled": {"value": settings.rent_collection_enabled, "enforced_by": APPENDIX_A["rent_collection_enabled"][1]},
        "deposit_collection_enabled": {"value": settings.deposit_collection_enabled, "enforced_by": APPENDIX_A["deposit_collection_enabled"][1]},
    }


def assert_protocol_invariants() -> None:
    """Refuse to start with a configurable Appendix A value set unsafely."""
    problems = []
    if settings.rent_collection_enabled:
        problems.append("rent_collection_enabled must be false (ZR-PAY-CFG-001 / Appendix A)")
    if settings.deposit_collection_enabled:
        problems.append("deposit_collection_enabled must be false (ZR-PAY-CFG-001 / Appendix A)")
    if problems:
        raise RuntimeError("ZR-AI-SEARCH-001 Appendix A: " + "; ".join(problems))
