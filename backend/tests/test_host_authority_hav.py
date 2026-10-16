"""ZR-HAV-001 host authority verification -- jurisdiction packs per UK nation
and US state, documents that never prove ownership, the extra host
capacities, legal registrations / licences and the HMO trigger, and the
versioned Host Declaration. Every case is decided automatically."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from app.services import authority_service as svc
from tests.conftest import _make_user
from tests.test_authority_verification import Flow, _pdf, env, ocr  # noqa: F401 -- shared fixtures

ADDRESS = "12 Lake View Road"
MANDATE_OWNER = "Vikram Shah"


def _uk_host(db: Session, email: str, *, nation: str = "England", postcode: str = "EH1 1AA", rooms: int = 1,
             room_capacity: int = 1, country: str = "GB", jurisdiction: str = "England"):
    party = Party(party_type="provider", status="active", jurisdiction=jurisdiction)
    db.add(party)
    db.flush()
    user = _make_user(db, email=email)
    user.party_id = party.id
    prop = Property(owner_party_id=party.id, address=ADDRESS, address_line_1=ADDRESS, city="Town",
                    postal_code=postcode, country_code=country, jurisdiction_code=jurisdiction,
                    administrative_area=nation, status="active")
    db.add(prop)
    db.flush()
    for _ in range(rooms):
        db.add(Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=False, status="active",
                    max_occupants=room_capacity))
    db.commit()
    return user, prop


def _doc(prop: Property, *lines: str) -> bytes:
    return _pdf(*lines, f"Property: {ADDRESS}, Town {prop.postal_code}")


def _reqs(f: Flow) -> dict:
    return {r["requirementId"] if "requirementId" in r else r["requirement_id"]: r for r in f.body["requirements"]}


class TestJurisdictionPacks:
    @pytest.mark.parametrize("nation,postcode,expected", [
        ("England", "M1 1AA", "GB-ENG"), ("Wales", "CF10 1AA", "GB-WLS"), ("Scotland", "EH1 1AA", "GB-SCT"),
        ("Northern Ireland", "BT1 1AA", "GB-NIR"), ("", "BT7 1NN", "GB-NIR"),
    ])
    def test_each_uk_nation_has_its_own_pack(self, db_session, nation, postcode, expected):
        _user, prop = _uk_host(db_session, f"pack-{expected}-{postcode[:2]}@test.com", nation=nation, postcode=postcode)
        assert svc.pack_code_for(prop) == expected
        assert svc.get_pack(db_session, expected).country_code == expected

    def test_us_state_pack_and_country_fallback(self, db_session):
        _u, ny = _uk_host(db_session, "pack-ny@test.com", nation="New York", country="US", jurisdiction="US")
        _u, tx = _uk_host(db_session, "pack-tx@test.com", nation="TX", country="US", jurisdiction="US")
        assert svc.pack_code_for(ny) == "US-NY"
        assert svc.get_pack(db_session, "US-TX").country_code == "US"  # no Texas pack: the US baseline
        assert svc.pack_code_for(tx) == "US-TX"

    def test_requirements_say_whether_law_or_policy_and_when(self, client, db_session):
        user, prop = _uk_host(db_session, "pack-basis@test.com", nation="Scotland")
        f = Flow(client, user, prop)
        f.start()
        reqs = _reqs(f)
        assert reqs["OWNER_PROPERTY_RIGHT"]["basis"] == "ZOIKO_POLICY"
        assert reqs["LANDLORD_REGISTRATION"]["basis"] == "LEGAL"
        assert reqs["LANDLORD_REGISTRATION"]["timing"] == "BEFORE_PUBLICATION"


class TestScotland:
    def test_scottish_title_and_landlord_registration_verify(self, client, db_session):
        user, prop = _uk_host(db_session, "sct-owner@test.com", nation="Scotland")
        f = Flow(client, user, prop)
        f.start()
        assert f.body["countryCode"] == "GB-SCT"
        f.upload("OWNER_PROPERTY_RIGHT", _doc(prop, "REGISTERS OF SCOTLAND", "TITLE SHEET", "Proprietor: Asha Rao"))
        f.submit()
        assert f.body["state"] == "ACTION_REQUIRED"
        assert "LANDLORD_REGISTRATION_MISSING" in f.body["reasonCodes"]

        f = Flow(client, user, prop)
        f.start()
        f.upload("LANDLORD_REGISTRATION", _pdf("SCOTTISH LANDLORD REGISTER", "Registered landlord: Asha Rao",
                                               "Registration number 123456/100/12345"))
        f.submit()
        assert f.body["state"] == "VERIFIED", f.body["reasonCodes"]

    def test_registration_for_someone_else_is_not_accepted(self, client, db_session):
        user, prop = _uk_host(db_session, "sct-other@test.com", nation="Scotland")
        f = Flow(client, user, prop)
        f.start()
        f.upload("OWNER_PROPERTY_RIGHT", _doc(prop, "REGISTERS OF SCOTLAND", "TITLE SHEET", "Proprietor: Asha Rao"))
        f.upload("LANDLORD_REGISTRATION", _pdf("SCOTTISH LANDLORD REGISTER", "Registered landlord: Priya Menon"))
        f.submit()
        assert "REGISTRATION_NOT_MATCHED" in f.body["reasonCodes"]


class TestEngland:
    def test_no_landlord_registration_and_land_registry_title_verifies(self, client, db_session):
        user, prop = _uk_host(db_session, "eng-owner@test.com", nation="England", postcode="M1 1AA")
        f = Flow(client, user, prop)
        f.start()
        assert "LANDLORD_REGISTRATION" not in _reqs(f)
        assert "HMO_LICENCE" not in _reqs(f)
        f.upload("OWNER_PROPERTY_RIGHT", _doc(prop, "HM LAND REGISTRY", "TITLE REGISTER", "Registered proprietor: Asha Rao"))
        f.submit()
        assert f.body["state"] == "VERIFIED", f.body["reasonCodes"]

    def test_hmo_licence_is_needed_once_the_rooms_hold_five(self, client, db_session):
        user, prop = _uk_host(db_session, "eng-hmo@test.com", nation="England", postcode="M1 1AA", rooms=5)
        f = Flow(client, user, prop)
        f.start()
        assert _reqs(f)["HMO_LICENCE"]["timing"] == "ON_TRIGGER"
        f.upload("OWNER_PROPERTY_RIGHT", _doc(prop, "HM LAND REGISTRY", "TITLE REGISTER", "Registered proprietor: Asha Rao"))
        f.submit()
        assert "HMO_LICENCE_MISSING" in f.body["reasonCodes"]

        f = Flow(client, user, prop)
        f.start()
        f.upload("HMO_LICENCE", _pdf("HMO LICENCE", "House in multiple occupation", "Licence holder: Asha Rao",
                                     "Premises: 99 Other Street, Town"))
        f.submit()
        assert "LICENCE_NOT_MATCHED" in f.body["reasonCodes"]

        f = Flow(client, user, prop)
        f.start()
        f.upload("HMO_LICENCE", _doc(prop, "HMO LICENCE", "House in multiple occupation", "Licence holder: Asha Rao"))
        f.submit()
        assert f.body["state"] == "VERIFIED", f.body["reasonCodes"]


class TestNeverOwnershipProof:
    def test_council_tax_bill_is_not_ownership_proof(self, client, db_session):
        user, prop = _uk_host(db_session, "eng-ctax@test.com", nation="England", postcode="M1 1AA")
        f = Flow(client, user, prop)
        f.start()
        f.upload("OWNER_PROPERTY_RIGHT", _doc(prop, "COUNCIL TAX BILL 2026/27", "Account holder: Asha Rao"))
        f.submit()
        assert f.body["state"] == "ACTION_REQUIRED"
        assert f.body["reasonCodes"][0] == "NOT_OWNERSHIP_PROOF"
        assert any(e["notOwnershipProof"] == "Council / property tax bill" for e in f.body["evidence"])

    def test_us_property_tax_record_no_longer_counts_as_ownership(self, client, db_session):
        user, prop = _uk_host(db_session, "us-tax@test.com", nation="TX", country="US", jurisdiction="US")
        f = Flow(client, user, prop)
        f.start()
        f.upload("OWNER_PROPERTY_RIGHT", _doc(prop, "COUNTY ASSESSOR", "PROPERTY TAX BILL", "Owner: Asha Rao"))
        f.submit()
        assert "NOT_OWNERSHIP_PROOF" in f.body["reasonCodes"]


class TestHostCapacities:
    def test_resident_homeowner_also_proves_they_live_there(self, client, db_session):
        user, prop = _uk_host(db_session, "eng-resident@test.com", nation="England", postcode="M1 1AA")
        f = Flow(client, user, prop)
        f.start("RESIDENT_OWNER")
        assert _reqs(f)["OWNER_OCCUPIER_RESIDENCE"]["required"] is True
        f.upload("OWNER_PROPERTY_RIGHT", _doc(prop, "HM LAND REGISTRY", "TITLE REGISTER", "Registered proprietor: Asha Rao"))
        f.submit()
        assert "RESIDENCE_PROOF_MISSING" in f.body["reasonCodes"]

        f = Flow(client, user, prop)
        f.start("RESIDENT_OWNER")
        # A utility bill is fine as proof of where someone lives (never of ownership).
        f.upload("OWNER_OCCUPIER_RESIDENCE", _doc(prop, "COUNCIL TAX BILL", "Asha Rao"))
        f.submit()
        assert f.body["state"] == "VERIFIED", f.body["reasonCodes"]

    def test_scottish_resident_homeowner_needs_no_landlord_registration(self, client, db_session):
        user, prop = _uk_host(db_session, "sct-resident@test.com", nation="Scotland")
        f = Flow(client, user, prop)
        f.start("RESIDENT_OWNER")
        assert "LANDLORD_REGISTRATION" not in _reqs(f)

    def test_estate_trustee_follows_the_entity_chain(self, client, db_session):
        user, prop = _uk_host(db_session, "eng-estate@test.com", nation="England", postcode="M1 1AA")
        f = Flow(client, user, prop)
        f.start("ESTATE_TRUSTEE")
        assert _reqs(f)["OWNER_ENTITY_AUTHORITY"]["required"] is True

    def test_master_lessee_takes_the_sublet_route(self, client, db_session):
        user, prop = _uk_host(db_session, "eng-master@test.com", nation="England", postcode="M1 1AA")
        f = Flow(client, user, prop)
        f.start("MASTER_LESSEE")
        assert f.body["route"] == "SUBLET"
        assert "SUBLET" in f.body["scopeCodes"]
        assert "TENANT_SUBLET_PERMISSION" in _reqs(f)


class TestAgents:
    def test_welsh_agent_needs_a_rent_smart_wales_licence_in_their_name(self, client, db_session):
        user, prop = _uk_host(db_session, "wls-agent@test.com", nation="Wales", postcode="CF10 1AA")
        f = Flow(client, user, prop)
        f.start("AGENT")
        f.details(principalName=MANDATE_OWNER, scopeCodes=["ADVERTISE", "RENT"])
        assert _reqs(f)["AGENT_LICENCE"]["basis"] == "LEGAL"
        f.upload("AGENT_MANDATE", _doc(prop, "LETTING AGREEMENT", f"Landlord: {MANDATE_OWNER}", "Agent: Asha Rao"))
        f.submit()
        assert "AGENT_LICENCE_MISSING" in f.body["reasonCodes"]

        f = Flow(client, user, prop)
        f.start("AGENT")
        f.upload("AGENT_LICENCE", _pdf("RENT SMART WALES", "Licence holder: Priya Menon"))
        f.submit()
        assert "REGISTRATION_NOT_MATCHED" in f.body["reasonCodes"]

        f = Flow(client, user, prop)
        f.start("AGENT")
        f.upload("AGENT_LICENCE", _pdf("RENT SMART WALES", "Licence holder: Asha Rao"))
        f.submit()
        assert f.body["state"] == "VERIFIED", f.body["reasonCodes"]


class TestHostDeclaration:
    def test_submit_records_the_versioned_declaration_and_hash(self, client, db_session):
        user, prop = _uk_host(db_session, "eng-decl@test.com", nation="England", postcode="M1 1AA")
        f = Flow(client, user, prop)
        f.start()
        assert f.body["declaration"]["version"] == svc.HOST_DECLARATION_VERSION
        assert f.body["declaration"]["signedVersion"] is None
        assert {i["code"] for i in f.body["declaration"]["items"]} >= {"ACCURACY", "LISTING_RIGHT", "LOCAL_RULES"}
        f.upload("OWNER_PROPERTY_RIGHT", _doc(prop, "HM LAND REGISTRY", "TITLE REGISTER", "Registered proprietor: Asha Rao"))
        f.submit()
        decl = f.body["declaration"]
        assert decl["signedVersion"] == svc.HOST_DECLARATION_VERSION
        assert decl["signedAt"] is not None and len(decl["hash"]) == 64


class TestDocumentListsStayCorrect:
    def test_a_stored_pack_listing_a_tax_receipt_never_offers_it_as_ownership(self, db_session):
        req = {"requirement_id": "OWNER_PROPERTY_RIGHT", "evidence_class": "PROPERTY_RIGHT",
               "documents": [{"label": "Sale deed", "keywords": ["sale deed"]},
                             {"label": "Property tax receipt", "keywords": ["property tax", "house tax"]}]}
        assert [d["label"] for d in svc.requirement_documents(req, "IN")] == ["Sale deed"]

    def test_executors_see_estate_documents_only(self, db_session):
        req = {"requirement_id": "OWNER_ENTITY_AUTHORITY", "evidence_class": "ORGANIZATION_LINK"}
        estate = [d["label"] for d in svc.requirement_documents(req, "GB-ENG", "ESTATE_TRUSTEE")]
        everything = [d["label"] for d in svc.requirement_documents(req, "GB-ENG", "REPRESENTATIVE")]
        assert estate and all("probate" in l.lower() or "trust" in l.lower() for l in estate)
        # A representative may act for a company, trust or estate.
        assert "Board resolution" in everything and set(estate) < set(everything)


class TestSmallDifferencesStillVerify:
    def test_ocr_slip_in_name_and_postcode_still_verifies(self, client, db_session):
        user, prop = _uk_host(db_session, "eng-slip@test.com", nation="England", postcode="M1 1AA")
        f = Flow(client, user, prop)
        f.start()
        # "ASHA RA0" (zero for O) wouldn't pass: 3-letter words must match exactly.
        f.upload("OWNER_PROPERTY_RIGHT", _pdf("HM LAND REGISTRY", "TITLE REGISTER", "Registered proprietor: ASHA RAO",
                                              f"Property: {ADDRESS}, Town M1 1AB"))
        f.submit()
        assert f.body["state"] == "VERIFIED", f.body["reasonCodes"]
