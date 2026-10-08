"""External provider response page API (ZR-AI-SEARCH-001 Section 9 steps 4-6,
Section 11).

The provider has no Zoiko Rooms account; the signed link from the outreach
email is their authority (and proof they control that contact). Every call is
rate limited per link, audited, and returns only what the provider may see:
the renter's consented demand summary, never the renter's identity, until both
sides agree to share contact details.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.correlation import get_correlation_id
from app.db.session import get_db
from app.models.external_search import ProviderOutreach
from app.services import provider_journey as journey
from app.services.public_rate_limit import check_public_rate_limit

router = APIRouter(prefix="/api/public/provider", tags=["provider-portal"])


class TokenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(..., min_length=10, max_length=400)


class RespondBody(TokenBody):
    decision: Literal["ACCEPT", "DECLINE"]
    model: Literal["CLAIM_AND_LIST", "ONE_OFF_INTRODUCTION"] | None = None
    provider_name: str | None = Field(default=None, max_length=200)
    accepted_terms: bool = False


class MessageBody(TokenBody):
    body: str = Field(..., min_length=1, max_length=5000)


def _outreach(db: Session, request: Request, token: str) -> ProviderOutreach:
    allowed = check_public_rate_limit(
        db, principal=f"provider:{token[-24:]}",
        limit=settings.public_room_search_rate_limit_max * 3,
        window_seconds=settings.public_room_search_rate_limit_window_seconds,
    )
    db.commit()
    if not allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Please wait a moment.")
    try:
        po = db.get(ProviderOutreach, journey.read_provider_token(token))
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if po is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This request no longer exists.")
    return po


@router.post("/request")
def view_request(body: TokenBody, request: Request, db: Session = Depends(get_db)) -> dict:
    """The provider's view of the renter's request (POST so the token never
    lands in server access logs)."""
    return journey.portal_view(db, _outreach(db, request, body.token))


@router.post("/respond")
def respond(body: RespondBody, request: Request, db: Session = Depends(get_db)) -> dict:
    po = _outreach(db, request, body.token)
    try:
        journey.respond(
            db, po, decision=body.decision, model=body.model, provider_name=body.provider_name,
            accepted_terms=body.accepted_terms, correlation_id=get_correlation_id(request),
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    except PermissionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    return journey.portal_view(db, po)


@router.post("/opt-out")
def opt_out(body: TokenBody, request: Request, db: Session = Depends(get_db)) -> dict:
    po = _outreach(db, request, body.token)
    journey.opt_out(db, po, correlation_id=get_correlation_id(request))
    db.commit()
    return {"opted_out": True}


@router.post("/messages")
def send_message(body: MessageBody, request: Request, db: Session = Depends(get_db)) -> dict:
    po = _outreach(db, request, body.token)
    try:
        journey.send_message(db, po, sender="PROVIDER", body=body.body)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    return journey.portal_view(db, po)


@router.post("/release")
def consent_to_release(body: TokenBody, request: Request, db: Session = Depends(get_db)) -> dict:
    po = _outreach(db, request, body.token)
    try:
        journey.consent_to_release(db, po, side="PROVIDER")
    except PermissionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    return journey.portal_view(db, po)
