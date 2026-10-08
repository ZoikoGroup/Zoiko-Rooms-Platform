"""Phase 3 tests -- referral monetization, feature-gated (SRCH-13).
Per Section 10 / ZR-PAY-CFG-001 monetary charging stays OFF until the
feature flag is enabled AND an ACTIVE, billing-enabled policy exists.
Also pins SRCH-12 payment safety (unverified external = no payment path).
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.external_search import ExternalCommercialPolicy, ExternalOpportunity
from app.services.commercial_policy import FEATURE_FLAG, commercial_policy_service
from app.services.feature_flags import set_flag

from tests.conftest import _make_admin


def _seed_policy(db, *, market_code="GB", provider_type="LANDLORD", billing_enabled=False, status="ACTIVE"):
    policy = ExternalCommercialPolicy(
        market_code=market_code,
        model="CLAIM_AND_LIST",
        provider_type=provider_type,
        fee_amount_minor=5000,
        currency="GBP",
        fee_share_basis=None,
        trigger_event="PROVIDER_ACCEPTED",
        legal_approval_ref="ZR-LEG-SRCH-001",
        tax_rule_ref="ZR-TAX-SRCH-001",
        billing_enabled=billing_enabled,
        contract_template_version="v1",
        effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
        effective_to=None,
        status=status,
    )
    db.add(policy)
    db.flush()
    return policy


class TestReferralBillingGate:
    def test_billing_off_by_default_even_when_policy_billing_enabled(self, db_session):
        _seed_policy(db_session, billing_enabled=True)
        assert commercial_policy_service.charge_eligible(
            db_session, market_code="GB", provider_type="LANDLORD"
        ) is False
        assert commercial_policy_service.quote(
            db_session, market_code="GB", provider_type="LANDLORD"
        ) is None

    def test_no_active_policy_blocks(self, db_session):
        assert commercial_policy_service.quote(
            db_session, market_code="GB", provider_type="LANDLORD"
        ) is None

    def test_draft_policy_blocks(self, db_session):
        _seed_policy(db_session, billing_enabled=True, status="DRAFT")
        assert commercial_policy_service.quote(
            db_session, market_code="GB", provider_type="LANDLORD"
        ) is None

    def test_disable_flag_blocks_even_with_enabled_policy(self, db_session):
        admin = _make_admin(db_session, role="super_admin")
        _seed_policy(db_session, billing_enabled=True)
        set_flag(db_session, admin, FEATURE_FLAG, False)
        assert commercial_policy_service.quote(
            db_session, market_code="GB", provider_type="LANDLORD"
        ) is None

    def test_market_pack_must_allow_fees(self, db_session):
        admin = _make_admin(db_session, role="super_admin")
        _seed_policy(db_session, billing_enabled=True)
        set_flag(db_session, admin, FEATURE_FLAG, True)
        assert commercial_policy_service.charge_eligible(
            db_session, market_code="GB", provider_type="LANDLORD"
        ) is False

    def test_enabling_flag_enables_quote(self, db_session):
        from app.models.external_search import ExternalMarketLegalPack

        admin = _make_admin(db_session, role="super_admin")
        _seed_policy(db_session, billing_enabled=True)
        set_flag(db_session, admin, FEATURE_FLAG, True)
        db_session.add(ExternalMarketLegalPack(
            market_code="GB", status="ACTIVE", legal_approved=True, privacy_approved=True,
            commercial_approved=True, referral_fees_enabled=True,
        ))
        db_session.flush()

        assert commercial_policy_service.charge_eligible(
            db_session, market_code="GB", provider_type="LANDLORD"
        ) is True
        quote = commercial_policy_service.quote(
            db_session, market_code="GB", provider_type="LANDLORD"
        )
        assert quote is not None
        assert quote["model"] == "CLAIM_AND_LIST"
        assert quote["fee_amount_minor"] == 5000
        assert quote["currency"] == "GBP"
        assert quote["billing_enabled"] is True


class TestPaymentSafety:
    def test_unverified_external_never_payment_eligible(self, db_session):
        opp = ExternalOpportunity(
            external_opportunity_id="opp-1",
            source_id="s1",
            status="PROVIDER_ACCEPTED",
            verification_status="NOT_VERIFIED_BY_ZOIKO_ROOMS",
        )
        db_session.add(opp)
        db_session.flush()
        assert commercial_policy_service.external_payment_eligible(opp) is False

    def test_internalized_verified_but_no_payment_receipt_authority_not_eligible(self, db_session):
        # Section 11.1: the separate payment-receipt authority is required in
        # addition to INTERNALIZED_VERIFIED + VERIFIED_AUTHORITY (SRCH-12).
        opp = ExternalOpportunity(
            external_opportunity_id="opp-2",
            source_id="s1",
            status="INTERNALIZED_VERIFIED",
            verification_status="VERIFIED_AUTHORITY",
        )
        db_session.add(opp)
        db_session.flush()
        assert commercial_policy_service.external_payment_eligible(opp) is False

    def test_internalized_verified_with_payment_receipt_authority_eligible(self, db_session):
        opp = ExternalOpportunity(
            external_opportunity_id="opp-3",
            source_id="s1",
            status="INTERNALIZED_VERIFIED",
            verification_status="VERIFIED_AUTHORITY",
            payment_receipt_authority_verified=True,
        )
        db_session.add(opp)
        db_session.flush()
        assert commercial_policy_service.external_payment_eligible(opp) is True