"""ZR-IDENTITY-001 Section 10: normalized, safe reason codes and the plain
language the person sees for each. The identity check is a face match between
the uploaded document photo and a selfie; the person never sees scores or
thresholds. Every remediation is "take the photos again", "fix your details",
"try later" or "contact support"."""

from __future__ import annotations

from dataclasses import dataclass

# What the person can do next (Section 5.9 remediation).
RETRY_CAPTURE = "RETRY_CAPTURE"
EDIT_DETAILS = "EDIT_DETAILS"
CONTACT_SUPPORT = "CONTACT_SUPPORT"
TRY_LATER = "TRY_LATER"


@dataclass(frozen=True)
class ReasonCode:
    code: str
    message: str
    actions: tuple[str, ...] = ()


_PROVIDER_CHECKING_COPY = "We're checking your identity. You can leave this page; we'll update your status here."
_RETIRED_COPY = "Identity verification has changed. Please verify again -- it only takes a few minutes."

REASON_CODES: dict[str, ReasonCode] = {
    r.code: r for r in (
        ReasonCode(
            "DOCUMENT_UNREADABLE",
            "We couldn't read your document clearly. Try again with the whole document in view and good lighting.",
            (RETRY_CAPTURE,),
        ),
        ReasonCode(
            "DOCUMENT_NUMBER_INVALID",
            "We couldn't confirm your document. Try again with the original document.",
            (RETRY_CAPTURE,),
        ),
        ReasonCode(
            "DOCUMENT_UNSUPPORTED",
            "This document can't be used for this verification. Try again with another accepted identity document.",
            (RETRY_CAPTURE,),
        ),
        ReasonCode(
            "DOCUMENT_EXPIRED",
            "This document has expired. Try again with a valid identity document.",
            (RETRY_CAPTURE,),
        ),
        ReasonCode(
            "NAME_MISMATCH",
            "The name on your document doesn't match the details on your account. Check your details and try again.",
            (EDIT_DETAILS, RETRY_CAPTURE),
        ),
        ReasonCode(
            "BINDING_FAILED",
            "We couldn't confirm the document belongs to you. Try again and make sure your face is clearly visible.",
            (RETRY_CAPTURE,),
        ),
        ReasonCode(
            "PROVIDER_UNAVAILABLE",
            "Identity verification is temporarily unavailable. Your progress is saved -- please try again later.",
            (TRY_LATER,),
        ),
        ReasonCode("DETAILS_INCOMPLETE", "Confirm your legal name and country before submitting.", (EDIT_DETAILS,)),
        ReasonCode(
            "AGE_REQUIREMENT_NOT_MET",
            "We couldn't complete verification with these details. Contact support if you think this is wrong.",
            (CONTACT_SUPPORT,),
        ),
        # Re-verification triggers (Section 7.3).
        ReasonCode(
            "LEGAL_DETAILS_CHANGED",
            "Your legal name or date of birth changed, so we need to verify your identity again.",
            (RETRY_CAPTURE,),
        ),
        ReasonCode(
            "PERIODIC_RENEWAL",
            "It's time to confirm your identity again. This is required periodically in your country.",
            (RETRY_CAPTURE,),
        ),
        ReasonCode(
            "ACCOUNT_RECOVERY",
            "Your account was recently recovered, so we need to confirm your identity again to keep it secure.",
            (RETRY_CAPTURE,),
        ),
        ReasonCode(
            "EVIDENCE_INVALIDATED",
            "We need to verify your identity again.",
            (RETRY_CAPTURE,),
        ),
        # Face-match outcomes.
        ReasonCode(
            "PROVIDER_DECLINED",
            "The photo of you doesn't match the photo on your identity document. You can try again or contact support.",
            (RETRY_CAPTURE, CONTACT_SUPPORT),
        ),
        ReasonCode(
            "FACE_MATCH_UNSURE",
            "We couldn't be sure your photo matches your document. Try again in good light, facing the camera, with a clear photo of your document.",
            (RETRY_CAPTURE,),
        ),
        ReasonCode(
            "NO_FACE_IN_DOCUMENT",
            "We couldn't find your face on the document photo. Photograph the side with your picture, flat and sharp.",
            (RETRY_CAPTURE,),
        ),
        ReasonCode(
            "NO_FACE_IN_SELFIE",
            "We couldn't see your face in the selfie. Face the camera in good light and try again.",
            (RETRY_CAPTURE,),
        ),
        ReasonCode(
            "MULTIPLE_FACES_IN_SELFIE",
            "More than one face is in your selfie. Make sure only you are in the picture.",
            (RETRY_CAPTURE,),
        ),
        ReasonCode(
            "FACE_TOO_SMALL",
            "Move a little closer so your face fills more of the selfie.",
            (RETRY_CAPTURE,),
        ),
        ReasonCode(
            "FACE_NOT_FRONTAL",
            "Look straight at the camera so we can see your whole face.",
            (RETRY_CAPTURE,),
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
            (RETRY_CAPTURE,),
        ),
        ReasonCode("PROVIDER_REVIEW", _PROVIDER_CHECKING_COPY),
        # Attempts closed when the upload / manual-review route was removed.
        ReasonCode("METHOD_RETIRED", _RETIRED_COPY, (RETRY_CAPTURE,)),
    )
}


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
