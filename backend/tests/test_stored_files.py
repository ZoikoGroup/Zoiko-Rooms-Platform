"""Every file is kept in the database (stored_files): stored, read back,
erased, read from the old disk location until it has been copied, and copied
in from disk once -- idempotently, with hashes checked and the encrypted flag
set for .enc files."""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy.orm import Session

from app.core import file_store
from app.core.config import settings
from app.models.stored_file import StoredFile
from app.services.file_copy import copy_disk_files_to_database
from tests.conftest import stored_refs


class TestFileStore:
    def test_put_read_delete(self, db_session: Session):
        ref = file_store.put(db_session, "receipt", b"%PDF-1.4 receipt", extension=".pdf", content_type="application/pdf")
        db_session.commit()
        assert ref.endswith(".pdf")
        assert file_store.read(db_session, "receipt", ref) == b"%PDF-1.4 receipt"
        assert file_store.exists(db_session, "receipt", ref)

        row = db_session.query(StoredFile).filter_by(category="receipt", storage_ref=ref).one()
        assert row.size_bytes == len(b"%PDF-1.4 receipt")
        assert row.sha256 == hashlib.sha256(b"%PDF-1.4 receipt").hexdigest()

        file_store.delete(db_session, "receipt", ref)
        db_session.commit()
        assert file_store.read(db_session, "receipt", ref) is None

    def test_categories_are_separate(self, db_session: Session):
        ref = file_store.put(db_session, "identity_document", b"secret", extension=".pdf")
        assert file_store.read(db_session, "listing_image", ref) is None

    def test_a_file_in_a_rolled_back_transaction_is_not_kept(self, db_engine):
        connection = db_engine.connect()
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            ref = file_store.put(session, "receipt", b"%PDF-1.4 orphan", extension=".pdf")
            session.rollback()  # the row that would have owned it failed
            assert file_store.read(session, "receipt", ref) is None
        finally:
            session.close()
            transaction.rollback()
            connection.close()

    def test_unknown_category_is_refused(self, db_session: Session):
        with pytest.raises(ValueError):
            file_store.put(db_session, "nonsense", b"x")

    def test_a_file_not_yet_copied_is_read_from_disk(self, db_session: Session, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "receipt_document_dir", str(tmp_path))
        (tmp_path / "legacy.pdf").write_bytes(b"%PDF-1.4 legacy")
        assert file_store.read(db_session, "receipt", "legacy.pdf") == b"%PDF-1.4 legacy"
        # A ref can never step outside its directory.
        assert file_store.read(db_session, "receipt", "../legacy.pdf") is None

    def test_delete_also_erases_a_copy_still_on_disk(self, db_session: Session, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "evidence_upload_dir", str(tmp_path))
        (tmp_path / "old.pdf").write_bytes(b"%PDF-1.4 old")
        file_store.delete(db_session, "dispute_evidence", "old.pdf")
        assert not (tmp_path / "old.pdf").exists()


class TestCopyFromDisk:
    def test_files_on_disk_are_copied_once_with_hashes_checked(self, db_session: Session, tmp_path, monkeypatch):
        receipts, evidence = tmp_path / "receipts", tmp_path / "authority"
        receipts.mkdir()
        evidence.mkdir()
        (receipts / "r1.pdf").write_bytes(b"%PDF-1.4 r1")
        (evidence / "e1.pdf.enc").write_bytes(b"ciphertext")
        for attr in ("upload_dir", "identity_upload_dir", "property_verification_upload_dir",
                     "property_location_upload_dir", "evidence_upload_dir", "agreement_document_dir",
                     "listing_fee_receipt_document_dir", "payout_statement_document_dir",
                     "service_fee_invoice_document_dir", "rent_invoice_document_dir"):
            monkeypatch.setattr(settings, attr, str(tmp_path / f"missing-{attr}"))
        monkeypatch.setattr(settings, "receipt_document_dir", str(receipts))
        monkeypatch.setattr(settings, "authority_upload_dir", str(evidence))

        report = copy_disk_files_to_database(db_session.connection())
        assert report["receipt"] == {"copied": 1, "skipped": 0, "mismatched": 0}
        assert report["authority_evidence"]["copied"] == 1
        assert file_store.read(db_session, "receipt", "r1.pdf") == b"%PDF-1.4 r1"
        enc = db_session.query(StoredFile).filter_by(category="authority_evidence", storage_ref="e1.pdf.enc").one()
        assert enc.encrypted and enc.content_type == "application/pdf"

        again = copy_disk_files_to_database(db_session.connection())
        assert again["receipt"] == {"copied": 0, "skipped": 1, "mismatched": 0}
        assert stored_refs(db_session, "receipt").count("r1.pdf") == 1
