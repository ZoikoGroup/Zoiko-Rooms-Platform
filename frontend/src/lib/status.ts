export const bookingStatusTone = {
  confirmed: "success",
  pending: "warning",
  cancelled: "danger",
  completed: "primary",
} as const;

export const paymentStatusTone = {
  paid: "success",
  unpaid: "warning",
  refunded: "neutral",
} as const;

export const listingStateTone = {
  DRAFT: "neutral",
  EVIDENCE_PENDING: "warning",
  REVIEW: "primary",
  REJECTED: "danger",
  APPROVED: "success",
  PUBLISHED: "success",
  PAUSED: "warning",
  SUSPENDED: "danger",
  WITHDRAWN: "neutral",
  ARCHIVED: "neutral",
} as const;

export const listingStateLabel = {
  DRAFT: "Draft",
  EVIDENCE_PENDING: "Evidence Pending",
  REVIEW: "Pending Review",
  REJECTED: "Rejected",
  APPROVED: "Approved",
  PUBLISHED: "Published",
  PAUSED: "Paused",
  SUSPENDED: "Suspended",
  WITHDRAWN: "Withdrawn",
  ARCHIVED: "Archived",
} as const;

export const applicationStatusTone = {
  SUBMITTED: "warning",
  WITHDRAWN: "neutral",
  DECIDED: "success",
} as const;

export const offerStatusTone = {
  DRAFT: "neutral",
  SENT: "warning",
  ACCEPTED: "success",
  DECLINED: "danger",
  EXPIRED: "neutral",
  WITHDRAWN: "neutral",
} as const;

export const offerStatusLabel = {
  DRAFT: "Draft",
  SENT: "Sent to renter",
  ACCEPTED: "Accepted",
  DECLINED: "Declined",
  EXPIRED: "Expired",
  WITHDRAWN: "Withdrawn",
} as const;

export const agreementStatusTone = {
  DRAFT: "neutral",
  SENT: "warning",
  SIGNED: "success",
  VOID: "danger",
} as const;

export const agreementStatusLabel = {
  DRAFT: "Draft",
  SENT: "Sent for signature",
  SIGNED: "Signed",
  VOID: "Void",
} as const;

export const occupancyStatusTone = {
  PENDING_MOVE_IN: "warning",
  ACTIVE: "success",
  ENDED: "neutral",
} as const;

export const occupancyStatusLabel = {
  PENDING_MOVE_IN: "Confirmed — Awaiting Move-In",
  ACTIVE: "Booked & Active",
  ENDED: "Stay Ended",
} as const;

export const obligationStatusTone = {
  PENDING: "warning",
  PARTIALLY_PAID: "warning",
  PAID: "success",
  WAIVED: "neutral",
  FAILED: "danger",
  REFUNDED: "neutral",
} as const;

export const obligationStatusLabel = {
  PENDING: "Payment due",
  PARTIALLY_PAID: "Partially paid",
  PAID: "Paid",
  WAIVED: "Waived",
  FAILED: "Payment failed",
  REFUNDED: "Refunded",
} as const;

// Distinct from the legacy `paymentStatusTone` above (which covers the old
// short-stay Payment.status values paid/unpaid/refunded).
export const simulatedPaymentStatusTone = {
  PENDING: "warning",
  SUCCEEDED: "success",
  FAILED: "danger",
} as const;

export const depositStatusTone = {
  HELD: "warning",
  RELEASED: "success",
  FORFEITED: "danger",
  PARTIALLY_RELEASED: "warning",
} as const;

export const depositStatusLabel = {
  HELD: "Held",
  RELEASED: "Released",
  FORFEITED: "Forfeited",
  PARTIALLY_RELEASED: "Partially released",
} as const;

export const payoutStatusTone = {
  PENDING: "warning",
  PAID: "success",
  FAILED: "danger",
  HELD: "danger",
} as const;

export const payoutStatusLabel = {
  PENDING: "Pending payout",
  PAID: "Paid to host",
  FAILED: "Payout failed",
  HELD: "Payout on hold",
} as const;

export const refundStatusTone = {
  REQUESTED: "warning",
  APPROVED: "primary",
  REJECTED: "danger",
  COMPLETED: "success",
} as const;

export const refundStatusLabel = {
  REQUESTED: "Requested",
  APPROVED: "Approved",
  REJECTED: "Rejected",
  COMPLETED: "Refunded",
} as const;

export const disputeStatusTone = {
  OPEN: "warning",
  RESOLVED: "success",
  REJECTED: "danger",
} as const;

export const disputeStatusLabel = {
  OPEN: "Open",
  RESOLVED: "Resolved",
  REJECTED: "Rejected",
} as const;

export const reconciliationStatusTone = {
  CLEAN: "success",
  DISCREPANCIES_FOUND: "danger",
} as const;

export const reconciliationStatusLabel = {
  CLEAN: "Clean — no discrepancies",
  DISCREPANCIES_FOUND: "Discrepancies found",
} as const;

// --- USER account surface ---

export const identityStatusTone = {
  not_submitted: "neutral",
  pending: "warning",
  verified: "success",
  rejected: "danger",
  expired: "danger",
  additional_evidence_required: "warning",
} as const;

export const identityStatusLabel = {
  not_submitted: "Not Verified",
  pending: "Verification Pending",
  verified: "Identity Verified",
  rejected: "Verification Rejected",
  expired: "Verification Expired",
  additional_evidence_required: "More Evidence Needed",
} as const;

export const subletRequestStatusTone = {
  pending_verification: "warning",
  pending_admin_review: "warning",
  approved: "success",
  rejected: "danger",
} as const;

export const subletRequestStatusLabel = {
  pending_verification: "Pending Verification",
  pending_admin_review: "Pending Admin Review",
  approved: "Approved",
  rejected: "Rejected",
} as const;

// --- Trust & Safety surface ---

export const authorityRecordStatusTone = {
  not_started: "neutral",
  pending: "warning",
  verified: "success",
  expiring: "warning",
  expired: "danger",
  failed: "danger",
  conflict: "danger",
  review_required: "warning",
} as const;

export const authorityRecordStatusLabel = {
  not_started: "Not started",
  pending: "Pending verification",
  verified: "Verified",
  expiring: "Expiring soon",
  expired: "Expired",
  failed: "Failed",
  conflict: "Conflict found",
  review_required: "Review required",
} as const;

export const marketReleaseStatusTone = {
  draft: "neutral",
  active: "success",
  disabled: "danger",
} as const;

export const marketReleaseStatusLabel = {
  draft: "Draft — not yet active",
  active: "Active",
  disabled: "Disabled",
} as const;

export const occupancyReviewStateTone = {
  UNKNOWN: "neutral",
  UNSUPPORTED: "warning",
  APPROVED: "success",
} as const;

export const occupancyReviewStateLabel = {
  UNKNOWN: "Not yet classified",
  UNSUPPORTED: "Classification unresolved",
  APPROVED: "Approved",
} as const;

// `OccupancyClassification.classification` is a free-text field (no fixed
// backend enum), so it can't have a lookup label map — humanize it generically.
export function formatClassificationLabel(value: string): string {
  if (!value) return "Not classified";
  return value
    .split("_")
    .filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(" ");
}
