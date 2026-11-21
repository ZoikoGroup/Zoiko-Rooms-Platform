"""Test doubles for the face-match identity check: the real comparison needs the OpenCV models, so most tests script
the score instead (see test_identity_face_match.py for tests that use the real models)."""

from __future__ import annotations

from app.services.identity import face_match
from tests.conftest import auth_user_cookie

# A tiny file that passes the route's checks (JPEG header, over the 5 KB minimum). The fake comparison never opens it.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 6000


class FakeFaces:
    """Replaces face_match.compare_faces / is_available. `score` is what the "comparison" returns; None = no face."""

    current: "FakeFaces"

    def __init__(self, score: float | None = 0.9, issues: list[str] | None = None, available: bool = True):
        self.score, self.issues, self.available, self.calls = score, issues or [], available, 0
        FakeFaces.current = self

    def install(self, monkeypatch) -> "FakeFaces":
        monkeypatch.setattr(face_match, "is_available", lambda: self.available)

        def compare(_document: bytes, _selfie: bytes):
            self.calls += 1
            if not self.available:
                raise face_match.FaceMatchUnavailable("models missing")
            return face_match.FaceComparison(self.score, list(self.issues))

        monkeypatch.setattr(face_match, "compare_faces", compare)
        return self


def face_match_request(client, user, session_id: int, *, document_type: str = "passport", document: bytes = JPEG,
                       selfie: bytes = JPEG):
    return client.post(
        f"/api/users/identity/verifications/{session_id}/face-match", data={"document_type": document_type},
        files={"document": ("id.jpg", document, "image/jpeg"), "selfie": ("me.jpg", selfie, "image/jpeg")},
        cookies=auth_user_cookie(user),
    )
