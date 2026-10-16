from datetime import date, datetime

from pydantic import Field

from app.schemas.common import CamelModel


class HostRefundRead(CamelModel):
    id: int
    occupancy_id: int
    termination_case_id: int | None
    amount: float
    currency: str
    reason: str
    due_by: date
    status: str
    payment_reference: str
    marked_paid_at: datetime | None
    confirmed_at: datetime | None
    deemed_confirmed: bool
    not_received_note: str
    created_at: datetime


class HostRefundMarkPaid(CamelModel):
    reference: str = Field(default="", max_length=200)


class HostRefundNotReceived(CamelModel):
    note: str = Field(default="", max_length=1000)
