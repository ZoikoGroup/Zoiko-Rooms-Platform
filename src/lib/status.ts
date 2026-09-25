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
  CANCELLED: "danger",
} as const;

export const terminationCaseStatusTone: Record<string, "warning" | "success" | "danger" | "neutral"> = {
  OPENED: "warning",
  SURRENDER_PROPOSED: "warning",
  SURRENDER_DECLINED: "danger",
  PENDING_REVIEW: "warning",
  REJECTED_PATHWAY: "danger",
  EFFECTIVE_DATE_SET: "success",
  TERMINATED: "neutral",
  WITHDRAWN: "neutral",
};

export const terminationCauseCodeLabel: Record<string, string> = {
  RENTER_ORDINARY_EARLY_EXIT: "Ending my stay early (ordinary notice)",
  RENTER_CONTRACT_BREAK: "Breaking my contract",
  RENTER_STATUTORY_RIGHT: "A protected/statutory right (e.g. safety, domestic violence)",
  MUTUAL_SURRENDER: "Mutual agreement with my host",
  ASSIGNMENT_OR_REPLACEMENT: "Assignment/replacement tenant arranged",
  HOST_FAULT_OR_NONPERFORMANCE: "Host fault or non-performance",
  HOST_LAWFUL_POSSESSION_ACTION: "Host lawful possession action",
  RENTER_BREACH: "Renter breach",
  PROPERTY_UNINHABITABLE: "Property uninhabitable",
  CASUALTY_OR_FORCE_EVENT: "Casualty / force majeure event",
  ABANDONMENT_REPORTED: "Abandonment reported",
  PLATFORM_SAFETY_INTERVENTION: "Platform safety intervention",
  LEGAL_OR_REGULATORY_ORDER: "Legal/regulatory order",
  OTHER_COUNSEL_APPROVED: "Other (counsel-approved)",
};

// Section 6 gap: the only cause codes a renter may self-invoke through
// POST .../termination-cases (RENTER_ONLY plus the shared MUTUAL code) --
// mirrors crud/termination.py's own HOST_ONLY_CAUSE_CODES rejection so the
// picker never offers a choice the backend would 400 on anyway.
export const RENTER_TERMINATION_CAUSE_CODES = [
  "RENTER_ORDINARY_EARLY_EXIT",
  "RENTER_CONTRACT_BREAK",
  "RENTER_STATUTORY_RIGHT",
  "MUTUAL_SURRENDER",
  "ASSIGNMENT_OR_REPLACEMENT",
  "LEGAL_OR_REGULATORY_ORDER",
  "OTHER_COUNSEL_APPROVED",
] as const;

export const refundEntitlementLineItemLabel: Record<string, string> = {
  EARNED_RENT: "Earned rent (kept)",
  REFUNDABLE_UNEARNED_RENT: "Refundable unearned rent",
  NOTICE_LIABILITY: "Early-termination charge",
  MITIGATION_CREDIT: "Mitigation credit",
  RENTER_FEE: "Fee",
  TAX: "Tax",
  OTHER_CREDIT: "Other credit",
};

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
  draft: "neutral",
  pending_verification: "warning",
  pending_admin_review: "warning",
  more_information_requested: "warning",
  tenant_response_submitted: "warning",
  approved: "success",
  rejected: "danger",
  withdrawn: "neutral",
  expired: "neutral",
  superseded: "neutral",
  cancelled_by_authority: "danger",
} as const;

export const subletRequestStatusLabel = {
  draft: "Draft",
  pending_verification: "Pending Verification",
  // Backend status name predates host decisions -- the host decides it now
  // (ZR-SUB-003), with Zoiko admins only able to override.
  pending_admin_review: "Awaiting Host Decision",
  more_information_requested: "More Information Requested",
  tenant_response_submitted: "Tenant Responded",
  approved: "Approved",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
  expired: "Expired",
  superseded: "Superseded",
  cancelled_by_authority: "Cancelled by Authority",
} as const;

// ZR-SUB-003 Section 5.3 FAIRNESS CONTROL: a decline must carry a
// centrally-configured reason code, not free text.
export const subletDeclineReasonCodeLabel: Record<string, string> = {
  PROPERTY_UNSUITABLE_FOR_ARRANGEMENT: "Property unsuitable for this arrangement",
  PROPOSED_OCCUPANT_NOT_ELIGIBLE: "Proposed occupant not eligible",
  INSUFFICIENT_INFORMATION_PROVIDED: "Insufficient information provided",
  TERMS_NOT_ACCEPTABLE: "Terms not acceptable",
  POLICY_OR_JURISDICTION_RESTRICTION: "Policy or jurisdiction restriction",
  AUTHORITY_OR_OWNERSHIP_CONCERN: "Authority or ownership concern",
  OTHER: "Other (explanation required)",
};

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

// ZR-SUB-003 Section 10 step-up authentication: these are the one real
// "risk signal" this taxonomy has -- an irreversible full handover of the
// tenancy to a new occupant. Mirrors backend models/sublet_request.py's
// REPLACING_ARRANGEMENT_TYPES exactly.
export const REPLACING_ARRANGEMENT_TYPES = ["ASSIGNMENT_FULL", "REPLACEMENT_OCCUPANT"] as const;

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

// ZR-PAY-LINK-003 Section 1.1/2: a separate claim from AuthorityRecord's own
// list-authority one -- "authority to receive payments" for a room.
export const paymentRecipientAuthorityStatusTone = {
  pending_step_up: "warning",
  pending: "warning",
  verified: "success",
  failed: "danger",
  revoked: "danger",
} as const;

export const paymentRecipientAuthorityStatusLabel = {
  pending_step_up: "Confirm this change",
  pending: "Pending verification",
  verified: "Verified",
  failed: "Failed",
  revoked: "Revoked",
} as const;

// ZR-PAY-LINK-003 Section 3.1: the consolidated recipient+destination
// connection status for a room (distinct from paymentRecipientAuthorityStatusTone
// above, which is just the authority claim's own status).
export const paymentConnectionStatusTone = {
  DRAFT: "neutral",
  RECIPIENT_SETUP_REQUIRED: "warning",
  PENDING_VERIFICATION: "warning",
  ACTIVE: "success",
  SUSPENDED: "danger",
} as const;

export const paymentConnectionStatusLabel = {
  DRAFT: "Payment setup pending",
  RECIPIENT_SETUP_REQUIRED: "Payment destination required",
  PENDING_VERIFICATION: "Pending verification",
  ACTIVE: "Payments active",
  SUSPENDED: "Payments suspended",
} as const;

export const propertyVerificationStatusTone = {
  pending: "warning",
  verified: "success",
  rejected: "danger",
  additional_evidence_required: "warning",
  revoked: "danger",
} as const;

export const propertyVerificationStatusLabel = {
  pending: "Pending verification",
  verified: "Verified",
  rejected: "Rejected",
  additional_evidence_required: "Additional evidence required",
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
  revoked: "danger",
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

// --- ZR-PAY-002: Listing Fee (the only Zoiko-collected payment) ---

export const listingFeePaymentStatusTone = {
  PENDING: "warning",
  SUCCEEDED: "success",
  FAILED: "danger",
} as const;

export const listingFeeRefundStatusTone = {
  REQUESTED: "warning",
  PROCESSING: "warning",
  PARTIALLY_REFUNDED: "primary",
  REFUNDED: "success",
  FAILED: "danger",
} as const;

// --- ZR-PAY-LINK-003 Section 16: rental payment records (evidence/workflow
// only) --- CONFIRMED collapses the ZR-PAY-002-era CONFIRMED_BY_RECIPIENT/
// CONFIRMED_BY_PROVIDER pair -- "who confirmed" is still shown separately
// via record.provenance wherever this status is rendered, never lost.

export const rentalPaymentStatusTone = {
  UPCOMING: "neutral",
  DUE: "warning",
  PAYMENT_SESSION_STARTED: "primary",
  PROVIDER_PROCESSING: "primary",
  PAYER_RECORDED: "primary",
  RECIPIENT_CONFIRMATION_PENDING: "primary",
  CONFIRMED: "success",
  PARTIALLY_PAID: "warning",
  OVERDUE: "danger",
  DISPUTED: "danger",
  REVERSED: "danger",
  WAIVED: "neutral",
  CANCELLED: "neutral",
} as const;

export const rentalPaymentStatusLabel = {
  UPCOMING: "Upcoming",
  DUE: "Payment due",
  PAYMENT_SESSION_STARTED: "Secure payment in progress",
  PROVIDER_PROCESSING: "Processing",
  PAYER_RECORDED: "Marked as paid",
  RECIPIENT_CONFIRMATION_PENDING: "Awaiting confirmation",
  CONFIRMED: "Confirmed",
  PARTIALLY_PAID: "Partially paid",
  OVERDUE: "Overdue",
  DISPUTED: "Disputed",
  REVERSED: "Reversed",
  WAIVED: "Waived",
  CANCELLED: "Cancelled",
} as const;

export const rentalPaymentInstructionStatusTone = {
  PENDING_VERIFICATION: "warning",
  PENDING_REVIEW: "warning",
  ACTIVE: "success",
  SUPERSEDED: "neutral",
  REJECTED: "danger",
} as const;

// -- Section 10 gap: ZR-ENG-CLR-010 general-purpose Dispute Resolution
// engine -- distinct from the finance.DisputeCase (chargeback) tones above.
export const disputeCaseStatusTone: Record<string, "primary" | "accent" | "success" | "warning" | "neutral" | "danger"> = {
  SUBMITTED: "warning",
  TRIAGED: "primary",
  LEGAL_REVIEW_REQUIRED: "danger",
  IN_PROGRESS: "primary",
  PARTIALLY_RESOLVED: "warning",
  RESOLVED: "success",
  CLOSED: "neutral",
  ON_HOLD: "neutral",
  EXTERNAL_PENDING: "warning",
  REOPENED: "danger",
};

export const disputeClaimStatusTone: Record<string, "primary" | "accent" | "success" | "warning" | "neutral" | "danger"> = {
  OPEN: "warning",
  RESPONSE_DUE: "warning",
  EVIDENCE: "primary",
  NEGOTIATION: "primary",
  INTERNAL_REVIEW: "danger",
  EXTERNAL_REFERRAL: "warning",
  UPHELD: "success",
  PARTLY_UPHELD: "success",
  NOT_UPHELD: "neutral",
  SETTLED: "success",
  WITHDRAWN: "neutral",
};

export const disputeSeverityTone: Record<string, "primary" | "accent" | "success" | "warning" | "neutral" | "danger"> = {
  "SEV-0": "danger",
  "SEV-1": "danger",
  "SEV-2": "warning",
  "SEV-3": "neutral",
};

export const disputeSettlementStatusTone: Record<string, "primary" | "accent" | "success" | "warning" | "neutral" | "danger"> = {
  SENT: "warning",
  COUNTERED: "primary",
  ACCEPTED: "success",
  REJECTED: "danger",
  EXPIRED: "neutral",
  EFFECTIVE: "success",
  VOID: "neutral",
};

export const disputeFinancialHoldStatusTone: Record<string, "primary" | "accent" | "success" | "warning" | "neutral" | "danger"> = {
  PROPOSED: "warning",
  ACTIVE: "danger",
  RELEASE_PENDING: "warning",
  RELEASED: "success",
  CLOSED: "neutral",
};

export const disputeDeadlineStatusTone: Record<string, "primary" | "accent" | "success" | "warning" | "neutral" | "danger"> = {
  PENDING: "warning",
  MET: "success",
  EXTENDED: "primary",
  CANCELLED: "neutral",
};

export const disputeExternalProceedingStatusTone: Record<string, "primary" | "accent" | "success" | "warning" | "neutral" | "danger"> = {
  FILED: "warning",
  ACCEPTED: "primary",
  PENDING: "warning",
  DISMISSED: "neutral",
  WITHDRAWN: "neutral",
};

export const disputeClaimFamilyLabel: Record<string, string> = {
  DEPOSIT: "Deposit",
  PAYMENT: "Payment",
  REFUND_PAYOUT: "Refund / Payout",
  PROPERTY_CONDITION: "Property Condition",
  BOOKING_AGREEMENT: "Booking Agreement",
  SUBLET_OCCUPANCY: "Sublet / Occupancy",
  MARKETPLACE_CONDUCT: "Marketplace Conduct",
  PROTECTED_SAFETY: "Protected / Safety",
  VERIFICATION_FRAUD: "Verification / Fraud",
  ZOIKO_SERVICE: "Zoiko Service",
};

export const disputeCaseReopenGroundsLabel: Record<string, string> = {
  MATERIAL_NEW_EVIDENCE: "Material new evidence",
  PROCESSING_ERROR: "Processing error",
  EXTERNAL_DECISION: "External decision",
  FRAUD_FINDING: "Fraud finding",
  INTERNAL_REVIEW_REQUESTED: "Internal review requested",
  OTHER: "Other",
};
