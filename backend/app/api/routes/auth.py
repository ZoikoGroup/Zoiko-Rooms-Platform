from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import COOKIE_NAME, get_current_admin
from app.core.config import settings
from app.core.security import create_access_token, verify_password
from app.crud.admin import authenticate, update_password
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.schemas.auth import AdminRead, LoginRequest, PasswordChangeRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_auth_cookie(response: Response, email: str) -> None:
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
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
    )


# Deliberately no public POST /register here -- Admin accounts are provisioned
# only by a super admin via POST /api/admin-users (see admin_users.py), never
# through public self-registration.


@router.post("/login", response_model=AdminRead)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    admin = authenticate(db, payload.email, payload.password)
    print("login api test", admin)
    if not admin:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if admin.approval_status == "pending":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your account is still pending super admin approval")
    if admin.approval_status == "rejected":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your registration was rejected. Contact your administrator.")
    if not admin.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account has been deactivated")
    _set_auth_cookie(response, admin.email)
    return admin


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
