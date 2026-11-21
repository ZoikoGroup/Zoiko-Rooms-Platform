/**
 * Identity verification -- types and API client for the person's flow (/api/users/identity) and the admin case
 * view. The person uploads a photo of their identity document and takes one selfie; the server compares the two
 * faces (it never stores the photos) and, if they match, the identity is verified. Nothing is read from the
 * document and there is no liveness test. The client never decides whether someone is verified.
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

/** The server compares the face on the document photo with the selfie. */
export type CaptureMode = "FACE_MATCH";

export interface IdentityPack {
  captureMode: CaptureMode;
  /** False when the face models aren't installed on the server: nobody can verify right now. */
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
  /** The document photo and selfie can be submitted now (details confirmed; in progress or asked for clearer photos). */
  captureAvailable: boolean;
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

/** THE identity check: send the photo of the identity document and the selfie together. The server compares the
 *  two faces (nothing is stored) and the result is in the response: VERIFIED, ACTION_REQUIRED (clearer photos
 *  needed) or FAILED. */
export function submitFaceMatch(id: number, documentType: string, document: Blob, selfie: Blob): Promise<IdentitySession> {
  const form = new FormData();
  form.append("document_type", documentType);
  form.append("document", document, document.type === "image/png" ? "document.png" : "document.jpg");
  form.append("selfie", selfie, selfie.type === "image/png" ? "selfie.png" : "selfie.jpg");
  return apiClientFetch<IdentitySession>(`${BASE}/verifications/${id}/face-match`, { method: "POST", body: form });
}

/** A new attempt after a declined one. */
export function restartIdentitySession(id: number): Promise<IdentitySession> {
  return apiClientFetch<IdentitySession>(`${BASE}/verifications/${id}/restart`, { method: "POST" });
}

export function createIdentityHandoff(id: number): Promise<{ token: string; expiresInSeconds: number }> {
  return apiClientFetch(`${BASE}/verifications/${id}/resume`, { method: "POST" });
}

export function claimIdentityHandoff(token: string): Promise<IdentitySession> {
  return apiClientFetch<IdentitySession>(`${BASE}/handoff/claim`, { method: "POST", body: JSON.stringify({ token }) });
}

// --- Admin case view (read-only: the face match decides) -------------------------------

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
  { value: "face_match", label: "Face match (document photo + selfie)" },
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

export function eraseIdentityData(partyId: number, reason: string): Promise<{ sessions: number }> {
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
  RETRY_CAPTURE: "Take a new photo of your document and a new selfie",
  EDIT_DETAILS: "Review your details",
  CONTACT_SUPPORT: "Contact support",
  TRY_LATER: "Try again later",
};
