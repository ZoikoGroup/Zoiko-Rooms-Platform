"""property evidence types per spec section 9

Revision ID: 4a26522998f0
Revises: ba4bff0dd48a
Create Date: 2026-10-05 16:27:34.122549

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4a26522998f0'
down_revision: Union[str, None] = 'ba4bff0dd48a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SPEC_TYPES = ["LAND_REGISTRY_RECORD", "PROPERTY_TAX_RECORD", "BUILDING_UNIT_RECORD", "TITLE_DEED",
              "MORTGAGE_INSURANCE_STATEMENT", "UTILITY_BILL"]


def upgrade() -> None:
    """ZR-PROPERTY-VERIFY-001 Section 9: accepted evidence is exactly the
    spec's list. Drops OTHER_OFFICIAL_DOCUMENT from stored packs; India gets
    the full Section 9 list."""
    import json

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, country_code, accepted_evidence_types FROM property_regulatory_packs")).fetchall()
    for row in rows:
        types = row.accepted_evidence_types
        if isinstance(types, str):
            types = json.loads(types)
        types = [t for t in (types or []) if t in SPEC_TYPES]
        if row.country_code in ("IN", "*"):
            types = list(SPEC_TYPES)
        bind.execute(sa.text("UPDATE property_regulatory_packs SET accepted_evidence_types = :t WHERE id = :i"),
                     {"t": json.dumps(types), "i": row.id})


def downgrade() -> None:
    pass  # the removed type isn't restored
