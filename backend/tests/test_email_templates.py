"""ZR-COMMS-EMAIL-001 template rules: every registered template renders in
the approved design with its preheader, footer blocks and plain-text twin;
messages go out on the right stream with template headers; codes never leak
into subject/preheader; alert emails support one-click unsubscribe."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.core import mailer
from app.core.config import settings
from app.services.email import blocks, registry
from app.services.email.design import Message, render_html, render_text
from tests.test_mailer import _FakeSMTP, _configure_smtp


@pytest.fixture()
def smtp(monkeypatch):
    _configure_smtp(monkeypatch)
    monkeypatch.setattr(settings, "email_from", "Zoiko Rooms <mailer@zoikorooms.com>")
    _FakeSMTP.instances.clear()
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)
    return _FakeSMTP


def _sent(smtp):
    return smtp.instances[-1].sent_messages[-1]


def _body(message, subtype):
    return next(p for p in message.walk() if p.get_content_type() == f"text/{subtype}").get_content()


ALL_SENDERS = [
    ("ZR-EML-VER-002", lambda: mailer.send_authority_status_email(
        "a@x.com", "Ravi", status_display="Action required", variant="action-required",
        message="Add the evidence still required for your role.", property_label="Property #6", verification_id=9)),
    ("ZR-EML-ID-002", lambda: mailer.send_welcome_email("a@x.com", "Ravi Kumar")),
    ("ZR-EML-ID-006", lambda: mailer.send_password_reset_email("a@x.com", "https://app/reset?t=1", 30, "Ravi")),
    ("ZR-EML-ID-003", lambda: mailer.send_payout_beneficiary_verification_code_email("a@x.com", "Ravi", "482913", 15)),
    ("ZR-EML-ID-003", lambda: mailer.send_rental_payment_instruction_verification_code_email("a@x.com", "Ravi", "482913", 15)),
    ("ZR-EML-VER-001", lambda: mailer.send_identity_verification_approved_email("a@x.com", "Ravi", verification_id=7)),
    ("ZR-EML-VER-001", lambda: mailer.send_identity_verification_rejected_email("a@x.com", "Ravi", "Blurry", verification_id=7)),
    ("ZR-EML-VER-001", lambda: mailer.send_identity_verification_additional_evidence_email("a@x.com", "Ravi", "", verification_id=7)),
    ("ZR-EML-LST-002", lambda: mailer.send_listing_published_email("a@x.com", "Ravi", "Sunny room", listing_id="L-1", market_name="London", min_stay_nights=30)),
    ("ZR-EML-LST-001", lambda: mailer.send_listing_rejected_email("a@x.com", "Ravi", "Sunny room", "Photos missing")),
    ("ZR-EML-MKT-001", lambda: mailer.send_alert_confirmation_email("a@x.com", "London", "https://api/unsub?t=1", max_price=900)),
    ("ZR-EML-MKT-002", lambda: mailer.send_alert_match_email("a@x.com", "London", ["Room A", "Room B"], "https://api/unsub?t=1")),
    ("ZR-EML-APP-004", lambda: mailer.send_application_decided_email("a@x.com", "Ravi", "Sunny room", True, application_id=3)),
    ("ZR-EML-BKG-002", lambda: mailer.send_agreement_executed_email("a@x.com", "Ravi", "Sunny room", agreement_id=2, move_in=date(2026, 10, 1), term_summary="11 months", payment_summary="£399.00 (GBP) rent")),
    ("ZR-EML-PAY-002", lambda: mailer.send_payment_confirmed_email("a@x.com", "Ravi", 399.0, "GBP", payment_id=5, paid_at=datetime(2026, 10, 1, 6, 11, tzinfo=timezone.utc), method_class="CARD")),
    ("ZR-EML-DEP-003", lambda: mailer.send_deposit_status_email("a@x.com", "Ravi", 120.0, True, held_amount=150.0, currency="GBP", deposit_id=4, room_label="Sunny room")),
    ("ZR-EML-RFD-001", lambda: mailer.send_refund_completed_email("a@x.com", "Ravi", 50.0, "GBP", refund_id=9)),
    ("ZR-EML-PYO-001", lambda: mailer.send_payout_paid_email("a@x.com", "Ravi", 349.0, "GBP", "2026-10", payout_id=6)),
    ("ZR-EML-OFR-001", lambda: mailer.send_offer_issued_email("a@x.com", "Ravi", "Sunny room", offer_id=4, terms_version=1, move_in=date(2026, 10, 1), rent_text="£399.00 (GBP) per month", deposit_text="£150.00 (GBP)", term_months=11)),
    ("ZR-EML-OFR-002", lambda: mailer.send_offer_outcome_email("a@x.com", "Ravi", "Sunny room", offer_id=4, variant="accepted", status_display="accepted")),
    ("ZR-EML-AGR-001", lambda: mailer.send_agreement_ready_email("a@x.com", "Ravi", "Sunny room", agreement_id=2, version_no=1, agreement_type="Room share agreement", required_signers="Host and renter")),
    ("ZR-EML-AGR-002", lambda: mailer.send_signature_status_email("a@x.com", "Ravi", "Sunny room", agreement_id=2, variant="signature-requested", signature_status="requested", completed_signers="Host", pending_signers="You (renter)")),
    ("ZR-EML-PAY-001", lambda: mailer.send_payment_due_email("a@x.com", "Ravi", obligation_id=8, purpose="the deposit for Sunny room", amount=150.0, currency="GBP", due_date=date(2026, 10, 1), payee="Anil (your host), paid directly")),
    ("ZR-EML-RENT-001", lambda: mailer.send_rent_reminder_email("a@x.com", "Ravi", obligation_id=9, amount=399.0, currency="GBP", due_date=date(2026, 11, 1), room_label="Sunny room", payee="Anil (your host), paid directly")),
]


class TestEveryTemplate:
    @pytest.mark.parametrize("template_id,send", ALL_SENDERS)
    def test_renders_in_the_approved_design_on_its_stream(self, smtp, template_id, send):
        send()
        spec = registry.get(template_id)
        message = _sent(smtp)
        html, text = _body(message, "html"), _body(message, "plain")

        assert message["X-Zoiko-Template"] == f"{template_id}@{spec.version}"
        assert message["X-Zoiko-Message-Class"] == f"tier-{spec.tier}"
        assert message["From"] == f"{spec.stream.display_name} <mailer@zoikorooms.com>"
        # Design system (Section 4.1)
        assert "max-width:600px" in html and "#12243F" in html and "#0B9C90" in html and "#5B47F5" in html
        assert "font-size:16px" in html
        assert 'name="color-scheme" content="light dark"' in html
        assert "<img" not in html  # no image-only meaning, no tracking pixels
        # Footer blocks + corporate identity in both versions (Section 05)
        for key in spec.blocks:
            assert blocks.BLOCKS[key].text in text
        assert blocks.SERVICE.text in text
        assert blocks.CORPORATE_IDENTITY in text
        assert blocks.CORPORATE_IDENTITY.replace("'", "&#x27;") in html
        # Plain text carries the CTA destination when the template has one
        if spec.cta_label:
            assert spec.cta_label in text

    def test_every_register_entry_has_a_sender(self):
        assert {tid for tid, _ in ALL_SENDERS} == set(registry.REGISTRY)


class TestSecurityRules:
    def test_one_time_code_never_appears_in_subject_or_preheader(self, smtp):
        mailer.send_payout_beneficiary_verification_code_email("a@x.com", "Ravi", "482913", 15)
        message = _sent(smtp)
        assert "482913" not in message["Subject"]
        spec = registry.get("ZR-EML-ID-003")
        assert "482913" not in spec.preheader
        assert "482913" in _body(message, "plain")
        assert message["From"].startswith("Zoiko Rooms Security")

    def test_dynamic_values_are_html_escaped(self, smtp):
        mailer.send_listing_rejected_email("a@x.com", "Ravi", "<script>alert(1)</script>", "x")
        html = _body(_sent(smtp), "html")
        assert "<script>" not in html and "&lt;script&gt;" in html


class TestUnsubscribe:
    def test_alert_emails_carry_rfc8058_one_click_headers(self, smtp):
        mailer.send_alert_match_email("a@x.com", "London", ["Room A"], "https://api.example/unsub?token=t")
        message = _sent(smtp)
        assert message["List-Unsubscribe"] == "<https://api.example/unsub?token=t>"
        assert message["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    def test_transactional_emails_have_no_unsubscribe(self, smtp):
        mailer.send_payment_confirmed_email("a@x.com", "Ravi", 10.0, "GBP")
        assert _sent(smtp)["List-Unsubscribe"] is None

    def test_one_click_post_endpoint_unsubscribes(self, client, db_session):
        r = client.post("/api/public/alerts", json={"email": "alert@test.com", "city": "London"})
        assert r.status_code == 201, r.text
        from app.models.room_alert import RoomAlert

        alert = db_session.get(RoomAlert, r.json()["id"])
        r = client.post(f"/api/public/alerts/{alert.id}/unsubscribe?token={alert.unsubscribe_token}",
                        data="List-Unsubscribe=One-Click")
        assert r.status_code == 200
        db_session.refresh(alert)
        assert alert.is_active is False


class TestStreams:
    def test_stream_address_override(self, smtp, monkeypatch):
        monkeypatch.setattr(settings, "email_from_money", "Zoiko Rooms Payments <payments@pay.zoikorooms.com>")
        mailer.send_refund_completed_email("a@x.com", "Ravi", 5.0, "GBP")
        assert _sent(smtp)["From"] == "Zoiko Rooms Payments <payments@pay.zoikorooms.com>"

    def test_plain_text_has_same_amount_and_cta_as_html(self):
        m = Message(spec=registry.get("ZR-EML-PAY-002"), subject_vars={"amount_localized": "£399.00 (GBP)"},
                    facts=[("Amount", "£399.00 (GBP)")], cta_url="https://app/pay")
        text, html = render_text(m), render_html(m)
        assert "£399.00 (GBP)" in text and "£399.00 (GBP)" in html
        assert "View Receipt: https://app/pay" in text and 'href="https://app/pay"' in html
