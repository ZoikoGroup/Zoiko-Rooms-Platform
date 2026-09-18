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
  PARTIALLY_EXECUTED: "warning",
  PAYMENT_IN_PROGRESS: "warning",
  PAYMENT_PENDING: "warning",
  SIGNED: "success",
  EXPIRED: "danger",
  VOID: "danger",
  AMENDMENT_PENDING: "warning",
} as const;

export const agreementStatusLabel = {
  DRAFT: "Draft",
  SENT: "Sent for signature",
  PARTIALLY_EXECUTED: "Partially signed",
  PAYMENT_IN_PROGRESS: "Awaiting payment",
  PAYMENT_PENDING: "Payment pending",
  SIGNED: "Signed",
  EXPIRED: "Expired",
  VOID: "Void",
  AMENDMENT_PENDING: "Amendment pending",
} as const;

export const occupancyStatusTone = {
  PENDING_MOVE_IN: "warning",
  ACTIVE: "success",
  ENDED: "neutral",
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

// ZR-ENG-CLR-003 Section 3's arrangement-type taxonomy, in renter-facing (first-person) terms.
export const subletArrangementTypeLabel = {
  ASSIGNMENT_FULL: "Full handover — I move out, they take over completely",
  REPLACEMENT_OCCUPANT: "Replace me as occupant — I move out, they take over completely",
  SUBLEASE_PARTIAL: "Sublease part of the room — I stay on, they get their own agreement",
  ADD_CO_TENANT: "Add a co-tenant — we'd both hold our own tenancy",
  LODGER_OR_LICENSEE: "Take on a lodger/licensee — they live here without full tenancy rights",
  ADDITIONAL_OCCUPANT: "Just let them live here — no separate tenancy or agreement",
} as const;

// Same taxonomy, third-person -- for the admin review console.
export const subletArrangementTypeAdminLabel = {
  ASSIGNMENT_FULL: "Full handover (tenant moves out, replacement takes over)",
  REPLACEMENT_OCCUPANT: "Occupant replacement (tenant moves out, replacement takes over)",
  SUBLEASE_PARTIAL: "Partial sublease (tenant stays, new occupant gets own agreement)",
  ADD_CO_TENANT: "Add co-tenant (both hold their own tenancy)",
  LODGER_OR_LICENSEE: "Lodger/licensee (no full tenancy rights)",
  ADDITIONAL_OCCUPANT: "Additional occupant only (no tenancy or agreement)",
} as const;

// Types that create a second, independent tenancy alongside the existing one --
// these are the only ones where a proposed rent for that new tenancy applies.
export const CO_TENANCY_ARRANGEMENT_TYPES = ["SUBLEASE_PARTIAL", "ADD_CO_TENANT", "LODGER_OR_LICENSEE"] as const;

export const bookingChangeRequestStatusTone = {
  AWAITING_HOST: "warning",
  AWAITING_RENTER: "warning",
  AWAITING_AGREEMENT_ACTION: "warning",
  EFFECTIVE: "success",
  REJECTED: "danger",
  WITHDRAWN: "neutral",
  EXPIRED: "neutral",
  CONFLICT: "danger",
  FAILED: "danger",
} as const;

export const bookingChangeRequestStatusLabel = {
  AWAITING_HOST: "Pending Review",
  AWAITING_RENTER: "Host proposed different terms",
  AWAITING_AGREEMENT_ACTION: "Approved — awaiting re-signature",
  EFFECTIVE: "Effective",
  REJECTED: "Declined",
  WITHDRAWN: "Withdrawn",
  EXPIRED: "Expired",
  CONFLICT: "Conflict — please submit a new request",
  FAILED: "Failed — please submit a new request",
} as const;

export const bookingChangeTypeLabel = {
  DATE_SHIFT: "Move-in date change",
  EXTENSION: "Stay extension",
  SHORTENING: "Stay shortening",
  PREMISES_CHANGE: "Room/property change",
  FINANCIAL_CHANGE: "Rent change",
  TERM_SHIFT: "Move-in date & term change",
  LEGAL_ORDER_CHANGE: "Legal/regulatory order change",
  DEPOSIT_CHANGE: "Deposit change",
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
  revoked: "danger",
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
  revoked: "Revoked",
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

// --- Verification (ZR-ENG-CLR-012) ---

export const occupancyEligibilityStatusTone = {
  IN_PROGRESS: "warning",
  PASS: "success",
  INCONCLUSIVE: "warning",
  TECHNICAL_ERROR: "warning",
  FAIL_INELIGIBLE: "danger",
  FRAUD_REVIEW: "danger",
  EXPIRED: "neutral",
  WAIVED_POLICY: "primary",
  SUSPENDED: "neutral",
} as const;

export const occupancyEligibilityMethodLabel = {
  DIGITAL_SHARE_CODE: "Digital share code",
  MANUAL_DOCUMENT_CHECK: "Manual document check",
} as const;

export const propertyComplianceCredentialStatusTone = {
  UNDER_REVIEW: "warning",
  VALID: "success",
  EXPIRING: "warning",
  EXPIRED: "warning",
  REVOKED: "danger",
  SUSPENDED: "neutral",
} as const;

export const amendmentStatusTone = {
  REQUESTED: "neutral",
  CLASSIFIED: "warning",
  TERMS_PROPOSED: "warning",
  APPROVALS_PENDING: "warning",
  GENERATED: "primary",
  EXECUTION_PENDING: "warning",
  EXECUTED: "primary",
  EFFECTIVE: "success",
} as const;

export const amendmentStatusLabel = {
  REQUESTED: "Requested",
  CLASSIFIED: "Classified",
  TERMS_PROPOSED: "Terms proposed",
  APPROVALS_PENDING: "Awaiting approval",
  GENERATED: "New version generated",
  EXECUTION_PENDING: "Awaiting re-signature",
  EXECUTED: "Executed",
  EFFECTIVE: "Effective",
} as const;

export const amendmentTypeLabel = {
  MATERIAL_CHANGE: "Material change",
  ADDENDUM: "Addendum (e.g. guarantor)",
  ASSIGNMENT_NOVATION: "Assignment / novation",
  RESTATED_AGREEMENT: "Restated agreement",
  RENEWAL: "Renewal",
  CORRECTION: "Correction",
} as const;

export const screeningDecisionStatusTone = {
  AUTHORIZED: "warning",
  PASS: "success",
  FAIL: "danger",
  INCONCLUSIVE: "warning",
  DISPUTED_SOURCE: "primary",
} as const;

export const renterVerificationStatusTone: Record<string, "neutral" | "warning" | "success" | "danger" | "primary"> = {
  not_submitted: "neutral",
  pending: "warning",
  verified: "success",
  rejected: "danger",
  expired: "danger",
  additional_evidence_required: "warning",
  IN_PROGRESS: "warning",
  PASS: "success",
  INCONCLUSIVE: "warning",
  TECHNICAL_ERROR: "warning",
  FAIL_INELIGIBLE: "danger",
  FRAUD_REVIEW: "danger",
  EXPIRED: "neutral",
  WAIVED_POLICY: "primary",
  SUSPENDED: "neutral",
};
