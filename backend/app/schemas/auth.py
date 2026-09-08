from app.schemas.common import CamelModel, NewPassword


class LoginRequest(CamelModel):
    email: str
    password: str


class AdminRead(CamelModel):
    id: int
    email: str
    full_name: str
    phone: str
    role: str


class PasswordChangeRequest(CamelModel):
    current_password: str
    new_password: NewPassword
