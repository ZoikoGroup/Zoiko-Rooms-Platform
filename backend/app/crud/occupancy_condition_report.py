"""Section 9 gap: the real, structured move-in/move-out condition report
this codebase previously had nowhere at all -- see
models/occupancy_condition_report.py's own docstring for exactly what was
missing and why this is many-rows-per-report_type, not a single event."""

from __future__ import annotations

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dispute_evidence_uploads import delete_dispute_evidence_file, save_dispute_evidence_file
from app.models.admin_user import AdminUser
from app.models.guest import Guest
from app.models.occupancy import Occupancy
from app.models.occupancy_condition_report import CONDITION_RATINGS, CONDITION_REPORT_TYPES, OccupancyConditionReportItem
from app.schemas.occupancy import ConditionReportItemRead


def to_condition_report_item_read(item: OccupancyConditionReportItem) -> ConditionReportItemRead:
    return ConditionReportItemRead(
        id=item.id, occupancy_id=item.occupancy_id, report_type=item.report_type, area=item.area,
        condition_rating=item.condition_rating, notes=item.notes, original_filename=item.original_filename,
        content_type=item.content_type, size_bytes=item.size_bytes,
        recorded_by_guest_id=item.recorded_by_guest_id, recorded_by_admin_id=item.recorded_by_admin_id,
        created_at=item.created_at, has_file=bool(item.stored_filename),
    )


async def add_condition_report_item(
    db: Session, occupancy: Occupancy, *,
    report_type: str, area: str = "", condition_rating: str | None = None, notes: str = "",
    file: UploadFile | None = None, guest: Guest | None = None, admin: AdminUser | None = None,
) -> OccupancyConditionReportItem:
    if occupancy.status in ("ENDED", "CANCELLED"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot add a condition report item to a closed-out occupancy")
    if report_type not in CONDITION_REPORT_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"report_type must be one of {CONDITION_REPORT_TYPES}")
    if condition_rating is not None and condition_rating not in CONDITION_RATINGS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"condition_rating must be one of {CONDITION_RATINGS}")
    if (guest is None) == (admin is None):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Exactly one of guest or admin must record this item")
    if file is None and not notes.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Provide a photo or notes -- a condition report item cannot be empty")

    stored_filename = original_filename = content_type = ""
    size_bytes = 0
    if file is not None:
        stored_filename, original_filename, content_type, size_bytes, _sha256 = await save_dispute_evidence_file(file)

    item = OccupancyConditionReportItem(
        occupancy_id=occupancy.id,
        report_type=report_type,
        area=area.strip(),
        condition_rating=condition_rating,
        notes=notes.strip(),
        stored_filename=stored_filename or None,
        original_filename=original_filename,
        content_type=content_type,
        size_bytes=size_bytes,
        recorded_by_guest_id=guest.id if guest is not None else None,
        recorded_by_admin_id=admin.id if admin is not None else None,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def list_condition_report_items(db: Session, occupancy: Occupancy, *, report_type: str | None = None) -> list[OccupancyConditionReportItem]:
    query = select(OccupancyConditionReportItem).where(OccupancyConditionReportItem.occupancy_id == occupancy.id)
    if report_type is not None:
        query = query.where(OccupancyConditionReportItem.report_type == report_type)
    return list(db.scalars(query.order_by(OccupancyConditionReportItem.created_at)))


def get_condition_report_item_or_404(db: Session, occupancy: Occupancy, item_id: int) -> OccupancyConditionReportItem:
    item = db.get(OccupancyConditionReportItem, item_id)
    if not item or item.occupancy_id != occupancy.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Condition report item not found")
    return item


def delete_condition_report_item(db: Session, item: OccupancyConditionReportItem) -> None:
    """Admin-only correction path -- removing a genuinely mistaken item
    (wrong occupancy, duplicate upload), not a data-subject erasure flow
    (unlike DisputeEvidenceItem, a condition report item is evidence about
    the ROOM, not personal content about the renter, so no legal-hold/
    erasure-request machinery applies here)."""
    if item.stored_filename:
        delete_dispute_evidence_file(item.stored_filename)
    db.delete(item)
    db.commit()
