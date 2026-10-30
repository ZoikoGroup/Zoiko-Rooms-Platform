"""A UK host with an England property and a renter whose verified identity is Indian:
agreements follow the property's own jurisdiction (its market release and clause registry).
Nothing in the pipeline reads the renter's country or nationality."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.identity_profile import IdentityProfile
from app.models.identity_verification import IdentityVerification
from app.models.leasing import Agreement
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.property import Property
from tests import test_host_move_in_confirmation as pipeline
from tests.test_host_move_in_confirmation import _drive_agreement_to_signed


def test_uk_host_and_indian_renter_reach_a_signed_agreement(client, db_session: Session, monkeypatch):
    real_host_and_listing = pipeline._make_host_and_listing
    real_verified_renter = pipeline._make_verified_renter

    def uk_host(db, *, email):
        host, listing_id = real_host_and_listing(db, email=email)
        party = db.get(Party, host.party_id)
        party.jurisdiction = "England"
        prop = db.query(Property).filter_by(owner_party_id=party.id).one()
        prop.jurisdiction_code = "England"
        db.commit()
        return host, listing_id

    def indian_renter(db, *, email):
        renter = real_verified_renter(db, email=email)
        party = db.get(Party, renter.party_id)
        party.jurisdiction = "IN"
        verification = db.query(IdentityVerification).filter_by(party_id=party.id).one()
        db.add(IdentityProfile(
            party_id=party.id, state="VERIFIED", assurance_level="IV-2", country_code="IN",
            given_name="Asha", family_name="Patel", verified_legal_name="Asha Patel",
            verified_at=datetime.now(timezone.utc), current_verification_id=verification.id,
        ))
        db.commit()
        return renter

    monkeypatch.setattr(pipeline, "_make_host_and_listing", uk_host)
    monkeypatch.setattr(pipeline, "_make_verified_renter", indian_renter)

    host, renter, _hc, _rc, occupancy_id = _drive_agreement_to_signed(
        client, db_session, host_email="xb-uk-host@test.com", renter_email="xb-in-renter@test.com",
    )

    # The two sides really are in different countries...
    assert db_session.get(Party, host.party_id).jurisdiction == "England"
    profile = db_session.query(IdentityProfile).filter_by(party_id=renter.party_id).one()
    assert profile.country_code == "IN" and profile.state == "VERIFIED"
    # ...and the whole pipeline still reached a signed, effective tenancy.
    occupancy = db_session.get(Occupancy, occupancy_id)
    agreement = db_session.query(Agreement).filter(Agreement.offer_id == occupancy.offer_id).one()
    assert agreement.status == "SIGNED"
