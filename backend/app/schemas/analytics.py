from app.schemas.common import CamelModel


class RevenueTrendPoint(CamelModel):
    month: str
    revenue: float
    bookings: int


class BookingsByTypePoint(CamelModel):
    type: str
    value: int


class OccupancyByCityPoint(CamelModel):
    city: str
    occupancy: int


class Section1MetricsRead(CamelModel):
    """ZR-ENG-CLR-001 Section 15 operational metrics -- see
    app/services/operational_metrics.py for what each one measures and why.
    Rate/average fields are null (not zero) when there's no data to compute
    them from yet."""

    approval_turnaround_seconds_avg: float | None
    publication_failure_rate: float | None
    hold_conversion_rate: float | None
    hold_expiry_rate: float | None
    stale_hold_count: int
    duplicate_confirmation_incidents: int
    manual_override_frequency: int
