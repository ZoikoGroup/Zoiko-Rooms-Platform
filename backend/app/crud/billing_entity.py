"""ZR-PAY-CFG-001 Section 7.1: the Billing Entity Registry. Super admin only
(enforced at the route). Every change is audit-logged; quotes and receipts
snapshot the entity's details at transaction time, so editing an entity
never rewrites a historical receipt."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.audit import log_audit_event
from app.models.admin_user import AdminUser
from app.models.billing_entity import BILLING_ENTITY_STATUSES, BillingEntity


def list_billing_entities(db: Session) -> list[BillingEntity]:
    return list(db.scalars(select(BillingEntity).order_by(BillingEntity.code)))


def get_billing_entity_or_404(db: Session, entity_id: int) -> BillingEntity:
    entity = db.get(BillingEntity, entity_id)
    if not entity:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Billing entity not found")
    return entity


def _normalize(data: dict) -> dict:
    data = dict(data)
    if "code" in data and data["code"] is not None:
        data["code"] = data["code"].strip().upper()
    if data.get("supported_currencies") is not None:
        data["supported_currencies"] = sorted({c.strip().upper() for c in data["supported_currencies"] if c.strip()})
    if data.get("supported_markets") is not None:
        data["supported_markets"] = sorted({m.strip() for m in data["supported_markets"] if m.strip()})
    if data.get("status") is not None and data["status"] not in BILLING_ENTITY_STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"status must be one of {list(BILLING_ENTITY_STATUSES)}")
    return data


def create_billing_entity(db: Session, admin: AdminUser, data: dict, *, correlation_id: str = "") -> BillingEntity:
    data = _normalize(data)
    if db.scalar(select(BillingEntity.id).where(BillingEntity.code == data["code"])):
        raise HTTPException(status.HTTP_409_CONFLICT, f"A billing entity with code {data['code']} already exists")
    entity = BillingEntity(**data)
    db.add(entity)
    db.commit()
    db.refresh(entity)
    log_audit_event(
        db, admin, "billing_entity.create", "billing_entity", str(entity.id), correlation_id,
        reason=f"{entity.code}: {entity.legal_name}",
    )
    db.commit()
    return entity


def update_billing_entity(
    db: Session, admin: AdminUser, entity: BillingEntity, updates: dict, *, correlation_id: str = "",
) -> BillingEntity:
    updates = _normalize({k: v for k, v in updates.items() if v is not None})
    updates.pop("code", None)  # the code is referenced by quotes/receipts -- never renamed
    for field, value in updates.items():
        setattr(entity, field, value)
    entity.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(entity)
    log_audit_event(
        db, admin, "billing_entity.update", "billing_entity", str(entity.id), correlation_id,
        reason=", ".join(sorted(updates)) or "no changes",
    )
    db.commit()
    return entity


def billing_entity_snapshot(entity: BillingEntity) -> dict:
    """What a quote/receipt freezes (ZR-PAY-CFG-001 Section 7.1: 'Receipts/
    invoices must snapshot the legal entity and registration data used at
    transaction time')."""
    return {
        "id": entity.id,
        "code": entity.code,
        "legal_name": entity.legal_name,
        "trading_name": entity.trading_name,
        "customer_facing_name": entity.customer_facing_name(),
        "registered_address": entity.registered_address,
        "company_registration_number": entity.company_registration_number,
        "tax_registration_type": entity.tax_registration_type,
        "tax_registration_number": entity.tax_registration_number,
    }
