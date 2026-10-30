from pydantic import Field

from app.schemas.common import CamelModel


class HostSubletPolicyRead(CamelModel):
    min_notice_enabled: bool
    min_notice_days: int


class HostSubletPolicyUpdate(CamelModel):
    min_notice_enabled: bool
    min_notice_days: int = Field(default=14, ge=1, le=365)
