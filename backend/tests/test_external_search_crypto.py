"""ZR-AI-SEARCH-001 Section 12 / Section 13 -- encryption at rest + relay
contact masking run strictly through the crypto helpers.

* Writers persist contact data as Fernet tokens (never plaintext in the
  *_encrypted columns).
* Readers transparently recover the plaintext; legacy plaintext written before
  encryption is tolerated; a corrupt token still surfaces as an error.
* Relay direct-contact release derives masked/forwarding handles from the
  decrypted value -- never the raw address.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest
from cryptography.fernet import InvalidToken

from app.core.field_encryption import encrypt_text
from app.models.external_search import ExternalOpportunity, SourceRightRegistry
from app.services.external_search_crypto import decrypt_contact, encrypt_optional
from app.services.relay_messaging import relay


class TestContactCryptoHelpers:
    def test_blank_values_never_become_ciphertext(self):
        assert encrypt_optional(None) is None
        assert encrypt_optional("   ") is None

    def test_encrypt_then_decrypt_round_trips(self):
        token = encrypt_optional("landlord@example.com")
        assert token is not None
        assert token != "landlord@example.com"
        assert decrypt_contact(token) == "landlord@example.com"

    def test_legacy_plaintext_is_returned_as_is(self):
        # Written before this module existed -- a read decision must not break.
        assert decrypt_contact("room-owner@example.net") == "room-owner@example.net"

    def test_empty_column_reads_as_empty(self):
        assert decrypt_contact("") == ""
        assert decrypt_contact(None) == ""

    def test_corrupt_token_still_surfaces(self):
        token = encrypt_text("secret-address-42")
        # Flip a ciphertext byte in the decoded payload (not the version byte)
        # and re-encode: still valid urlsafe base64, still Fernet-shaped, wrong
        # HMAC, so it must raise -- never be silently returned as "legacy".
        # (Editing a base64 character directly can land on padding bits and
        # leave the payload unchanged.)
        raw = bytearray(base64.urlsafe_b64decode(token))
        raw[30] ^= 0x01
        tampered = base64.urlsafe_b64encode(bytes(raw)).decode()
        assert tampered != token
        with pytest.raises(InvalidToken):
            decrypt_contact(tampered)

    def test_non_fernet_blob_is_legacy_plaintext_not_an_error(self):
        # Distinct from a tampered token: the shape pre-check sends legacy
        # values through unchanged rather than raising.
        assert decrypt_contact("room-owner@example.net") == "room-owner@example.net"


def _seed_partner_source(db):
    db.add(
        SourceRightRegistry(
            source_id="pf-1",
            source_name_internal="Partner Feed Co",
            territories=["GB"],
            acquisition_mode="PARTNER_FEED",
            legal_approved=True,
            security_approved=True,
            status="ACTIVE",
            display_permitted=True,
            masking_permitted=True,
            permitted_fields=["provider_name", "approx_location", "provider_contact", "source_url"],
            cache_ttl_seconds=600,
            source_brand_display_rule="Partner Feed Co",
        )
    )
    db.flush()


class TestPartnerFeedEncryptionAtRest:
    def test_contact_fields_are_stored_encrypted_only(self, db_session):
        from app.services.partner_feeds import partner_feed_adapter

        _seed_partner_source(db_session)
        created = partner_feed_adapter.ingest_feed(
            db_session,
            source_id="pf-1",
            items=[
                {
                    "external_id": "PF-0001",
                    "provider_name": "Jane Landlord",
                    "approx_location": "Leeds city centre",
                    "provider_contact": "jane@partner.example",
                    "source_url": "https://partner.example/rooms/PF-0001",
                }
            ],
            correlation_id="c-feed-crypto",
        )
        assert len(created) == 1
        opp = created[0]

        stored_contact: str = opp.provider_contact_encrypted
        stored_url: str = opp.source_url_encrypted
        assert stored_contact and stored_contact != "jane@partner.example"
        assert stored_url and stored_url != "https://partner.example/rooms/PF-0001"
        # raw_data keeps the original but the *_encrypted columns never do.
        assert opp.raw_data["provider_contact"] == "jane@partner.example"
        # reader recovers both
        assert decrypt_contact(stored_contact) == "jane@partner.example"
        assert decrypt_contact(stored_url) == "https://partner.example/rooms/PF-0001"

    def test_unpermitted_contact_field_is_never_copied(self, db_session):
        from app.services.partner_feeds import partner_feed_adapter

        db_session.add(
            SourceRightRegistry(
                source_id="pf-min",
                source_name_internal="Minimal",
                territories=["GB"],
                acquisition_mode="PARTNER_FEED",
                legal_approved=True,
                security_approved=True,
                status="ACTIVE",
                display_permitted=True,
                permitted_fields=["provider_name"],
                cache_ttl_seconds=600,
            )
        )
        db_session.flush()
        created = partner_feed_adapter.ingest_feed(
            db_session,
            source_id="pf-min",
            items=[
                {
                    "external_id": "PF-0002",
                    "provider_name": "Sam",
                    "provider_contact": "sam@example.com",
                }
            ],
        )
        assert len(created) == 1
        assert created[0].provider_contact_encrypted is None


def _accepted_opportunity(db, *, contact=None, contact_encrypted=None):
    opp = ExternalOpportunity(
        external_opportunity_id="relay-crypto-1",
        source_id="s1",
        status="PROVIDER_ACCEPTED",
        verification_status="VERIFICATION_IN_PROGRESS",
        provider_name="Landlord",
        approx_location="Cardiff centre",
        provider_contact_encrypted=(
            contact_encrypted if contact_encrypted is not None else encrypt_text(contact or "")
        ),
    )
    opp.intro_unlocked_at = datetime.now(timezone.utc)
    db.add(opp)
    db.flush()
    return opp


class TestRelayContactMasking:
    def test_release_mask_derived_from_decrypted_contact(self, db_session):
        opp = _accepted_opportunity(db_session, contact="landlord@example.com")
        release = relay.request_direct_contact_release(
            db_session, opportunity_id=opp.id, actor_topic="RENTER", correlation_id="c-r1"
        )
        relay.request_direct_contact_release(
            db_session, opportunity_id=opp.id, actor_topic="PROVIDER", correlation_id="c-r1"
        )
        assert release.released_mask == relay.mask_email("landlord@example.com")
        assert release.released_mask.endswith("@relay.zoikorooms.com")
        assert "landlord@example.com" not in release.released_mask

    def test_legacy_plaintext_contact_release_still_masks(self, db_session):
        opp = _accepted_opportunity(db_session, contact_encrypted="landlord@legacy.example")
        release = relay.request_direct_contact_release(
            db_session, opportunity_id=opp.id, actor_topic="RENTER"
        )
        relay.request_direct_contact_release(
            db_session, opportunity_id=opp.id, actor_topic="PROVIDER"
        )
        assert release.released_mask == relay.mask_email("landlord@legacy.example")
        assert "legacy.example" not in release.released_mask