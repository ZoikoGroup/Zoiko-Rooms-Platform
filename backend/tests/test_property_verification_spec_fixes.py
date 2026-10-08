"""ZR-PROPERTY-VERIFY-001 rules the existing property verification broke:
public location privacy (Section 10), invalidation on a material address
change (Section 13.3), no approval on map evidence alone (Section 8 / P0 #2)
and evidence encrypted at rest (P0 #11)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud import property_verification as crud
from app.crud.listing import to_public_listing_read
from app.models.domain_event import DomainEvent
from app.models.listing import Listing
from app.services import geocoding
from tests.conftest import _make_admin, auth_user_cookie
from tests.test_property_verification import _declare, _make_host_with_room


def _found(address, city, landmark, jurisdiction_code):
    return geocoding.GeocodeResult(geocoding.FOUND, "test", f"{address}, {city}", latitude=12.971234, longitude=77.594567,
                                   formatted_address=f"{address}, {city}", precision="HOUSE", country_code="IN")


def _aged(db: Session, record):
    record.created_at = datetime.now(timezone.utc) - timedelta(seconds=crud.AUTO_VERIFY_DELAY_SECONDS + 5)
    db.commit()
    crud.verify_due_property_verifications(db, room_id=record.room_id)
    db.refresh(record)
    return record


@pytest.fixture()
def host_room(db_session: Session, monkeypatch):
    _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
    monkeypatch.setattr(geocoding, "geocode_address", _found)
    return _make_host_with_room(db_session, email="pv-spec-host@test.com")


class TestMapEvidenceAloneNeverVerifies:
    def test_a_map_hit_with_unreadable_evidence_goes_to_a_reviewer(self, db_session, host_room):
        user, room = host_room
        record = _declare(db_session, user, room, evidence_ref="photo of deed")  # nothing to compare
        assert record.geocode_status == geocoding.FOUND
        assert (record.name_matched, record.address_matched) == (None, None)
        assert record.verifier_notes == crud.REVIEW_PENDING_NOTE
        assert _aged(db_session, record).status == "pending"
        assert crud.get_valid_property_verification_for_room(db_session, room.id) is None

    def test_a_map_hit_with_matching_evidence_auto_verifies(self, db_session, host_room):
        user, room = host_room
        record = _declare(db_session, user, room, evidence_ref="Property Address: 1 Verify Way, Bengaluru")
        assert record.address_matched is True
        assert _aged(db_session, record).status == "verified"

    def test_a_row_queued_for_auto_verify_without_a_match_is_never_verified(self, db_session, host_room):
        """Defence in depth: the auto-verify sweep itself also requires a match."""
        user, room = host_room
        record = _declare(db_session, user, room, evidence_ref="photo of deed")
        record.verifier_notes = crud.AUTO_VERIFY_PENDING_NOTE
        db_session.commit()
        assert _aged(db_session, record).status == "pending"


class TestAddressChangeInvalidates:
    def _verified(self, db_session, host_room):
        user, room = host_room
        record = _aged(db_session, _declare(db_session, user, room,
                                            evidence_ref="Property Address: 1 Verify Way, Bengaluru"))
        assert record.status == "verified"
        return user, room, record

    def _update(self, client, user, room, **changes):
        body = {"address": room.property.address, "city": room.property.city, "landmark": room.property.landmark,
                "jurisdictionCode": room.property.jurisdiction_code, **changes}
        return client.put(f"/api/users/hosting/properties/{room.property_id}", json=body, cookies=auth_user_cookie(user),
                          headers={"If-Match": str(room.property.location_version)})

    def test_a_new_address_revokes_the_verification_and_keeps_history(self, client, db_session, host_room):
        user, room, record = self._verified(db_session, host_room)
        r = self._update(client, user, room, address="99 Another Road")
        assert r.status_code == 200, r.text
        db_session.refresh(record)
        assert record.status == "revoked" and record.verifier_notes == crud.ADDRESS_CHANGED_NOTE
        assert crud.get_valid_property_verification_for_room(db_session, room.id) is None
        event = db_session.query(DomainEvent).filter_by(event_type="PROPERTY_VERIFICATION_INVALIDATED").one()
        assert (event.previous_state, event.new_state, event.payload["reasonCode"]) == ("verified", "revoked", "ADDRESS_CHANGED")

    def test_a_cosmetic_edit_keeps_the_verification(self, client, db_session, host_room):
        user, room, record = self._verified(db_session, host_room)
        r = self._update(client, user, room, address="  1  verify way, ")
        assert r.status_code == 200, r.text
        db_session.refresh(record)
        assert record.status == "verified"

    def test_material_change_rules(self):
        base = {"address": "1 Verify Way", "city": "Bengaluru", "landmark": None, "jurisdiction_code": "IN"}
        assert not crud.is_material_address_change(base, {**base, "address": "1 VERIFY WAY,"})
        assert crud.is_material_address_change(base, {**base, "city": "Mysuru"})
        assert crud.is_material_address_change(base, {**base, "landmark": "Near the lake"})


class TestPublicLocationPrivacy:
    def test_public_listings_never_carry_the_street_address_or_exact_pin(self, db_session, host_room):
        user, room = host_room
        listing = Listing(name="Quiet room", room_type="private_room", city="Bengaluru", location="1 Verify Way",
                          latitude=12.971234, longitude=77.594567, price_per_night=40, guests=1, room_id=room.id,
                          party_id=user.party_id, slug="quiet-room-spec", id="lst-spec-privacy")
        db_session.add(listing)
        db_session.commit()
        public = to_public_listing_read(listing)
        assert public.location == "" and public.city == "Bengaluru"
        assert (public.latitude, public.longitude) == (12.97, 77.59)  # ~1 km cell, never the exact pin
        assert "1 Verify Way" not in public.model_dump_json()


class TestEvidenceEncryptedAtRest:
    def test_the_stored_file_is_encrypted_and_reads_back_decrypted(self, tmp_path, monkeypatch):
        import asyncio
        import io

        from fastapi import UploadFile

        from app.core import property_verification_uploads as uploads

        monkeypatch.setattr(settings, "property_verification_upload_dir", str(tmp_path))
        original = b"%PDF-1.4\n" + b"Property Address: 1 Verify Way, Bengaluru\n" * 50
        stored, _name, content_type, _size, _sha = asyncio.run(uploads.save_property_verification_document(
            UploadFile(file=io.BytesIO(original), filename="deed.pdf")))
        assert stored.endswith(".enc") and content_type == "application/pdf"
        on_disk = (tmp_path / stored).read_bytes()
        assert b"Verify Way" not in on_disk and b"%PDF" not in on_disk
        assert uploads.read_property_verification_document(stored) == original

    def test_files_saved_before_encryption_still_read(self, tmp_path, monkeypatch):
        from app.core import property_verification_uploads as uploads

        monkeypatch.setattr(settings, "property_verification_upload_dir", str(tmp_path))
        (tmp_path / "legacy.pdf").write_bytes(b"%PDF-1.4 legacy")
        assert uploads.read_property_verification_document("legacy.pdf") == b"%PDF-1.4 legacy"
        assert uploads.read_property_verification_document("missing.pdf.enc") is None
