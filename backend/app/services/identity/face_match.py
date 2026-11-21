"""Identity verification by face match -- the whole check.

The person uploads a photo of their identity document and takes one selfie. We find the face on each and compare
them with OpenCV's YuNet detector and SFace recogniser, both running on this server (nothing is sent to a third
party). If the faces match, the identity is verified.

WHAT THIS PROVES, AND WHAT IT DOES NOT: that the selfie shows the same face as the picture they uploaded. Nothing is
read from the document, so it does not prove the picture is a genuine ID, that it has not expired, that the holder
is an adult, or that the name on it is the account's name. There is no liveness check either, so a held-up photo of
a face is not detected. That is the product decision for this check; see services/identity/service.py:verify_faces.

The photos are decoded in memory, compared and dropped. Nothing is written to disk or the database."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger("uvicorn.error")

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_MAX_PIXELS = 40_000_000
_MIN_DOCUMENT_FACE_PX = 40
_MIN_SELFIE_FACE_HEIGHT_RATIO = 0.15
_MAX_SELFIE_YAW = 0.35  # nose offset in inter-eye distances: 0 = straight at the camera


class FaceMatchUnavailable(RuntimeError):
    """The face models or OpenCV are not installed on this server."""


@dataclass
class FaceComparison:
    score: float | None  # cosine similarity, higher = more alike; None when no comparison was possible
    issues: list[str] = field(default_factory=list)  # safe reason codes (see reason_codes.py)


def _model_path(configured: str) -> Path:
    path = Path(configured)
    return path if path.is_absolute() else _BACKEND_ROOT / path


_lock = threading.Lock()
_engine = None


def _load_engine():
    """OpenCV + the two models, loaded once. Raises FaceMatchUnavailable."""
    global _engine
    if _engine is not None:
        return _engine
    with _lock:
        if _engine is not None:
            return _engine
        try:
            import cv2  # noqa: F401
            import numpy  # noqa: F401
        except ImportError as exc:
            raise FaceMatchUnavailable("opencv-python-headless is not installed") from exc
        detector, recogniser = _model_path(settings.face_detector_model), _model_path(settings.face_recognizer_model)
        for model in (detector, recogniser):
            if not model.is_file():
                raise FaceMatchUnavailable(f"missing face model {model} (run python download_face_models.py)")
        import cv2

        _engine = (str(detector), cv2.FaceRecognizerSF.create(str(recogniser), ""))
        return _engine


def is_available() -> bool:
    try:
        _load_engine()
        return True
    except FaceMatchUnavailable:
        return False


@dataclass
class _Face:
    box: tuple[float, float, float, float]
    yaw: float
    row: object  # the detector's row, needed by the recogniser

    @property
    def area(self) -> float:
        return self.box[2] * self.box[3]


def _decode(content: bytes):
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[0] * image.shape[1] > _MAX_PIXELS:
        return None
    return image


def _detect(image, detector_model: str) -> list[_Face]:
    import cv2

    height, width = image.shape[:2]
    scale = min(1.0, 1280.0 / max(height, width))
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1.0 else image
    detector = cv2.FaceDetectorYN.create(detector_model, "", (small.shape[1], small.shape[0]), 0.8, 0.3, 5000)
    _ok, rows = detector.detect(small)
    faces: list[_Face] = []
    for row in (rows if rows is not None else []):
        full = row.copy()
        full[:14] = full[:14] / scale  # back to the original image's pixels
        left_eye, right_eye, nose = full[4:6], full[6:8], full[8:10]
        eye_distance = max(float(abs(right_eye[0] - left_eye[0])), 1e-6)
        yaw = float((nose[0] - (left_eye[0] + right_eye[0]) / 2.0) / eye_distance)
        faces.append(_Face((float(full[0]), float(full[1]), float(full[2]), float(full[3])), yaw, full))
    return sorted(faces, key=lambda f: f.area * float(f.row[14]), reverse=True)


def compare_faces(document_photo: bytes, selfie: bytes) -> FaceComparison:
    """Compare the face on the document photo with the selfie. Never raises for bad photos: returns issue codes the
    person can act on. Raises FaceMatchUnavailable only when the models are missing."""
    import cv2

    detector_model, recogniser = _load_engine()
    document = _decode(document_photo)
    picture = _decode(selfie)
    if document is None or picture is None:
        return FaceComparison(None, ["DOCUMENT_UNREADABLE"])

    document_faces = _detect(document, detector_model)
    if not document_faces or min(document_faces[0].box[2], document_faces[0].box[3]) < _MIN_DOCUMENT_FACE_PX:
        return FaceComparison(None, ["NO_FACE_IN_DOCUMENT"])

    selfie_faces = _detect(picture, detector_model)
    if not selfie_faces:
        return FaceComparison(None, ["NO_FACE_IN_SELFIE"])
    if len(selfie_faces) > 1 and selfie_faces[1].area > 0.4 * selfie_faces[0].area:
        return FaceComparison(None, ["MULTIPLE_FACES_IN_SELFIE"])
    face = selfie_faces[0]
    if face.box[3] / picture.shape[0] < _MIN_SELFIE_FACE_HEIGHT_RATIO:
        return FaceComparison(None, ["FACE_TOO_SMALL"])
    if abs(face.yaw) > _MAX_SELFIE_YAW:
        return FaceComparison(None, ["FACE_NOT_FRONTAL"])

    reference = recogniser.feature(recogniser.alignCrop(document, document_faces[0].row))
    candidate = recogniser.feature(recogniser.alignCrop(picture, face.row))
    return FaceComparison(float(recogniser.match(reference, candidate, cv2.FaceRecognizerSF_FR_COSINE)))
