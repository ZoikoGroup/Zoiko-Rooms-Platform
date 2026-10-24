"""ZR-46: the admin Rent payment support lookup finds obligations the way
people refer to them -- the number, "#5", the "ZR-R-5" reference renters and
hosts see, or a payment reference -- and lists recent ones with no query."""

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie
from tests.test_rental_payment_records import _make_obligation

URL = "/api/finance/rental-payments/obligations"


class TestObligationSearch:
    def test_finds_by_id_reference_and_payment_reference(self, client, db_session: Session):
        first, _t1, _r1 = _make_obligation(db_session)
        second, _t2, _r2 = _make_obligation(db_session)
        second.payment_reference = "ZR-SUB-ABC123"
        db_session.commit()
        admin = _make_admin(db_session, email="ob-search@test.com", role="super_admin")
        db_session.commit()
        cookies = auth_admin_cookie(admin)

        for q in (str(first.id), f"#{first.id}", f"ZR-R-{first.id}", f"zr-r-{first.id}"):
            r = client.get(URL, params={"q": q}, cookies=cookies)
            assert r.status_code == 200, r.text
            assert [o["id"] for o in r.json()] == [first.id], q

        r = client.get(URL, params={"q": "sub-abc"}, cookies=cookies)
        assert [o["id"] for o in r.json()] == [second.id]

        assert client.get(URL, params={"q": "999999"}, cookies=cookies).json() == []
        assert client.get(URL, params={"q": "nothing-like-this"}, cookies=cookies).json() == []

        recent = [o["id"] for o in client.get(URL, cookies=cookies).json()]
        assert recent[:2] == [second.id, first.id]

    def test_needs_an_admin(self, client, db_session: Session):
        assert client.get(URL).status_code == 401
