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


class TestServingPhotos:
    def test_an_uploaded_photo_is_served_from_the_database_at_its_url(self, client, db_session: Session, tmp_path):
        admin = _make_admin(db_session, email="uploads-serve@test.com", role="admin")
        r = client.post("/api/uploads/images", files=[("files", ("p.png", _PNG_BYTES, "image/png"))], cookies=auth_admin_cookie(admin))
        url = r.json()["urls"][0]
        assert list(tmp_path.iterdir()) == []  # nothing written to disk

        served = client.get(url)
        assert served.status_code == 200
        assert served.content == _PNG_BYTES
        assert served.headers["content-type"] == "image/png"
        assert "immutable" in served.headers["cache-control"]

        again = client.get(url, headers={"If-None-Match": served.headers["etag"]})
        assert again.status_code == 304

    def test_an_unknown_photo_is_404(self, client):
        assert client.get("/uploads/does-not-exist.png").status_code == 404

    def test_private_documents_are_never_served_here(self, client, db_session: Session):
        from app.core import file_store

        ref = file_store.put(db_session, "identity_document", b"%PDF-1.4 private", extension=".pdf")
        db_session.commit()
        assert client.get(f"/uploads/{ref}").status_code == 404
