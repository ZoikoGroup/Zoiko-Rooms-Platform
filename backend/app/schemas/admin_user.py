from datetime import datetime

from app.schemas.common import CamelModel


class AdminUserCreate(CamelModel):
    email: str
    password: str
    full_name: str = "New Admin"
    phone: str = ""
    role: str = "admin"
    # ZR-ENG-CLR-010 Section 12/20: optional dispute specialization -- see
    # models/admin_user.py's DISPUTE_ADMIN_ROLES. None (the default) means
    # not yet specialized, not "no dispute access".
    dispute_role: str | None = None
    # ZR-PAY-LINK-003 Section 17: optional rental-payment "Staff" tier -- see
    # models/admin_user.py's PAYMENT_STAFF_ROLES. None (the default) means
    # not payment support staff.
    payment_staff_role: str | None = None


class AdminUserUpdate(CamelModel):
    full_name: str | None = None
    phone: str | None = None
    role: str | None = None
    is_active: bool | None = None
    dispute_role: str | None = None
    payment_staff_role: str | None = None


class AdminUserRead(CamelModel):
    id: int
    email: str
    full_name: str
    phone: str
    role: str
    dispute_role: str | None = None
    payment_staff_role: str | None = None
    is_active: bool
    approval_status: str
    created_at: datetime
