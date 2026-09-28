"""Gap-free numbering for tax documents (Listing Fee receipts and credit
notes). One row per series; the next number is taken under a row lock in
the same transaction that creates the document, so a number is only ever
used by a document that was actually saved (crud/document_sequence.py)."""

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocumentSequence(Base):
    __tablename__ = "document_sequences"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    next_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
