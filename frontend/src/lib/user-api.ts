import { ApiError, apiClientFetch } from "@/lib/api-client";
import {
  Agreement,
  HostedListing,
  IdentityDocumentType,
  IdentityVerificationRecord,
  Obligation,
  Offer,
  Property,
  PublicListing,
  PublicListingsPage,
  PublishEligibility,
  Room,
  SimulatedPayment,
  SubletRequest,
  UserApplication,
  UserOccupancy,
} from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * Typed clients for the USER-facing backend surface. Every path here is an endpoint
 * that already exists on the FastAPI app -- nothing is invented. Auth is the
 * `zoiko_user_token` cookie, sent automatically by `apiClientFetch`.
 */

/** Pulls a readable message out of whatever the API layer threw. */
export function errorMessage(err: unknown, fallback = "Something went wrong. Please try again."): string {
  if (err instanceof ApiError) return err.message || fallback;
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

// --- Identity verification -------------------------------------------------

/** Submits a real uploaded document -- this is a multipart request, not JSON, so
 *  apiClientFetch is told to let the browser set its own multipart boundary. */
export function submitIdentityVerification(payload: {
  documentType: IdentityDocumentType;
  file: File;
  documentNumber?: string;
  customDocumentName?: string;
}): Promise<IdentityVerificationRecord> {
  const form = new FormData();
  form.append("document_type", payload.documentType);
  form.append("document_number", payload.documentNumber ?? "");
  form.append("custom_document_name", payload.customDocumentName ?? "");
  form.append("file", payload.file);
  return apiClientFetch<IdentityVerificationRecord>("/api/users/identity-verifications", {
    method: "POST",
    body: form,
  });
}

export function listIdentityVerifications(): Promise<IdentityVerificationRecord[]> {
  return apiClientFetch<IdentityVerificationRecord[]>("/api/users/identity-verifications");
}

export function getIdentityVerification(verificationId: number): Promise<IdentityVerificationRecord> {
  return apiClientFetch<IdentityVerificationRecord>(`/api/users/identity-verifications/${verificationId}`);
}

/** Opens/downloads the caller's own uploaded document. The backend enforces
 *  ownership by party_id -- this URL 403s for anyone else's verification, so it's
 *  safe to build client-side with nothing but the verification id. */
export function identityDocumentUrl(verificationId: number): string {
  return `${API_URL}/api/users/identity-verifications/${verificationId}/document`;
}

/** True when at least one submitted document has been approved by a super admin. */
export function hasVerifiedIdentity(records: IdentityVerificationRecord[]): boolean {
  return records.some((record) => record.status === "verified");
}

// --- Renting ---------------------------------------------------------------

/** Public catalogue of PUBLISHED listings -- the inventory a user can apply to. */
export interface PublicListingFilters {
  city?: string;
  minPrice?: number;
  maxPrice?: number;
  roomType?: string;
  amenities?: string[];
  limit?: number;
  offset?: number;
}

/** Server-side filtered + paginated search over PUBLISHED listings. Filtering
 *  happens entirely in the backend query -- never fetch everything and filter
 *  client-side. */
export function listPublicListings(filters: PublicListingFilters = {}): Promise<PublicListingsPage> {
  const params = new URLSearchParams();
  if (filters.city) params.set("city", filters.city);
  if (filters.minPrice != null) params.set("min_price", String(filters.minPrice));
  if (filters.maxPrice != null) params.set("max_price", String(filters.maxPrice));
  if (filters.roomType) params.set("room_type", filters.roomType);
  if (filters.amenities?.length) params.set("amenities", filters.amenities.join(","));
  params.set("limit", String(filters.limit ?? 20));
  params.set("offset", String(filters.offset ?? 0));
  return apiClientFetch<PublicListingsPage>(`/api/public/listings?${params.toString()}`);
}

/** Single-listing detail view -- same PUBLISHED-only visibility as the list endpoint. */
export function getPublicListing(listingId: string): Promise<PublicListing> {
  return apiClientFetch<PublicListing>(`/api/public/listings/${listingId}`);
}

export interface RoomAlert {
  id: string;
  city: string;
  minPrice: number | null;
  maxPrice: number | null;
  roomType: string | null;
  isActive: boolean;
}

/** No auth -- reachable from the public find-a-room page before a visitor has
 *  any account. Backend emails a confirmation with an unsubscribe link. */
export function createRoomAlert(payload: {
  email: string;
  city: string;
  minPrice?: number | null;
  maxPrice?: number | null;
  roomType?: string | null;
}): Promise<RoomAlert> {
  return apiClientFetch<RoomAlert>("/api/public/alerts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function submitRentalApplication(payload: {
  listingId: string;
  message?: string;
  desiredMoveIn?: string | null;
}): Promise<UserApplication> {
  return apiClientFetch<UserApplication>("/api/users/rentals/applications", {
    method: "POST",
    body: JSON.stringify({ message: "", desiredMoveIn: null, ...payload }),
  });
}

export function listRentalApplications(): Promise<UserApplication[]> {
  return apiClientFetch<UserApplication[]>("/api/users/rentals/applications");
}

export function getRentalApplication(applicationId: number): Promise<UserApplication> {
  return apiClientFetch<UserApplication>(`/api/users/rentals/applications/${applicationId}`);
}

export function withdrawRentalApplication(applicationId: number): Promise<UserApplication> {
  return apiClientFetch<UserApplication>(`/api/users/rentals/applications/${applicationId}/withdraw`, {
    method: "POST",
  });
}

export function getApplicationAgreement(applicationId: number): Promise<Agreement | null> {
  return apiClientFetch<Agreement | null>(`/api/users/rentals/applications/${applicationId}/agreement`);
}

export function getApplicationOffer(applicationId: number): Promise<Offer | null> {
  return apiClientFetch<Offer | null>(`/api/users/rentals/applications/${applicationId}/offer`);
}

/** Downloads the renter's own copy of the agreement PDF -- raw fetch (not
 *  apiClientFetch) since the response is a binary blob, not JSON. */
export async function downloadApplicationAgreementPdf(applicationId: number, agreementId: number): Promise<void> {
  const res = await fetch(`${API_URL}/api/users/rentals/applications/${applicationId}/agreement/pdf`, {
    credentials: "include",
  });
  if (!res.ok) throw new ApiError(res.status, "Could not download the agreement PDF.");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `agreement-${agreementId}.pdf`;
  link.click();
  URL.revokeObjectURL(url);
}

export function signApplicationAgreement(applicationId: number): Promise<Agreement> {
  return apiClientFetch<Agreement>(`/api/users/rentals/applications/${applicationId}/agreement/sign`, {
    method: "POST",
  });
}

export function listOccupancies(): Promise<UserOccupancy[]> {
  return apiClientFetch<UserOccupancy[]>("/api/users/rentals/occupancies");
}

export function getOccupancy(occupancyId: number): Promise<UserOccupancy> {
  return apiClientFetch<UserOccupancy>(`/api/users/rentals/occupancies/${occupancyId}`);
}

export function requestMoveOut(occupancyId: number, desiredMoveOutDate: string): Promise<UserOccupancy> {
  return apiClientFetch<UserOccupancy>(`/api/users/rentals/occupancies/${occupancyId}/request-move-out`, {
    method: "POST",
    body: JSON.stringify({ desiredMoveOutDate }),
  });
}

export function submitSubletRequest(
  occupancyId: number,
  payload: { proposedRenterPartyId: number; authorityEvidenceRef?: string }
): Promise<SubletRequest> {
  return apiClientFetch<SubletRequest>(`/api/users/rentals/occupancies/${occupancyId}/sublet-request`, {
    method: "POST",
    // The backend rejects the request unless occupancyId in the body matches the path.
    body: JSON.stringify({ occupancyId, authorityEvidenceRef: "", ...payload }),
  });
}

export function listSubletRequests(): Promise<SubletRequest[]> {
  return apiClientFetch<SubletRequest[]>("/api/users/rentals/sublet-requests");
}

export function submitListingReview(payload: { listingId: string; rating: number; comment?: string }) {
  return apiClientFetch("/api/users/rentals/reviews", {
    method: "POST",
    body: JSON.stringify({ listingId: payload.listingId, rating: payload.rating, comment: payload.comment ?? "" }),
  });
}

// --- Hosting ---------------------------------------------------------------

export function listHostedProperties(): Promise<Property[]> {
  return apiClientFetch<Property[]>("/api/users/hosting/properties");
}

export function createHostedProperty(payload: { address: string; city: string }): Promise<Property> {
  return apiClientFetch<Property>("/api/users/hosting/properties", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateHostedProperty(
  propertyId: number,
  payload: { address: string; city: string }
): Promise<Property> {
  return apiClientFetch<Property>(`/api/users/hosting/properties/${propertyId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function listHostedRooms(propertyId: number): Promise<Room[]> {
  return apiClientFetch<Room[]>(`/api/users/hosting/properties/${propertyId}/rooms`);
}

export function createHostedRoom(
  propertyId: number,
  payload: { size: number; hasEnsuite: boolean }
): Promise<Room> {
  return apiClientFetch<Room>(`/api/users/hosting/properties/${propertyId}/rooms`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateHostedRoom(
  propertyId: number,
  roomId: number,
  payload: { size: number; hasEnsuite: boolean }
): Promise<Room> {
  return apiClientFetch<Room>(`/api/users/hosting/properties/${propertyId}/rooms/${roomId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export interface HostedListingInput {
  name: string;
  roomType: string;
  city: string;
  location: string;
  pricePerNight: number;
  currency: string;
  guests: number;
  bedrooms: number;
  bathrooms: number;
  size: number;
  description: string;
  amenities: string[];
  images: string[];
  minStayNights: number;
  roomId: number;
  contactName: string;
  contactPhone: string;
  contactEmail: string;
}

export function listHostedListings(): Promise<HostedListing[]> {
  return apiClientFetch<HostedListing[]>("/api/users/hosting/listings");
}

export function createHostedListing(payload: HostedListingInput): Promise<HostedListing> {
  return apiClientFetch<HostedListing>("/api/users/hosting/listings", {
    method: "POST",
    // Whole-home / nightly inventory is rejected platform-wide, so propertyType is fixed.
    body: JSON.stringify({ propertyType: "private_room", tags: [], featured: false, ...payload }),
  });
}

export function updateHostedListing(
  listingId: string,
  payload: Partial<HostedListingInput>
): Promise<HostedListing> {
  return apiClientFetch<HostedListing>(`/api/users/hosting/listings/${listingId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function getHostedListingPublishEligibility(listingId: string): Promise<PublishEligibility> {
  return apiClientFetch<PublishEligibility>(`/api/users/hosting/listings/${listingId}/publish-eligibility`);
}

/** Publishing itself is always an explicit admin/super-admin decision -- a host can
 *  only ask for review. */
export function submitHostedListingForReview(listingId: string): Promise<HostedListing> {
  return apiClientFetch<HostedListing>(`/api/users/hosting/listings/${listingId}/submit-for-review`, {
    method: "POST",
  });
}

// --- Payments --------------------------------------------------------------

export function listUserPayments(): Promise<SimulatedPayment[]> {
  return apiClientFetch<SimulatedPayment[]>("/api/users/payments");
}

export function listUserObligations(): Promise<Obligation[]> {
  return apiClientFetch<Obligation[]>("/api/users/payments/obligations");
}

export function payUserObligation(obligationId: number): Promise<SimulatedPayment> {
  return apiClientFetch<SimulatedPayment>(`/api/users/payments/obligations/${obligationId}/pay`, {
    method: "POST",
  });
}

