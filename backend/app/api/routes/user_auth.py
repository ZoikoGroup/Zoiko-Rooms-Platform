from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import USER_COOKIE_NAME, get_current_user
from app.core.config import settings
from app.core.mailer import send_email, send_password_reset_email
from app.core.security import create_access_token, verify_password
from app.crud import notification as notif_crud
from app.crud.password_reset import create_reset_token, reset_password_with_token
from app.crud.user import (
    authenticate_user,
    create_user,
    get_user_by_email,
    update_user_password,
    update_user_profile,
)
from app.db.session import get_db
from app.models.user_account import UserAccount
from app.schemas.user import (
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    ResetPasswordRequest,
    UserLoginRequest,
    UserPasswordChangeRequest,
    UserProfileUpdateRequest,
    UserRead,
    UserRegisterRequest,
    UserRegisterResponse,
)

router = APIRouter(prefix="/api/users", tags=["user-auth"])

# Identical regardless of whether the email matched an account, so this endpoint
# can't be used to enumerate registered users.
GENERIC_FORGOT_PASSWORD_MESSAGE = "If an account exists for that email, a password reset link has been sent."


def _set_user_cookie(response: Response, email: str) -> None:
    token = create_access_token(subject=email, token_type="user")
    response.set_cookie(
        key=USER_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        # SameSite=None is required for the cookie to be sent on cross-site requests
        # (frontend and backend on different domains) -- but browsers only honor
        # None when Secure is also set, so fall back to Lax for local http dev.
        samesite="none" if settings.cookie_secure else "lax",
        domain=settings.cookie_domain or None,
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
    )


@router.post("/register", response_model=UserRegisterResponse, status_code=status.HTTP_201_CREATED)
def register_user(payload: UserRegisterRequest, db: Session = Depends(get_db)):
    """Register a new user account."""
    if get_user_by_email(db, payload.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")

    try:
        user = create_user(db, payload.email, payload.password, payload.full_name, payload.phone)
        notif_crud.notify_user(
            db, user.id,
            title="Welcome to Zoiko Rooms",
            message="Your account is ready. Verify your identity to start renting or hosting.",
            notification_type="user.registered",
        )
        db.commit()
        send_email(
            user.email,
            "Welcome to Zoiko Rooms",
            heading=f"Welcome, {user.full_name.split(' ')[0]}!",
            body_lines=[
                "Your Zoiko Rooms account is ready.",
                "Verify your identity to apply for a room or start hosting one of your own.",
            ],
            cta_label="Verify your identity",
            cta_url=f"{settings.frontend_url}/account/identity",
        )
        return UserRegisterResponse(message="Registration successful", user_id=user.id)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.post("/login", response_model=UserRead)
def login_user(payload: UserLoginRequest, response: Response, db: Session = Depends(get_db)):
    """Authenticate user and set cookie."""
    user = authenticate_user(db, payload.email, payload.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account has been deactivated")

    _set_user_cookie(response, user.email)
    return user


@router.post("/logout")
def logout_user(response: Response):
    """Logout user by deleting cookie."""
    response.delete_cookie(USER_COOKIE_NAME, path="/", domain=settings.cookie_domain)
    return {"ok": True}


@router.get("/me", response_model=UserRead)
def get_current_user_profile(user: UserAccount = Depends(get_current_user)):
    """Get authenticated user's profile."""
    return user


@router.put("/profile")
def update_profile(
    payload: UserProfileUpdateRequest,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update user profile (full_name, phone)."""
    updated_user = update_user_profile(db, user, payload.full_name, payload.phone)
    return updated_user


@router.put("/password")
def change_user_password(
    payload: UserPasswordChangeRequest,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change user password."""
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    update_user_password(db, user, payload.new_password)
    return {"ok": True}


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Requests a password reset. Always returns the same response, whether or not
    the email belongs to an account -- never reveals which."""
    user = get_user_by_email(db, payload.email)
    if user and user.is_active:
        raw_token = create_reset_token(db, user)
        reset_link = f"{settings.frontend_url}/account/reset-password?token={raw_token}"
        send_password_reset_email(user.email, reset_link, settings.password_reset_token_expire_minutes)
    return ForgotPasswordResponse(message=GENERIC_FORGOT_PASSWORD_MESSAGE)


@router.post("/reset-password", response_model=ForgotPasswordResponse)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Consumes a reset token issued by /forgot-password. Every USER session issued
    before this call succeeds is invalidated -- see get_current_user."""
    if len(payload.new_password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Password must be at least 8 characters")
    if not reset_password_with_token(db, payload.token, payload.new_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is invalid or has expired")
    return ForgotPasswordResponse(message="Your password has been reset. You can now sign in.")
