"""seed England market policy pack

Revision ID: 294dc9f9f823
Revises: 7b6ac63867c8
Create Date: 2026-09-17 16:27:44.704592

"""
from datetime import date, datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '294dc9f9f823'
down_revision: Union[str, None] = '7b6ac63867c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # England is this platform's actual supported jurisdiction
    # (services/agreement_profile.py:SUPPORTED_JURISDICTION) and, since the
    # jurisdiction/currency default-value fix, the default for every new
    # Property/Party/Listing -- but market_policy_packs (0027) only ever
    # seeded "IN". Every deposit/offer-terms/sublet/screening/termination
    # action for a new, otherwise-fully-set-up England listing was failing
    # closed with "No market policy pack configured for jurisdiction
    # 'England'" because this row never existed. occupancy_eligibility_*
    # set per models/market_policy.py's own comment: "only England's row
    # (seeded separately) sets this True" -- England's real mechanism is a
    # GOV.UK online share code or an accepted manual document check
    # (Section 34), never implemented as a live Home Office API integration
    # here, so it's recorded as a manual, admin-checked note.
    policy_packs = sa.table(
        "market_policy_packs",
        sa.column("jurisdiction_code", sa.String),
        sa.column("version", sa.Integer),
        sa.column("effective_from", sa.Date),
        sa.column("confidence", sa.String),
        sa.column("legal_source_note", sa.String),
        sa.column("occupancy_eligibility_required", sa.Boolean),
        sa.column("occupancy_eligibility_method_note", sa.String),
        sa.column("created_at", sa.DateTime),
    )
    op.bulk_insert(
        policy_packs,
        [
            {
                "jurisdiction_code": "England",
                "version": 1,
                "effective_from": date(2026, 1, 1),
                "confidence": "REVIEW_REQUIRED",
                "legal_source_note": (
                    "Placeholder MVP values, not verified legal research. Every field left "
                    "unset here resolves to this model's own column default (see "
                    "models/market_policy.py), the same REVIEW_REQUIRED-honesty posture as "
                    "every other market pack in this build. Must be reviewed against actual "
                    "UK/England statutory requirements before this governs real money or real "
                    "tenancies."
                ),
                "occupancy_eligibility_required": True,
                "occupancy_eligibility_method_note": (
                    "Right-to-rent check: GOV.UK online share code lookup or an accepted "
                    "manual document check, recorded manually by an admin -- no live Home "
                    "Office API integration exists in this build."
                ),
                "created_at": datetime.now(timezone.utc),
            }
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM market_policy_packs WHERE jurisdiction_code = 'England' AND version = 1")
