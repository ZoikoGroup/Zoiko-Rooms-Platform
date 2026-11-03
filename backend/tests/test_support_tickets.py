"""Help & Support tickets (crud/support_ticket.py, routes/support_tickets.py)."""

from sqlalchemy.orm import Session

from app.models.notification import Notification
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie
from tests.test_self_listing_restriction import _make_host_and_listing

PDF = b"%PDF-1.4 test document"


def _file_ticket(client, user, **extra):
    data = {"category": "payments", "issue": "Payment record mismatch", "description": "Rent shows unpaid", **extra}
    return client.post("/api/users/support/tickets", data=data, cookies=auth_user_cookie(user))


class TestSupportTickets:
    def test_full_lifecycle_with_attachments_and_notifications(self, client, db_session: Session):
        user = _make_user(db_session, email="sup-user-a@test.com")
        admin = _make_admin(db_session, email="sup-admin-a@test.com")
        db_session.commit()
        user_cookies, admin_cookies = auth_user_cookie(user), auth_admin_cookie(admin)

        r = client.post(
            "/api/users/support/tickets",
            data={"category": "payments", "issue": "Payment record mismatch", "description": "Rent shows unpaid",
                  "timeSensitive": "true", "replyChannel": "app"},
            files=[("files", ("receipt.pdf", PDF, "application/pdf"))],
            cookies=user_cookies,
        )
        assert r.status_code == 201, r.text
        ticket = r.json()
        ref = ticket["ref"]
        assert ref.startswith("ZR-SUP-") and ticket["status"] == "SUBMITTED" and ticket["timeSensitive"] is True
        assert [m["sender"] for m in ticket["messages"]] == ["USER"]
        [attachment] = ticket["attachments"]
        assert attachment["originalFilename"] == "receipt.pdf"

        r = client.get(f"/api/users/support/tickets/{ref}/attachments/{attachment['id']}", cookies=user_cookies)
        assert r.status_code == 200 and r.content == PDF

        # Support sees it, asks for something specific.
        [admin_ticket] = client.get("/api/admin/support/tickets", cookies=admin_cookies).json()
        assert admin_ticket["userEmail"] == "sup-user-a@test.com"
        tid = admin_ticket["id"]
        r = client.post(f"/api/admin/support/tickets/{tid}/status", json={"status": "ACTION_REQUIRED"}, cookies=admin_cookies)
        assert r.status_code == 422  # must say what's needed
        r = client.post(
            f"/api/admin/support/tickets/{tid}/status",
            json={"status": "ACTION_REQUIRED", "actionRequest": "Bank statement for October"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        # The user's reply with a file puts it back in review.
        r = client.post(
            f"/api/users/support/tickets/{ref}/messages", data={"body": "Here it is"},
            files=[("files", ("statement.pdf", PDF, "application/pdf"))], cookies=user_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "IN_REVIEW" and r.json()["actionRequest"] is None
        assert len(r.json()["attachments"]) == 2

        # Two support replies: both reach the user (no dedupe swallowing the second).
        for text in ("Thanks, checking now.", "Found the issue."):
            assert client.post(f"/api/admin/support/tickets/{tid}/messages", json={"body": text}, cookies=admin_cookies).status_code == 200
        replies = db_session.query(Notification).filter(
            Notification.recipient_user_id == user.id, Notification.notification_type == "support_ticket.support_replied",
        ).count()
        assert replies == 2

        r = client.post(
            f"/api/admin/support/tickets/{tid}/status",
            json={"status": "RESOLVED", "resolution": "Payment re-recorded as paid."}, cookies=admin_cookies,
        )
        assert r.json()["status"] == "RESOLVED"

        # Resolved: replying is refused, reopening works.
        assert client.post(f"/api/users/support/tickets/{ref}/messages", data={"body": "x"}, cookies=user_cookies).status_code == 409
        r = client.post(f"/api/users/support/tickets/{ref}/reopen", json={"reason": "Still unpaid"}, cookies=user_cookies)
        assert r.status_code == 200 and r.json()["status"] == "IN_REVIEW"

        r = client.post(
            f"/api/admin/support/tickets/{tid}/status", json={"status": "CLOSED", "resolution": "Done."}, cookies=admin_cookies,
        )
        assert r.json()["status"] == "CLOSED"
        assert client.post(f"/api/users/support/tickets/{ref}/reopen", json={}, cookies=user_cookies).status_code == 409

    def test_tickets_are_private_to_their_owner(self, client, db_session: Session):
        owner = _make_user(db_session, email="sup-owner-b@test.com")
        other = _make_user(db_session, email="sup-other-b@test.com")
        db_session.commit()
        ref = _file_ticket(client, owner).json()["ref"]
        assert client.get(f"/api/users/support/tickets/{ref}", cookies=auth_user_cookie(other)).status_code == 404
        assert client.get("/api/users/support/tickets", cookies=auth_user_cookie(other)).json() == []
        r = client.post(f"/api/users/support/tickets/{ref}/messages", data={"body": "hi"}, cookies=auth_user_cookie(other))
        assert r.status_code == 404

    def test_linked_item_must_belong_to_the_user(self, client, db_session: Session):
        host, _listing_id = _make_host_and_listing(db_session, email="sup-host-c@test.com")
        stranger = _make_user(db_session, email="sup-stranger-c@test.com")
        db_session.commit()
        from app.models.property import Property

        prop_id = db_session.query(Property).filter(Property.owner_party_id == host.party_id).first().id
        r = _file_ticket(client, host, category="listings", issue="Property data", linkedType="property", linkedId=str(prop_id))
        assert r.status_code == 201, r.text
        assert r.json()["linkedId"] == prop_id
        r = _file_ticket(client, stranger, category="listings", issue="Property data", linkedType="property", linkedId=str(prop_id))
        assert r.status_code == 404

    def test_rejects_bad_input(self, client, db_session: Session):
        user = _make_user(db_session, email="sup-user-d@test.com")
        db_session.commit()
        assert _file_ticket(client, user, category="nonsense").status_code == 422
        assert _file_ticket(client, user, description="   ").status_code == 422
        r = client.post(
            "/api/users/support/tickets",
            data={"category": "tech", "issue": "Page error", "description": "broken"},
            files=[("files", ("evil.exe", b"MZ\x90\x00", "application/octet-stream"))],
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 400
        assert client.get("/api/users/support/tickets", cookies=auth_user_cookie(user)).json() == []


class _Hit:
    def __init__(self, title: str, content: str):
        self.document = type("Doc", (), {"title": title})()
        self.chunk = type("Chunk", (), {"content": content})()


class TestSupportAutomation:
    def _no_llm(self, monkeypatch, hits):
        from app.core.config import settings
        from app.services import support_assistant

        monkeypatch.setattr(settings, "groq_api_key", "")
        monkeypatch.setattr(support_assistant, "_articles", lambda db, ticket: hits)

    def test_knowledge_base_match_posts_an_automatic_first_reply(self, client, db_session: Session, monkeypatch):
        self._no_llm(monkeypatch, [_Hit("Paying rent", "Rent is paid to the host's payment details shown on your rental.")])
        user = _make_user(db_session, email="sup-auto-a@test.com")
        db_session.commit()
        ref = _file_ticket(client, user).json()["ref"]

        r = client.get(f"/api/users/support/tickets/{ref}", cookies=auth_user_cookie(user))
        senders = [m["sender"] for m in r.json()["messages"]]
        assert senders == ["USER", "ASSISTANT"]
        assert "Paying rent" in r.json()["messages"][1]["body"]
        assert r.json()["status"] == "SUBMITTED", "support still sees it until the user says it's solved"

        r = client.post(f"/api/users/support/tickets/{ref}/solved", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "RESOLVED"
        assert client.post(f"/api/users/support/tickets/{ref}/solved", cookies=auth_user_cookie(user)).status_code == 409

    def test_no_match_or_safety_ticket_gets_no_automatic_reply(self, client, db_session: Session, monkeypatch):
        user = _make_user(db_session, email="sup-auto-b@test.com")
        db_session.commit()
        self._no_llm(monkeypatch, [])
        ref = _file_ticket(client, user).json()["ref"]
        r = client.get(f"/api/users/support/tickets/{ref}", cookies=auth_user_cookie(user))
        assert [m["sender"] for m in r.json()["messages"]] == ["USER"]

        self._no_llm(monkeypatch, [_Hit("Safety", "Call emergency services.")])
        ref = _file_ticket(client, user, category="safety", issue="Unsafe room").json()["ref"]
        r = client.get(f"/api/users/support/tickets/{ref}", cookies=auth_user_cookie(user))
        assert [m["sender"] for m in r.json()["messages"]] == ["USER"]

    def test_quiet_tickets_are_closed_automatically(self, client, db_session: Session, monkeypatch):
        from datetime import datetime, timedelta, timezone

        from app.models.support_ticket import SupportTicket
        from app.services.support_assistant import close_inactive_tickets

        self._no_llm(monkeypatch, [])
        user = _make_user(db_session, email="sup-auto-c@test.com")
        db_session.commit()
        waiting = db_session.query(SupportTicket).filter_by(ref=_file_ticket(client, user).json()["ref"]).one()
        resolved = db_session.query(SupportTicket).filter_by(ref=_file_ticket(client, user).json()["ref"]).one()
        fresh = db_session.query(SupportTicket).filter_by(ref=_file_ticket(client, user).json()["ref"]).one()
        old = datetime.now(timezone.utc) - timedelta(days=15)
        waiting.status, waiting.action_request, waiting.updated_at = "ACTION_REQUIRED", "Send a receipt", old
        resolved.status, resolved.resolution, resolved.resolved_at, resolved.updated_at = "RESOLVED", "Fixed", old, old
        fresh.status, fresh.action_request = "ACTION_REQUIRED", "Send a receipt"
        db_session.commit()

        assert close_inactive_tickets(db_session) == 2
        for t in (waiting, resolved, fresh):
            db_session.refresh(t)
        assert waiting.status == "CLOSED" and resolved.status == "CLOSED" and fresh.status == "ACTION_REQUIRED"


class TestSupportEmailAndBadge:
    def _sent(self, monkeypatch):
        sent = []
        monkeypatch.setattr("app.core.mailer.send_email", lambda to, subject, **kw: sent.append((to, subject, kw)) or True)
        return sent

    def test_reply_and_status_change_are_emailed_when_customer_chose_email(self, client, db_session: Session, monkeypatch):
        sent = self._sent(monkeypatch)
        user = _make_user(db_session, email="sup-mail-user@test.com")
        admin = _make_admin(db_session, email="sup-mail-admin@test.com")
        db_session.commit()
        tid = _file_ticket(client, user, replyChannel="both").json()["ref"]
        admin_cookies = auth_admin_cookie(admin)
        [t] = client.get("/api/admin/support/tickets", cookies=admin_cookies).json()

        r = client.post(f"/api/admin/support/tickets/{t['id']}/messages", json={"body": "Looking into it"}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        r = client.post(
            f"/api/admin/support/tickets/{t['id']}/status", json={"status": "RESOLVED", "resolution": "Fixed the record"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert [s[0] for s in sent] == ["sup-mail-user@test.com"] * 2
        assert "Looking into it" in sent[0][2]["body_lines"][0] and tid in sent[0][1]
        assert "Fixed the record" in sent[1][2]["body_lines"][0]

    def test_no_email_when_customer_chose_app_only(self, client, db_session: Session, monkeypatch):
        sent = self._sent(monkeypatch)
        user = _make_user(db_session, email="sup-app-user@test.com")
        admin = _make_admin(db_session, email="sup-app-admin@test.com")
        db_session.commit()
        _file_ticket(client, user, replyChannel="app")
        admin_cookies = auth_admin_cookie(admin)
        [t] = client.get("/api/admin/support/tickets", cookies=admin_cookies).json()
        client.post(f"/api/admin/support/tickets/{t['id']}/messages", json={"body": "Hi"}, cookies=admin_cookies)
        assert sent == []

    def test_attention_count_tracks_cases_waiting_on_support(self, client, db_session: Session):
        user = _make_user(db_session, email="sup-badge-user@test.com")
        admin = _make_admin(db_session, email="sup-badge-admin@test.com")
        db_session.commit()
        admin_cookies, user_cookies = auth_admin_cookie(admin), auth_user_cookie(user)
        count = lambda: client.get("/api/admin/support/tickets/attention-count", cookies=admin_cookies).json()["count"]
        assert count() == 0

        ref = _file_ticket(client, user, replyChannel="app").json()["ref"]
        assert count() == 1  # new, not picked up
        [t] = client.get("/api/admin/support/tickets", cookies=admin_cookies).json()
        client.post(f"/api/admin/support/tickets/{t['id']}/messages", json={"body": "On it"}, cookies=admin_cookies)
        assert count() == 0  # support answered, ball is with the customer
        client.post(f"/api/users/support/tickets/{ref}/messages", data={"body": "Thanks, one more thing"}, cookies=user_cookies)
        assert count() == 1  # the customer spoke last
