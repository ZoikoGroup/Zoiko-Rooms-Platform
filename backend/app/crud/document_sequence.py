"""Gap-free document numbers (models/document_sequence.py). Call inside the
same transaction (and savepoint) that inserts the numbered document: the
row lock serialises concurrent issuers, and if the insert is rolled back the
increment rolls back with it -- so no number is ever skipped or reused."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.document_sequence import DocumentSequence


def next_document_number(db: Session, series: str) -> int:
    row = db.scalar(select(DocumentSequence).where(DocumentSequence.name == series).with_for_update())
    if row is None:
        try:
            with db.begin_nested():
                db.add(DocumentSequence(name=series, next_value=1))
                db.flush()
        except IntegrityError:
            pass  # created concurrently -- lock the winner's row below
        row = db.scalar(select(DocumentSequence).where(DocumentSequence.name == series).with_for_update())
    value = row.next_value
    row.next_value = value + 1
    db.flush()
    return value
