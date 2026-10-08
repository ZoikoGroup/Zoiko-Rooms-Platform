import { ApiError } from "@/lib/api-client";

/**
 * REST client for the ZR-AI-SEARCH-001 external search protocol (Phase 0).
 * Calls the thin /api/users/external-search/* and /api/admin/external-search/*
 * endpoints, which share the same deterministic orchestrator/outreach services
 * as the chat tools. Backend payloads are snake_case; this module maps them to
 * camelCase types for the UI.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type ExternalSearchState =
  | "INTERNAL_VERIFIED"
  | "INTERNAL_ZERO"
  | "EXTERNAL_FALLBACK_ELIGIBLE"
  | "EXTERNAL_QUEUED"
  | "EXTERNAL_DISCOVERED"
  | "NEEDS_PROVIDER_CONSENT"
  | "REQUIRES_UNLOCK"
  | "BLOCKED";

export interface ExternalSearchRequest {
  q?: string;
  city?: string;
  country?: string;
  minPrice?: number;
  maxPrice?: number;
}

// The source identity is never sent to the client (ZR-AI-SEARCH-001 §8).
/**
 * §7.4 minimal lead summary: the only renter details shareable before the
 * provider accepts. Must match LEAD_SUMMARY_FIELDS in the backend schema.
 */
export const LEAD_SUMMARY_FIELDS = [
  { key: "desired_area", label: "Desired area" },
  { key: "move_in_window", label: "Move-in date or window" },
  { key: "budget_band", label: "Budget band" },
  { key: "room_type", label: "Room type" },
  { key: "occupants", label: "Number of occupants" },
  { key: "requirements", label: "Objective requirements (e.g. furnished)" },
] as const;

export const DEFAULT_LEAD_SUMMARY: string[] = ["desired_area", "move_in_window", "budget_band", "room_type"];

export interface ExternalCard {
  canonicalId: string | null;
  title: string;
  locationCity: string | null;
  locationRegion: string | null;
  locationCountry: string | null;
  rentMonthly: number | null;
  currency: string | null;
  deposit: number | null;
  availabilityText: string | null;
  roomType: string | null;
  occupancy: string | null;
  amenities: string[];
  distanceKm: number | null;
  qualityScore: number;
  imagesPresent: boolean;
  lastSeenAt: string | null;
  verificationStatus: string;
  isUnlocked: boolean;
  opportunityId: number | null;
}

export interface InternalRoomSummary {
  id: string;
  name: string;
  city: string;
  roomType: string | null;
  propertyType: string | null;
  state: string;
  pricePerMonth: number;
  currency: string | null;
  country?: string | null;
  /** Shown as-is; a published (or paid) listing is never implied verified. */
  verificationStatus: "INTERNAL_VERIFIED" | "INTERNAL_UNVERIFIED";
}

export interface ExternalSearchResponse {
  state: ExternalSearchState;
  internalMatches: number;
  internalResults: InternalRoomSummary[];
  externalMatches: ExternalCard[];
  fallbackTriggered: boolean;
  consentRequired: boolean;
  disclosureText: string;
  guardrailNotes: string[];
  auditId: string | null;
}

export interface ExternalContactResult {
  outreachId: number;
  status: string;
  channel: string;
}

export interface MyExternalOpportunity {
  opportunityId: number;
  externalOpportunityId: string;
  status: string;
  verificationStatus: string;
  approxLocation: string | null;
  outreachStatus: string;
  providerResponse: string;
  requestedAt: string | null;
  masked: boolean;
}

export interface OutreachQueueItem {
  outreachId: number;
  opportunityId: number;
  externalOpportunityId: string;
  status: string;
  verificationStatus: string;
  approxLocation: string | null;
  channel: string;
  outreachStatus: string;
  requestedAt: string | null;
  requestedByUserId: number;
  requestedByEmail: string | null;
}

type Json = Record<string, unknown>;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      credentials: "include",
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(0, "Server disconnected. Please make sure the backend is running and try again.");
  }
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as Json;
    throw new ApiError(res.status, (body.detail as string) ?? `Request to ${path} failed with ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

function toExternalCard(raw: Json): ExternalCard {
  return {
    canonicalId: (raw.canonical_id as string | null) ?? null,
    title: String(raw.title ?? "[External listing]"),
    locationCity: (raw.location_city as string | null) ?? null,
    locationRegion: (raw.location_region as string | null) ?? null,
    locationCountry: (raw.location_country as string | null) ?? null,
    rentMonthly: (raw.rent_monthly as number | null) ?? null,
    currency: (raw.currency as string | null) ?? null,
    deposit: (raw.deposit as number | null) ?? null,
    availabilityText: (raw.availability_text as string | null) ?? null,
    roomType: (raw.room_type as string | null) ?? null,
    occupancy: (raw.occupancy as string | null) ?? null,
    amenities: Array.isArray(raw.amenities) ? (raw.amenities as string[]) : [],
    distanceKm: (raw.distance_km as number | null) ?? null,
    qualityScore: Number(raw.quality_score ?? 0),
    imagesPresent: Boolean(raw.images_present),
    lastSeenAt: (raw.last_seen_at as string | null) ?? null,
    verificationStatus: String(raw.verification_status ?? "unverified"),
    isUnlocked: Boolean(raw.is_unlocked),
    opportunityId: raw.opportunity_id != null ? Number(raw.opportunity_id) : null,
  };
}

function toInternalRoomSummary(raw: Json): InternalRoomSummary {
  return {
    id: String(raw.id ?? ""),
    name: String(raw.name ?? ""),
    city: String(raw.city ?? ""),
    roomType: (raw.roomType as string | null) ?? null,
    propertyType: (raw.propertyType as string | null) ?? null,
    state: String(raw.state ?? ""),
    pricePerMonth: Number(raw.pricePerMonth ?? 0),
    currency: (raw.currency as string | null) ?? null,
    country: (raw.country as string | null) ?? undefined,
    verificationStatus:
      raw.verificationStatus === "INTERNAL_VERIFIED" ? "INTERNAL_VERIFIED" : "INTERNAL_UNVERIFIED",
  };
}

function toMyOpportunity(raw: Json): MyExternalOpportunity {
  return {
    opportunityId: Number(raw.opportunity_id ?? 0),
    externalOpportunityId: String(raw.external_opportunity_id ?? ""),
    status: String(raw.status ?? ""),
    verificationStatus: String(raw.verification_status ?? ""),
    approxLocation: (raw.approx_location as string | null) ?? null,
    outreachStatus: String(raw.outreach_status ?? ""),
    providerResponse: String(raw.provider_response ?? "NO_RESPONSE"),
    requestedAt: (raw.requested_at as string | null) ?? null,
    masked: Boolean(raw.masked),
  };
}

function toOutreachQueueItem(raw: Json): OutreachQueueItem {
  return {
    outreachId: Number(raw.outreach_id ?? 0),
    opportunityId: Number(raw.opportunity_id ?? 0),
    externalOpportunityId: String(raw.external_opportunity_id ?? ""),
    status: String(raw.status ?? ""),
    verificationStatus: String(raw.verification_status ?? ""),
    approxLocation: (raw.approx_location as string | null) ?? null,
    channel: String(raw.channel ?? ""),
    outreachStatus: String(raw.outreach_status ?? ""),
    requestedAt: (raw.requested_at as string | null) ?? null,
    requestedByUserId: Number(raw.requested_by_user_id ?? 0),
    requestedByEmail: (raw.requested_by_email as string | null) ?? null,
  };
}

/** Internal-first room search with a controlled external fallback. */
export async function searchRooms(
  requestBody: ExternalSearchRequest,
  signal?: AbortSignal
): Promise<ExternalSearchResponse> {
  const raw = await request<Json>("/api/users/external-search/search", {
    method: "POST",
    body: JSON.stringify(requestBody),
    signal,
  });
  return {
    state: String(raw.state ?? "BLOCKED") as ExternalSearchState,
    internalMatches: Number(raw.internal_matches ?? 0),
    internalResults: Array.isArray(raw.internal_results)
      ? raw.internal_results.map((r) => toInternalRoomSummary(r as Json))
      : [],
    externalMatches: Array.isArray(raw.external_matches)
      ? raw.external_matches.map((c) => toExternalCard(c as Json))
      : [],
    fallbackTriggered: Boolean(raw.fallback_triggered),
    consentRequired: Boolean(raw.consent_required),
    disclosureText: String(raw.disclosure_text ?? "External results are not verified by Zoiko Rooms."),
    guardrailNotes: Array.isArray(raw.guardrail_notes) ? (raw.guardrail_notes as string[]) : [],
    auditId: (raw.audit_id as string | null) ?? null,
  };
}

/** Ask Zoiko Rooms to contact the provider for an external opportunity. */
export async function requestProviderContact(
  opportunityId: number,
  payload: { message: string; consentFields: string[] },
  signal?: AbortSignal
): Promise<ExternalContactResult> {
  const raw = await request<Json>(`/api/users/external-search/opportunities/${opportunityId}/contact`, {
    method: "POST",
    body: JSON.stringify({ message: payload.message, consent_fields: payload.consentFields }),
    signal,
  });
  return {
    outreachId: Number(raw.outreach_id ?? 0),
    status: String(raw.status ?? ""),
    channel: String(raw.channel ?? ""),
  };
}

/** List the current user's external discovery/contact requests (masked). */
export async function listMyExternalOpportunities(signal?: AbortSignal): Promise<MyExternalOpportunity[]> {
  const rows = await request<Json[]>("/api/users/external-search/opportunities", { signal });
  return rows.map((r) => toMyOpportunity(r as Json));
}

/** Admin: pending provider outreach queue. */
export async function listExternalOutreachQueue(limit = 50, signal?: AbortSignal): Promise<OutreachQueueItem[]> {
  const rows = await request<Json[]>(`/api/admin/external-search/outreach?limit=${limit}`, { signal });
  return rows.map((r) => toOutreachQueueItem(r as Json));
}