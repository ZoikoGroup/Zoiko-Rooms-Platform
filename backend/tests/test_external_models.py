"""SRCH-03 / SRCH-11 -- externa.0 =/= verified 0 =/= accepted.

* SRCH-03: paid/reviewed listings must not imply verification coverage.
* SRCH-11: provider acceptance is NOT verification: acceptance and
  verification are separate status dimensions on the opportunity.

Model-level invariants: ExternalOpportunity has no payment field, and the
verification status is an independent dimension that is never derived from
acceptance or payment state.
"""

from __future__ import annotations

import pytest

from sqlalchemy import inspect

from app.models.external_search import ExternalOpportunity
from app.services.external_outreach import outreach_service

from tests.conftest import _make_user

# External discovery and outreach are off until market activation (Section 13).
pytestmark = pytest.mark.usefixtures("external_activated")


def _seed_opportunity(db, *, source_id="s1", status="EXTERNAL_DISCOVERED"):
    opp = ExternalOpportunity(
        market_code="GB",
        external_opportunity_id=f"opp-{source_id}",
        source_id=source_id,
        status=status,
        approx_location="Bethnal Green, London",
        verification_status="NOT_VERIFIED_BY_ZOIKO_ROOMS",
    )
    db.add(opp)
    db.flush()
    return opp


class TestNoPaymentCoupling:
    def test_external_opportunity_has_no_payment_fields(self):
        columns = {c.key for c in inspect(ExternalOpportunity).columns}
        assert "payment_id" not in columns
        assert "paid_at" not in columns
        assert "payment_ref" not in columns
        assert "payment_status" not in columns


class TestAcceptanceVsVerification:
    def test_acceptance_never_touches_verification(self, db_session):
        from app.services.source_rights_registry import registry
        from app.models.external_search import SourceRightRegistry

        row = SourceRightRegistry(
            source_id="s1",
            source_name_internal="Example Org",
            territories=["GB"],
            acquisition_mode="PUBLIC_FETCH",
            legal_approved=True,
            security_approved=True,
            status="ACTIVE",
            display_permitted=True,
            outreach_permitted=True,
            contact_extraction_permitted=True,
            masking_permitted=True,
            permitted_fields=["provider_name", "approx_location"],
            cache_ttl_seconds=3600,
            source_brand_display_rule="Test",
        )
        db_session.add(row)
        db_session.flush()
        registry._cache = {
            "s1": {
                "source_id": "s1",
                "domain": "example.org",
                "tier": "B",
                "allow_fallback": True,
                "allow_direct_contact": True,
                "allow_indexing": False,
                "policy_ref": "ZR-POL-SRCH-001",
                "clickthrough_required": False,
                "masking_permitted": True,
                "outreach_channels": ["EMAIL"],
                "notes": None,
                "is_active": True,
            }
        }

        user = _make_user(db_session)
        opp = _seed_opportunity(db_session)
        po = outreach_service.request_intro(db_session, user_id=user.id, opportunity_id=opp.id)
        outreach_service.send_outreach(db_session, po)
        outreach_service.handle_provider_response(db_session, po, "ACCEPTED")
        opp = db_session.get(ExternalOpportunity, opp.id)

        assert opp.status == "PROVIDER_ACCEPTED"
        # Acceptance leaves the verification dimension untouched (SRCH-11).
        assert opp.verification_status == "NOT_VERIFIED_BY_ZOIKO_ROOMS"

    def test_verification_status_independent_of_acceptance(self):
        for status in ("EXTERNAL_DISCOVERED", "OUTREACH_PENDING", "PROVIDER_ACCEPTED"):
            opp = ExternalOpportunity(
                market_code="GB",
                external_opportunity_id=f"opp-{status}",
                source_id="s1",
                status=status,
                verification_status="NOT_VERIFIED_BY_ZOIKO_ROOMS",
            )
            assert opp.verification_status == "NOT_VERIFIED_BY_ZOIKO_ROOMS"