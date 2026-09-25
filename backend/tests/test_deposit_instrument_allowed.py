"""ZR-ENG-CLR-002 Section 2/13.2: deposit_instrument_allowed was previously
stored on MarketPolicyPack but never read by any business logic -- a
PROHIBITED market could still have a deposit added, and a REQUIRED one could
still have none. crud/leasing.py:add_offer_terms is the single place a
deposit amount is actually set."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.crud import leasing as leasing_crud
from app.models.admin_user import AdminUser
from app.models.guest import Guest
from app.models.leasing import Application, Offer
from app.models.listing import Listing
from app.models.market_policy import MarketPolicyPack
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from app.schemas.leasing import OfferTermsCreate
from tests.conftest import _make_admin


def _make_accepted_offer(db: Session, *, suffix: str) -> Offer:
    """An Application -> Offer chain with no terms added yet -- the exact
    pre-state add_offer_terms is called against."""
    owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(owner_party)
    db.flush()
    prop = Property(owner_party_id=owner_party.id, address="1 Test St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()
    listing = Listing(
        id=f"L-DEPINSTR-{suffix}", slug=f"depinstr-{suffix}", name="Deposit Instrument Test Listing",
        room_type="Private room", city="Bengaluru", location="Koramangala", price_per_night=500, guests=1,
        rating=4.5, review_count=0, party_id=owner_party.id, owner_id=None, room_id=room.id, state="PUBLISHED",
    )
    db.add(listing)
    db.flush()

    guest = Guest(id=f"G-DEPINSTR-{suffix}", name="Test Renter", email=f"depinstr-{suffix}@test.com", joined_at=date.today())
    db.add(guest)
    db.flush()

    application = Application(listing_id=listing.id, guest_id=guest.id, status="DECIDED")
    db.add(application)
    db.flush()
    offer = Offer(application_id=application.id, listing_id=listing.id, guest_id=application.guest_id, status="DRAFT")
    db.add(offer)
    db.commit()
    return offer


def _set_deposit_policy(db: Session, allowed: str) -> None:
    """Overrides the conftest-seeded England pack (Property.jurisdiction_code
    defaults to 'England') with a higher version so resolve_market_policy picks it."""
    db.add(MarketPolicyPack(
        jurisdiction_code="England", version=2, effective_from=date(2026, 1, 1),
        deposit_instrument_allowed=allowed, legal_source_note="Test override.",
    ))
    db.commit()


class TestDepositProhibited:
    def test_a_nonzero_deposit_is_rejected(self, db_session: Session):
        offer = _make_accepted_offer(db_session, suffix="prohibited")
        _set_deposit_policy(db_session, "PROHIBITED")
        admin = _make_admin(db_session, email="depinstr-prohibited-admin@test.com", role="super_admin")

        with pytest.raises(HTTPException) as exc_info:
            leasing_crud.add_offer_terms(
                db_session, offer, admin,
                OfferTermsCreate(monthlyRent=500, depositAmount=200, startDate=date.today(), termMonths=6),
            )
        assert exc_info.value.status_code == 400
        assert "not permitted" in str(exc_info.value.detail)

    def test_a_zero_deposit_is_allowed(self, db_session: Session):
        offer = _make_accepted_offer(db_session, suffix="prohibitedzero")
        _set_deposit_policy(db_session, "PROHIBITED")
        admin = _make_admin(db_session, email="depinstr-prohibitedzero-admin@test.com", role="super_admin")

        terms = leasing_crud.add_offer_terms(
            db_session, offer, admin,
            OfferTermsCreate(monthlyRent=500, depositAmount=0, startDate=date.today(), termMonths=6),
        )
        assert terms.deposit_amount == 0


class TestDepositRequired:
    def test_a_zero_deposit_is_rejected(self, db_session: Session):
        offer = _make_accepted_offer(db_session, suffix="required")
        _set_deposit_policy(db_session, "REQUIRED")
        admin = _make_admin(db_session, email="depinstr-required-admin@test.com", role="super_admin")

        with pytest.raises(HTTPException) as exc_info:
            leasing_crud.add_offer_terms(
                db_session, offer, admin,
                OfferTermsCreate(monthlyRent=500, depositAmount=0, startDate=date.today(), termMonths=6),
            )
        assert exc_info.value.status_code == 400
        assert "required" in str(exc_info.value.detail).lower()

    def test_a_nonzero_deposit_is_allowed(self, db_session: Session):
        offer = _make_accepted_offer(db_session, suffix="requiredok")
        _set_deposit_policy(db_session, "REQUIRED")
        admin = _make_admin(db_session, email="depinstr-requiredok-admin@test.com", role="super_admin")

        terms = leasing_crud.add_offer_terms(
            db_session, offer, admin,
            OfferTermsCreate(monthlyRent=500, depositAmount=200, startDate=date.today(), termMonths=6),
        )
        assert terms.deposit_amount == 200


class TestDepositOptionalIsUnaffected:
    """OPTIONAL is the model default and the only value every existing pack in
    this platform has ever used -- the fail-safe must not change its behavior."""

    def test_optional_allows_either(self, db_session: Session):
        offer_with = _make_accepted_offer(db_session, suffix="optionalwith")
        admin = _make_admin(db_session, email="depinstr-optional-admin@test.com", role="super_admin")

        terms_with = leasing_crud.add_offer_terms(
            db_session, offer_with, admin,
            OfferTermsCreate(monthlyRent=500, depositAmount=200, startDate=date.today(), termMonths=6),
        )
        assert terms_with.deposit_amount == 200

        offer_without = _make_accepted_offer(db_session, suffix="optionalwithout")
        terms_without = leasing_crud.add_offer_terms(
            db_session, offer_without, admin,
            OfferTermsCreate(monthlyRent=500, depositAmount=0, startDate=date.today(), termMonths=6),
        )
        assert terms_without.deposit_amount == 0


class TestSchemaValidation:
    def test_invalid_value_is_rejected_at_the_schema_level(self):
        from app.schemas.market_policy import MarketPolicyPackCreate

        with pytest.raises(Exception):
            MarketPolicyPackCreate(
                jurisdictionCode="England", effectiveFrom=date.today(), depositInstrumentAllowed="MADE_UP_VALUE",
            )
