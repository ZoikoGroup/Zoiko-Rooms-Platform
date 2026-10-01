"""ZR-IDENTITY-001 Section 10: normalized, safe reason codes and the plain
language the person sees for each. Internal-only signals (a duplicate
document, an unreadable name) are shown with neutral review copy -- the
person never sees fraud rules, vendor scores or matching thresholds."""

from __future__ import annotations

from dataclasses import dataclass

# What the person can do next (Section 5.9 remediation).
RETRY_CAPTURE = "RETRY_CAPTURE"
UPLOAD_ANOTHER = "UPLOAD_ANOTHER_DOCUMENT"
CHOOSE_METHOD = "CHOOSE_ANOTHER_METHOD"
REQUEST_REVIEW = "REQUEST_REVIEW"
EDIT_DETAILS = "EDIT_DETAILS"
CONTACT_SUPPORT = "CONTACT_SUPPORT"
TRY_LATER = "TRY_LATER"


@dataclass(frozen=True)
class ReasonCode:
    code: str
    message: str
    actions: tuple[str, ...] = ()


_MANUAL_REVIEW_COPY = "We need to review this verification. You can leave this page; we'll update your status here."

REASON_CODES: dict[str, ReasonCode] = {
    r.code: r for r in (
        ReasonCode(
            "DOCUMENT_UNREADABLE",
            "We couldn't read this document clearly. Try again with the full document in view or upload the original file.",
            (RETRY_CAPTURE, UPLOAD_ANOTHER, CHOOSE_METHOD),
        ),
        ReasonCode(
            "DOCUMENT_NUMBER_INVALID",
            "We couldn't confirm the document number. Check the number you entered matches your document, "
            "or upload a clearer copy.",
            (RETRY_CAPTURE, UPLOAD_ANOTHER, REQUEST_REVIEW),
        ),
        ReasonCode(
            "DOCUMENT_UNSUPPORTED",
            "This document can't be used for this verification. Choose another accepted identity document.",
            (UPLOAD_ANOTHER, CHOOSE_METHOD),
        ),
        ReasonCode(
            "DOCUMENT_EXPIRED",
            "This document can't be used for this verification. Choose another accepted identity document.",
            (UPLOAD_ANOTHER, CHOOSE_METHOD),
        ),
        ReasonCode(
            "NAME_MISMATCH",
            "The name on this identity information does not match the details on your account. "
            "Review your details or choose another verification option.",
            (EDIT_DETAILS, UPLOAD_ANOTHER, REQUEST_REVIEW),
        ),
        ReasonCode(
            "BINDING_FAILED",
            "We couldn't complete this identity check. Try again or choose another verification option.",
            (RETRY_CAPTURE, CHOOSE_METHOD),
        ),
        ReasonCode(
            "PROVIDER_UNAVAILABLE",
            "Identity verification is temporarily unavailable. Your progress is saved. "
            "Try again later or choose another available method.",
            (TRY_LATER, CHOOSE_METHOD),
        ),
        ReasonCode("MORE_INFORMATION_NEEDED", "We need one more step to complete verification.", (UPLOAD_ANOTHER, REQUEST_REVIEW)),
        ReasonCode("DETAILS_INCOMPLETE", "Confirm your legal name and country before submitting.", (EDIT_DETAILS,)),
        ReasonCode(
            "AGE_REQUIREMENT_NOT_MET",
            "We couldn't complete verification with these details. Contact support if you think this is wrong.",
            (CONTACT_SUPPORT,),
        ),
        ReasonCode(
            "REVIEWER_REJECTED",
            "We couldn't verify your identity with the information provided. Try another method or contact support.",
            (CHOOSE_METHOD, CONTACT_SUPPORT),
        ),
        # Re-verification triggers (Section 7.3).
        ReasonCode(
            "LEGAL_DETAILS_CHANGED",
            "Your legal name or date of birth changed, so we need to verify your identity again.",
            (UPLOAD_ANOTHER, CHOOSE_METHOD),
        ),
        ReasonCode(
            "PERIODIC_RENEWAL",
            "It's time to confirm your identity again. This is required periodically in your country.",
            (UPLOAD_ANOTHER, CHOOSE_METHOD),
        ),
        ReasonCode(
            "ACCOUNT_RECOVERY",
            "Your account was recently recovered, so we need to confirm your identity again to keep it secure.",
            (UPLOAD_ANOTHER, CHOOSE_METHOD),
        ),
        ReasonCode(
            "EVIDENCE_INVALIDATED",
            "We need to verify your identity again with another document.",
            (UPLOAD_ANOTHER, CHOOSE_METHOD),
        ),
        # Hosted-provider (Veriff) outcomes, mapped to safe copy (ZR-IDV-ADR-001 Section 9).
        ReasonCode(
            "PROVIDER_DECLINED",
            "We couldn't verify your identity. You can try another verification option or contact support.",
            (CHOOSE_METHOD, CONTACT_SUPPORT),
        ),
        ReasonCode(
            "SESSION_EXPIRED",
            "Your verification session expired. Start again -- it only takes a few minutes.",
            (RETRY_CAPTURE,),
        ),
        ReasonCode(
            "SESSION_ABANDONED",
            "Your verification wasn't completed. Resume or start again.",
            (RETRY_CAPTURE,),
        ),
        ReasonCode(
            "RESUBMISSION_REQUESTED",
            "We need another capture of your document or a clearer photo of you.",
            (RETRY_CAPTURE, UPLOAD_ANOTHER),
        ),
        # Internal routing reasons -- the person only ever sees review copy.
        ReasonCode("PROVIDER_REVIEW", _MANUAL_REVIEW_COPY),
        ReasonCode("AUTOMATED_CHECK_INSUFFICIENT", _MANUAL_REVIEW_COPY),
        ReasonCode("MANUAL_REVIEW", _MANUAL_REVIEW_COPY),
        ReasonCode("NAME_NOT_CHECKABLE", _MANUAL_REVIEW_COPY),
        ReasonCode("DUPLICATE_EVIDENCE", _MANUAL_REVIEW_COPY),
        ReasonCode("ALTERNATIVE_REQUESTED", _MANUAL_REVIEW_COPY),
        ReasonCode("ESCALATED", _MANUAL_REVIEW_COPY),
        ReasonCode("REVIEWER_APPROVED", "Your identity check is complete."),
    )
}

# Codes a reviewer may record with a decision (Section 13).
REVIEWER_REASON_CODES = (
    "REVIEWER_APPROVED", "DOCUMENT_UNREADABLE", "DOCUMENT_NUMBER_INVALID", "DOCUMENT_UNSUPPORTED",
    "DOCUMENT_EXPIRED", "NAME_MISMATCH", "BINDING_FAILED", "MORE_INFORMATION_NEEDED", "REVIEWER_REJECTED",
    "DUPLICATE_EVIDENCE", "ESCALATED",
)
# Why someone asks for the alternative / manual route (Section 8.3).
ALTERNATIVE_REASON_CODES = ("NO_CAMERA", "ACCESSIBILITY", "NO_ACCEPTED_DOCUMENT", "PROVIDER_UNAVAILABLE", "OTHER")


def describe(codes: list[str] | tuple[str, ...]) -> dict:
    """The first known code's plain-language message plus the union of
    remediation actions -- what the person's status page shows."""
    known = [REASON_CODES[c] for c in codes if c in REASON_CODES]
    if not known:
        return {"message": "", "actions": []}
    actions: list[str] = []
    for r in known:
        actions.extend(a for a in r.actions if a not in actions)
    return {"message": known[0].message, "actions": actions}
