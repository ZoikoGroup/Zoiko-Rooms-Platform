"""ZR-COMMS-EMAIL-001 Section 1.3: every email gets an auditable delivery
record; a repeated trigger (same dedupe key) sends nothing the second time;
failed sends are retried and recorded; no rendered body is stored."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mailer
from app.core.config import settings
from app.models.email_message import EmailMessage
from app.services.email import message_log
from tests.test_mailer import _FakeSMTP, _configure_smtp


@pytest.fixture()
def email_log(db_session: Session, monkeypatch):
    db_session._zr_owned_by_log = False  # message_log must not close the test session
    monkeypatch.setattr(message_log, "_session_factory", lambda: db_session)
    return db_session


@pytest.fixture()
def smtp(monkeypatch):
    _configure_smtp(monkeypatch)
    _FakeSMTP.instances.clear()
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(mailer, "SMTP_RETRY_DELAY_SECONDS", 0)
    return _FakeSMTP


def _rows(db: Session) -> list[EmailMessage]:
    return list(db.scalars(select(EmailMessage).order_by(EmailMessage.id)))


def _sent_count() -> int:
    return sum(len(i.sent_messages) for i in _FakeSMTP.instances)


class TestDeliveryRecord:
    def test_sent_email_is_recorded_without_its_body(self, email_log, smtp):
        mailer.send_payment_confirmed_email("payer@test.com", "Ravi", 399.0, "GBP", payment_id=5)
        [row] = _rows(email_log)
        assert row.template_id == "ZR-EML-PAY-002" and row.template_version == "1.0.0"
        assert row.status == "SENT" and row.provider == "smtp" and row.attempts == 1
        assert row.stream == "money" and row.tier == 0
        assert row.related_entity_type == "simulated_payment" and row.related_entity_id == "5"
        assert len(row.content_hash) == 64 and row.sent_at is not None
        assert not hasattr(row, "subject") and not hasattr(row, "body")

    def test_dev_outbox_delivery_is_recorded_as_outbox(self, email_log, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "email_provider", "file")
        monkeypatch.setattr(mailer, "DEV_MAIL_OUTBOX_DIR", tmp_path)
        mailer.send_welcome_email("new@test.com", "New User")
        [row] = _rows(email_log)
        assert row.status == "OUTBOX" and row.provider == "file"


class TestDeduplication:
    def test_same_event_twice_sends_once(self, email_log, smtp):
        mailer.send_payment_confirmed_email("payer@test.com", "Ravi", 399.0, "GBP", payment_id=5)
        mailer.send_payment_confirmed_email("payer@test.com", "Ravi", 399.0, "GBP", payment_id=5)
        assert _sent_count() == 1
        rows = _rows(email_log)
        assert [r.status for r in rows] == ["SENT", "SUPPRESSED"]
        assert rows[1].duplicate_of_id == rows[0].id

    def test_same_event_to_two_recipients_sends_to_both(self, email_log, smtp):
        mailer.send_agreement_executed_email("renter@test.com", "R", "Room", agreement_id=2)
        mailer.send_agreement_executed_email("host@test.com", "H", "Room", agreement_id=2, recipient_is_host=True)
        assert _sent_count() == 2

    def test_different_events_are_not_deduplicated(self, email_log, smtp):
        mailer.send_payment_confirmed_email("payer@test.com", "Ravi", 10.0, "GBP", payment_id=1)
        mailer.send_payment_confirmed_email("payer@test.com", "Ravi", 10.0, "GBP", payment_id=2)
        assert _sent_count() == 2

    def test_codes_are_never_deduplicated(self, email_log, smtp):
        mailer.send_payout_beneficiary_verification_code_email("h@test.com", "H", "111111", 15)
        mailer.send_payout_beneficiary_verification_code_email("h@test.com", "H", "222222", 15)
        assert _sent_count() == 2


class TestFailureAndRetry:
    def test_transient_failure_is_retried_then_recorded_sent(self, email_log, smtp, monkeypatch):
        calls = {"n": 0}

        class _FlakySMTP(_FakeSMTP):
            def send_message(self, message):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise ConnectionResetError("reset")
                super().send_message(message)

        monkeypatch.setattr(mailer.smtplib, "SMTP", _FlakySMTP)
        assert mailer.deliver("p@test.com", _payment_message()) is True
        [row] = _rows(email_log)
        assert row.status == "SENT" and row.attempts == 2

    def test_permanent_failure_is_recorded_and_can_be_resent_later(self, email_log, smtp, monkeypatch):
        class _DownSMTP(_FakeSMTP):
            def __enter__(self):
                raise ConnectionRefusedError("down")

        monkeypatch.setattr(mailer.smtplib, "SMTP", _DownSMTP)
        assert mailer.send_payment_confirmed_email("p@test.com", "R", 1.0, "GBP", payment_id=9) is None
        [row] = _rows(email_log)
        assert row.status == "FAILED" and row.attempts == mailer.SMTP_ATTEMPTS
        assert "ConnectionRefusedError" in row.last_error

        monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)
        mailer.send_payment_confirmed_email("p@test.com", "R", 1.0, "GBP", payment_id=9)
        [row] = _rows(email_log)  # same record reclaimed, not a duplicate row
        assert row.status == "SENT" and row.attempts == mailer.SMTP_ATTEMPTS + 1

    def test_logging_failure_never_blocks_the_email(self, smtp, monkeypatch):
        def broken():
            raise RuntimeError("db down")

        monkeypatch.setattr(message_log, "_session_factory", broken)
        mailer.send_payment_confirmed_email("p@test.com", "R", 1.0, "GBP", payment_id=1)
        assert _sent_count() == 1


def _payment_message():
    from app.services.email import registry
    from app.services.email.design import Message

    return Message(spec=registry.get("ZR-EML-PAY-002"), subject_vars={"amount_localized": "£1.00 (GBP)"},
                   dedupe_key="PAY-002:test-retry")
