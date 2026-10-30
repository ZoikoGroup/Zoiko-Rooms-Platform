from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.host_sublet_policy import HostSubletPolicy


def get_policy(db: Session, party_id: int) -> HostSubletPolicy:
    """The host's saved sublet rules, or an unsaved all-off default."""
    row = db.scalar(select(HostSubletPolicy).where(HostSubletPolicy.party_id == party_id))
    return row or HostSubletPolicy(party_id=party_id, min_notice_enabled=False, min_notice_days=14)


def save_policy(db: Session, party_id: int, *, min_notice_enabled: bool, min_notice_days: int) -> HostSubletPolicy:
    row = db.scalar(select(HostSubletPolicy).where(HostSubletPolicy.party_id == party_id))
    if row is None:
        row = HostSubletPolicy(party_id=party_id)
        db.add(row)
    row.min_notice_enabled = min_notice_enabled
    row.min_notice_days = min_notice_days
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row
