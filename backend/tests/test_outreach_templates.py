"""SRCH-10 -- provider outreach template compliance (Section 9.1).

The rendered outreach message must identify Zoiko Rooms, state the genuine
purpose (renter asked us to contact them), offer Claim & List with an
alternative to decline, and never claim verification/approval/partnership,
impersonate an availability enquiry, or mention renter fees / rent
collection / undisclosed commission.
"""

from __future__ import annotations

from app.services.outreach_templates import (
    FORBIDDEN_PHRASES,
    TEMPLATE_COMPLIANCE_NOTES,
    render_provider_outreach,
)


def _render(**kw) -> str:
    defaults = dict(
        provider_name="Riverside Properties",
        approx_location="Bethnal Green, London",
        claim_url="https://zoikorooms.com/providers/claim",
    )
    defaults.update(kw)
    return render_provider_outreach(**defaults)


class TestIdentity:
    def test_identifies_zoiko_rooms(self):
        body = _render()
        assert "Zoiko Rooms" in body
        assert "Zoiko Realty Group Inc." in body

    def test_genuine_purpose_stated(self):
        body = _render()
        assert "asked us to contact you" in body


class TestNoFalseClaims:
    def test_no_verified_or_approved_claim(self):
        body = _render().lower()
        assert "your listing is verified" not in body
        assert "listing is verified" not in body
        assert "verified by zoiko" not in body
        assert "approved" not in body

    def test_no_partnership_claim(self):
        assert "partnered" not in _render().lower()

    def test_forbidden_phrases_absent(self):
        body = _render().lower()
        for phrase in FORBIDDEN_PHRASES:
            assert phrase not in body

    def test_not_an_impersonated_availability_enquiry(self):
        body = _render().lower()
        assert "is your property still available" not in body


class TestVoluntaryDecision:
    def test_claim_and_list_offered(self):
        body = _render()
        assert "claim this room" in body
        assert "claim_url" not in body

    def test_decline_option_offered(self):
        body = _render()
        assert "decline this introduction" in body

    def test_decline_is_an_alternative(self):
        body = _render()
        assert "Alternatively" in body


class TestComplianceNotesGate:
    def test_all_compliance_notes_present(self):
        assert all(TEMPLATE_COMPLIANCE_NOTES.values())