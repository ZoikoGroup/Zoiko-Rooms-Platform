"""Referral monetization -- feature-gated billing configuration for external
provider conversion (ZR-AI-SEARCH-001 Phase 3 / Section 10).

Per Section 10 and ZR-PAY-CFG-001 this is feature-gated: monetary charging
under the external models stays DISABLED until EVERY of the following holds:
  * feature flag ``external.referral_billing`` is enabled (default False),
  * the market's policy row is ACTIVE (not DRAFT/EXPIRED),
  * ``billing_enabled`` on the row is True (hard default False, changed only
    by a migration/seed after legal approval),
  * contract templates, the price book and billing infrastructure are live.

``quote``/``charge_eligible`` fail closed: any unresolved or disabled gate
returns None/False, and every decision is audited so a future enablement is
traceable.
"""

from __future__ import annotations

from datetime import date, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.external_search import ExternalCommercialPolicy

FEATURE_FLAG = "external.referral_billing"


class CommercialPolicyService:
    def policy_enabled(self, db: Session, *, market_code: str) -> bool:
        from app.services.feature_flags import is_enabled

        return is_enabled(db, FEATURE_FLAG, market=market_code)

    def resolve_policy(
        self,
        db: Session,
        *,
        market_code: str,
        provider_type: str,
    ) -> ExternalCommercialPolicy | None:
        """The single ACTIVE commercial policy for a market+provider type, or
        None (fail closed). Only informational rows and never a charge."""
        today = date.today()
        stmt = (
            select(ExternalCommercialPolicy)
            .where(
                ExternalCommercialPolicy.market_code == market_code,
                ExternalCommercialPolicy.provider_type == provider_type,
                ExternalCommercialPolicy.status == "ACTIVE",
                ExternalCommercialPolicy.effective_from <= today,
                # An expired policy never applies.
                (ExternalCommercialPolicy.effective_to.is_(None)) | (ExternalCommercialPolicy.effective_to >= today),
            )
            .order_by(ExternalCommercialPolicy.effective_from.desc())
        )
        return db.scalars(stmt.limit(1)).first()

    def charge_eligible(
        self,
        db: Session,
        *,
        market_code: str,
        provider_type: str,
        correlation_id: str = "",
    ) -> bool:
        """True only when the feature flag AND an ACTIVE billing-enabled policy
        exist. Default False (SRCH-13 release blocker)."""
        if not self.policy_enabled(db, market_code=market_code):
            self._audit(db, market_code, provider_type, "blocked", "feature_flag_off", correlation_id)
            return False
        from app.services.market_legal_pack import referral_fees_allowed

        # Section 13: referral/success fees need the market's Legal Pack too.
        if not referral_fees_allowed(db, market_code):
            self._audit(db, market_code, provider_type, "blocked", "market_pack_disallows_fees", correlation_id)
            return False
        policy = self.resolve_policy(db, market_code=market_code, provider_type=provider_type)
        if policy is None or not policy.billing_enabled:
            self._audit(db, market_code, provider_type, "blocked", "billing_disabled_or_no_active_policy", correlation_id)
            return False
        # Section 10.2: never a percentage of rent / rent deduction.
        if str(policy.fee_share_basis or "").upper() == "RENT":
            self._audit(db, market_code, provider_type, "blocked", "rent_based_fee_prohibited", correlation_id)
            return False
        self._audit(db, market_code, provider_type, "eligible", "billing_enabled", correlation_id)
        return True

    def quote(
        self,
        db: Session,
        *,
        market_code: str,
        provider_type: str,
        correlation_id: str = "",
    ) -> dict[str, Any] | None:
        """An informational fee quote. None whenever any gate is unresolved or
        disabled -- the caller must treat None as 'no charge applies'."""
        if not self.charge_eligible(db, market_code=market_code, provider_type=provider_type, correlation_id=correlation_id):
            return None
        policy = self.resolve_policy(db, market_code=market_code, provider_type=provider_type)
        if policy is None:
            return None
        return {
            "market_code": policy.market_code,
            "model": policy.model,
            "provider_type": policy.provider_type,
            "trigger_event": policy.trigger_event,
            "fee_amount_minor": policy.fee_amount_minor,
            "currency": policy.currency,
            "fee_share_basis": policy.fee_share_basis,
            "legal_approval_ref": policy.legal_approval_ref,
            "tax_rule_ref": policy.tax_rule_ref,
            "contract_template_version": policy.contract_template_version,
            "billing_enabled": policy.billing_enabled,
        }

    @staticmethod
    def _audit(db: Session, market_code: str, provider_type: str, outcome: str, reason: str, correlation_id: str) -> None:
        from app.services.audit_ext import log_external_search_event

        log_external_search_event(
            db,
            action=f"commercial_policy.{outcome}",
            resource_type="external_commercial_policy",
            resource_id=f"{market_code}:{provider_type}",
            correlation_id=correlation_id,
            reason=reason,
        )

    @staticmethod
    def external_payment_eligible(opportunity: Any) -> bool:
        """SRCH-12 / Section 11.1 payment safety: an external lead only becomes
        eligible for payment workflows once it is INTERNALIZED, fully verified,
        holds the SEPARATE payment-receipt authority, and any sublet evidence
        has the required landlord/agent permission confirmed. Payment-receipt
        authority is never implied by provider acceptance or by
        VERIFIED_AUTHORITY alone."""
        return (
            opportunity.status == "INTERNALIZED_VERIFIED"
            and opportunity.verification_status == "VERIFIED_AUTHORITY"
            and bool(getattr(opportunity, "payment_receipt_authority_verified", False))
            and not (
                bool(getattr(opportunity, "sublet_evidence_encrypted", False))
                and not bool(getattr(opportunity, "sublet_permission_verified", False))
            )
        )


commercial_policy_service = CommercialPolicyService()