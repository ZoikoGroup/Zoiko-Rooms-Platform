"""One-time reset for manual multi-account E2E testing: wipes every row in the
dev database (schema stays, via a full DROP/CREATE SCHEMA + alembic upgrade),
then seeds exactly three named accounts plus the minimum baseline data needed
to actually walk the full renter journey (search -> apply -> offer -> agreement
-> sign) end to end without hitting an empty-state or fail-closed jurisdiction
gate.

Accounts seeded:
  - venky   | admin        | admin dashboard, day-to-day operations
  - indra   | super_admin  | admin dashboard, full platform authority
  - anil    | customer     | renter-facing account (/account/login)

Baseline data seeded (England jurisdiction, deliberately -- see
app/services/agreement_profile.py:SUPPORTED_JURISDICTION -- the only
jurisdiction this build's agreement engine can currently carry all the way
through to a signed agreement, via the native Mode A path -- an active
MarketRelease plus the self-healing default ClauseDefinition registry, NOT
an AgreementFormTemplate, which is only for the B/C/D non-native modes):
  - 1 Property + 1 Room (England, London)
  - 1 published Listing on that room (GBP)
  - 1 active MarketRelease for "England" (required for resolve_agreement_profile
    to resolve anything at all -- the default clause registry self-seeds the
    first time anything touches it, no manual seeding needed there)
  - 1 verified AuthorityRecord + 1 resolved OccupancyClassification for the
    room (jurisdiction_gates_pass's other two structural requirements)
  - 1 MarketPolicyPack for "England" (deposit/sublet/verification policy --
    occupancy_eligibility_required=True, matching England's real
    right-to-rent law)

Deliberately NOT seeded (these are the two real, renter-side gates this
setup is meant to exercise manually, not bypass):
  - Anil's identity verification (submit + admin-approve via the UI)
  - Anil's OCCUPANCY_ELIGIBILITY check for England (admin-decide via the UI)

Run with: python reset_and_seed_demo.py
"""
import subprocess
import sys
from datetime import date, datetime, timezone

import sqlalchemy as sa

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal, engine
from app.models.admin_user import AdminUser
from app.models.authority_record import AuthorityRecord
from app.models.guest import Guest
from app.models.listing import Listing
from app.models.market_policy import MarketPolicyPack
from app.models.market_release import MarketRelease
from app.models.occupancy_classification import OccupancyClassification
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from app.models.user_account import UserAccount

DEMO_PASSWORD = "Demo@12345"


def reset_schema() -> None:
    print("Dropping and recreating the public schema...")
    with engine.connect() as conn:
        conn.execute(sa.text("DROP SCHEMA public CASCADE"))
        conn.execute(sa.text("CREATE SCHEMA public"))
        conn.commit()
    print("Schema wiped. Applying migrations (alembic upgrade head)...")
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"])
    if result.returncode != 0:
        raise SystemExit("alembic upgrade head failed -- aborting before seeding.")
    print("Migrations applied.")


def seed() -> None:
    db = SessionLocal()
    try:
        # -- Admin accounts --
        venky = AdminUser(
            email="venky@zoikorooms.com", hashed_password=hash_password(DEMO_PASSWORD),
            full_name="Venky", phone="+91 90000 00001", role="admin",
            is_active=True, approval_status="approved",
        )
        indra = AdminUser(
            email="indra@zoikorooms.com", hashed_password=hash_password(DEMO_PASSWORD),
            full_name="Indra", phone="+91 90000 00002", role="super_admin",
            is_active=True, approval_status="approved",
        )
        # crud/payment_provider.py:get_system_admin resolves settings.seed_admin_email
        # as the actor a real Stripe webhook is attributed to -- must exist as a
        # real row for the payment-provider callback path (real or simulated) to
        # work at all, independent of the 3 named demo accounts above.
        system_admin = AdminUser(
            email=settings.seed_admin_email, hashed_password=hash_password(DEMO_PASSWORD),
            full_name="System", phone="", role="super_admin",
            is_active=True, approval_status="approved",
        )
        db.add_all([venky, indra, system_admin])
        db.flush()

        # -- Customer/renter account --
        anil_party = Party(party_type="renter", status="active", jurisdiction="England")
        db.add(anil_party)
        db.flush()

        anil_user = UserAccount(
            email="anil@zoikorooms.com", hashed_password=hash_password(DEMO_PASSWORD),
            full_name="Anil", phone="+91 90000 00003", party_id=anil_party.id,
            is_active=True, email_verified=True,
        )
        db.add(anil_user)
        db.flush()

        anil_guest = Guest(
            id="G-ANIL", name="Anil", email=anil_user.email, phone=anil_user.phone,
            avatar="", location="London, England", joined_at=date.today(), status="active",
            user_account_id=anil_user.id,
        )
        db.add(anil_guest)

        # -- Baseline property/room/listing (England, so the agreement engine
        # can carry a renter all the way through signing, not just to
        # "routed to manual review") --
        host_party = Party(party_type="provider", status="active", jurisdiction="England")
        db.add(host_party)
        db.flush()

        prop = Property(
            owner_party_id=host_party.id, address="10 Baker Street, London",
            city="London", status="active", jurisdiction_code="England",
        )
        db.add(prop)
        db.flush()

        room = Room(
            property_id=prop.id, room_type="Private room", size=140, has_ensuite=False,
            status="active", max_occupants=1,
        )
        db.add(room)
        db.flush()

        listing = Listing(
            id="L-DEMO-1", slug="demo-baker-street-room", name="Demo Room, Baker Street",
            property_type="private_room", room_type="Private room", city="London",
            location="Baker Street, London", price_per_night=45.0, currency="GBP",
            guests=1, bedrooms=1, bathrooms=1, size=140,
            images=[], amenities=["Free WiFi", "Furnished"], tags=[],
            description="Seeded demo listing for end-to-end manual testing.",
            min_stay_nights=30, state="PUBLISHED", published_at=datetime.now(timezone.utc),
            party_id=host_party.id, room_id=room.id,
        )
        db.add(listing)
        db.flush()

        # -- Market Release for England, active -- resolve_agreement_profile
        # fails closed (returns None) without this, regardless of anything
        # else being seeded correctly. The default ClauseDefinition registry
        # self-seeds the first time resolve_agreement_profile runs.
        release = MarketRelease(
            jurisdiction="England", status="active", min_stay_nights=30, manual_agreement_only=False,
        )
        db.add(release)
        db.flush()
        listing.market_release_id = release.id

        # -- Authority record + occupancy classification for the room --
        # jurisdiction_gates_pass's other two structural requirements,
        # independent of the market release above.
        db.add(AuthorityRecord(
            party_id=host_party.id, room_id=room.id, authority_type="LANDLORD_REGISTRATION",
            evidence_ref="seed-demo-authority-1", status="verified", verified_at=datetime.now(timezone.utc),
        ))
        db.add(OccupancyClassification(
            room_id=room.id, classification="PRIVATE_ROOM_SHARE", confidence=0.95,
            evidence_ref="seed-demo-classification-1", jurisdiction="England", rule_version=1,
            review_state="RESOLVED",
        ))

        # -- Market Policy Pack for England (deposit/sublet/verification
        # policy, REVIEW_REQUIRED confidence -- same honesty convention as
        # every other pack this platform ships) --
        pack = MarketPolicyPack(
            jurisdiction_code="England", version=1, effective_from=date(2026, 1, 1),
            confidence="REVIEW_REQUIRED",
            legal_source_note="Seeded placeholder for local E2E testing -- not verified legal research.",
            occupancy_eligibility_required=True,
            occupancy_eligibility_method_note="Online share-code lookup or an accepted manual document check.",
        )
        db.add(pack)
        db.commit()

        print("Seed complete.")
        print(f"  admin        venky@zoikorooms.com / {DEMO_PASSWORD}")
        print(f"  super_admin  indra@zoikorooms.com / {DEMO_PASSWORD}")
        print(f"  customer     anil@zoikorooms.com  / {DEMO_PASSWORD}")
        print(f"  listing      {listing.id} ({listing.name}) -- published, England, GBP")
    finally:
        db.close()


if __name__ == "__main__":
    reset_schema()
    seed()
