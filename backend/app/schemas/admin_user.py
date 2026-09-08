from datetime import datetime

from app.schemas.common import CamelModel, NewPassword


class AdminUserCreate(CamelModel):
    email: str
    password: NewPassword
    full_name: str = "New Admin"
    phone: str = ""
    role: str = "admin"


class AdminUserUpdate(CamelModel):
    full_name: str | None = None
    phone: str | None = None
    role: str | None = None
    is_active: bool | None = None


class AdminUserRead(CamelModel):
    id: int
    email: str
    full_name: str
    phone: str
    role: str
    is_active: bool
    approval_status: str
    created_at: datetime
