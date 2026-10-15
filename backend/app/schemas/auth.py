from app.schemas.common import CamelModel


class LoginRequest(CamelModel):
    email: str
    password: str
    # False: the sign-in cookie lasts only until the browser is closed.
    remember: bool = True


class AdminForgotPasswordRequest(CamelModel):
    email: str


class AdminResetPasswordRequest(CamelModel):
    token: str
    new_password: str


class AdminRead(CamelModel):
    id: int
    email: str
    full_name: str
    phone: str
    role: str


class PasswordChangeRequest(CamelModel):
    current_password: str
    new_password: str
