"""The identity check: compare the face on the uploaded document photo with the selfie. If they match, verified."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.identity_verification import IdentityVerification
from app.services.identity import face_match
from tests.helpers_identity import FakeFaces, JPEG, face_match_request
from tests.test_identity_global_flow import BASE, _details, _person, _start, _submit, _super_admin  # noqa: F401
from tests.conftest import auth_user_cookie


@pytest.fixture()
def faces(monkeypatch):
    return FakeFaces().install(monkeypatch)


def _open_session(client, db: Session, email: str):
    user = _person(db, email)
    assert _details(client, user).status_code == 200
    sid = _start(client, user).json()["id"]
    assert _submit(client, user, sid).status_code == 200
    return user, sid


def test_matching_faces_verify_the_identity(client, db_session, faces):
    user, sid = _open_session(client, db_session, "fm-pass@test.com")
    faces.score = 0.62
    r = face_match_request(client, user, sid)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "VERIFIED"
    assert client.get(BASE, cookies=auth_user_cookie(user)).json()["state"] == "VERIFIED"


def test_clearly_different_faces_are_declined(client, db_session, faces):
    user, sid = _open_session(client, db_session, "fm-fail@test.com")
    faces.score = 0.10
    body = face_match_request(client, user, sid).json()
    assert body["state"] == "FAILED" and body["reasonCodes"] == ["PROVIDER_DECLINED"]
    assert body["canRestart"] is True


def test_an_in_between_score_asks_for_clearer_photos_and_can_be_retried(client, db_session, faces):
    user, sid = _open_session(client, db_session, "fm-unsure@test.com")
    faces.score = 0.32  # between 0.25 and 0.40
    body = face_match_request(client, user, sid).json()
    assert body["state"] == "ACTION_REQUIRED" and body["reasonCodes"] == ["FACE_MATCH_UNSURE"]
    assert body["captureAvailable"] is True  # the same attempt accepts new photos
    faces.score = 0.7
    assert face_match_request(client, user, sid).json()["state"] == "VERIFIED"


@pytest.mark.parametrize("issue", ["NO_FACE_IN_DOCUMENT", "NO_FACE_IN_SELFIE", "MULTIPLE_FACES_IN_SELFIE",
                                   "FACE_TOO_SMALL", "FACE_NOT_FRONTAL"])
def test_photo_problems_are_never_a_pass(client, db_session, faces, issue):
    user, sid = _open_session(client, db_session, f"fm-{issue.lower()}@test.com")
    faces.score, faces.issues = None, [issue]
    body = face_match_request(client, user, sid).json()
    assert body["state"] == "ACTION_REQUIRED" and body["reasonCodes"] == [issue] and body["message"]


def test_the_score_exactly_at_the_pass_line_passes_and_just_below_does_not(client, db_session, faces):
    user, sid = _open_session(client, db_session, "fm-edge@test.com")
    faces.score = 0.3999
    assert face_match_request(client, user, sid).json()["state"] == "ACTION_REQUIRED"
    faces.score = 0.40
    assert face_match_request(client, user, sid).json()["state"] == "VERIFIED"


def test_nothing_is_read_from_the_document_so_nothing_about_it_is_kept(client, db_session, faces):
    user, sid = _open_session(client, db_session, "fm-nodata@test.com")
    face_match_request(client, user, sid)
    db_session.expire_all()
    record = db_session.get(IdentityVerification, sid)
    assert (record.masked_document_number, record.encrypted_reference, record.document_file_path) == ("", None, None)
    assert record.document_type == "passport"


def test_the_document_type_must_be_an_accepted_one(client, db_session, faces):
    user, sid = _open_session(client, db_session, "fm-doctype@test.com")
    assert face_match_request(client, user, sid, document_type="library_card").status_code == 400
    assert faces.calls == 0


def test_the_photos_must_be_real_images_of_a_sane_size(client, db_session, faces):
    user, sid = _open_session(client, db_session, "fm-bad@test.com")
    assert face_match_request(client, user, sid, document=b"x" * 9000).status_code == 400  # not JPG/PNG
    assert face_match_request(client, user, sid, selfie=JPEG[:100]).status_code == 400  # far too small
    assert faces.calls == 0


def test_photos_cannot_be_submitted_before_the_details_are_confirmed(client, db_session, faces):
    user = _person(db_session, "fm-early@test.com")
    _details(client, user)
    sid = _start(client, user).json()["id"]  # started but not attested
    assert face_match_request(client, user, sid).status_code == 409


def test_nobody_can_submit_photos_to_someone_elses_verification(client, db_session, faces):
    owner, sid = _open_session(client, db_session, "fm-owner@test.com")
    other = _person(db_session, "fm-other@test.com")
    assert face_match_request(client, other, sid).status_code == 403


def test_a_verified_person_cannot_resubmit(client, db_session, faces):
    user, sid = _open_session(client, db_session, "fm-once@test.com")
    face_match_request(client, user, sid)
    assert face_match_request(client, user, sid).status_code == 409


def test_without_the_face_models_nobody_is_verified(client, db_session, monkeypatch):
    FakeFaces(available=False).install(monkeypatch)
    user = _person(db_session, "fm-nomodels@test.com")
    _details(client, user)
    sid = _start(client, user).json()["id"]
    body = _submit(client, user, sid).json()
    assert body["state"] == "IN_PROGRESS" and body["reasonCodes"] == ["PROVIDER_UNAVAILABLE"]
    assert client.get(BASE, cookies=auth_user_cookie(user)).json()["state"] != "VERIFIED"


def test_the_old_vendor_endpoints_are_gone(client, db_session, faces):
    user, sid = _open_session(client, db_session, "fm-gone@test.com")
    cookies = auth_user_cookie(user)
    for path in (f"{BASE}/verifications/{sid}/capture", f"{BASE}/verifications/{sid}/complete",
                 f"{BASE}/verifications/{sid}/refresh", "/api/v1/webhooks/veriff/decision"):
        assert client.post(path, cookies=cookies).status_code in (404, 405), path


# ---- the real models (skipped when they aren't downloaded) -----------------------------------------------------

def _models_present() -> bool:
    return face_match.is_available()


@pytest.mark.skipif(not _models_present(), reason="face models not downloaded (python download_face_models.py)")
class TestRealModels:
    def test_a_blank_picture_has_no_face(self):
        import cv2
        import numpy as np

        blank = cv2.imencode(".jpg", np.full((600, 800, 3), 128, np.uint8))[1].tobytes()
        result = face_match.compare_faces(blank, blank)
        assert result.score is None and result.issues == ["NO_FACE_IN_DOCUMENT"]

    def test_garbage_bytes_are_reported_not_raised(self):
        assert face_match.compare_faces(b"not an image", b"not an image").issues == ["DOCUMENT_UNREADABLE"]
