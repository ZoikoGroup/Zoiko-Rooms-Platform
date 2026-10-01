/**
 * ZR-IDENTITY-001 global identity verification -- types and API client for
 * the person's step-by-step flow (/api/users/identity) and the reviewer
 * case view. Every status here is server-authoritative; the client never
 * decides whether someone is verified.
 */

import { apiClientFetch } from "@/lib/api-client";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type IdentityState =
  | "NOT_STARTED"
  | "IN_PROGRESS"
  | "PROCESSING"
  | "PENDING_REVIEW"
  | "ACTION_REQUIRED"
  | "VERIFIED"
  | "REVERIFICATION_REQUIRED"
  | "FAILED";

export type IdentityMethod = "DIGITAL_IDENTITY" | "DOCUMENT" | "MANUAL";
export type IdentityRole = "OWNER" | "AGENT" | "SUBLETTER";
export type AlternativeReason = "NO_CAMERA" | "ACCESSIBILITY" | "NO_ACCEPTED_DOCUMENT" | "PROVIDER_UNAVAILABLE" | "OTHER";

/** UPLOAD: the document is uploaded to Zoiko. PROVIDER_HOSTED: the document
 *  and selfie are captured inside the provider's flow (Veriff). */
export type CaptureMode = "UPLOAD" | "PROVIDER_HOSTED";

export interface IdentityPack {
  captureMode: CaptureMode;
  providerAvailable: boolean;
  selfieCheck: boolean;
  countryCode: string;
  countryName: string;
  version: number;
  acceptedDocumentTypes: string[];
  availableMethods: IdentityMethod[];
  dateOfBirthRequired: boolean;
  minimumAge: number | null;
  biometricConsentText: string;
  privacyNoticeText: string;
}

export interface IdentityDashboard {
  state: IdentityState;
  header: string;
  primaryAction: string | null;
  attention: boolean;
  message: string;
  actions: string[];
}

export interface IdentitySession {
  id: number;
  state: IdentityState;
  method: IdentityMethod;
  roleContext: string;
  documentType: string;
  maskedDocumentNumber: string;
  hasDocument: boolean;
  documentOriginalName: string;
  attested: boolean;
  reasonCodes: string[];
  message: string;
  actions: string[];
  /** Hosted capture: the provider flow can be (re)opened now. */
  launchAvailable: boolean;
  /** Only on submit / launch responses -- never stored client-side. */
  launchUrl: string | null;
  canRestart: boolean;
  createdAt: string;
  submittedAt: string | null;
  decidedAt: string | null;
}

export interface IdentityProfile {
  state: IdentityState;
  assuranceLevel: string;
  givenName: string;
  middleNames: string;
  familyName: string;
  dateOfBirth: string | null;
  countryCode: string;
  verifiedLegalName: string;
  verifiedAt: string | null;
  reverificationRequiredAt: string | null;
  verificationMethod: string;
  dashboard: IdentityDashboard;
  pack: IdentityPack;
  currentSession: IdentitySession | null;
}

export interface IdentityCountry {
  countryCode: string;
  countryName: string;
}

export interface IdentityDetailsInput {
  givenName: string;
  middleNames: string;
  familyName: string;
  dateOfBirth: string | null;
  countryCode: string;
  currentPassword?: string;
}

const BASE = "/api/users/identity";

export function getMyIdentity(): Promise<IdentityProfile> {
  return apiClientFetch<IdentityProfile>(BASE);
}

export function saveIdentityDetails(input: IdentityDetailsInput): Promise<IdentityProfile> {
  return apiClientFetch<IdentityProfile>(`${BASE}/details`, { method: "PUT", body: JSON.stringify(input) });
}

export function listIdentityCountries(): Promise<IdentityCountry[]> {
  return apiClientFetch<IdentityCountry[]>(`${BASE}/countries`);
}

export function getIdentityPolicy(country: string): Promise<IdentityPack> {
  return apiClientFetch<IdentityPack>(`${BASE}/policy?country=${encodeURIComponent(country)}`);
}

/** Create or resume a session. The idempotency key makes a double-click or a
 *  retried request return the same session instead of a second one. */
export function startIdentitySession(method: IdentityMethod, roleContext: string, idempotencyKey: string): Promise<IdentitySession> {
  return apiClientFetch<IdentitySession>(`${BASE}/verifications`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ method, roleContext }),
  });
}

export function getIdentitySession(id: number): Promise<IdentitySession> {
  return apiClientFetch<IdentitySession>(`${BASE}/verifications/${id}`);
}

export function uploadIdentityDocument(id: number, documentType: string, documentNumber: string, file: File): Promise<IdentitySession> {
  const form = new FormData();
  form.append("document_type", documentType);
  form.append("document_number", documentNumber);
  form.append("file", file);
  return apiClientFetch<IdentitySession>(`${BASE}/verifications/${id}/document`, { method: "POST", body: form });
}

export function submitIdentitySession(id: number, attested: boolean): Promise<IdentitySession> {
  return apiClientFetch<IdentitySession>(`${BASE}/verifications/${id}/submit`, {
    method: "POST",
    body: JSON.stringify({ attested }),
  });
}

export function requestIdentityAlternative(id: number, reasonCode: AlternativeReason, note: string): Promise<IdentitySession> {
  return apiClientFetch<IdentitySession>(`${BASE}/verifications/${id}/alternative`, {
    method: "POST",
    body: JSON.stringify({ reasonCode, note }),
  });
}

/** Reopen the provider's capture flow for an unfinished session. */
export function launchIdentitySession(id: number): Promise<IdentitySession> {
  return apiClientFetch<IdentitySession>(`${BASE}/verifications/${id}/launch`, { method: "POST" });
}

/** Ask the server to check with the provider for a decision (rate-limited). */
export function refreshIdentitySession(id: number): Promise<IdentitySession> {
  return apiClientFetch<IdentitySession>(`${BASE}/verifications/${id}/refresh`, { method: "POST" });
}

/** A new attempt after an expired, abandoned or declined one. */
export function restartIdentitySession(id: number): Promise<IdentitySession> {
  return apiClientFetch<IdentitySession>(`${BASE}/verifications/${id}/restart`, { method: "POST" });
}

export function createIdentityHandoff(id: number): Promise<{ token: string; expiresInSeconds: number }> {
  return apiClientFetch(`${BASE}/verifications/${id}/resume`, { method: "POST" });
}

export function claimIdentityHandoff(token: string): Promise<IdentitySession> {
  return apiClientFetch<IdentitySession>(`${BASE}/handoff/claim`, { method: "POST", body: JSON.stringify({ token }) });
}

// --- Reviewer (admin) ---------------------------------------------------------

export type ReviewerDecision = "APPROVE" | "ACTION_REQUIRED" | "REJECT" | "ESCALATE";

export interface IdentityCaseHistoryItem {
  eventType: string;
  occurredAt: string;
  actorKind: string;
  actorId: string;
  previousState: string | null;
  newState: string | null;
  reasonCodes: string[];
  decision: string | null;
}

export interface IdentityCase {
  id: number;
  partyId: number;
  accountName: string;
  accountEmail: string;
  countryCode: string;
  roleContext: string;
  method: string;
  providerCode: string;
  state: IdentityState;
  assuranceLevel: string;
  reasonCodes: string[];
  checks: Record<string, unknown>;
  duplicateOfVerificationId: number | null;
  legalName: string;
  profileState: string;
  documentType: string;
  maskedDocumentNumber: string;
  providerDecision: string;
  providerSessionRef: string;
  consentNoticeVersion: string;
  hasDocument: boolean;
  documentContentType: string;
  evidencePurgedAt: string | null;
  alternativeReason: string;
  escalated: boolean;
  verifierNotes: string;
  submittedAt: string | null;
  decidedAt: string | null;
  history: IdentityCaseHistoryItem[];
}

export interface IdentityQueueItem {
  id: number;
  partyId: number;
  documentType: string;
  sessionState: IdentityState;
  methodType: string;
  countryCode: string;
  roleContext: string;
  reasonCodes: string[];
  escalatedAt: string | null;
  submittedAt: string | null;
  createdAt: string;
  status: string;
}

export interface IdentityMetrics {
  periodDays: number;
  started: number;
  submitted: number;
  verified: number;
  startToVerifiedRate: number | null;
  manualReviewRate: number | null;
  actionRequiredRecoveryRate: number | null;
  providerErrorRate: number | null;
  timeToDecisionHours: {
    automated: { median: number | null; p90: number | null };
    manual: { median: number | null; p90: number | null };
  };
  byMethod: Record<string, { submitted: number; verified: number }>;
  reasonCodes: Record<string, number>;
  pendingReview: number;
  providerOutcomes: Record<string, number>;
  providerSessionsCreated: number;
  webhookAuthFailures: number;
  webhookEvents: number;
  webhookDuplicateRate: number | null;
  webhookProcessingLagSeconds: number | null;
  webhookUnprocessed: number;
  webhookFailed: number;
  reconciledSessions: number;
  staleSessions: number;
}

export interface IdentityPackAdmin extends IdentityPack {
  id: number;
  evidenceRetentionDays: number | null;
  reverificationIntervalDays: number | null;
  reverifyOnAccountRecovery: boolean;
  maxAttemptsPerDay: number;
  documentProviderCode: string;
}

export const PROVIDER_OPTIONS: { value: string; label: string }[] = [
  { value: "veriff", label: "Veriff (document + selfie)" },
  { value: "zoiko_document_check", label: "Built-in document check" },
  { value: "signed_webhook", label: "Other signed-webhook provider" },
];

export function listIdentityPacks(): Promise<IdentityPackAdmin[]> {
  return apiClientFetch<IdentityPackAdmin[]>(`/api/identity-verifications/packs`);
}

export function updateIdentityPack(id: number, changes: Partial<Record<string, unknown>>): Promise<IdentityPackAdmin> {
  return apiClientFetch<IdentityPackAdmin>(`/api/identity-verifications/packs/${id}`, {
    method: "PUT",
    body: JSON.stringify(changes),
  });
}

export function reconcileIdentityCase(id: number): Promise<{ result: string }> {
  return apiClientFetch(`/api/identity-verifications/${id}/reconcile`, { method: "POST" });
}

export function eraseIdentityData(partyId: number, reason: string): Promise<{ sessions: number; providerDeleted: number; providerFailed: number }> {
  return apiClientFetch(`/api/identity-verifications/parties/${partyId}/erase`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export function listIdentityQueue(filter: string): Promise<IdentityQueueItem[]> {
  const query = filter === "all" ? "" : `?status=${encodeURIComponent(filter)}`;
  return apiClientFetch<IdentityQueueItem[]>(`/api/identity-verifications${query}`);
}

export function getIdentityCase(id: number): Promise<IdentityCase> {
  return apiClientFetch<IdentityCase>(`/api/identity-verifications/${id}/case`);
}

export function getReviewerReasonCodes(): Promise<{ decisions: ReviewerDecision[]; reasonCodes: { code: string; message: string }[] }> {
  return apiClientFetch(`/api/identity-verifications/reason-codes`);
}

export function decideIdentityCase(id: number, decision: ReviewerDecision, reasonCode: string, note: string) {
  return apiClientFetch(`/api/identity-verifications/${id}/decision`, {
    method: "POST",
    body: JSON.stringify({ decision, reasonCode, note }),
  });
}

export function getIdentityMetrics(days = 30): Promise<IdentityMetrics> {
  return apiClientFetch<IdentityMetrics>(`/api/identity-verifications/metrics?days=${days}`);
}

/** The reviewer's secure, inline viewer source (never a download link). */
export function identityEvidenceUrl(id: number): string {
  return `${API_URL}/api/identity-verifications/${id}/document`;
}

// --- Display helpers -------------------------------------------------------------

export const identityStateLabel: Record<IdentityState, string> = {
  NOT_STARTED: "Not verified",
  IN_PROGRESS: "Verification in progress",
  PROCESSING: "Checking identity",
  PENDING_REVIEW: "In review",
  ACTION_REQUIRED: "Action required",
  VERIFIED: "Identity Verified",
  REVERIFICATION_REQUIRED: "Verify again",
  FAILED: "Could not verify",
};

/** Colour always paired with text and an icon (Section 11). */
export const identityStateTone: Record<IdentityState, "success" | "warning" | "danger" | "neutral" | "primary"> = {
  NOT_STARTED: "neutral",
  IN_PROGRESS: "primary",
  PROCESSING: "primary",
  PENDING_REVIEW: "primary",
  ACTION_REQUIRED: "warning",
  VERIFIED: "success",
  REVERIFICATION_REQUIRED: "warning",
  FAILED: "danger",
};

export const roleLabel: Record<IdentityRole, string> = {
  OWNER: "Owner / co-owner",
  AGENT: "Agent or property manager",
  SUBLETTER: "Tenant / subletter",
};

export const alternativeReasonLabel: Record<AlternativeReason, string> = {
  NO_CAMERA: "My device doesn't have a suitable camera",
  ACCESSIBILITY: "I need an accessible alternative",
  NO_ACCEPTED_DOCUMENT: "I don't have an accepted document",
  PROVIDER_UNAVAILABLE: "The check isn't working for me",
  OTHER: "Something else",
};

export const remediationLabel: Record<string, string> = {
  RETRY_CAPTURE: "Retry the photo or scan",
  UPLOAD_ANOTHER_DOCUMENT: "Upload another accepted document",
  CHOOSE_ANOTHER_METHOD: "Choose another verification option",
  REQUEST_REVIEW: "Ask for a manual review",
  EDIT_DETAILS: "Review your details",
  CONTACT_SUPPORT: "Contact support",
  TRY_LATER: "Try again later",
};

// --- Veriff go-live readiness (ZR-IDV-ADR-001 Sections 15 / 17) ---------------------

export interface VeriffReadiness {
  provider: string;
  integration: string;
  isProduction: boolean;
  plan: "decision" | "full_auto" | string;
  enabled: boolean;
  disabledReason: string;
  webhookUrls: { decision: string; events: string; fullAuto: string };
  checks: { code: string; label: string; ok: boolean }[];
  lastConnectionTest: { ok: boolean; detail: string; at: string; integration: string } | null;
  lastAuthenticatedWebhookAt: string | null;
  lastRejectedWebhookAt: string | null;
  gates: {
    code: string;
    label: string;
    confirmed: boolean;
    confirmedAt: string | null;
    confirmedByAdminId: number | null;
    evidenceReference: string;
    note: string;
  }[];
}

export interface ReasonMapping {
  id: number;
  providerCode: string;
  providerDecision: string;
  providerReasonCode: string;
  zoikoReasonCode: string;
  description: string;
}

export function getVeriffReadiness(): Promise<VeriffReadiness> {
  return apiClientFetch<VeriffReadiness>(`/api/identity-verifications/providers/veriff/readiness`);
}

export function setVeriffGate(code: string, confirmed: boolean, evidenceReference: string, note: string): Promise<VeriffReadiness> {
  return apiClientFetch<VeriffReadiness>(`/api/identity-verifications/providers/veriff/gates/${code}`, {
    method: "PUT",
    body: JSON.stringify({ confirmed, evidenceReference, note }),
  });
}

export function runVeriffConnectionTest(): Promise<VeriffReadiness> {
  return apiClientFetch<VeriffReadiness>(`/api/identity-verifications/providers/veriff/connection-test`, { method: "POST" });
}

export function listReasonMappings(): Promise<ReasonMapping[]> {
  return apiClientFetch<ReasonMapping[]>(`/api/identity-verifications/providers/reason-mappings`);
}

export function upsertReasonMapping(mapping: Omit<ReasonMapping, "id">): Promise<ReasonMapping> {
  return apiClientFetch<ReasonMapping>(`/api/identity-verifications/providers/reason-mappings`, {
    method: "PUT",
    body: JSON.stringify(mapping),
  });
}
