from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import COOKIE_NAME, get_current_admin
from app.core.config import settings
from app.core.rate_limit import login_limiter
from app.core.security import create_access_token, verify_password
from app.crud.admin import authenticate, update_password
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.schemas.auth import (
    AdminForgotPasswordRequest,
    AdminRead,
    AdminResetPasswordRequest,
    LoginRequest,
    PasswordChangeRequest,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_auth_cookie(response: Response, email: str, *, remember: bool = True) -> None:
    token = create_access_token(subject=email, token_type="admin")
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        # SameSite=None is required for the cookie to be sent on cross-site requests
        # (frontend and backend on different domains) -- but browsers only honor
        # None when Secure is also set, so fall back to Lax for local http dev.
        samesite="none" if settings.cookie_secure else "lax",
        domain=settings.cookie_domain or None,
        # Not remembered: a session cookie, gone when the browser closes.
        max_age=settings.jwt_expire_minutes * 60 if remember else None,
        path="/",
    )


# Deliberately no public POST /register here -- Admin accounts are provisioned
# only by a super admin via POST /api/admin-users (see admin_users.py), never
# through public self-registration.


@router.post("/login", response_model=AdminRead)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    # Keyed on the submitted email (not the eventual admin.id, which we don't
    # have until after authenticate() succeeds) -- this is a brute-force guard
    # against repeatedly guessing one account's password, so it must throttle
    # failed attempts too, not just successful ones.
    if not login_limiter.allow(f"login:{payload.email.lower()}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts. Please wait a moment and try again.")
    admin = authenticate(db, payload.email, payload.password)
    if not admin:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if admin.approval_status == "pending":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your account is still pending super admin approval")
    if admin.approval_status == "rejected":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your registration was rejected. Contact your administrator.")
    if not admin.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account has been deactivated")
    _set_auth_cookie(response, admin.email, remember=payload.remember)
    return admin


GENERIC_ADMIN_RESET_MESSAGE = "If an admin account exists for that email, a password reset link has been sent."


@router.post("/forgot-password")
def forgot_password(payload: AdminForgotPasswordRequest, db: Session = Depends(get_db)):
    """Same response whether or not the address is an admin account."""
    from app.core.mailer import send_password_reset_email
    from app.crud.admin import get_admin_by_email
    from app.crud.password_reset import create_admin_reset_token

    if not login_limiter.allow(f"admin-reset:{payload.email.strip().lower()}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Please wait a moment and try again.")
    admin = get_admin_by_email(db, payload.email.strip())
    if admin and admin.is_active and admin.approval_status == "approved":
        raw_token = create_admin_reset_token(db, admin)
        link = f"{settings.frontend_url}/login/reset-password?token={raw_token}"
        send_password_reset_email(admin.email, link, settings.password_reset_token_expire_minutes, admin.full_name)
    return {"message": GENERIC_ADMIN_RESET_MESSAGE}


@router.post("/reset-password")
def reset_password(payload: AdminResetPasswordRequest, db: Session = Depends(get_db)):
    from app.crud.password_reset import reset_admin_password_with_token

    if len(payload.new_password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Password must be at least 8 characters")
    if not reset_admin_password_with_token(db, payload.token, payload.new_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is invalid or has expired")
    return {"message": "Your password has been reset. You can now sign in."}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/", domain=settings.cookie_domain)
    return {"ok": True}


@router.get("/me", response_model=AdminRead)
def me(admin: AdminUser = Depends(get_current_admin)):
    return admin


@router.put("/password")
def change_password(
    payload: PasswordChangeRequest,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, admin.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    update_password(db, admin, payload.new_password)
    return {"ok": True}
