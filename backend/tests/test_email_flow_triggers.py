"""The rental flow sends the right ZR-COMMS-EMAIL-001 template at each step:
offer sent / revised / counter-proposed / counter answered / accepted /
declined, agreement ready, signature status, booking confirmed (both SIGNED
paths), signature deadline reminders, and rent/deposit due-soon."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.crud import leasing as leasing_crud
from tests.conftest import auth_user_cookie
from tests.test_host_offer_agreement_decisions import _make_agreement_eligible, _submit_and_approve_application
from tests.test_self_listing_restriction import _make_host_and_listing, _make_verified_renter

START = date.today() + timedelta(days=5)


@pytest.fixture()
def sent(monkeypatch):
    """Captures every template the flow asks the mailer to send."""
    calls: list[tuple[str, str, dict]] = []
    for name in (
        "send_offer_issued_email", "send_offer_outcome_email", "send_agreement_ready_email",
        "send_signature_status_email", "send_agreement_executed_email",
    ):
        def fake(to_email, full_name, listing_name, _name=name, **kw):
            calls.append((_name, to_email, kw))
        monkeypatch.setattr(leasing_crud, name, fake)
    return calls


def _names(calls, to=None):
    return [(n, kw.get("variant")) for n, t, kw in calls if to is None or t == to]


def _sent_offer(client, db: Session, suffix: str):
    host, listing_id = _make_host_and_listing(db, email=f"flowmail-host-{suffix}@test.com")
    renter = _make_verified_renter(db, email=f"flowmail-renter-{suffix}@test.com")
    application_id = _submit_and_approve_application(client, host, renter, listing_id)
    hc = auth_user_cookie(host)
    offer_id = client.post(f"/api/users/hosting/applications/{application_id}/offers", cookies=hc).json()["id"]
    client.post(f"/api/users/hosting/offers/{offer_id}/terms",
                json={"monthlyRent": 500, "depositAmount": 500, "startDate": START.isoformat(), "termMonths": 6}, cookies=hc)
    assert client.post(f"/api/users/hosting/offers/{offer_id}/send", cookies=hc).status_code == 200
    return host, renter, listing_id, offer_id


class TestOfferEmails:
    def test_sent_offer_emails_the_renter(self, client, db_session, sent):
        _, renter, _, _ = _sent_offer(client, db_session, "a")
        name, to, kw = sent[-1]
        assert name == "send_offer_issued_email" and to == renter.email and kw["amended"] is False
        assert kw["terms_version"] == 1 and kw["move_in"] == START

    def test_counter_proposal_and_answers(self, client, db_session, sent):
        host, renter, _, offer_id = _sent_offer(client, db_session, "b")
        rc, hc = auth_user_cookie(renter), auth_user_cookie(host)

        client.post(f"/api/users/rentals/offers/{offer_id}/counter", json={"monthlyRent": 450, "depositAmount": 450}, cookies=rc)
        assert ("send_offer_outcome_email", "counter-proposal-received") in _names(sent, host.email)

        counter_id = client.get(f"/api/users/hosting/offers/{offer_id}", cookies=hc).json()["counterProposals"][0]["id"]
        client.post(f"/api/users/hosting/offers/{offer_id}/counters/{counter_id}/reject", cookies=hc)
        assert ("send_offer_outcome_email", "counter-proposal-declined") in _names(sent, renter.email)

        client.post(f"/api/users/rentals/offers/{offer_id}/counter", json={"monthlyRent": 460, "depositAmount": 460}, cookies=rc)
        counter_id = client.get(f"/api/users/hosting/offers/{offer_id}", cookies=hc).json()["counterProposals"][-1]["id"]
        client.post(f"/api/users/hosting/offers/{offer_id}/counters/{counter_id}/accept", json={"note": "OK"}, cookies=hc)
        amended = [kw for n, t, kw in sent if n == "send_offer_issued_email" and kw["amended"]]
        assert amended and amended[-1]["terms_version"] == 2 and amended[-1]["host_note"] == "OK"

    def test_accept_emails_both_parties(self, client, db_session, sent):
        host, renter, _, offer_id = _sent_offer(client, db_session, "c")
        client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(renter))
        assert ("send_offer_outcome_email", "accepted") in _names(sent, renter.email)
        assert ("send_offer_outcome_email", "accepted-host-copy") in _names(sent, host.email)

    def test_decline_emails_both_parties(self, client, db_session, sent):
        host, renter, _, offer_id = _sent_offer(client, db_session, "d")
        client.post(f"/api/users/rentals/offers/{offer_id}/decline", cookies=auth_user_cookie(renter))
        assert ("send_offer_outcome_email", "declined") in _names(sent, renter.email)
        assert ("send_offer_outcome_email", "declined-host-copy") in _names(sent, host.email)


def _sent_agreement(client, db, suffix):
    host, renter, listing_id, offer_id = _sent_offer(client, db, suffix)
    client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(renter))
    _make_agreement_eligible(db, listing_id)
    hc = auth_user_cookie(host)
    agreement_id = client.post(f"/api/users/hosting/offers/{offer_id}/agreement", cookies=hc).json()["id"]
    assert client.post(f"/api/users/hosting/agreements/{agreement_id}/send", cookies=hc).status_code == 200
    return host, renter, agreement_id


class TestAgreementEmails:
    def test_agreement_ready_and_signature_status(self, client, db_session, sent):
        host, renter, agreement_id = _sent_agreement(client, db_session, "e")
        assert ("send_agreement_ready_email", None) in _names(sent, renter.email)

        from tests.test_host_offer_agreement_decisions import _host_deliver_all_disclosures

        _host_deliver_all_disclosures(client, auth_user_cookie(host), agreement_id)
        r = client.post(f"/api/users/hosting/agreements/{agreement_id}/sign", cookies=auth_user_cookie(host))
        assert r.status_code == 200, r.text
        assert ("send_signature_status_email", "signature-requested") in _names(sent, renter.email)

    def test_deadline_reminder_job_emails_pending_signer(self, client, db_session, sent):
        _, renter, agreement_id = _sent_agreement(client, db_session, "f")
        from app.models.leasing import Agreement

        agreement = db_session.get(Agreement, agreement_id)
        agreement.offer.confirmation_expires_at = datetime.now(timezone.utc) + timedelta(hours=5)
        db_session.commit()
        sent.clear()
        assert leasing_crud.send_signature_deadline_reminders(db_session) == 1
        assert ("send_signature_status_email", "reminder") in _names(sent, renter.email)

    def test_booking_confirmed_when_payment_clears_after_signing(self, db_session, sent, monkeypatch):
        """The normal sign-then-pay order reaches SIGNED in
        confirm_agreement_payment -- it must send ZR-EML-BKG-002 too."""
        calls = []
        monkeypatch.setattr(leasing_crud, "_email_booking_confirmed", lambda db, agreement: calls.append(agreement.id))
        monkeypatch.setattr(leasing_crud, "_all_initial_obligations_paid", lambda agreement: True)
        monkeypatch.setattr(leasing_crud, "freeze_agreement_version", lambda db, agreement: None)
        monkeypatch.setattr(leasing_crud, "_ensure_pending_move_in_occupancy", lambda db, agreement: None)

        class _A:
            id = 77
            status = "PAYMENT_IN_PROGRESS"
            signed_by_provider_at = signed_by_renter_at = datetime.now(timezone.utc)
            payment_session_expires_at = None
            offer = None

        monkeypatch.setattr(leasing_crud, "_notify_offer_guest", lambda *a, **k: None)
        import app.crud.booking_change_requests as bcr

        monkeypatch.setattr(bcr, "_complete_premises_change_if_applicable", lambda db, a: None)
        monkeypatch.setattr(db_session, "refresh", lambda obj: None)
        leasing_crud.confirm_agreement_payment(db_session, _A())
        assert calls == [77]


class TestDueSoonEmails:
    def test_rent_and_deposit_due_soon(self, db_session, monkeypatch):
        from types import SimpleNamespace

        import app.core.mailer as mailer
        from app.services import rental_payment_due_soon as due

        rent_calls, pay_calls = [], []
        monkeypatch.setattr(mailer, "send_rent_reminder_email", lambda *a, **k: rent_calls.append(k))
        monkeypatch.setattr(mailer, "send_payment_due_email", lambda *a, **k: pay_calls.append(k))
        monkeypatch.setattr("app.crud.guest.get_user_for_guest", lambda db, g: SimpleNamespace(email="t@x.com", full_name="T"))
        monkeypatch.setattr("app.crud.user.get_user_by_party_id", lambda db, pid: SimpleNamespace(full_name="Anil"))

        def ob(kind, oid):
            return SimpleNamespace(
                id=oid, obligation_type=kind, tenant=object(), recipient_party_id=1, amount=399, currency="GBP",
                due_date=date(2026, 11, 1), agreement=None, occupancy=None, occupancy_id=None,
                display_label="deposit" if kind == "DEPOSIT" else "rent",
            )

        due._email_due_soon(db_session, ob("RENT", 1))
        due._email_due_soon(db_session, ob("DEPOSIT", 2))
        assert rent_calls[0]["obligation_id"] == 1 and rent_calls[0]["autopay_status"] == "Not set up"
        assert pay_calls[0]["obligation_id"] == 2 and "deposit" in pay_calls[0]["purpose"]
