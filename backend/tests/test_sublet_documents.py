"""ZR-SUB-003 Section 10/11: sublet_document -- reuses the generic Evidence
Vault (EvidenceArtifact) rather than a new table, plus real signed, time-
limited download URLs (previously no route in this codebase used that
pattern at all -- every download was a plain authenticated-session GET)."""

from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app.core.signed_urls import generate_signed_download_token
from app.crud import sublet as sublet_crud
from app.models.user_account import UserAccount
from sqlalchemy import select
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_sublet_arrangement_classification import _make_active_tenancy

_PDF_BYTES = b"%PDF-1.4 fake sublet document content"


def _host_for(db: Session, suffix: str) -> UserAccount:
    return db.scalar(select(UserAccount).where(UserAccount.email == f"host-{suffix}@test.com"))


class TestTenantDocumentUpload:
    def test_tenant_can_upload_list_and_download_own_document(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="doc1")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)

        r = client.post(
            f"/api/users/rentals/sublet-requests/{sublet_request.id}/documents",
            files={"file": ("consent.pdf", _PDF_BYTES, "application/pdf")},
            cookies=auth_user_cookie(tenant_user),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["originalFilename"] == "consent.pdf"
        assert body["scanStatus"] == "NOT_SCANNED"
        assert "token=" in body["downloadUrl"]

        r = client.get(
            f"/api/users/rentals/sublet-requests/{sublet_request.id}/documents", cookies=auth_user_cookie(tenant_user),
        )
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1

        download_path = body["downloadUrl"]
        r = client.get(download_path, cookies=auth_user_cookie(tenant_user))
        assert r.status_code == 200
        assert r.content == _PDF_BYTES

    def test_a_different_tenant_cannot_upload_or_view(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="doc2")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        other_tenant, _p2, _party2, _occ2 = _make_active_tenancy(db_session, suffix="doc2other")

        r = client.post(
            f"/api/users/rentals/sublet-requests/{sublet_request.id}/documents",
            files={"file": ("consent.pdf", _PDF_BYTES, "application/pdf")},
            cookies=auth_user_cookie(other_tenant),
        )
        assert r.status_code == 403

        r = client.get(
            f"/api/users/rentals/sublet-requests/{sublet_request.id}/documents", cookies=auth_user_cookie(other_tenant),
        )
        assert r.status_code == 403


class TestHostDocumentAccess:
    def test_host_can_view_and_upload_documents(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="doc3")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        host_user = _host_for(db_session, "doc3")

        client.post(
            f"/api/users/rentals/sublet-requests/{sublet_request.id}/documents",
            files={"file": ("consent.pdf", _PDF_BYTES, "application/pdf")},
            cookies=auth_user_cookie(tenant_user),
        )

        r = client.get(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/documents", cookies=auth_user_cookie(host_user),
        )
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1
        download_path = r.json()[0]["downloadUrl"]

        r = client.get(download_path, cookies=auth_user_cookie(host_user))
        assert r.status_code == 200
        assert r.content == _PDF_BYTES

        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/documents",
            files={"file": ("landlord-letter.pdf", _PDF_BYTES, "application/pdf")},
            cookies=auth_user_cookie(host_user),
        )
        assert r.status_code == 201, r.text


class TestAdminDocumentAccess:
    def test_admin_can_view_documents(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="doc4")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        client.post(
            f"/api/users/rentals/sublet-requests/{sublet_request.id}/documents",
            files={"file": ("consent.pdf", _PDF_BYTES, "application/pdf")},
            cookies=auth_user_cookie(tenant_user),
        )

        admin = _make_admin(db_session, email="doc4-admin@test.com", role="super_admin")
        r = client.get(f"/api/occupancy/sublet-requests/{sublet_request.id}/documents", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1


class TestSignedDownloadToken:
    def test_a_tampered_token_is_rejected(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="doc5")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        r = client.post(
            f"/api/users/rentals/sublet-requests/{sublet_request.id}/documents",
            files={"file": ("consent.pdf", _PDF_BYTES, "application/pdf")},
            cookies=auth_user_cookie(tenant_user),
        )
        document_id = r.json()["id"]

        r = client.get(
            f"/api/users/rentals/sublet-requests/{sublet_request.id}/documents/{document_id}/file?token=garbage",
            cookies=auth_user_cookie(tenant_user),
        )
        assert r.status_code == 403

    def test_an_expired_token_is_rejected(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="doc6")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        r = client.post(
            f"/api/users/rentals/sublet-requests/{sublet_request.id}/documents",
            files={"file": ("consent.pdf", _PDF_BYTES, "application/pdf")},
            cookies=auth_user_cookie(tenant_user),
        )
        document_id = r.json()["id"]

        expired_token = generate_signed_download_token("sublet_document", str(document_id), ttl_seconds=-10)
        r = client.get(
            f"/api/users/rentals/sublet-requests/{sublet_request.id}/documents/{document_id}/file?token={expired_token}",
            cookies=auth_user_cookie(tenant_user),
        )
        assert r.status_code == 403

    def test_a_token_minted_for_a_different_document_is_rejected(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="doc7")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        r = client.post(
            f"/api/users/rentals/sublet-requests/{sublet_request.id}/documents",
            files={"file": ("consent.pdf", _PDF_BYTES, "application/pdf")},
            cookies=auth_user_cookie(tenant_user),
        )
        document_id = r.json()["id"]

        wrong_token = generate_signed_download_token("sublet_document", "999999")
        r = client.get(
            f"/api/users/rentals/sublet-requests/{sublet_request.id}/documents/{document_id}/file?token={wrong_token}",
            cookies=auth_user_cookie(tenant_user),
        )
        assert r.status_code == 403
