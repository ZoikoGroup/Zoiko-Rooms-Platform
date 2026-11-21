#!/usr/bin/env python
"""Download the two small face models used by identity verification into backend/models/.

    python download_face_models.py

YuNet (face detector, MIT) and SFace (face recogniser, Apache-2.0) from the OpenCV model zoo, ~38 MB. They run on this
server; no image is sent anywhere. Exits 0 even if a download fails (so a deploy never breaks on it): identity
verification then reports "temporarily unavailable" until the models are present."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

BASE = "https://github.com/opencv/opencv_zoo/raw/main/models"
MODELS = {
    "yunet.onnx": f"{BASE}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "sface.onnx": f"{BASE}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}


def main() -> int:
    target = Path(__file__).resolve().parent / "models"
    target.mkdir(exist_ok=True)
    ok = True
    for name, url in MODELS.items():
        destination = target / name
        if destination.is_file() and destination.stat().st_size > 100_000:
            print(f"{name}: already present")
            continue
        try:
            response = httpx.get(url, follow_redirects=True, timeout=300)
            response.raise_for_status()
            destination.write_bytes(response.content)
            print(f"{name}: downloaded ({len(response.content) // 1024} KB)")
        except Exception as exc:  # network down, GitHub unreachable...
            ok = False
            print(f"{name}: FAILED ({exc.__class__.__name__}) -- identity verification stays unavailable until it is downloaded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
