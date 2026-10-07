"""Thin helpers for auditing ZR-AI-SEARCH-001 events on the shared append-only
chain (app.crud.audit.log_audit_event).

User-driven actions pass actor=None (mirroring the existing user_chat.py
pattern). When no Session is supplied, a short-lived one is opened so the
orchestrator (which does not own a transaction) can still audit.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session


def _commit(db: Session | None) -> None:
    from app.db.session import SessionLocal

    if db is not None:
        db.flush()
        return
    try:
        with SessionLocal() as s:
            _write(s)
    except Exception:  # noqa: BLE001
        return


def _write(db: Session, action: str, resource_type: str, resource_id: str,
           correlation_id: str, reason: str, before_state: str | None,
           after_state: str | None) -> None:
    from app.crud.audit import log_audit_event

    log_audit_event(
        db,
        actor=None,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        correlation_id=correlation_id,
        reason=reason,
        before_state=before_state,
        after_state=after_state,
    )


def log_external_search_event(
    db: Session | None,
    action: str,
    resource_type: str,
    resource_id: str,
    correlation_id: str = "",
    reason: str = "",
    before_state: str | None = None,
    after_state: str | None = None,
) -> None:
    if db is not None:
        _write(db, action, resource_type, resource_id, correlation_id, reason, before_state, after_state)
        return
    from app.db.session import SessionLocal

    try:
        with SessionLocal() as s:
            _write(s, action, resource_type, resource_id, correlation_id, reason, before_state, after_state)
            s.commit()
    except Exception:  # noqa: BLE001 - audit must never break the search
        return


def log_external_search_blocked(
    db: Session | None,
    action: str,
    resource_id: str,
    correlation_id: str = "",
    reason: str = "",
) -> None:
    log_external_search_event(
        db, action, "external_search", resource_id, correlation_id, reason
    )