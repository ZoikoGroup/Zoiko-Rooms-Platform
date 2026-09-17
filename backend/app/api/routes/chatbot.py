import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from groq import APIConnectionError, APIStatusError, RateLimitError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin
from app.core.correlation import get_correlation_id
from app.core.rate_limit import chat_limiter
from app.crud.audit import log_audit_event
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.models.chat import ChatConversation, ChatMessage
from app.schemas.chat import ChatConversationRead, ChatMessageRead, ChatSendMessageRequest
from app.services.chat_service import (
    ChatServiceError,
    stream_assistant_reply,
)

# The user-side chatbot (scoped to zoiko_user_token) is deferred to Phase 2;
# it will reuse the same service layer once it ships.
router = APIRouter(prefix="/api/admin/chat", tags=["chatbot"], dependencies=[Depends(get_current_admin)])

MAX_HISTORY_MESSAGES = 30
TITLE_MAX_LENGTH = 60

logger = logging.getLogger("zoiko.chatbot")

# User-facing error messages. Deliberately free of env-var names and provider
# internals -- technical detail goes to the server log only (see _log_failure).
ERR_NOT_CONFIGURED = "Assistant isn't configured yet. Contact your administrator."
ERR_RATE_LIMITED = "Assistant is busy right now -- try again in a few seconds."
ERR_NETWORK = "Lost connection to the assistant. Reconnecting didn't work, please retry."
ERR_GENERIC = "The assistant ran into a problem. Please try again."


def _log_failure(exc: Exception) -> None:
    logger.exception("chatbot stream failed: %s", exc)


def _get_owned_conversation(db: Session, admin: AdminUser, conversation_id: int) -> ChatConversation:
    conversation = db.get(ChatConversation, conversation_id)
    if not conversation or conversation.admin_id != admin.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


def _conversation_history(conversation: ChatConversation) -> list[dict]:
    """OpenAI/Groq-shaped message history. Tool traffic is intentionally not
    replayed -- only user/assistant text is needed for context."""
    messages: list[dict] = []
    for m in list(conversation.messages)[-MAX_HISTORY_MESSAGES:]:
        content = (m.content or "").strip()
        if not content:
            continue
        role = "assistant" if m.role == "assistant" else "user"
        messages.append({"role": role, "content": content})
    return messages


def _sse(event_type: str, payload: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"


@router.get("/conversations", response_model=list[ChatConversationRead])
def list_conversations(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    # Strictly scoped to the authenticated admin -- no cross-admin leakage.
    conversations = db.scalars(
        select(ChatConversation)
        .where(ChatConversation.admin_id == admin.id)
        .order_by(ChatConversation.updated_at.desc())
        .limit(50)
    )
    return [ChatConversationRead.model_validate(c) for c in conversations]


@router.post("/conversations", response_model=ChatConversationRead, status_code=status.HTTP_201_CREATED)
def create_conversation(
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    conversation = ChatConversation(admin_id=admin.id)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    log_audit_event(db, admin, "chat.conversation.create", "chat_conversation", str(conversation.id), get_correlation_id(request))
    return ChatConversationRead.model_validate(conversation)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: int,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    conversation = _get_owned_conversation(db, admin, conversation_id)
    db.delete(conversation)  # messages cascade via FK ondelete=CASCADE
    db.commit()
    log_audit_event(db, admin, "chat.conversation.delete", "chat_conversation", str(conversation_id), get_correlation_id(request))


@router.get("/conversations/{conversation_id}/messages", response_model=list[ChatMessageRead])
def list_messages(
    conversation_id: int,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    conversation = _get_owned_conversation(db, admin, conversation_id)
    return [ChatMessageRead.model_validate(m) for m in conversation.messages]


@router.post("/conversations/{conversation_id}/messages/stream")
def send_message_stream(
    conversation_id: int,
    payload: ChatSendMessageRequest,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    conversation = _get_owned_conversation(db, admin, conversation_id)
    content = payload.content.strip()
    if not content:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Message content is required")

    # Per-admin throttling of the streaming endpoint (not just relied on the
    # Groq provider's own limit) to bound cost and abuse.
    if not chat_limiter.allow(f"admin:{admin.id}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Please wait a moment.")

    # Auto-title from the first user message (ChatGPT-style).
    if conversation.title == "New conversation":
        conversation.title = content[:TITLE_MAX_LENGTH]
    user_message = ChatMessage(conversation_id=conversation.id, role="user", content=content)
    db.add(user_message)
    db.commit()

    history = _conversation_history(conversation)
    correlation_id = get_correlation_id(request)

    def event_stream():
        tool_calls_made: list[dict] = []

        def audit(action: str, reason: str) -> None:
            log_audit_event(db, admin, action, "chat_conversation", str(conversation.id), correlation_id, reason=reason)

        try:
            for event_type, data in stream_assistant_reply(db, admin, history):
                if event_type == "tool":
                    tool_calls_made.append({"name": data["name"]})
                    yield _sse("tool", data)
                elif event_type == "done":
                    data_obj = data
                    blocks = data_obj["blocks"]
                    meta = data_obj.get("meta", {})
                    text_parts = [b["text"] for b in blocks if b["type"] == "text"]
                    final_text = "\n".join(p for p in text_parts if p.strip())
                    tool_results_made = [b for b in blocks if b["type"] == "tool_result"]
                    assistant_message = ChatMessage(
                        conversation_id=conversation.id,
                        role="assistant",
                        content=final_text,
                        tool_calls_json=json.dumps(tool_calls_made),
                        tool_results_json=json.dumps(tool_results_made),
                        meta_json=json.dumps(meta),
                    )
                    db.add(assistant_message)
                    db.commit()
                    audit("chat.message", f"tools={[c['name'] for c in tool_calls_made]}")
                    # Deterministic guardrail surfaces for the client + audit hook.
                    guardrail = {
                        "risk": meta.get("risk"),
                        "risk_topic": meta.get("risk_topic"),
                        "action_tier": meta.get("action_tier"),
                        "determination_blocked": meta.get("determination_blocked", False),
                    }
                    if guardrail["determination_blocked"]:
                        audit("chat.guardrail.determination_blocked", f"tier={guardrail['action_tier']}")
                    yield _sse(
                        "done",
                        {
                            "messageId": assistant_message.id,
                            "content": final_text,
                            "guardrail": guardrail,
                        },
                    )
                elif event_type == "text":
                    yield _sse("text", data)
                else:
                    yield _sse(event_type, data)
        except RateLimitError as exc:
            _log_failure(exc)
            audit("chat.error", "rate_limited")
            yield _sse("error", {"message": ERR_RATE_LIMITED})
        except APIConnectionError as exc:
            _log_failure(exc)
            audit("chat.error", "connection_failed")
            yield _sse("error", {"message": ERR_NETWORK})
        except ChatServiceError as exc:
            _log_failure(exc.log_detail or exc)
            audit("chat.error", f"config:{exc.log_detail[:80]}")
            yield _sse("error", {"message": str(exc) or ERR_NOT_CONFIGURED})
        except APIStatusError as exc:
            _log_failure(exc)
            audit("chat.error", f"provider_status:{exc.status_code}")
            message = (
                ERR_NOT_CONFIGURED
                if exc.status_code in (401, 403)
                else ERR_GENERIC
            )
            yield _sse("error", {"message": message})
        except Exception as exc:  # noqa: BLE001 - never leak internals to the client stream
            _log_failure(exc)
            audit("chat.error", f"unexpected:{type(exc).__name__}")
            yield _sse("error", {"message": ERR_GENERIC})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
