"""Human handoff subsystem (Phase 4) — FRS-HO-001..004.

Deterministic, model-outside helpers that produce a *minimum-necessary* handoff
packet for a chat conversation, persist the governed ``AiHandoff`` request, and
detect when a user explicitly requests a human.

Key invariants:
  * FRS-HO-001 — packet carries a user-visible summary, relevant resource IDs,
    recent action/error status and an approved conversation excerpt.
  * FRS-HO-002 — hidden prompts, secrets, unrelated private context and internal
    model reasoning are excluded from the packet.
  * FRS-HO-003 — consent/notice state is recorded per packet.
  * FRS-HO-004 — we never fabricate a case/ticket reference; ``support_case_ref``
    is only ever set by an authoritative external system.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.crud.audit import log_audit_event
from app.models.chat import ChatConversation, ChatMessage
from app.models.handoff import (
    HANDOFF_ACTIVE_STATUS,
    AiHandoff,
)
from app.models.user_account import UserAccount

# Controlled reason-code taxonomy (subset curated from the handoff spec).
HANDOFF_REASON_CODES = (
    "USER_REQUEST",
    "DISPUTE",
    "SENSITIVE_COMPLIANCE",
    "SAFETY",
    "ACCESSIBILITY",
    "REPEATED_FAILURE",
    "EVIDENCE_CONFLICT",
)

UNAVAILABLE = "UNAVAILABLE"

# Terms that signal the user wants a person, in user-safe matching only.
_REQUEST_HUMAN = re.compile(
    r"\b(talk|speak|connect|transfer|put me( through)?)(\s+to)?(\s+(with|to))?\s+(a\s+)?(human|person|agent|"
    r"representative|support)\b|\b(real\s+person|human\s+agent|speak\s+to\s+a\s+human)\b|"
    r"\b(contact|reach|connect me with)\s+(a\s+)?support\b|request\s+a\s+human",
    re.IGNORECASE,
)

# Anything carrying hidden/private/internal material must not reach the packet.
_EXCLUDED_MARKERS = (
    "system prompt",
    "system_prompt",
    "your instructions",
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "access_token",
    "authorization",
    "bearer",
)

_DEFAULT_EXCERPT_COUNT = 6


@dataclass(frozen=True)
class HandoffPacket:
    summary: str
    reason_code: str
    urgency: str
    resource_ids: list[str] = field(default_factory=list)
    conversation_excerpt: str = ""
    consent_state: str = "NOTICE_GIVEN"
    supports_arc: bool = False

    def to_manifest(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "reason_code": self.reason_code,
            "urgency": self.urgency,
            "resource_ids": self.resource_ids,
            "conversation_excerpt": self.conversation_excerpt,
            "consent_state": self.consent_state,
            "shares_support_case_ref": self.supports_arc,
        }


def handoff_requested(user_text: str) -> bool:
    """Deterministic detector: did the user explicitly ask for a human?"""
    return bool(_REQUEST_HUMAN.search(user_text or ""))


def _sanitize_line(text: str) -> str:
    lowered = text.lower()
    return any(marker in lowered for marker in _EXCLUDED_MARKERS)


def _excerpt(messages: list[ChatMessage] | None, limit: int = _DEFAULT_EXCERPT_COUNT) -> str:
    """Approved, sanitized conversation excerpt (FRS-HO-001/-002).

    Only user/assistant text is included (never tool JSON, runtime meta_json,
    prompts or reasoning). Any line that references a secret/prompt is dropped.
    """
    rows = []
    for m in (messages or [])[-limit:]:
        content = (m.content or "").strip()
        if not content or m.role not in ("user", "assistant"):
            continue
        if _sanitize_line(content):
            continue
        rows.append(f"{'User' if m.role == 'user' else 'Assistant'}: {content}")
    return "\n".join(rows)


def _recent_resource_ids(messages: list[ChatMessage] | None) -> list[str]:
    """Collect listing/resource IDs referenced in recent assistant text."""
    ids: list[str] = []
    for m in list(messages or [])[-10:]:
        if m.role != "assistant":
            continue
        for tok in re.findall(r"\bL\d{2,}\b", m.content or ""):
            if tok not in ids:
                ids.append(tok)
    return ids


def update_urgency(reason_code: str, explicit_urgency: str | None) -> str:
    """Resolve urgency, escalating SAFETY / SENSITIVE_COMPLIANCE automatically."""
    if explicit_urgency in ("NORMAL", "HIGH", "SAFETY_CRITICAL"):
        return explicit_urgency
    if reason_code in ("SAFETY",):
        return "SAFETY_CRITICAL"
    if reason_code in ("DISPUTE", "SENSITIVE_COMPLIANCE"):
        return "HIGH"
    return "NORMAL"


def build_handoff_packet(
    conversation: ChatConversation,
    *,
    reason_code: str,
    urgency: str | None,
    summary: str | None,
    request_text: str,
) -> HandoffPacket:
    """Build a minimum-necessary, sanitized handoff packet for a conversation."""
    reason = reason_code if reason_code in HANDOFF_REASON_CODES else "USER_REQUEST"
    msgs = list(conversation.messages) if conversation is not None else []
    resource_ids = _recent_resource_ids(msgs)
    excerpt = _excerpt(msgs)
    if summary and summary.strip():
        rendered_summary = summary.strip()
    else:
        rendered_summary = request_text.strip() or "User requested human support."
    return HandoffPacket(
        summary=rendered_summary[:1000],
        reason_code=reason,
        urgency=update_urgency(reason, urgency),
        resource_ids=resource_ids,
        conversation_excerpt=excerpt,
        consent_state="NOTICE_GIVEN" if reason == "USER_REQUEST" else "REVIEW_REQUIRED",
    )


def active_handoff_for(db: Session, conversation_id: int) -> AiHandoff | None:
    return db.query(AiHandoff).filter(
        AiHandoff.conversation_id == conversation_id,
        AiHandoff.status.in_(HANDOFF_ACTIVE_STATUS),
    ).order_by(AiHandoff.created_at.desc()).first()


def create_handoff(
    db: Session,
    actor: UserAccount,
    conversation: ChatConversation,
    *,
    reason_code: str,
    urgency: str | None = None,
    summary: str | None = None,
    request_text: str = "",
) -> AiHandoff:
    """Persist a new handoff request. Raises ValueError if one is already active."""
    existing = active_handoff_for(db, conversation.id)
    if existing is not None:
        raise ValueError(f"conversation already has an active handoff: {existing.id}")

    packet = build_handoff_packet(
        conversation,
        reason_code=reason_code,
        urgency=urgency,
        summary=summary,
        request_text=request_text,
    )
    handoff = AiHandoff(
        conversation_id=conversation.id,
        initiator_user_id=actor.id,
        reason_code=packet.reason_code,
        urgency=packet.urgency,
        support_case_ref=None,  # never fabricated (FRS-HO-004)
        shared_context_manifest=json.dumps(packet.to_manifest()),
        summary=packet.summary,
        status="REQUESTED",
        consent_state=packet.consent_state,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(handoff)
    db.flush()
    log_audit_event(
        db,
        None,
        "user_chat.handoff.created",
        "ai_handoff",
        str(handoff.id),
        reason=f"conversation:{conversation.id} user:{actor.id} reason:{packet.reason_code} urgency:{packet.urgency}",
    )
    return handoff


def cancel_handoff(db: Session, actor: UserAccount, handoff: AiHandoff) -> AiHandoff:
    """Cancel a pending (REQUESTED) handoff. #never-fabricated stays intact."""
    if handoff.status not in ("REQUESTED",):
        raise ValueError(f"cannot cancel handoff in status {handoff.status}")
    handoff.status = "CLOSED"
    handoff.updated_at = datetime.now(timezone.utc)
    db.flush()
    log_audit_event(
        db,
        None,
        "user_chat.handoff.cancelled",
        "ai_handoff",
        str(handoff.id),
        reason=f"user:{actor.id} conversation:{handoff.conversation_id}",
    )
    return handoff


def add_bridge_message(db: Session, handoff: AiHandoff, text: str) -> AiHandoff:
    """Append a bridge message to a REQUESTED handoff (support workflow permitting)."""
    if handoff.status != "REQUESTED":
        raise ValueError(f"cannot message handoff in status {handoff.status}")
    messages = json.loads(handoff.bridge_messages_json or "[]")
    messages.append({"ts": datetime.now(timezone.utc).isoformat(), "text": (text or "").strip()})
    handoff.bridge_messages_json = json.dumps(messages)
    handoff.updated_at = datetime.now(timezone.utc)
    db.flush()
    return handoff
