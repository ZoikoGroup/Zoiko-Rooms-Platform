/**
 * ZR-IDENTITY-001 / ZR-IDV-ADR-001 identity verification -- types and API
 * client for the person's flow (/api/users/identity) and the admin case
 * view. The document and selfie are photographed in Zoiko's own capture
 * screens; each photo goes from our backend straight to Veriff (never
 * stored) and only Veriff's decision can verify someone -- there is no
 * manual-review route. The client never decides whether someone is verified.
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

export type IdentityMethod = "DOCUMENT";
export type IdentityRole = "OWNER" | "AGENT" | "SUBLETTER";

/** A provider (Veriff) session decides the verification. */
export type CaptureMode = "PROVIDER_HOSTED";
/** The photos taken in Zoiko's capture screens (Veriff media contexts). */
export type CaptureContext = "document-front" | "document-back" | "face";

export interface IdentityPack {
  captureMode: CaptureMode;
  /** False when Veriff isn't configured / enabled: nobody can verify now. */
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
  /** Photos can be taken now (in progress, or Veriff asked for new ones). */
  captureAvailable: boolean;
  /** Which photos were already sent to Veriff for this attempt. */
  captured: CaptureContext[];
  /** The chosen document has a back side Veriff needs. */
  backRequired: boolean;
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

export function submitIdentitySession(id: number, attested: boolean): Promise<IdentitySession> {
  return apiClientFetch<IdentitySession>(`${BASE}/verifications/${id}/submit`, {
    method: "POST",
    body: JSON.stringify({ attested }),
  });
}

/** Send one photo from the capture screen; the backend relays it straight
 *  to Veriff. `documentType` is required with the document's front. */
export function captureIdentityPhoto(id: number, context: CaptureContext, photo: Blob, documentType = ""): Promise<IdentitySession> {
  const form = new FormData();
  form.append("context", context);
  form.append("document_type", documentType);
  form.append("file", photo, photo.type === "image/png" ? "photo.png" : "photo.jpg");
  return apiClientFetch<IdentitySession>(`${BASE}/verifications/${id}/capture`, { method: "POST", body: form });
}

/** All photos taken: Veriff starts deciding. The result arrives later. */
export function completeIdentityCapture(id: number): Promise<IdentitySession> {
  return apiClientFetch<IdentitySession>(`${BASE}/verifications/${id}/complete`, { method: "POST" });
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

// --- Admin case view (read-only: Veriff decides) -------------------------------

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
  actionRequiredRecoveryRate: number | null;
  providerErrorRate: number | null;
  timeToDecisionHours: { median: number | null; p90: number | null };
  byMethod: Record<string, { submitted: number; verified: number }>;
  reasonCodes: Record<string, number>;
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
  /** Null until VERIFF_COST_PER_SESSION is configured. */
  providerCost: {
    currency: string;
    perSession: number;
    sessions: number;
    total: number;
    perCompletedVerification: number | null;
    perApprovedAccount: number | null;
  } | null;
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

export const remediationLabel: Record<string, string> = {
  RETRY_CAPTURE: "Take new photos of your document and a selfie",
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
