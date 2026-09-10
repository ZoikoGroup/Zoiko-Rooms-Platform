from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.domain_event import DomainEvent


def emit_event(
    db: Session,
    event_type: str,
    resource_type: str,
    resource_id: str,
    payload: dict | None = None,
    *,
    correlation_id: str = "",
    idempotency_key: str | None = None,
) -> DomainEvent:
    """Append a domain event row within the same transaction as the mutation that caused
    it, so it's only ever visible once that transaction commits. No async consumer is
    wired up yet -- this is the outbox scaffold described in Phase 1 of the roadmap.

    ZR-ENG-CLR-001 Section 11.3/12.2: correlation_id ties this event back to the
    request that caused it. idempotency_key, when supplied, makes this call itself
    idempotent -- if a row with the same key already exists (this exact emit having
    already happened, e.g. a retried command), that row is returned unchanged rather
    than creating a duplicate. The partial unique index on idempotency_key is the
    actual guarantee for a genuine concurrent double-emit, same pattern as
    services/inventory.py:create_hold -- a pre-check for the fast path, the
    constraint as the backstop."""
    if idempotency_key is None:
        event = DomainEvent(
            event_type=event_type, resource_type=resource_type, resource_id=resource_id,
            payload=payload or {}, correlation_id=correlation_id, idempotency_key=None,
        )
        db.add(event)
        db.flush()
        return event

    existing = db.scalar(select(DomainEvent).where(DomainEvent.idempotency_key == idempotency_key))
    if existing is not None:
        return existing

    # The insert itself must happen inside the SAVEPOINT -- entering
    # begin_nested() flushes whatever's already pending into the *outer*
    # transaction first, so a new object only added beforehand would never
    # actually be protected by the nested rollback below.
    try:
        with db.begin_nested():
            event = DomainEvent(
                event_type=event_type, resource_type=resource_type, resource_id=resource_id,
                payload=payload or {}, correlation_id=correlation_id, idempotency_key=idempotency_key,
            )
            db.add(event)
            db.flush()
    except IntegrityError:
        return db.scalar(select(DomainEvent).where(DomainEvent.idempotency_key == idempotency_key))
    return event
