"""Admin Payments page: Zoiko's own Listing Fee revenue, and the rent
records between hosts and renters (paid directly -- Zoiko never holds it).
See crud/payments_overview.py."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_super_admin
from app.crud import payments_overview as overview_crud
from app.db.session import get_db
from app.schemas.common import CamelModel

router = APIRouter(prefix="/api/finance/payments-overview", tags=["payments-overview"], dependencies=[Depends(require_super_admin)])


class CurrencyTotal(CamelModel):
    currency: str
    amount: float


class ListingFeeRevenueRead(CamelModel):
    collected: list[CurrencyTotal]
    refunded: list[CurrencyTotal]
    paid_count: int
    pending_count: int
    failed_count: int


class RentBucketSummary(CamelModel):
    count: int
    totals: list[CurrencyTotal]


class RentRecordRow(CamelModel):
    obligation_id: int
    obligation_label: str
    status: str
    bucket: str
    amount: float
    outstanding_amount: float
    currency: str
    due_date: date
    renter_name: str
    host_name: str
    listing_name: str


class RentRecordsRead(CamelModel):
    summary: dict[str, RentBucketSummary]
    rows: list[RentRecordRow]


@router.get("/listing-fees", response_model=ListingFeeRevenueRead)
def get_listing_fee_revenue(db: Session = Depends(get_db)):
    return overview_crud.listing_fee_revenue(db)


@router.get("/rent", response_model=RentRecordsRead)
def get_rent_records(
    bucket: str | None = Query(default=None), q: str = Query(default=""), db: Session = Depends(get_db),
):
    """bucket: confirmed | awaiting_host | overdue | disputed | due (omit for all).
    q: matches renter, host or listing name, or the obligation ID."""
    if bucket and bucket not in overview_crud.RENT_BUCKETS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"bucket must be one of {tuple(overview_crud.RENT_BUCKETS)}")
    return overview_crud.rent_records(db, bucket=bucket, search=q)
