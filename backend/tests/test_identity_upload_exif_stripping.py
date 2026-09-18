"""ZR-ENG-CLR-012 Section 18: EXIF/metadata (GPS coordinates, device info --
real PII unrelated to proving identity) must be stripped before an uploaded
image is persisted, not merely hidden from a later viewer."""

from __future__ import annotations

import io

import pytest
from PIL import Image
from PIL.ExifTags import Base as ExifTags

from app.core.identity_uploads import _strip_image_metadata


def _jpeg_with_gps_exif() -> bytes:
    img = Image.new("RGB", (20, 20), color=(120, 60, 200))
    exif = img.getexif()
    exif[ExifTags.Make.value] = "SmokeTestCameraCo"
    exif[ExifTags.GPSInfo.value] = {1: "N", 2: (12, 34, 56.0), 3: "E", 4: (77, 12, 33.0)}
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


class TestExifStripping:
    def test_gps_and_camera_exif_removed_from_jpeg(self):
        original = _jpeg_with_gps_exif()
        original_exif = Image.open(io.BytesIO(original)).getexif()
        assert ExifTags.GPSInfo.value in original_exif or original_exif.get_ifd(0x8825)

        cleaned = _strip_image_metadata(original, ".jpg")
        cleaned_exif = Image.open(io.BytesIO(cleaned)).getexif()
        assert len(cleaned_exif) == 0
        assert not cleaned_exif.get_ifd(0x8825)

    def test_image_content_still_decodes_correctly_after_stripping(self):
        original = _jpeg_with_gps_exif()
        cleaned = _strip_image_metadata(original, ".jpg")
        img = Image.open(io.BytesIO(cleaned))
        img.load()
        assert img.size == (20, 20)

    def test_pdf_passes_through_untouched(self):
        pdf_bytes = b"%PDF-1.4 not an image"
        assert _strip_image_metadata(pdf_bytes, ".pdf") == pdf_bytes

    def test_corrupted_image_bytes_fail_closed_to_original(self):
        garbage = b"\xff\xd8\xff" + b"not actually a valid jpeg"
        assert _strip_image_metadata(garbage, ".jpg") == garbage
