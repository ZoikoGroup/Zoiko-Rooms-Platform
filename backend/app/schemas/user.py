from datetime import datetime

from app.schemas.common import CamelModel, NewPassword


class UserLoginRequest(CamelModel):
    email: str
    password: str


class UserRegisterRequest(CamelModel):
    email: str
    password: NewPassword
    full_name: str
    phone: str = ""


class UserRegisterResponse(CamelModel):
    message: str
    user_id: int | None = None


class UserRead(CamelModel):
    id: int
    email: str
    full_name: str
    phone: str
    party_id: int | None
    email_verified: bool
    is_active: bool
    created_at: datetime


class UserPasswordChangeRequest(CamelModel):
    current_password: str
    new_password: NewPassword


class UserProfileUpdateRequest(CamelModel):
    full_name: str
    phone: str = ""


class ForgotPasswordRequest(CamelModel):
    email: str


class ForgotPasswordResponse(CamelModel):
    message: str


class ResetPasswordRequest(CamelModel):
    token: str
    new_password: NewPassword
