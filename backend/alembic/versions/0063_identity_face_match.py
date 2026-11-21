"""Identity is now a face match on this server: existing country packs that named the retired outside provider say so

Data only -- no schema change. The retired provider's tables (identity_provider_events, identity_provider_reason_mappings,
identity_go_live_gates, identity_provider_checks) and the legacy provider_* columns are deliberately kept so existing
rows and audit history are not lost; nothing reads or writes them any more.

Revision ID: 0063_identity_face_match
Revises: 0062_cotenant_host_first
Create Date: 2026-11-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "0063_identity_face_match"
down_revision: Union[str, None] = "0062_cotenant_host_first"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE identity_regulatory_packs SET document_provider_code = 'face_match' WHERE document_provider_code = 'veriff'")


def downgrade() -> None:
    op.execute("UPDATE identity_regulatory_packs SET document_provider_code = 'veriff' WHERE document_provider_code = 'face_match'")
