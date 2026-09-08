"""Coverage for routes/uploads.py + core/image_uploads.py -- the admin listing
image upload endpoint. Redirects settings.upload_dir to a pytest tmp_path so
no real repo directories are touched. Content is validated by sniffing real
magic bytes, never the filename extension or client-declared Content-Type
(see image_uploads.py), so tests exercise that directly rather than trusting
the upload's declared type.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import settings
from tests.conftest import _make_admin, auth_admin_cookie

_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class TestUploadImages:
    def test_valid_jpeg_upload_returns_url(self, client, db_session: Session, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
        admin = _make_admin(db_session, email="upload-admin@test.com")
        db_session.commit()

        resp = client.post(
            "/api/uploads/images",
            files={"files": ("photo.jpg", _JPEG_BYTES, "image/jpeg")},
            cookies=auth_admin_cookie(admin),
        )
        assert resp.status_code == 200, resp.text
        urls = resp.json()["urls"]
        assert len(urls) == 1
        assert urls[0].startswith("/uploads/")
        assert urls[0].endswith(".jpg")

        stored_files = list(tmp_path.iterdir())
        assert len(stored_files) == 1

    def test_multiple_valid_images(self, client, db_session: Session, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
        admin = _make_admin(db_session, email="upload-admin2@test.com")
        db_session.commit()

        resp = client.post(
            "/api/uploads/images",
            files=[
                ("files", ("a.jpg", _JPEG_BYTES, "image/jpeg")),
                ("files", ("b.png", _PNG_BYTES, "image/png")),
            ],
            cookies=auth_admin_cookie(admin),
        )
        assert resp.status_code == 200
        assert len(resp.json()["urls"]) == 2

    def test_content_that_isnt_really_an_image_is_rejected_regardless_of_filename(
        self, client, db_session: Session, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
        admin = _make_admin(db_session, email="upload-admin3@test.com")
        db_session.commit()

        resp = client.post(
            "/api/uploads/images",
            # .jpg filename and image/jpeg content-type, but the actual bytes are
            # plain text -- must be rejected by magic-byte sniffing, not trusted.
            files={"files": ("fake.jpg", b"not actually an image", "image/jpeg")},
            cookies=auth_admin_cookie(admin),
        )
        assert resp.status_code == 400
        assert list(tmp_path.iterdir()) == []

    def test_empty_file_is_rejected(self, client, db_session: Session, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
        admin = _make_admin(db_session, email="upload-admin4@test.com")
        db_session.commit()

        resp = client.post(
            "/api/uploads/images",
            files={"files": ("empty.jpg", b"", "image/jpeg")},
            cookies=auth_admin_cookie(admin),
        )
        assert resp.status_code == 400

    def test_oversized_file_is_rejected(self, client, db_session: Session, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
        monkeypatch.setattr(settings, "max_upload_size_mb", 1)
        admin = _make_admin(db_session, email="upload-admin5@test.com")
        db_session.commit()

        oversized = _JPEG_BYTES + b"\x00" * (2 * 1024 * 1024)
        resp = client.post(
            "/api/uploads/images",
            files={"files": ("big.jpg", oversized, "image/jpeg")},
            cookies=auth_admin_cookie(admin),
        )
        assert resp.status_code == 400

    def test_too_many_files_rejected(self, client, db_session: Session, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
        admin = _make_admin(db_session, email="upload-admin6@test.com")
        db_session.commit()

        files = [("files", (f"img{i}.jpg", _JPEG_BYTES, "image/jpeg")) for i in range(11)]
        resp = client.post("/api/uploads/images", files=files, cookies=auth_admin_cookie(admin))
        assert resp.status_code == 400
        assert list(tmp_path.iterdir()) == []

    def test_requires_admin_auth(self, client):
        resp = client.post("/api/uploads/images", files={"files": ("a.jpg", _JPEG_BYTES, "image/jpeg")})
        assert resp.status_code == 401
