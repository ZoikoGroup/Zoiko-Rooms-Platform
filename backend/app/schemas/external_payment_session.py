from datetime import datetime

from app.schemas.common import CamelModel


class ExternalPaymentSessionRead(CamelModel):
    id: int
    obligation_id: int
    status: str
    amount: float
    currency: str
    failure_message: str
    created_at: datetime
    resolved_at: datetime | None


class ExternalPaymentSessionCreateResult(CamelModel):
    session: ExternalPaymentSessionRead
    checkout_url: str
