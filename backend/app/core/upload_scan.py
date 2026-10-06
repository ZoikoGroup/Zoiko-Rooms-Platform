"""Upload safety checks for evidence files (ZR-AUTHORITY-002 Section 13:
"Scan uploads for malware and validate MIME/content; protect against
decompression bombs and malformed PDFs/images").

inspect() runs the structural checks every time and the malware scan when a
ClamAV daemon is configured (settings.clamav_host). It returns a scan status:

- CLEAN        -- the scanner answered OK
- NOT_SCANNED  -- no scanner configured; structural checks passed
- ERROR        -- a configured scanner couldn't answer; route to a reviewer

A file that fails a structural check or that the scanner flags raises a 400
and is never stored."""

from __future__ import annotations

import io
import socket
import struct
import warnings

from fastapi import HTTPException, status
from PIL import Image

from app.core.config import settings

# Well above any phone photo of a document; bombs are far larger.
MAX_IMAGE_PIXELS = 60_000_000
# PDF features that run code or carry other files -- never needed for evidence.
_PDF_ACTIVE = (b"/JavaScript", b"/JS ", b"/JS(", b"/JS<", b"/Launch", b"/EmbeddedFile", b"/RichMedia", b"/XFA")


def _reject(message: str) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, message)


def _check_image(content: bytes) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(content))
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise _reject("This image is too large to process -- upload a smaller photo or a PDF")
            image.verify()
    except HTTPException:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise _reject("This image is too large to process -- upload a smaller photo or a PDF")
    except Exception:
        raise _reject("This image is damaged or not a real image -- upload it again")


def _check_pdf(content: bytes) -> None:
    if b"%%EOF" not in content[-2048:]:
        raise _reject("This PDF is incomplete or damaged -- upload it again")
    if any(marker in content for marker in _PDF_ACTIVE):
        raise _reject("This PDF contains scripts or attachments we can't accept -- print or save it as a plain PDF")


def clamav_scan(content: bytes) -> str:
    """clamd INSTREAM: CLEAN, INFECTED or ERROR."""
    try:
        with socket.create_connection((settings.clamav_host, settings.clamav_port),
                                      timeout=settings.clamav_timeout_seconds) as sock:
            sock.sendall(b"zINSTREAM\0")
            for start in range(0, len(content), 64 * 1024):
                chunk = content[start:start + 64 * 1024]
                sock.sendall(struct.pack("!L", len(chunk)) + chunk)
            sock.sendall(struct.pack("!L", 0))
            reply = b""
            while not reply.endswith(b"\0"):
                part = sock.recv(4096)
                if not part:
                    break
                reply += part
    except OSError:
        return "ERROR"
    text = reply.rstrip(b"\0").decode("utf-8", "replace")
    if text.endswith("OK"):
        return "CLEAN"
    if text.endswith("FOUND"):
        return "INFECTED"
    return "ERROR"


def inspect(content: bytes, content_type: str) -> str:
    if content_type == "application/pdf":
        _check_pdf(content)
    elif content_type.startswith("image/"):
        _check_image(content)
    if not settings.clamav_host:
        return "NOT_SCANNED"
    result = clamav_scan(content)
    if result == "INFECTED":
        raise _reject("This file can't be accepted. Upload a different copy of the document.")
    return result
