"""The incoming tenant's deposit on a sublease is agreed between the people involved -- it can be a different
amount or none at all. Left blank, the original tenancy's deposit is copied as before."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.crud import sublet as sublet_crud
from app.models.finance import Obligation
from app.models.leasing import Agreement
from app.models.sublet_request import SubletRequest
from tests.test_sublet_arrangement_classification import _make_active_tenancy
from tests.test_sublet_payments import _approve, _obligations


def _deposits(db: Session, agreement_id: int) -> list[float]:
    return [float(o.amount) for o in db.query(Obligation).filter_by(agreement_id=agreement_id, obligation_type="DEPOSIT")]


class TestNegotiatedDeposit:
    def test_blank_keeps_copying_the_original_deposit(self, client, db_session: Session):
        tenant_user, _p, party_id, occ_id = _make_active_tenancy(db_session, suffix="nd1")
        body = _approve(client, db_session, occ_id, tenant_user, party_id, "SUBLEASE_PARTIAL", "nd1", proposed_monthly_rent=300)
        assert len(_deposits(db_session, body["newAgreementId"])) == 1
        assert db_session.get(SubletRequest, body["id"]).deposit_disposition == "SEPARATE_DEPOSIT_CREATED"

    def test_an_agreed_amount_replaces_it(self, client, db_session: Session):
        tenant_user, _p, party_id, occ_id = _make_active_tenancy(db_session, suffix="nd2")
        body = _approve(client, db_session, occ_id, tenant_user, party_id, "SUBLEASE_PARTIAL", "nd2",
                        proposed_monthly_rent=300, proposed_deposit_amount=100)
        assert _deposits(db_session, body["newAgreementId"]) == [100.0]
        assert float(_obligations(db_session, body["newAgreementId"])["DEPOSIT"].amount) == 100.0

    def test_zero_means_no_separate_deposit_at_all(self, client, db_session: Session):
        tenant_user, _p, party_id, occ_id = _make_active_tenancy(db_session, suffix="nd3")
        body = _approve(client, db_session, occ_id, tenant_user, party_id, "SUBLEASE_PARTIAL", "nd3",
                        proposed_monthly_rent=300, proposed_deposit_amount=0)
        assert _deposits(db_session, body["newAgreementId"]) == []
        assert "DEPOSIT" not in _obligations(db_session, body["newAgreementId"])
        assert "RENT" in _obligations(db_session, body["newAgreementId"])
        assert db_session.get(SubletRequest, body["id"]).deposit_disposition == "NO_SEPARATE_DEPOSIT"
        assert db_session.get(Agreement, body["newAgreementId"]).status == "PAYMENT_IN_PROGRESS"

    def test_it_cannot_be_negative_or_above_the_markets_cap(self, db_session: Session):
        tenant_user, _p, party_id, occ_id = _make_active_tenancy(db_session, suffix="nd4")
        from app.models.occupancy import Occupancy
        db_session.get(Occupancy, occ_id).room.max_occupants = 2
        db_session.commit()
        for bad in (-1, 300 * 3 + 1):
            with pytest.raises(HTTPException) as err:
                sublet_crud.submit_sublet_request(
                    db_session, tenant_user, occ_id, party_id, "SUBLEASE_PARTIAL",
                    proposed_monthly_rent=300, proposed_deposit_amount=bad,
                )
            assert err.value.status_code == 400

    def test_the_request_shows_what_was_proposed(self, client, db_session: Session):
        tenant_user, _p, party_id, occ_id = _make_active_tenancy(db_session, suffix="nd5")
        body = _approve(client, db_session, occ_id, tenant_user, party_id, "SUBLEASE_PARTIAL", "nd5",
                        proposed_monthly_rent=300, proposed_deposit_amount=50)
        read = sublet_crud.to_sublet_request_read(db_session, db_session.get(SubletRequest, body["id"]))
        assert read.proposed_monthly_rent == 300 and read.proposed_deposit_amount == 50
