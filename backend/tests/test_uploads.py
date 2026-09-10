"""Coverage gap found during a full backend read-through: /api/uploads/images
(admin listing-photo upload) had zero test coverage anywhere in the suite."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from tests.conftest import _make_admin, auth_admin_cookie

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32
_JPEG_BYTES = b"\xff\xd8\xff" + b"0" * 32


@pytest.fixture(autouse=True)
def _isolate_upload_dir(tmp_path, monkeypatch):
    """Uploads write real files to settings.upload_dir -- point that at a
    throwaway pytest tmp_path instead of the real dev uploads/ folder."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))


class TestUploadImages:
    def test_requires_admin_auth(self, client):
        r = client.post("/api/uploads/images", files=[("files", ("photo.png", _PNG_BYTES, "image/png"))])
        assert r.status_code == 401, r.text

    def test_admin_can_upload_a_valid_png(self, client, db_session: Session):
        admin = _make_admin(db_session, email="uploads-admin@test.com", role="admin")
        r = client.post(
            "/api/uploads/images",
            files=[("files", ("photo.png", _PNG_BYTES, "image/png"))],
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text
        urls = r.json()["urls"]
        assert len(urls) == 1
        assert urls[0].startswith("/uploads/")
        assert urls[0].endswith(".png")

    def test_admin_can_upload_multiple_images(self, client, db_session: Session):
        admin = _make_admin(db_session, email="uploads-admin2@test.com", role="admin")
        r = client.post(
            "/api/uploads/images",
            files=[
                ("files", ("a.png", _PNG_BYTES, "image/png")),
                ("files", ("b.jpg", _JPEG_BYTES, "image/jpeg")),
            ],
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text
        urls = r.json()["urls"]
        assert len(urls) == 2
        assert urls[0].endswith(".png")
        assert urls[1].endswith(".jpg")

    def test_rejects_a_file_whose_content_is_not_a_supported_image(self, client, db_session: Session):
        """Validated against the actual file bytes, not the filename/declared
        content-type -- a .png extension with non-image content must still fail."""
        admin = _make_admin(db_session, email="uploads-admin3@test.com", role="admin")
        r = client.post(
            "/api/uploads/images",
            files=[("files", ("fake.png", b"not actually a png", "image/png"))],
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 400, r.text

    def test_rejects_an_empty_file(self, client, db_session: Session):
        admin = _make_admin(db_session, email="uploads-admin4@test.com", role="admin")
        r = client.post(
            "/api/uploads/images",
            files=[("files", ("empty.png", b"", "image/png"))],
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 400, r.text
