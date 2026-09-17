from app.schemas.common import CamelModel


class RoomAlertCreate(CamelModel):
    email: str
    city: str
    min_price: float | None = None
    max_price: float | None = None
    room_type: str | None = None


class RoomAlertRead(CamelModel):
    id: str
    city: str
    min_price: float | None
    max_price: float | None
    room_type: str | None
    is_active: bool
