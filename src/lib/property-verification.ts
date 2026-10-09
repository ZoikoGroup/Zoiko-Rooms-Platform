/**
 * ZR-PROPERTY-VERIFY-001 -- property & location verification: types and API
 * client for the host flow (/api/users/property-verifications), the
 * provider-neutral location API (/api/location) and the Trust & Safety
 * review (/api/property-verifications). Every status is server-authoritative;
 * address/map success alone never verifies a property.
 */

import { apiClientFetch } from "@/lib/api-client";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const BASE = "/api/users/property-verifications";

export type PropertyVerificationState =
  | "NOT_STARTED" | "IN_PROGRESS" | "MANUAL_REVIEW" | "ACTION_REQUIRED" | "VERIFIED"
  | "EXPIRING_SOON" | "EXPIRED" | "REJECTED" | "INVALIDATED";
export type PropertyKind = "HOUSE" | "APARTMENT" | "OTHER";
export type EvidenceType =
  | "LAND_REGISTRY_RECORD" | "PROPERTY_TAX_RECORD" | "BUILDING_UNIT_RECORD" | "TITLE_DEED"
  | "MORTGAGE_INSURANCE_STATEMENT" | "UTILITY_BILL";
export type PinStatus = "" | "AUTO_CONFIRMED" | "USER_CONFIRMED" | "ADJUSTED" | "REVIEW_REQUIRED";

export interface StructuredAddress {
  addressLine1: string;
  addressLine2: string;
  subpremise: string;
  locality: string;
  administrativeArea: string;
  postalCode: string;
  countryCode: string;
  formatted?: string;
}

export interface GeoPoint {
  latitude: number;
  longitude: number;
}

export interface PropertyVerificationSession {
  id: number;
  propertyId: number;
  state: PropertyVerificationState;
  version: number;
  countryCode: string;
  entryMode: "" | "SELECTED" | "MANUAL";
  submittedAddress: Partial<StructuredAddress>;
  canonicalAddress: Partial<StructuredAddress>;
  suggestedAddress: Partial<StructuredAddress> | null;
  addressStatus: "" | "VALIDATED" | "PARTIAL" | "UNRESOLVED";
  addressConfirmed: boolean;
  geocodeStatus: "" | "RESOLVED" | "AMBIGUOUS" | "NOT_FOUND";
  /** Plain-language confidence, never a provider score. */
  locationConfidence: "" | "EXACT" | "HIGH" | "MEDIUM" | "LOW";
  originalLocation: GeoPoint | null;
  confirmedLocation: GeoPoint | null;
  pinStatus: PinStatus;
  pinReverseGeocode: string;
  propertyKind: "" | PropertyKind;
  buildingName: string;
  unit: string;
  floor: string;
  possibleDuplicate: boolean;
  /** The host's answer to the possible-duplicate match (Screen 4). */
  duplicateHostAnswer: "" | "NOT_SAME_PROPERTY" | "SAME_PROPERTY";
  duplicateHostNote: string;
  evidence: {
    id: number; evidenceType: EvidenceType; originalFilename: string; contentType: string; createdAt: string;
    /** What could be read from the document -- plain checks, never scores. */
    reading: {
      quality: "GOOD" | "POOR" | "UNREADABLE" | "OCR_UNAVAILABLE";
      addressFound: boolean | null; postalCodeFound: boolean | null; unitFound: boolean | null;
      looksLikeChosenType: boolean | null; outdated: boolean;
    };
  }[];
  sourceCheck: string;
  reasonCodes: string[];
  message: string;
  cta: string;
  submittedAt: string | null;
  verifiedAt: string | null;
  expiresAt: string | null;
  editable: boolean;
  canRestart: boolean;
}

export interface PropertyPolicy {
  countryCode: string;
  countryName: string;
  requiredAddressFields: string[];
  acceptedEvidenceTypes: EvidenceType[];
  unitRequiredFor: PropertyKind[];
  /** Section 17: the country's address field order (snake_case) and local field names. */
  addressFieldOrder: string[];
  addressLabels: Record<string, string>;
  provider: string;
  autocomplete: boolean;
}

export interface AddressSuggestion {
  id: string;
  text: string;
  secondary: string;
}

function versioned(session: { version: number }): HeadersInit {
  return { "If-Match": String(session.version) };
}

export function getPropertyVerificationForProperty(propertyId: number) {
  return apiClientFetch<{ propertyId: number; state: PropertyVerificationState; verification: PropertyVerificationSession | null }>(
    `${BASE}/properties/${propertyId}`,
  );
}

export function getPropertyVerificationForRoom(roomId: number) {
  return apiClientFetch<{ propertyId: number; propertyLabel: string; state: PropertyVerificationState;
    verification: PropertyVerificationSession | null }>(`${BASE}/rooms/${roomId}`);
}

export function getPropertyPolicy(country: string) {
  return apiClientFetch<PropertyPolicy>(`${BASE}/policy?country=${encodeURIComponent(country)}`);
}

export function startPropertyVerification(propertyId: number, idempotencyKey: string) {
  return apiClientFetch<PropertyVerificationSession>(BASE, {
    method: "POST", headers: { "Idempotency-Key": idempotencyKey }, body: JSON.stringify({ propertyId }),
  });
}

/** Only the structured fields -- a pre-filled address can carry display-only
 *  extras (e.g. `formatted`) that the API rejects. */
function addressPayload(a: StructuredAddress) {
  return {
    addressLine1: a.addressLine1 ?? "", addressLine2: a.addressLine2 ?? "", subpremise: a.subpremise ?? "",
    locality: a.locality ?? "", administrativeArea: a.administrativeArea ?? "", postalCode: a.postalCode ?? "",
    countryCode: a.countryCode ?? "",
  };
}

export function setPropertyAddress(session: PropertyVerificationSession, address: StructuredAddress,
                                   entryMode: "SELECTED" | "MANUAL", providerPlaceId = "") {
  return apiClientFetch<PropertyVerificationSession>(`${BASE}/${session.id}/address`, {
    method: "PUT", headers: versioned(session),
    body: JSON.stringify({ address: addressPayload(address), entryMode, providerPlaceId }),
  });
}

export function confirmPropertyAddress(session: PropertyVerificationSession, useSuggestion: boolean) {
  return apiClientFetch<PropertyVerificationSession>(`${BASE}/${session.id}/address/confirm`, {
    method: "POST", headers: versioned(session), body: JSON.stringify({ useSuggestion }),
  });
}

export function confirmPropertyLocation(session: PropertyVerificationSession,
                                        body: { action: "confirm" | "adjust"; latitude?: number; longitude?: number; reason?: string }) {
  return apiClientFetch<PropertyVerificationSession>(`${BASE}/${session.id}/confirm-location`, {
    method: "POST", headers: versioned(session), body: JSON.stringify(body),
  });
}

export function answerPropertyDuplicate(session: PropertyVerificationSession,
                                        answer: "NOT_SAME_PROPERTY" | "SAME_PROPERTY", note: string) {
  return apiClientFetch<PropertyVerificationSession>(`${BASE}/${session.id}/duplicate-answer`, {
    method: "POST", headers: versioned(session), body: JSON.stringify({ answer, note }),
  });
}

export function setPropertyUnit(session: PropertyVerificationSession,
                                body: { propertyKind: PropertyKind; buildingName: string; unit: string; floor: string }) {
  return apiClientFetch<PropertyVerificationSession>(`${BASE}/${session.id}/unit`, {
    method: "PUT", headers: versioned(session), body: JSON.stringify(body),
  });
}

export function uploadPropertyEvidence(session: PropertyVerificationSession, evidenceType: EvidenceType, file: File) {
  const form = new FormData();
  form.append("evidence_type", evidenceType);
  form.append("file", file);
  return apiClientFetch<PropertyVerificationSession>(`${BASE}/${session.id}/evidence`, {
    method: "POST", headers: versioned(session), body: form,
  });
}

export function removePropertyEvidence(session: PropertyVerificationSession, evidenceId: number) {
  return apiClientFetch<PropertyVerificationSession>(`${BASE}/${session.id}/evidence/${evidenceId}`, {
    method: "DELETE", headers: versioned(session),
  });
}

export function submitPropertyVerification(session: PropertyVerificationSession, idempotencyKey: string) {
  return apiClientFetch<PropertyVerificationSession>(`${BASE}/${session.id}/submit`, {
    method: "POST", headers: { ...versioned(session), "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ attested: true }),
  });
}

export function restartPropertyVerification(session: PropertyVerificationSession) {
  return apiClientFetch<PropertyVerificationSession>(`${BASE}/${session.id}/restart`, { method: "POST" });
}

export function propertyEvidenceUrl(verificationId: number, evidenceId: number, admin = false) {
  return admin
    ? `${API_URL}/api/property-verifications/${verificationId}/evidence/${evidenceId}/document`
    : `${API_URL}${BASE}/${verificationId}/evidence/${evidenceId}/document`;
}

// -- location API (server-side proxy; no provider key in the browser) ---------

export function suggestAddresses(query: string, country: string, sessionToken: string) {
  return apiClientFetch<{ available: boolean; suggestions: AddressSuggestion[] }>("/api/location/suggestions", {
    method: "POST", body: JSON.stringify({ query, country, sessionToken }),
  });
}

/** The address at a point -- the host's phone location or a spot tapped on the map. */
export function reverseLocation(point: GeoPoint) {
  return apiClientFetch<{ found: boolean; address?: StructuredAddress }>("/api/location/reverse", {
    method: "POST", body: JSON.stringify(point),
  });
}

/** Resolve a free-form place (e.g. a Plus Code "W8FP+3HR Madhira") to a point. */
export function geocodePlace(query: string, locality: string, countryCode: string) {
  return apiClientFetch<{ geocodeStatus: string; location?: { latitude: number; longitude: number } }>(
    "/api/location/geocode", {
      method: "POST",
      body: JSON.stringify({ address: { addressLine1: query, locality, countryCode } }),
    });
}

export function retrieveAddress(id: string, sessionToken: string) {
  return apiClientFetch<{ address: StructuredAddress; provider: string; providerPlaceId: string;
    location: { latitude: number; longitude: number } | null }>("/api/location/retrieve", {
    method: "POST", body: JSON.stringify({ id, sessionToken }),
  });
}

// -- Trust & Safety ------------------------------------------------------------

export type ReviewDecision = "APPROVE" | "REQUEST_INFO" | "REJECT";

export interface PropertyReviewQueueItem {
  id: number;
  propertyId: number;
  state: PropertyVerificationState;
  countryCode: string;
  reasonCodes: string[];
  possibleDuplicate: boolean;
  awaitingSecondApproval: boolean;
  assignedAdminId: number | null;
  submittedAt: string | null;
  createdAt: string;
}

export interface PinAdjustment {
  at: string;
  latitude: number;
  longitude: number;
  movedMeters: number | null;
  reverseGeocode: string;
  reason: string;
  reviewRequired: boolean;
}

export interface PropertyReviewCase extends PropertyVerificationSession {
  propertyLabel: string;
  jurisdiction: string;
  hostIdentityVerified: boolean;
  provider: string;
  locationPrecision: string;
  pinMovePolicyMeters: number;
  pinMovedMeters: number | null;
  pinAdjustHistory: PinAdjustment[];
  assignedAdminId: number | null;
  assignedAt: string | null;
  addressLocal: string;
  pinAdjustReason: string;
  pinAdjustCount: number;
  duplicateOfPropertyId: number | null;
  awaitingSecondApproval: boolean;
  existenceStatus: string;
  evidenceDetail: {
    id: number; evidenceType: EvidenceType; originalFilename: string; contentType: string; readable: boolean;
    addressMatched: boolean | null; unitMatched: boolean | null; reusedElsewhere: boolean; scanStatus: string; purged: boolean;
    tamperSignal: boolean;
    textSource: "PDF_TEXT" | "OCR" | "NONE"; ocrConfidence: number | null; quality: string;
    postalMatched: boolean | null; ownerNameMatched: boolean | null; documentTypeMatched: boolean | null;
    documentYear: number | null; referenceNumber: string; signals: string[];
  }[];
  history: { eventType: string; occurredAt: string; actorKind: string; previousState: string | null; newState: string | null; reasonCodes: string[] }[];
}

export function listPropertyReviewQueue(state = "MANUAL_REVIEW") {
  return apiClientFetch<PropertyReviewQueueItem[]>(`/api/property-verifications?state=${encodeURIComponent(state)}`);
}

export function getPropertyReviewCase(id: number) {
  return apiClientFetch<PropertyReviewCase>(`/api/property-verifications/${id}/case`);
}

export function getPropertyReviewReasons() {
  return apiClientFetch<Record<ReviewDecision, { code: string; message: string }[]>>("/api/property-verifications/review-reasons");
}

export function reviewPropertyVerification(id: number, decision: ReviewDecision, reasonCode: string, note: string) {
  return apiClientFetch<PropertyVerificationSession>(`/api/property-verifications/${id}/review`, {
    method: "POST", body: JSON.stringify({ decision, reasonCode, note }),
  });
}

/** Section 16: take (or release) a case -- one reviewer holds it at a time. */
export function assignPropertyReviewCase(id: number, release = false) {
  return apiClientFetch<{ id: number; assignedAdminId: number | null }>(`/api/property-verifications/${id}/assign`, {
    method: "POST", body: JSON.stringify({ release }),
  });
}

export function getPropertyVerificationMetrics(days = 30) {
  return apiClientFetch<Record<string, unknown>>(`/api/property-verifications/metrics?days=${days}`);
}

// -- labels --------------------------------------------------------------------

export const propertyStateLabel: Record<PropertyVerificationState, string> = {
  NOT_STARTED: "Property verification required",
  IN_PROGRESS: "Verification in progress",
  MANUAL_REVIEW: "Property verification in review",
  ACTION_REQUIRED: "Action required",
  VERIFIED: "Property Verified",
  EXPIRING_SOON: "Verification expiring soon",
  EXPIRED: "Verification expired",
  REJECTED: "Could not verify",
  INVALIDATED: "Property verification required",
};

export const propertyStateTone: Record<PropertyVerificationState, "success" | "warning" | "danger" | "neutral" | "primary"> = {
  NOT_STARTED: "warning", IN_PROGRESS: "primary", MANUAL_REVIEW: "neutral", ACTION_REQUIRED: "warning",
  VERIFIED: "success", EXPIRING_SOON: "warning", EXPIRED: "danger", REJECTED: "danger", INVALIDATED: "warning",
};

export const propertyStateCta: Partial<Record<PropertyVerificationState, string>> = {
  NOT_STARTED: "Verify property", IN_PROGRESS: "Continue verification", MANUAL_REVIEW: "View status",
  ACTION_REQUIRED: "Fix verification", EXPIRING_SOON: "Renew verification", EXPIRED: "Renew verification",
  REJECTED: "Review reason", INVALIDATED: "Reverify property",
};

export const evidenceTypeLabel: Record<EvidenceType, string> = {
  LAND_REGISTRY_RECORD: "Official land / property registry or cadastral record",
  PROPERTY_TAX_RECORD: "Property tax / assessment record",
  BUILDING_UNIT_RECORD: "Official building / unit record",
  TITLE_DEED: "Deed / title / ownership record",
  MORTGAGE_INSURANCE_STATEMENT: "Mortgage / insurance / property statement (supporting only)",
  UTILITY_BILL: "Utility / service document (supporting only)",
};

export const confidenceLabel: Record<string, string> = { EXACT: "Exact", HIGH: "High", MEDIUM: "Medium", LOW: "Low" };

// -- Admin: map provider health ----------------------------------------------------

export interface LocationApiHealth {
  api: string;
  usedFor?: string;
  ok: boolean;
  reason: string;
  message?: string;
  fix?: string;
}

export interface LocationHealth {
  primary: string;
  google: LocationApiHealth[];
  /** Configured backup providers in use; empty = Google only. */
  fallbacks: string[];
}

/** Which Google Maps Platform APIs the server key can use right now (super admin). */
export function getLocationHealth() {
  return apiClientFetch<LocationHealth>("/api/admin/location/health");
}
