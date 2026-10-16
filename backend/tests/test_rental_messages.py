"""Host <-> renter messaging, one thread per application (crud/rental_message.py)."""

from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.party import Party
from tests.conftest import _make_user
from tests.conftest import auth_user_cookie
from tests.test_host_offer_agreement_decisions import _submit_and_approve_application
from tests.test_self_listing_restriction import _make_host_and_listing, _make_verified_renter


def _setup(client, db_session: Session, suffix: str):
    host, listing_id = _make_host_and_listing(db_session, email=f"msg-host-{suffix}@test.com")
    renter = _make_verified_renter(db_session, email=f"msg-renter-{suffix}@test.com")
    application_id = _submit_and_approve_application(client, host, renter, listing_id)
    return host, renter, application_id


def _notifications_for(db_session: Session, user, application_id: int) -> list[Notification]:
    """Both sides' message notifications (the host's copy is *_for_host)."""
    return (
        db_session.query(Notification)
        .filter(
            Notification.recipient_user_id == user.id,
            Notification.notification_type.like("rental_message.received%"),
            Notification.related_entity_id.like(f"{application_id}:%"),
        )
        .all()
    )


class TestRentalMessages:
    def test_renter_and_host_exchange_messages_with_notifications(self, client, db_session: Session):
        host, renter, application_id = _setup(client, db_session, "a")
        renter_cookies, host_cookies = auth_user_cookie(renter), auth_user_cookie(host)

        r = client.post(f"/api/users/rentals/applications/{application_id}/messages", json={"body": "Is parking included?"}, cookies=renter_cookies)
        assert r.status_code == 201, r.text
        assert r.json()["senderRole"] == "RENTER" and r.json()["mine"] is True
        assert len(_notifications_for(db_session, host, application_id)) == 1

        # The host sees one unread conversation, then reading marks it read.
        r = client.get("/api/users/hosting/conversations", cookies=host_cookies)
        assert r.status_code == 200, r.text
        [conversation] = r.json()
        assert conversation["applicationId"] == application_id
        assert conversation["unreadCount"] == 1
        assert conversation["lastMessagePreview"] == "Is parking included?"

        r = client.get(f"/api/users/hosting/applications/{application_id}/messages", cookies=host_cookies)
        assert [(m["senderRole"], m["mine"]) for m in r.json()] == [("RENTER", False)]
        assert r.json()[0]["readAt"] is not None
        assert client.get("/api/users/hosting/conversations", cookies=host_cookies).json()[0]["unreadCount"] == 0

        r = client.post(f"/api/users/hosting/applications/{application_id}/messages", json={"body": "Yes, one space."}, cookies=host_cookies)
        assert r.status_code == 201, r.text
        assert len(_notifications_for(db_session, renter, application_id)) == 1

        r = client.get(f"/api/users/rentals/applications/{application_id}/messages", cookies=renter_cookies)
        assert [(m["body"], m["mine"]) for m in r.json()] == [("Is parking included?", True), ("Yes, one space.", False)]
        [renter_conversation] = client.get("/api/users/rentals/conversations", cookies=renter_cookies).json()
        assert renter_conversation["counterpartName"]

    def test_other_users_cannot_read_or_post(self, client, db_session: Session):
        _host, _renter, application_id = _setup(client, db_session, "b")
        other_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(other_party)
        db_session.flush()
        other_host = _make_user(db_session, email="msg-otherhost-b@test.com")
        other_host.party_id = other_party.id
        other_renter = _make_verified_renter(db_session, email="msg-otherrenter-b@test.com")
        db_session.commit()

        for cookies, prefix in ((auth_user_cookie(other_renter), "rentals"), (auth_user_cookie(other_host), "hosting")):
            assert client.get(f"/api/users/{prefix}/applications/{application_id}/messages", cookies=cookies).status_code == 403
            r = client.post(f"/api/users/{prefix}/applications/{application_id}/messages", json={"body": "hi"}, cookies=cookies)
            assert r.status_code == 403

    def test_empty_message_is_rejected(self, client, db_session: Session):
        _host, renter, application_id = _setup(client, db_session, "c")
        r = client.post(f"/api/users/rentals/applications/{application_id}/messages", json={"body": ""}, cookies=auth_user_cookie(renter))
        assert r.status_code == 422


class TestSubtenantThread:
    def test_subtenant_gets_their_own_thread_with_the_host(self, client, db_session: Session):
        from app.crud import sublet as sublet_crud
        from app.models.occupancy import Occupancy
        from tests.test_host_move_in_confirmation import _drive_agreement_to_signed

        host, renter, host_cookies, renter_cookies, occupancy_id = _drive_agreement_to_signed(
            client, db_session, host_email="msg-host-sub@test.com", renter_email="msg-renter-sub@test.com",
        )
        assert client.post(f"/api/users/hosting/occupancies/{occupancy_id}/confirm-move-in", cookies=host_cookies).status_code == 200
        application_id = db_session.get(Occupancy, occupancy_id).offer.application_id

        # The original renter talks to the host first.
        assert client.post(
            f"/api/users/rentals/applications/{application_id}/messages", json={"body": "Original renter here"}, cookies=renter_cookies,
        ).status_code == 201

        subtenant = _make_verified_renter(db_session, email="msg-subtenant-sub@test.com")
        db_session.commit()
        sublet_request = sublet_crud.submit_sublet_request(db_session, renter, occupancy_id, subtenant.party_id, "ASSIGNMENT_FULL")
        assert client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve",
            json={"stepUpPassword": "password123", "authorityConfirmed": True}, cookies=host_cookies,
        ).status_code == 200
        sub_cookies = auth_user_cookie(subtenant)

        # The subtenant's rental carries the application id, and their thread starts empty.
        [rental] = client.get("/api/users/rentals/occupancies", cookies=sub_cookies).json()
        assert rental["applicationId"] == application_id
        assert client.get(f"/api/users/rentals/applications/{application_id}/messages", cookies=sub_cookies).json() == []

        assert client.post(
            f"/api/users/rentals/applications/{application_id}/messages", json={"body": "New tenant here"}, cookies=sub_cookies,
        ).status_code == 201

        # The host sees two separate threads and can reply to the subtenant's.
        conversations = client.get("/api/users/hosting/conversations", cookies=host_cookies).json()
        assert len(conversations) == 2
        sub_thread = next(c for c in conversations if c["lastMessagePreview"] == "New tenant here")
        r = client.post(
            f"/api/users/hosting/applications/{application_id}/messages?guestId={sub_thread['guestId']}",
            json={"body": "Welcome!"}, cookies=host_cookies,
        )
        assert r.status_code == 201, r.text
        sub_view = client.get(f"/api/users/rentals/applications/{application_id}/messages", cookies=sub_cookies).json()
        assert [m["body"] for m in sub_view] == ["New tenant here", "Welcome!"]
        original_view = client.get(f"/api/users/rentals/applications/{application_id}/messages", cookies=renter_cookies).json()
        assert [m["body"] for m in original_view] == ["Original renter here"]

    def test_every_message_notifies_not_just_the_first(self, client, db_session: Session):
        host, renter, application_id = _setup(client, db_session, "repeat")
        for text in ("First question", "Second question"):
            r = client.post(
                f"/api/users/rentals/applications/{application_id}/messages", json={"body": text}, cookies=auth_user_cookie(renter),
            )
            assert r.status_code == 201, r.text
        assert len(_notifications_for(db_session, host, application_id)) == 2
