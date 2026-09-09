"""Tests for the existing mailer's dev-outbox and SMTP paths -- no real network
or credentials are used or required. These prove:
  - the default (unconfigured) path writes a real, readable file, not just a
    "pretend it worked" stub;
  - the SMTP path actually builds and would send a correct message once
    SMTP_HOST/SMTP_USERNAME/SMTP_PASSWORD are set (verified via a fake
    smtplib.SMTP standing in for a real server -- this is NOT a test of real
    inbox delivery, see the final report);
  - a missing SMTP config, or a raised exception talking to the server, is
    logged and returns False -- it never raises into the caller.
"""

from __future__ import annotations

import logging

from app.core import mailer
from app.core.config import settings


def test_dev_outbox_writes_a_real_file(tmp_path, monkeypatch):
    monkeypatch.setattr(mailer, "DEV_MAIL_OUTBOX_DIR", tmp_path)
    monkeypatch.setattr(settings, "email_provider", "file")

    sent = mailer.send_email(
        "outbox-test@example.com", "Test subject",
        heading="Test heading", body_lines=["line one", "line two"],
    )

    assert sent is True
    files = list(tmp_path.glob("*.txt"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "Test subject" in content
    assert "line one" in content
    assert "outbox-test@example.com" in content


class _FakeSMTP:
    """Stands in for smtplib.SMTP -- records what a real send would have done
    without opening a socket. starttls/login/send_message are the exact calls
    _send_via_smtp makes; asserting on them proves the message is built and
    dispatched correctly, not that a real inbox received anything."""

    instances: list["_FakeSMTP"] = []

    def __init__(self, host, port, timeout=10):
        self.host = host
        self.port = port
        self.started_tls = False
        self.logged_in_as = None
        self.sent_messages = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.logged_in_as = (username, password)

    def send_message(self, message):
        self.sent_messages.append(message)


def _configure_smtp(monkeypatch, *, host="smtp.test.internal"):
    monkeypatch.setattr(settings, "email_provider", "smtp")
    monkeypatch.setattr(settings, "smtp_host", host)
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_username", "test-user")
    monkeypatch.setattr(settings, "smtp_password", "test-pass")
    monkeypatch.setattr(settings, "smtp_use_tls", True)
    monkeypatch.setattr(settings, "email_from", "Zoiko Rooms <no-reply@zoikorooms.com>")


def test_smtp_path_sends_a_correctly_built_message(monkeypatch):
    _configure_smtp(monkeypatch)
    _FakeSMTP.instances.clear()
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)

    sent = mailer.send_email(
        "renter@example.com", "Your payment has been received",
        heading="Payment received", body_lines=["We've recorded your payment."],
    )

    assert sent is True
    assert len(_FakeSMTP.instances) == 1
    fake = _FakeSMTP.instances[0]
    assert fake.started_tls is True
    assert fake.logged_in_as == ("test-user", "test-pass")
    assert len(fake.sent_messages) == 1
    message = fake.sent_messages[0]
    assert message["To"] == "renter@example.com"
    assert message["Subject"] == "Your payment has been received"


def test_smtp_not_configured_fails_safely_and_logs(monkeypatch, caplog):
    monkeypatch.setattr(settings, "email_provider", "smtp")
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_username", "")
    monkeypatch.setattr(settings, "smtp_password", "")

    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        sent = mailer.send_email("renter@example.com", "Subject", heading="H", body_lines=["body"])

    assert sent is False
    assert any("SMTP_HOST/SMTP_USERNAME/SMTP_PASSWORD" in r.message for r in caplog.records)


def test_smtp_server_error_is_caught_not_raised(monkeypatch, caplog):
    _configure_smtp(monkeypatch)

    class _ExplodingSMTP(_FakeSMTP):
        def __enter__(self):
            raise ConnectionRefusedError("no route to host")

    monkeypatch.setattr(mailer.smtplib, "SMTP", _ExplodingSMTP)

    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        sent = mailer.send_email("renter@example.com", "Subject", heading="H", body_lines=["body"])

    assert sent is False
    assert any("failed to send email" in r.message for r in caplog.records)
