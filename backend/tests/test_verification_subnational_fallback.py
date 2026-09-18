"""ZR-ENG-CLR-012 AC-16: "The Requirement Resolver is effective-dated and
supports country plus subnational/local rules." Convention: a hyphenated
jurisdiction code ('GB-SCT') falls back to the country-level pack ('GB')
when no subnational-specific pack is configured -- scoped to the
verification resolvers only, never resolve_market_policy itself (deposit/
sublet/rent-change must keep exact-match fail-closed behavior)."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.market_policy import MarketPolicyPack
from app.services.verification_requirements import (
    is_screening_check_type_permitted,
    resolve_property_compliance_requirements,
    resolve_verification_requirements,
)
from app.crud.market_policy import resolve_market_policy


def _pack(db: Session, jurisdiction: str, **kwargs) -> MarketPolicyPack:
    pack = MarketPolicyPack(jurisdiction_code=jurisdiction, version=1, effective_from=date.today(), **kwargs)
    db.add(pack)
    db.commit()
    db.refresh(pack)
    return pack


class TestSubnationalFallback:
    def test_occupancy_eligibility_falls_back_to_country_pack(self, db_session: Session):
        _pack(db_session, "GB", occupancy_eligibility_required=True, occupancy_eligibility_method_note="UK-wide check")
        requirements = resolve_verification_requirements(db_session, "GB-SCT")
        assert len(requirements) == 1
        assert requirements[0].requirement_code == "OCCUPANCY_ELIGIBILITY"

    def test_subnational_specific_pack_takes_priority_over_country_pack(self, db_session: Session):
        _pack(db_session, "GB", occupancy_eligibility_required=True)
        _pack(db_session, "GB-SCT", occupancy_eligibility_required=False)
        requirements = resolve_verification_requirements(db_session, "GB-SCT")
        assert requirements == []

    def test_property_compliance_falls_back_to_country_pack(self, db_session: Session):
        _pack(db_session, "GB", required_property_compliance_codes=["GAS_SAFETY_CERT"])
        codes = resolve_property_compliance_requirements(db_session, "GB-WLS")
        assert codes == ["GAS_SAFETY_CERT"]

    def test_screening_permission_falls_back_to_country_pack(self, db_session: Session):
        _pack(db_session, "GB", screening_prohibited_check_types=["CRIMINAL_RECORD"])
        assert is_screening_check_type_permitted(db_session, "GB-NIR", "CRIMINAL_RECORD") is False
        assert is_screening_check_type_permitted(db_session, "GB-NIR", "AFFORDABILITY") is True

    def test_no_fallback_when_no_hyphen_and_no_pack(self, db_session: Session):
        assert resolve_verification_requirements(db_session, "ZZ-NOWHERE-BUT-NO-PACK") == []
        assert resolve_verification_requirements(db_session, "ZZNOWHERE") == []

    def test_resolve_market_policy_itself_never_falls_back(self, db_session: Session):
        """The exact-match resolver deposit/sublet/rent-change rely on must
        stay fail-closed -- only the verification-layer wrapper adds
        subnational fallback."""
        import pytest
        from fastapi import HTTPException

        _pack(db_session, "GB", occupancy_eligibility_required=True)
        with pytest.raises(HTTPException):
            resolve_market_policy(db_session, "GB-SCT")
