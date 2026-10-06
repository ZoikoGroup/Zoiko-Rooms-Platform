"""authority per-document lists and detected document

Revision ID: f7b3d1e8c925
Revises: e5a2c0d9b713
Create Date: 2026-10-06 18:00:00.000000

"""
import json
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7b3d1e8c925'
down_revision: Union[str, None] = 'e5a2c0d9b713'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """ZR-AUTHORITY-002 Section 4: each requirement lists its accepted
    documents (name + identifying phrases); OCR records which one an upload
    is. Active packs without per-document lists get the catalogue for their
    country as a NEW version (earlier versions stay as history)."""
    from app.services.authority_service import default_documents

    op.add_column("authority_evidence", sa.Column("detected_document", sa.String(length=120), nullable=False,
                                                  server_default=""))
    bind = op.get_bind()
    columns = ("country_code", "country_name", "version", "requirements", "terminology", "sublet_consent_required",
               "co_owner_consent_required", "parallel_identity_intake", "default_validity_days", "expiring_soon_days",
               "evidence_retention_days", "listing_control")
    rows = bind.execute(sa.text(
        f"SELECT id, {', '.join(columns)} FROM authority_regulatory_packs WHERE active = :t"), {"t": True}).fetchall()
    now = datetime.now(timezone.utc)
    for row in rows:
        data = dict(row._mapping)
        requirements = data["requirements"]
        if isinstance(requirements, str):
            requirements = json.loads(requirements)
        if all(r.get("documents") for reqs in (requirements or {}).values() for r in reqs):
            continue
        refreshed = {}
        for route, reqs in (requirements or {}).items():
            refreshed[route] = []
            for req in reqs:
                docs = req.get("documents") or default_documents(data["country_code"], req["requirement_id"])
                refreshed[route].append({
                    **req, "documents": docs, "accepted_examples": [d["label"] for d in docs],
                    "keywords": list(dict.fromkeys(k for d in docs for k in d["keywords"])),
                })
        terminology = data["terminology"]
        if isinstance(terminology, str):
            terminology = json.loads(terminology)
        bind.execute(sa.text("UPDATE authority_regulatory_packs SET active = :f WHERE id = :i"),
                     {"f": False, "i": data["id"]})
        bind.execute(sa.text(
            "INSERT INTO authority_regulatory_packs (country_code, country_name, version, active, requirements, "
            "terminology, sublet_consent_required, co_owner_consent_required, parallel_identity_intake, "
            "default_validity_days, expiring_soon_days, evidence_retention_days, listing_control, created_at, "
            "updated_at) VALUES (:country_code, :country_name, :version, :active, CAST(:requirements AS JSON), "
            "CAST(:terminology AS JSON), :sublet_consent_required, :co_owner_consent_required, "
            ":parallel_identity_intake, :default_validity_days, :expiring_soon_days, :evidence_retention_days, "
            ":listing_control, :now, :now)"),
            {**{k: data[k] for k in columns if k not in ("version", "requirements", "terminology")},
             "version": data["version"] + 1, "active": True, "requirements": json.dumps(refreshed),
             "terminology": json.dumps(terminology or {}), "now": now})


def downgrade() -> None:
    op.drop_column("authority_evidence", "detected_document")
