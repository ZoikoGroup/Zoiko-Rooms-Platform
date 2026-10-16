"""Account settings for a USER: display/workspace/privacy preferences, profile
photo, sign-in email change, signing out other sessions, data export, and
self-service account closure."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import file_store
from app.core.config import settings
from app.core.security import verify_password
from app.crud.guest import get_guest_for_user
from app.crud.user import get_user_by_email
from app.models.leasing import Agreement, Application, Offer
from app.models.listing import Listing
from app.models.notification import Notification
from app.models.occupancy import Occupancy
from app.models.property import Property
from app.models.rental_payment import RentalPaymentObligation
from app.models.room import Room
from app.models.support_ticket import SupportTicket
from app.models.user_account import UserAccount
from app.models.user_settings import EmailChangeRequest, UserPreference

# --- Preferences -------------------------------------------------------------

# key -> allowed values (None = validated separately below). Defaults are
# what a user who has never saved sees.
PREFERENCE_FIELDS: dict[str, tuple | None] = {
    "language": ("en-US", "en-GB", "hi-IN", "te-IN"),
    "region": ("GB", "US", "IN"),
    "currencyDisplay": ("LISTING", "GBP", "USD", "INR"),
    "timeZone": None,
    "timeFormat": ("12h", "24h"),
    "density": ("comfortable", "compact"),
    "motion": ("system", "reduce"),
    "defaultWorkspace": ("renter", "host"),
    "personalizedRecommendations": (True, False),
    "optionalAnalytics": (True, False),
}
PREFERENCE_DEFAULTS: dict = {
    "language": "en-GB",
    "region": "GB",
    "currencyDisplay": "LISTING",
    "timeZone": "Europe/London",
    "timeFormat": "24h",
    "density": "comfortable",
    "motion": "system",
    "defaultWorkspace": "renter",
    "personalizedRecommendations": True,
    "optionalAnalytics": False,
}


def get_preferences(db: Session, user: UserAccount) -> dict:
    row = db.get(UserPreference, user.id)
    stored = {k: v for k, v in (row.data if row else {}).items() if k in PREFERENCE_FIELDS}
    return {**PREFERENCE_DEFAULTS, **stored}


def update_preferences(db: Session, user: UserAccount, changes: dict) -> dict:
    unknown = sorted(set(changes) - set(PREFERENCE_FIELDS))
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown preference(s): {', '.join(unknown)}")
    for key, value in changes.items():
        allowed = PREFERENCE_FIELDS[key]
        if key == "timeZone":
            try:
                ZoneInfo(str(value))
            except (ZoneInfoNotFoundError, ValueError):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown time zone")
        elif value not in allowed or (isinstance(value, bool) != isinstance(allowed[0], bool)):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid value for {key}")
    row = db.get(UserPreference, user.id)
    if row is None:
        row = UserPreference(user_id=user.id, data={})
        db.add(row)
    row.data = {**(row.data or {}), **changes}
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return get_preferences(db, user)


# --- Profile photo -----------------------------------------------------------

AVATAR_CATEGORY = "avatar"
AVATAR_MAX_BYTES = 5 * 1024 * 1024
_IMAGE_SIGNATURES = [
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
]


async def save_avatar(db: Session, user: UserAccount, upload: UploadFile) -> UserAccount:
    contents = await upload.read()
    if not contents:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The photo is empty")
    if len(contents) > AVATAR_MAX_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Photo must be 5 MB or smaller")
    is_webp = contents[:4] == b"RIFF" and contents[8:12] == b"WEBP"
    sniffed = (".webp", "image/webp") if is_webp else next(
        ((ext, ct) for magic, ext, ct in _IMAGE_SIGNATURES if contents.startswith(magic)), None,
    )
    if sniffed is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload a JPG, PNG or WebP image")
    previous = user.avatar_ref
    user.avatar_ref = file_store.put(db, AVATAR_CATEGORY, contents, extension=sniffed[0], content_type=sniffed[1])
    user.avatar_updated_at = datetime.now(timezone.utc)
    if previous:
        file_store.delete(db, AVATAR_CATEGORY, previous)
    db.commit()
    db.refresh(user)
    return user


def remove_avatar(db: Session, user: UserAccount) -> UserAccount:
    if user.avatar_ref:
        file_store.delete(db, AVATAR_CATEGORY, user.avatar_ref)
    user.avatar_ref = None
    user.avatar_updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


def avatar_response(db: Session, user: UserAccount):
    from fastapi.responses import Response

    from app.models.stored_file import StoredFile

    if not user.avatar_ref:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No profile photo")
    data = file_store.read_or_404(db, AVATAR_CATEGORY, user.avatar_ref)
    content_type = db.scalar(
        select(StoredFile.content_type).where(StoredFile.category == AVATAR_CATEGORY, StoredFile.storage_ref == user.avatar_ref)
    ) or "image/jpeg"
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "private, max-age=3600"})


# --- Sign-in email change ----------------------------------------------------


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def request_email_change(db: Session, user: UserAccount, new_email: str, current_password: str) -> str:
    """Checks the password, then returns a raw single-use token for the
    confirmation link (only its hash is stored). Earlier pending requests are
    superseded."""
    new_email = new_email.strip().lower()
    if not verify_password(current_password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    if "@" not in new_email or len(new_email) > 255:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter a valid email address")
    if new_email == user.email.lower():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That's already your email address")
    if get_user_by_email(db, new_email) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That email address is already in use")
    now = datetime.now(timezone.utc)
    for pending in db.scalars(select(EmailChangeRequest).where(EmailChangeRequest.user_id == user.id, EmailChangeRequest.used_at.is_(None))):
        pending.used_at = now
    raw = secrets.token_urlsafe(32)
    db.add(EmailChangeRequest(
        user_id=user.id, new_email=new_email, token_hash=_hash(raw),
        expires_at=now + timedelta(minutes=settings.email_change_token_expire_minutes),
    ))
    db.commit()
    return raw


def pending_email_change(db: Session, user: UserAccount) -> str | None:
    now = datetime.now(timezone.utc)
    for req in db.scalars(
        select(EmailChangeRequest).where(EmailChangeRequest.user_id == user.id, EmailChangeRequest.used_at.is_(None))
        .order_by(EmailChangeRequest.id.desc())
    ):
        expires = req.expires_at if req.expires_at.tzinfo else req.expires_at.replace(tzinfo=timezone.utc)
        if expires > now:
            return req.new_email
    return None


def confirm_email_change(db: Session, raw_token: str) -> UserAccount:
    req = db.scalar(select(EmailChangeRequest).where(EmailChangeRequest.token_hash == _hash(raw_token)))
    now = datetime.now(timezone.utc)
    expires = None if req is None else (req.expires_at if req.expires_at.tzinfo else req.expires_at.replace(tzinfo=timezone.utc))
    if req is None or req.used_at is not None or expires < now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This confirmation link is invalid or has expired")
    user = db.get(UserAccount, req.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This confirmation link is invalid or has expired")
    if get_user_by_email(db, req.new_email) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That email address is already in use")
    user.email = req.new_email
    user.email_verified = True
    user.updated_at = now
    req.used_at = now
    db.commit()
    db.refresh(user)
    return user


# --- Sessions ----------------------------------------------------------------


def revoke_sessions(db: Session, user: UserAccount) -> None:
    """Every token issued before this second stops working (deps.get_current_user).
    The caller re-issues a cookie for the session making the request."""
    user.sessions_revoked_at = datetime.now(timezone.utc).replace(microsecond=0)
    db.commit()


# --- Closure -----------------------------------------------------------------

_OPEN_LISTING_STATES = ("REVIEW", "APPROVED", "PUBLISHED", "PAUSED", "QUARANTINED")
_UNSETTLED_RENT = ("DUE", "OVERDUE", "PARTIALLY_PAID", "PAYER_RECORDED", "RECIPIENT_CONFIRMATION_PENDING", "DISPUTED")


def closure_blockers(db: Session, user: UserAccount) -> list[dict]:
    """What must be resolved before the account can be closed, counted on the
    server so it can't be bypassed."""
    blockers: list[dict] = []

    def add(code: str, label: str, count: int) -> None:
        if count:
            blockers.append({"code": code, "label": label, "count": count})

    if user.party_id:
        room_ids = select(Room.id).join(Property, Property.id == Room.property_id).where(Property.owner_party_id == user.party_id)
        add("HOST_TENANCIES", "Active or upcoming tenancies on your rooms", db.scalar(
            select(func.count(Occupancy.id)).where(Occupancy.room_id.in_(room_ids), Occupancy.status.in_(("ACTIVE", "PENDING_MOVE_IN")))
        ) or 0)
        add("HOST_LISTINGS", "Listings that are live, paused or in review", db.scalar(
            select(func.count(Listing.id)).where(Listing.party_id == user.party_id, Listing.state.in_(_OPEN_LISTING_STATES))
        ) or 0)
        add("HOST_APPLICATIONS", "Applications waiting for your decision", db.scalar(
            select(func.count(Application.id)).join(Listing, Listing.id == Application.listing_id)
            .where(Listing.party_id == user.party_id, Application.status == "SUBMITTED")
        ) or 0)
        add("HOST_AGREEMENTS", "Agreements still being signed", db.scalar(
            select(func.count(Agreement.id)).join(Offer, Offer.id == Agreement.offer_id).join(Listing, Listing.id == Offer.listing_id)
            .where(Listing.party_id == user.party_id, Agreement.status.in_(("SENT", "PARTIALLY_EXECUTED", "PAYMENT_IN_PROGRESS", "PAYMENT_PENDING", "AMENDMENT_PENDING")))
        ) or 0)

    guest = get_guest_for_user(db, user)
    if guest is not None:
        add("RENTER_TENANCIES", "Your active or upcoming tenancies", db.scalar(
            select(func.count(Occupancy.id)).where(Occupancy.guest_id == guest.id, Occupancy.status.in_(("ACTIVE", "PENDING_MOVE_IN")))
        ) or 0)
        add("RENTER_APPLICATIONS", "Your applications still open", db.scalar(
            select(func.count(Application.id)).where(Application.guest_id == guest.id, Application.status == "SUBMITTED")
        ) or 0)
        add("RENTER_OFFERS", "Offers you've accepted that aren't finished", db.scalar(
            select(func.count(Offer.id)).outerjoin(Occupancy, Occupancy.offer_id == Offer.id)
            .where(Offer.guest_id == guest.id, Offer.status == "ACCEPTED", Occupancy.id.is_(None))
        ) or 0)
        add("RENTER_RENT", "Rent payments not yet settled", db.scalar(
            select(func.count(RentalPaymentObligation.id))
            .where(RentalPaymentObligation.tenant_guest_id == guest.id, RentalPaymentObligation.status.in_(_UNSETTLED_RENT))
        ) or 0)
    return blockers


def close_account(db: Session, user: UserAccount, password: str, reason: str) -> None:
    if not verify_password(password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Password is incorrect")
    blockers = closure_blockers(db, user)
    if blockers:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"message": "Resolve these before closing your account", "reasons": [f"{b['label']} ({b['count']})" for b in blockers]},
        )
    now = datetime.now(timezone.utc)
    user.is_active = False
    user.closed_at = now
    user.closure_reason = reason.strip()[:500]
    user.sessions_revoked_at = now.replace(microsecond=0)
    user.updated_at = now
    db.commit()


# --- Data export -------------------------------------------------------------


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def build_data_export(db: Session, user: UserAccount) -> dict:
    """Everything the account holds about the user, as one JSON document."""
    from app.crud import notification as notif_crud

    guest = get_guest_for_user(db, user)
    pref = notif_crud.get_or_create_preference_for_user(db, user.id)
    export: dict = {
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "profile": {
            "id": user.id, "email": user.email, "fullName": user.full_name, "phone": user.phone,
            "emailVerified": user.email_verified, "createdAt": _iso(user.created_at),
        },
        "preferences": get_preferences(db, user),
        "notificationPreferences": {
            "optedOutCategories": list(pref.opted_out_categories or []),
            "quietHoursEnabled": pref.quiet_hours_enabled,
            "quietHoursStartMinute": pref.quiet_hours_start_minute,
            "quietHoursEndMinute": pref.quiet_hours_end_minute,
        },
        "asRenter": {"applications": [], "tenancies": [], "rentObligations": []},
        "asHost": {"properties": [], "listings": []},
        "supportTickets": [],
        "notifications": [],
    }
    if guest is not None:
        export["asRenter"]["applications"] = [
            {"id": a.id, "listingId": a.listing_id, "status": a.status, "message": a.message,
             "desiredMoveIn": _iso(a.desired_move_in), "submittedAt": _iso(a.submitted_at)}
            for a in db.scalars(select(Application).where(Application.guest_id == guest.id))
        ]
        export["asRenter"]["tenancies"] = [
            {"id": o.id, "listingId": o.listing_id, "status": o.status, "moveInDate": _iso(o.move_in_date),
             "expectedEndDate": _iso(o.expected_end_date), "moveOutDate": _iso(o.move_out_date)}
            for o in db.scalars(select(Occupancy).where(Occupancy.guest_id == guest.id))
        ]
        export["asRenter"]["rentObligations"] = [
            {"id": r.id, "type": r.obligation_type, "amount": float(r.amount), "currency": r.currency,
             "dueDate": _iso(r.due_date), "status": r.status}
            for r in db.scalars(select(RentalPaymentObligation).where(RentalPaymentObligation.tenant_guest_id == guest.id))
        ]
    if user.party_id:
        export["asHost"]["properties"] = [
            {"id": p.id, "address": p.address, "city": p.city, "status": p.status}
            for p in db.scalars(select(Property).where(Property.owner_party_id == user.party_id))
        ]
        export["asHost"]["listings"] = [
            {"id": l.id, "name": l.name, "state": l.state, "roomId": l.room_id}
            for l in db.scalars(select(Listing).where(Listing.party_id == user.party_id))
        ]
    for t in db.scalars(select(SupportTicket).where(SupportTicket.user_id == user.id)):
        export["supportTickets"].append({
            "ref": t.ref, "category": t.category, "issue": t.issue, "status": t.status, "createdAt": _iso(t.created_at),
            "messages": [{"sender": m.sender, "body": m.body, "createdAt": _iso(m.created_at)} for m in t.messages],
        })
    export["notifications"] = [
        {"title": n.title, "message": n.message, "type": n.notification_type, "createdAt": _iso(n.created_at)}
        for n in db.scalars(select(Notification).where(Notification.recipient_user_id == user.id).order_by(Notification.id.desc()).limit(500))
    ]
    return export
