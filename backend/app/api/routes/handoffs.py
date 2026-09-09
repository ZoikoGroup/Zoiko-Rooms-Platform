"""Human handoff routes — scoped to the user chatbot (zoiko_user_token).

Implements the governed handoff lifecycle from the API spec §15: create, read,
optional bridge message, and cancel. Actual case/ticket creation is the support
system's job; this layer only ever records a REQUEST with minimum-necessary
context (FRS-HO-001..004).
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.correlation import get_correlation_id
from app.db.session import get_db
from app.models.chat import ChatConversation
from app.models.handoff import AiHandoff
from app.models.user_account import UserAccount
from app.services.handoff import (
    HANDOFF_REASON_CODES,
    add_bridge_message,
    cancel_handoff,
    create_handoff,
)

router = APIRouter(
    prefix="/api/users/chat/handoffs",
    tags=["user-chat-handoff"],
    dependencies=[Depends(get_current_user)],
)


def _get_owned_conversation(db: Session, user: UserAccount, conversation_id: int) -> ChatConversation:
    conversation = db.get(ChatConversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


def _get_owned_handoff(db: Session, user: UserAccount, handoff_id: int) -> AiHandoff:
    handoff = db.get(AiHandoff, handoff_id)
    if not handoff or handoff.initiator_user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Handoff not found")
    return handoff


def _serialize(handoff: AiHandoff) -> dict:
    manifest = json.loads(handoff.shared_context_manifest or "{}")
    return {
        "id": handoff.id,
        "conversationId": handoff.conversation_id,
        "reasonCode": handoff.reason_code,
        "urgency": handoff.urgency,
        "status": handoff.status,
        "supportCaseRef": handoff.support_case_ref,
        "summary": handoff.summary,
        "consentState": handoff.consent_state,
        "contextManifest": manifest,
        "bridgeMessages": json.loads(handoff.bridge_messages_json or "[]"),
        "createdAt": handoff.created_at.isoformat() if handoff.created_at else None,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create(
    payload: dict,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation_id = payload.get("conversationId")
    if not conversation_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "conversationId is required")
    conversation = _get_owned_conversation(db, user, int(conversation_id))

    reason_code = payload.get("reasonCode", "USER_REQUEST")
    if reason_code not in HANDOFF_REASON_CODES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"invalid reasonCode: {reason_code}")

    try:
        handoff = create_handoff(
            db,
            user,
            conversation,
            reason_code=reason_code,
            urgency=payload.get("urgency"),
            summary=payload.get("summary"),
            request_text=payload.get("requestText", ""),
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    db.commit()
    db.refresh(handoff)
    return _serialize(handoff)


@router.get("/{handoff_id}")
def read(
    handoff_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    handoff = _get_owned_handoff(db, user, handoff_id)
    return _serialize(handoff)


@router.post("/{handoff_id}/messages")
def post_bridge_message(
    handoff_id: int,
    payload: dict,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    handoff = _get_owned_handoff(db, user, handoff_id)
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "text is required")
    try:
        add_bridge_message(db, handoff, text)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    db.commit()
    db.refresh(handoff)
    return _serialize(handoff)


@router.post("/{handoff_id}/cancel")
def cancel(
    handoff_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    handoff = _get_owned_handoff(db, user, handoff_id)
    try:
        cancel_handoff(db, user, handoff)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    db.commit()
    db.refresh(handoff)
    return _serialize(handoff)
