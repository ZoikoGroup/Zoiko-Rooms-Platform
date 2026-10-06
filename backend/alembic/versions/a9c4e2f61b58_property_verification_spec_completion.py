"""property verification spec completion

Revision ID: a9c4e2f61b58
Revises: f7b3d1e8c925
Create Date: 2026-10-06 21:00:00.000000

"""
import json
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9c4e2f61b58'
down_revision: Union[str, None] = 'f7b3d1e8c925'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ORDER = ["address_line_1", "address_line_2", "locality", "administrative_area", "postal_code"]
_LABELS = {
    "*": {"address_line_1": "Address line 1", "address_line_2": "Address line 2", "locality": "City / locality",
          "administrative_area": "Region / state", "postal_code": "Postal code"},
    "GB": {"address_line_1": "Address line 1", "address_line_2": "Address line 2", "locality": "Town / city",
           "administrative_area": "County (optional)", "postal_code": "Postcode"},
    "IN": {"address_line_1": "House / flat no., street", "address_line_2": "Area / locality / village",
           "locality": "City / town", "administrative_area": "State", "postal_code": "PIN code"},
    "US": {"address_line_1": "Street address", "address_line_2": "Apt, suite, unit (optional)", "locality": "City",
           "administrative_area": "State", "postal_code": "ZIP code"},
}


def upgrade() -> None:
    """ZR-PROPERTY-VERIFY-001 completion: per-country address field order and
    labels (Section 17), evidence retention (Section 13), reviewer case
    assignment (Section 16), expiring-soon notice (Section 7), per-adjustment
    pin history (Section 6.4), evidence tamper signal + soft delete
    (Section 14) and the host's local-script address (Section 20)."""
    op.add_column("property_regulatory_packs", sa.Column("address_field_order", sa.JSON(), nullable=True))
    op.add_column("property_regulatory_packs", sa.Column("address_labels", sa.JSON(), nullable=True))
    op.add_column("property_location_verifications", sa.Column("pin_adjust_history", sa.JSON(), nullable=True))
    op.add_column("property_location_verifications", sa.Column("assigned_admin_id", sa.Integer(), nullable=True))
    op.add_column("property_location_verifications",
                  sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("property_location_verifications",
                  sa.Column("expiring_notified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("fk_plv_assigned_admin", "property_location_verifications", "admin_users",
                          ["assigned_admin_id"], ["id"])
    op.add_column("property_location_evidence", sa.Column("tamper_signal", sa.Boolean(), nullable=False,
                                                          server_default=sa.false()))
    op.add_column("property_location_evidence", sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("properties", sa.Column("address_local", sa.String(length=600), nullable=False, server_default=""))

    # Active packs get the field order / labels and a retention period as a
    # NEW version (history kept), unless an admin already set them.
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT * FROM property_regulatory_packs WHERE active = :t"), {"t": True}).fetchall()
    now = datetime.now(timezone.utc)
    for row in rows:
        data = dict(row._mapping)
        pack_id = data.pop("id")
        data["version"] += 1
        data["address_field_order"] = json.dumps(_ORDER)
        data["address_labels"] = json.dumps(_LABELS.get(data["country_code"], _LABELS["*"]))
        data["evidence_retention_days"] = data.get("evidence_retention_days") or 730
        for key in ("required_address_fields", "accepted_evidence_types", "unit_required_for"):
            if not isinstance(data[key], str):
                data[key] = json.dumps(data[key] or [])
        data["created_at"] = data["updated_at"] = now
        bind.execute(sa.text("UPDATE property_regulatory_packs SET active = :f WHERE id = :i"),
                     {"f": False, "i": pack_id})
        columns = list(data)
        json_cols = {"required_address_fields", "accepted_evidence_types", "unit_required_for", "address_field_order",
                     "address_labels"}
        values = ", ".join(f"CAST(:{c} AS JSON)" if c in json_cols else f":{c}" for c in columns)
        bind.execute(sa.text(f"INSERT INTO property_regulatory_packs ({', '.join(columns)}) VALUES ({values})"), data)


def downgrade() -> None:
    op.drop_column("properties", "address_local")
    op.drop_column("property_location_evidence", "removed_at")
    op.drop_column("property_location_evidence", "tamper_signal")
    op.drop_constraint("fk_plv_assigned_admin", "property_location_verifications", type_="foreignkey")
    op.drop_column("property_location_verifications", "expiring_notified_at")
    op.drop_column("property_location_verifications", "assigned_at")
    op.drop_column("property_location_verifications", "assigned_admin_id")
    op.drop_column("property_location_verifications", "pin_adjust_history")
    op.drop_column("property_regulatory_packs", "address_labels")
    op.drop_column("property_regulatory_packs", "address_field_order")
