/**
 * ZR-AUTHORITY-002 -- Authority Verification: types and API client for the
 * host flow (/api/users/.../authority-verifications), the public owner /
 * landlord confirmation (/api/authority-confirmations) and the Trust &
 * Safety review (/api/authority-verifications). Requirements, states and
 * allowed actions all come from the server; the client never decides
 * authority.
 */

import { apiClientFetch } from "@/lib/api-client";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const BASE = "/api/users/authority-verifications";
const ADMIN = "/api/authority-verifications";

export type AuthorityState =
  | "NOT_STARTED" | "COLLECTING" | "SUBMITTED" | "MANUAL_REVIEW" | "ACTION_REQUIRED" | "VERIFIED"
  | "EXPIRING_SOON" | "EXPIRED" | "REVOKED" | "REJECTED" | "SUPERSEDED";
export type RelationshipType =
  | "OWNER" | "CO_OWNER" | "REPRESENTATIVE" | "AGENT" | "PROPERTY_MANAGER" | "TENANT_SUBLETTER";
export type AuthorityRoute = "OWNER" | "AGENT" | "SUBLET";
export type ScopeCode = "ADVERTISE" | "RENT" | "MANAGE" | "SUBLET" | "COLLECT_RENT";
export type AllowedAction =
  | "EDIT_DETAILS" | "ADD_EVIDENCE" | "REQUEST_CONFIRMATION" | "SUBMIT" | "RENEW" | "REVOKE"
  | "REQUEST_RECONSIDERATION" | "START_NEW";

export interface AuthorityRequirement {
  requirement_id: string;
  evidence_class: string;
  title: string;
  purpose: string;
  accepted_examples: string[];
  required: boolean;
  owner_confirmation: boolean;
  status: "MISSING" | "READY" | "NEEDS_REPLACEMENT" | "AWAITING_CONFIRMATION";
  evidence_ids: number[];
  confirmation_pending: boolean;
  confirmed: boolean;
}

export interface AuthorityEvidence {
  id: number;
  requirementId: string;
  evidenceType: string;
  sourceType: "UPLOAD" | "OWNER_CONFIRMATION";
  issuer: string;
  documentReference: string;
  issuedAt: string | null;
  expiresAt: string | null;
  originalFilename: string;
  contentType: string;
  fileSize: number;
  processingStatus: string;
  available: boolean;
  createdAt: string;
}

export interface AuthorityConfirmation {
  id: number;
  kind: "MANDATE" | "SUBLET_PERMISSION" | "CO_OWNER_CONSENT";
  requirementId: string;
  recipientName: string;
  status: "PENDING" | "CONFIRMED" | "DECLINED" | "EXPIRED";
  expiresAt: string;
  respondedAt: string | null;
  responderName: string;
  createdAt: string;
}

export interface Organization {
  id: number;
  name: string;
  registrationNumber: string;
  countryCode: string;
  representativeRole: string;
  status: "PENDING" | "VERIFIED" | "REJECTED";
  verifiedAt: string | null;
}

export interface AuthorityVerification {
  id: number;
  propertyId: number;
  roomScopeIds: number[];
  relationshipType: RelationshipType;
  route: AuthorityRoute;
  actingCapacity: "" | "PERSONAL" | "ORGANIZATION" | null;
  organization: Organization | null;
  countryCode: string;
  packVersion: number;
  state: AuthorityState;
  assuranceLevel: string;
  principalName: string;
  scopeCodes: ScopeCode[];
  restrictions: string;
  effectiveAt: string | null;
  expiresAt: string | null;
  revokedAt: string | null;
  revocationReasonCode: string;
  reasonCodes: string[];
  reason: { message: string; cta: string };
  requirements: AuthorityRequirement[];
  evidence: AuthorityEvidence[];
  confirmations: AuthorityConfirmation[];
  allowedActions: AllowedAction[];
  previousId: number | null;
  supersededById: number | null;
  isReconsideration: boolean;
  submittedAt: string | null;
  decidedAt: string | null;
  verifiedAt: string | null;
  createdAt: string;
  version: number;
}

export interface PublicationEligibility {
  propertyId: number;
  identityVerified: boolean;
  propertyVerified: boolean;
  rooms: { roomId: number; authority: boolean; authorityExpiresAt: string | null }[];
  reasonCodes: string[];
  eligible: boolean;
}

export const relationshipLabel: Record<RelationshipType, string> = {
  OWNER: "I own this property",
  CO_OWNER: "I co-own this property",
  REPRESENTATIVE: "I represent the owning company, trust or estate",
  AGENT: "I am the owner's agent",
  PROPERTY_MANAGER: "I manage this property for the owner",
  TENANT_SUBLETTER: "I rent this property and want to sublet",
};

export const relationshipHint: Record<RelationshipType, string> = {
  OWNER: "You'll upload a document showing you own the property.",
  CO_OWNER: "You'll upload ownership evidence. Some countries also need your co-owner's consent.",
  REPRESENTATIVE: "You'll upload ownership evidence and proof you can act for the owning entity.",
  AGENT: "You'll provide the owner's mandate. We may ask the owner to confirm.",
  PROPERTY_MANAGER: "You'll provide the management agreement. We may ask the owner to confirm.",
  TENANT_SUBLETTER: "You'll provide your tenancy and your landlord's permission to sublet.",
};

export const scopeLabel: Record<ScopeCode, string> = {
  ADVERTISE: "Advertise the property",
  RENT: "Agree rentals",
  MANAGE: "Manage the property",
  SUBLET: "Sublet",
  COLLECT_RENT: "Collect rent",
};

export const authorityStateLabel: Record<AuthorityState, string> = {
  NOT_STARTED: "Authority not verified",
  COLLECTING: "Authority in progress",
  SUBMITTED: "Checking your authority",
  MANUAL_REVIEW: "Authority in review",
  ACTION_REQUIRED: "Authority action required",
  VERIFIED: "Listing Authority Verified",
  EXPIRING_SOON: "Authority expiring soon",
  EXPIRED: "Authority expired",
  REVOKED: "Authority withdrawn",
  REJECTED: "Authority not verified",
  SUPERSEDED: "Replaced by a newer verification",
};

export const authorityStateCta: Partial<Record<AuthorityState, string>> = {
  NOT_STARTED: "Verify authority",
  COLLECTING: "Continue",
  MANUAL_REVIEW: "View status",
  ACTION_REQUIRED: "Fix now",
  EXPIRING_SOON: "Renew",
  EXPIRED: "Renew",
  REVOKED: "Review status",
  REJECTED: "Review reason",
};

export function authorityStateTone(state: AuthorityState): "success" | "warning" | "danger" | "neutral" {
  if (state === "VERIFIED") return "success";
  if (state === "ACTION_REQUIRED" || state === "EXPIRING_SOON") return "warning";
  if (state === "EXPIRED" || state === "REVOKED" || state === "REJECTED") return "danger";
  return "neutral";
}

function versioned(v: { version: number }): HeadersInit {
  return { "If-Match": String(v.version) };
}

export function listPropertyAuthority(propertyId: number) {
  return apiClientFetch<AuthorityVerification[]>(`/api/users/properties/${propertyId}/authority-verifications`);
}

export function startAuthorityVerification(propertyId: number, relationshipType: RelationshipType, idempotencyKey: string) {
  return apiClientFetch<AuthorityVerification>(`/api/users/properties/${propertyId}/authority-verifications`, {
    method: "POST", headers: { "Idempotency-Key": idempotencyKey }, body: JSON.stringify({ relationshipType }),
  });
}

export function getAuthorityVerification(id: number) {
  return apiClientFetch<AuthorityVerification>(`${BASE}/${id}`);
}

export function getPublicationEligibility(propertyId: number) {
  return apiClientFetch<PublicationEligibility>(`/api/users/properties/${propertyId}/publication-eligibility`);
}

export function setAuthorityDetails(v: AuthorityVerification, details: {
  actingCapacity?: "PERSONAL" | "ORGANIZATION"; organizationId?: number; principalName?: string;
  scopeCodes?: ScopeCode[]; effectiveAt?: string; expiresAt?: string; restrictions?: string;
}) {
  return apiClientFetch<AuthorityVerification>(`${BASE}/${v.id}/details`, {
    method: "PUT", headers: versioned(v), body: JSON.stringify(details),
  });
}

export function uploadAuthorityEvidence(v: AuthorityVerification, requirementId: string, file: File,
  extra: { issuer?: string; documentReference?: string; issuedAt?: string; expiresAt?: string; replacesId?: number } = {}) {
  const form = new FormData();
  form.append("requirementId", requirementId);
  form.append("file", file);
  if (extra.issuer) form.append("issuer", extra.issuer);
  if (extra.documentReference) form.append("documentReference", extra.documentReference);
  if (extra.issuedAt) form.append("issuedAt", extra.issuedAt);
  if (extra.expiresAt) form.append("expiresAt", extra.expiresAt);
  if (extra.replacesId) form.append("replacesId", String(extra.replacesId));
  return apiClientFetch<AuthorityVerification>(`${BASE}/${v.id}/evidence`, {
    method: "POST", headers: versioned(v), body: form,
  });
}

export function removeAuthorityEvidence(v: AuthorityVerification, evidenceId: number) {
  return apiClientFetch<AuthorityVerification>(`${BASE}/${v.id}/evidence/${evidenceId}`, {
    method: "DELETE", headers: versioned(v),
  });
}

export function authorityEvidenceUrl(verificationId: number, evidenceId: number) {
  return `${API_URL}${BASE}/${verificationId}/evidence/${evidenceId}/document`;
}

export function requestOwnerConfirmation(v: AuthorityVerification, requirementId: string, recipientEmail: string,
  recipientName: string, idempotencyKey: string) {
  return apiClientFetch<AuthorityVerification>(`${BASE}/${v.id}/owner-confirmations`, {
    method: "POST", headers: { ...versioned(v), "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ requirementId, recipientEmail, recipientName }),
  });
}

export function submitAuthorityVerification(v: AuthorityVerification, idempotencyKey: string) {
  return apiClientFetch<AuthorityVerification>(`${BASE}/${v.id}/submit`, {
    method: "POST", headers: { ...versioned(v), "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ attested: true }),
  });
}

export function renewAuthorityVerification(v: AuthorityVerification, reconsiderationNote?: string) {
  return apiClientFetch<AuthorityVerification>(`${BASE}/${v.id}/renew`, {
    method: "POST", body: JSON.stringify(reconsiderationNote === undefined ? {} : { reconsiderationNote }),
  });
}

export function revokeAuthorityVerification(v: AuthorityVerification) {
  return apiClientFetch<AuthorityVerification>(`${BASE}/${v.id}/revoke`, { method: "POST" });
}

export function listOrganizations() {
  return apiClientFetch<Organization[]>("/api/users/organizations");
}

export function createOrganization(body: { name: string; registrationNumber?: string; countryCode?: string;
  representativeRole?: string }) {
  return apiClientFetch<Organization>("/api/users/organizations", { method: "POST", body: JSON.stringify(body) });
}

// -- public owner / landlord confirmation --------------------------------------------

export interface ConfirmationSummary {
  status: AuthorityConfirmation["status"];
  kind: AuthorityConfirmation["kind"];
  expiresAt: string;
  requesterName: string;
  propertyCity: string;
  propertyCountry: string;
  relationshipType: RelationshipType;
  scopeCodes: ScopeCode[];
  effectiveAt: string | null;
  endsAt: string | null;
  restrictions: string;
}

export function getConfirmation(token: string) {
  return apiClientFetch<ConfirmationSummary>(`/api/authority-confirmations/${encodeURIComponent(token)}`);
}

export function respondToConfirmation(token: string, body: { code: string; decision: "CONFIRM" | "DECLINE";
  responderName: string }) {
  return apiClientFetch<{ status: string }>(`/api/authority-confirmations/${encodeURIComponent(token)}`, {
    method: "POST", body: JSON.stringify(body),
  });
}

// -- Trust & Safety -------------------------------------------------------------------

export interface AuthorityQueueItem {
  id: number;
  propertyId: number;
  partyId: number;
  state: AuthorityState;
  relationshipType: RelationshipType;
  route: AuthorityRoute;
  countryCode: string;
  reasonCodes: string[];
  awaitingSecondApproval: boolean;
  isReconsideration: boolean;
  submittedAt: string | null;
  createdAt: string;
}

export interface AuthorityCase extends Omit<AuthorityVerification, "evidence"> {
  partyId: number;
  verifiedLegalName: string;
  property: { id: number; address: string; city: string; postalCode: string; verified: boolean };
  evidence: (AuthorityEvidence & { readable: boolean | null; propertyMatched: boolean | null; nameMatched: boolean | null;
    principalMatched: boolean | null; reusedElsewhere: boolean; tamperSignal: boolean })[];
  matchResults: Record<string, boolean | null>;
  reviewReasonCodes: string[];
  reviewNote: string;
  reconsiderationNote: string;
  awaitingSecondApproval: boolean;
  firstApproverAdminId: number | null;
  otherClaims: { id: number; partyId: number; relationshipType: RelationshipType; state: AuthorityState; createdAt: string }[];
  events: { type: string; previousState: string | null; newState: string | null; actorKind: string; createdAt: string }[];
}

export function listAuthorityQueue(state = "MANUAL_REVIEW") {
  return apiClientFetch<AuthorityQueueItem[]>(`${ADMIN}?state=${encodeURIComponent(state)}`);
}

export function getAuthorityCase(id: number) {
  return apiClientFetch<AuthorityCase>(`${ADMIN}/${id}`);
}

export function getAuthorityReviewReasons() {
  return apiClientFetch<Record<"APPROVE" | "REQUEST_EVIDENCE" | "REJECT", { code: string; message: string }[]>>(
    `${ADMIN}/review-reasons`,
  );
}

export function reviewAuthority(c: { id: number; version: number }, decision: string, reasonCode: string, note: string) {
  return apiClientFetch<AuthorityVerification>(`${ADMIN}/${c.id}/review`, {
    method: "POST", headers: versioned(c), body: JSON.stringify({ decision, reasonCode, note }),
  });
}

export function adminRevokeAuthority(id: number, reasonCode: string) {
  return apiClientFetch<AuthorityVerification>(`${ADMIN}/${id}/revoke`, {
    method: "POST", body: JSON.stringify({ reasonCode }),
  });
}

export function adminAuthorityEvidenceUrl(verificationId: number, evidenceId: number) {
  return `${API_URL}${ADMIN}/${verificationId}/evidence/${evidenceId}/document`;
}

export function getAuthorityMetrics(days = 30) {
  return apiClientFetch<Record<string, unknown>>(`${ADMIN}/metrics?days=${days}`);
}

export function listPendingOrganizations() {
  return apiClientFetch<Organization[]>(`${ADMIN}/organizations?status_filter=PENDING`);
}

export function decideOrganization(id: number, approve: boolean) {
  return apiClientFetch<Organization>(`${ADMIN}/organizations/${id}/decision`, {
    method: "POST", body: JSON.stringify({ approve }),
  });
}
