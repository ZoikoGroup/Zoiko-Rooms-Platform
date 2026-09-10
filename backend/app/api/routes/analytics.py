from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_super_admin
from app.crud import analytics as crud
from app.db.session import get_db
from app.schemas.analytics import BookingsByTypePoint, OccupancyByCityPoint, RevenueTrendPoint, Section1MetricsRead
from app.services.operational_metrics import compute_section1_metrics

router = APIRouter(prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(require_super_admin)])

 
@router.get("/revenue-trend", response_model=list[RevenueTrendPoint])
def get_revenue_trend(db: Session = Depends(get_db)):
    return crud.revenue_trend(db)


@router.get("/bookings-by-type", response_model=list[BookingsByTypePoint])
def get_bookings_by_type(db: Session = Depends(get_db)):
    return crud.bookings_by_type(db)


@router.get("/occupancy-by-city", response_model=list[OccupancyByCityPoint])
def get_occupancy_by_city(db: Session = Depends(get_db)):
    return crud.occupancy_by_city(db)


@router.get("/section1-operational-metrics", response_model=Section1MetricsRead)
def get_section1_operational_metrics(db: Session = Depends(get_db)):
    """ZR-ENG-CLR-001 Section 15 metrics, computed on demand -- see
    app/services/operational_metrics.py."""
    return compute_section1_metrics(db)
