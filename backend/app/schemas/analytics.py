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


class DisputeOperationalMetricsRead(CamelModel):
    """ZR-ENG-CLR-010 Section 28 operational metrics -- see
    app/services/dispute_operational_metrics.py for what each one measures
    and why deadline breaches / time-to-triage / fairness monitoring are
    deliberately not reported here. Rate/average fields are null (not
    zero) when there's no data to compute them from yet."""

    total_cases: int
    cases_by_status: dict[str, int]
    cases_by_severity: dict[str, int]
    claims_by_family: dict[str, int]
    claims_by_authority_class: dict[str, int]
    claims_by_outcome: dict[str, int]
    avg_resolution_time_days: float | None
    reopen_rate: float | None
    settlement_rate: float | None
    external_referral_rate: float | None
    avg_active_financial_hold_age_days: float | None
    avg_released_financial_hold_lifetime_days: float | None
