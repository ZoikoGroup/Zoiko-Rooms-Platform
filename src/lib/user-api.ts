import { ApiError, apiClientFetch } from "@/lib/api-client";
import {
  Agreement,
  Application,
  BookingChangeRequest,
  DisclosureRequirement,
  EvidenceArtifact,
  HandoverEvent,
  HostedListing,
  IdentityDocumentType,
  IdentityVerificationRecord,
  ListingFeeCheckoutSession,
  ListingFeePayment,
  ListingFeeQuote,
  ListingFeeRefund,
  Offer,
  PaymentPreview,
  Property,
  PublicListing,
  PublicListingsPage,
  PublishEligibility,
  RentalPaymentCorrection,
  RentalPaymentDiscrepancyReason,
  RentalPaymentDispute,
  RentalPaymentInstruction,
  RentalPaymentMethodCategory,
  RentalPaymentObligation,
  RenterVerificationStatus,
  Room,
  SimulatedPayment,
  SubletArrangementType,
  SubletRenterLookup,
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

/** ZR-ENG-CLR-012 Section 19: the renter's own identity + occupancy-eligibility
 *  status summary, scoped server-side to their own party. */
export function getMyVerificationStatus(): Promise<RenterVerificationStatus> {
  return apiClientFetch<RenterVerificationStatus>("/api/users/verification-status");
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

export function getOwnOffer(applicationId: number): Promise<Offer> {
  return apiClientFetch<Offer>(`/api/users/rentals/applications/${applicationId}/offer`);
}

/** overrideReason: only needed when the occupant-overlap check comes back
 *  BLOCK (ZR-ENG-CLR-001 Rule 6/Section 9) -- the renter self-declares why
 *  they still want to proceed, captured on the offer for later admin
 *  review. Leave blank for a normal accept. */
export function acceptOwnOffer(offerId: number, overrideReason?: string): Promise<Offer> {
  return apiClientFetch<Offer>(`/api/users/rentals/offers/${offerId}/accept`, {
    method: "POST",
    body: JSON.stringify({ overrideReason: overrideReason ?? "" }),
  });
}

export function declineOwnOffer(offerId: number): Promise<Offer> {
  return apiClientFetch<Offer>(`/api/users/rentals/offers/${offerId}/decline`, { method: "POST" });
}

export function signOwnAgreement(agreementId: number): Promise<Agreement> {
  return apiClientFetch<Agreement>(`/api/users/rentals/agreements/${agreementId}/sign`, { method: "POST" });
}

export function listOwnAgreementDisclosures(agreementId: number): Promise<DisclosureRequirement[]> {
  return apiClientFetch<DisclosureRequirement[]>(`/api/users/rentals/agreements/${agreementId}/disclosures`);
}

export function acknowledgeOwnDisclosure(agreementId: number, disclosureId: number): Promise<DisclosureRequirement> {
  return apiClientFetch<DisclosureRequirement>(
    `/api/users/rentals/agreements/${agreementId}/disclosures/${disclosureId}/acknowledge`, { method: "POST" },
  );
}

export function listOccupancies(): Promise<UserOccupancy[]> {
  return apiClientFetch<UserOccupancy[]>("/api/users/rentals/occupancies");
}

export function getOccupancy(occupancyId: number): Promise<UserOccupancy> {
  return apiClientFetch<UserOccupancy>(`/api/users/rentals/occupancies/${occupancyId}`);
}

export function confirmHandoverReceipt(occupancyId: number): Promise<HandoverEvent> {
  return apiClientFetch<HandoverEvent>(`/api/users/rentals/occupancies/${occupancyId}/handover/receipt`, {
    method: "POST",
    body: JSON.stringify({ evidenceRef: "", notes: "" }),
  });
}

export function lookupSubletRenter(email: string): Promise<SubletRenterLookup> {
  return apiClientFetch<SubletRenterLookup>(`/api/users/rentals/sublet-lookup?email=${encodeURIComponent(email)}`);
}

export function submitSubletRequest(
  occupancyId: number,
  payload: {
    proposedRenterPartyId: number;
    authorityEvidenceRef?: string;
    arrangementType?: SubletArrangementType;
    proposedMonthlyRent?: number;
  }
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

// --- Booking change requests (ZR-ENG-CLR-008 Section 8 MVP) ---------------

export function submitDateChangeRequest(
  agreementId: number,
  payload: { proposedStartDate: string; reason?: string }
): Promise<BookingChangeRequest> {
  return apiClientFetch<BookingChangeRequest>(`/api/users/rentals/agreements/${agreementId}/change-requests`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function submitTermShiftRequest(
  agreementId: number,
  payload: { proposedStartDate: string; newTermMonths: number; reason?: string }
): Promise<BookingChangeRequest> {
  return apiClientFetch<BookingChangeRequest>(`/api/users/rentals/agreements/${agreementId}/term-shift-requests`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function submitExtensionRequest(
  agreementId: number,
  payload: { additionalTermMonths: number; reason?: string }
): Promise<BookingChangeRequest> {
  return apiClientFetch<BookingChangeRequest>(`/api/users/rentals/agreements/${agreementId}/extension-requests`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function submitShorteningRequest(
  agreementId: number,
  payload: { reducedTermMonths: number; reason?: string }
): Promise<BookingChangeRequest> {
  return apiClientFetch<BookingChangeRequest>(`/api/users/rentals/agreements/${agreementId}/shortening-requests`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function submitPremisesChangeRequest(
  agreementId: number,
  payload: { targetListingId: string; reason?: string }
): Promise<BookingChangeRequest> {
  return apiClientFetch<BookingChangeRequest>(`/api/users/rentals/agreements/${agreementId}/premises-change-requests`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function submitFinancialChangeRequest(
  agreementId: number,
  payload: { proposedMonthlyRent: number; reason?: string }
): Promise<BookingChangeRequest> {
  return apiClientFetch<BookingChangeRequest>(`/api/users/rentals/agreements/${agreementId}/financial-change-requests`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listMyChangeRequests(): Promise<BookingChangeRequest[]> {
  return apiClientFetch<BookingChangeRequest[]>("/api/users/rentals/change-requests");
}

export function withdrawChangeRequest(bcrId: number): Promise<BookingChangeRequest> {
  return apiClientFetch<BookingChangeRequest>(`/api/users/rentals/change-requests/${bcrId}/withdraw`, {
    method: "POST",
  });
}

export function submitDepositChangeRequest(
  agreementId: number,
  payload: { proposedDepositAmount: number; reason?: string }
): Promise<BookingChangeRequest> {
  return apiClientFetch<BookingChangeRequest>(`/api/users/rentals/agreements/${agreementId}/deposit-change-requests`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function acceptAlternativeChangeTerms(bcrId: number): Promise<BookingChangeRequest> {
  return apiClientFetch<BookingChangeRequest>(`/api/users/rentals/change-requests/${bcrId}/accept-alternative`, {
    method: "POST",
  });
}

export function declineAlternativeChangeTerms(bcrId: number): Promise<BookingChangeRequest> {
  return apiClientFetch<BookingChangeRequest>(`/api/users/rentals/change-requests/${bcrId}/decline-alternative`, {
    method: "POST",
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

// --- Applications to review (ZR-ENG-CLR-011 Section 10) --------------------

/** Applications submitted to any of the host's own party-owned listings. */
export function listHostedApplications(): Promise<Application[]> {
  return apiClientFetch<Application[]>("/api/users/hosting/applications");
}

export function decideHostedApplication(
  applicationId: number,
  payload: { decision: "APPROVED" | "REJECTED"; note?: string; reasonCode?: string }
): Promise<Application> {
  return apiClientFetch<Application>(`/api/users/hosting/applications/${applicationId}/decide`, {
    method: "POST",
    body: JSON.stringify({ reasonCode: "", note: "", ...payload }),
  });
}

// --- Offers and agreements (ZR-ENG-CLR-004 Section 4.3) ---------------------

export function createHostedOffer(applicationId: number): Promise<Offer> {
  return apiClientFetch<Offer>(`/api/users/hosting/applications/${applicationId}/offers`, { method: "POST" });
}

export function getHostedOffer(offerId: number): Promise<Offer> {
  return apiClientFetch<Offer>(`/api/users/hosting/offers/${offerId}`);
}

export function addHostedOfferTerms(
  offerId: number,
  payload: { monthlyRent: number; depositAmount: number; startDate: string; termMonths: number }
): Promise<Offer> {
  return apiClientFetch(`/api/users/hosting/offers/${offerId}/terms`, {
    method: "POST",
    body: JSON.stringify(payload),
  }).then(() => getHostedOffer(offerId));
}

export function sendHostedOffer(offerId: number): Promise<Offer> {
  return apiClientFetch<Offer>(`/api/users/hosting/offers/${offerId}/send`, { method: "POST" });
}

export function createHostedAgreement(offerId: number): Promise<Agreement> {
  return apiClientFetch<Agreement>(`/api/users/hosting/offers/${offerId}/agreement`, { method: "POST" });
}

export function getHostedAgreement(agreementId: number): Promise<Agreement> {
  return apiClientFetch<Agreement>(`/api/users/hosting/agreements/${agreementId}`);
}

export function sendHostedAgreement(agreementId: number): Promise<Agreement> {
  return apiClientFetch<Agreement>(`/api/users/hosting/agreements/${agreementId}/send`, { method: "POST" });
}

export function signHostedAgreement(agreementId: number): Promise<Agreement> {
  return apiClientFetch<Agreement>(`/api/users/hosting/agreements/${agreementId}/sign`, { method: "POST" });
}

export function listHostedAgreementDisclosures(agreementId: number): Promise<DisclosureRequirement[]> {
  return apiClientFetch<DisclosureRequirement[]>(`/api/users/hosting/agreements/${agreementId}/disclosures`);
}

export function deliverHostedDisclosure(agreementId: number, disclosureId: number): Promise<DisclosureRequirement> {
  return apiClientFetch<DisclosureRequirement>(
    `/api/users/hosting/agreements/${agreementId}/disclosures/${disclosureId}/deliver`, { method: "POST" },
  );
}

// --- Payments --------------------------------------------------------------

export function listUserPayments(): Promise<SimulatedPayment[]> {
  return apiClientFetch<SimulatedPayment[]>("/api/users/payments");
}

/** Renter self-service payment for their own rent/deposit obligation --
 *  real PSP dispatch, not an admin manually recording it. */
export function payOwnObligation(obligationId: number, methodClass: string): Promise<SimulatedPayment> {
  return apiClientFetch<SimulatedPayment>(`/api/users/payments/obligations/${obligationId}/pay`, {
    method: "POST",
    body: JSON.stringify({ methodClass }),
  });
}

/** Real, jurisdiction-resolved payment methods for this obligation -- never
 *  hard-code a method list on the frontend (ZR-ENG-CLR-005 AC-12). */
export function getObligationAvailableMethods(obligationId: number): Promise<{ methodClasses: string[] }> {
  return apiClientFetch<{ methodClasses: string[] }>(`/api/users/payments/obligations/${obligationId}/available-methods`);
}

export function getOwnAgreementPaymentPreview(agreementId: number): Promise<PaymentPreview> {
  return apiClientFetch<PaymentPreview>(`/api/users/rentals/agreements/${agreementId}/payment-preview`);
}


// --- Listing Fee (ZR-PAY-002 Section 8) --------------------------------------
// The only payment Zoiko Rooms collects for itself.

export function createListingFeeQuote(listingId: string): Promise<ListingFeeQuote> {
  return apiClientFetch<ListingFeeQuote>(`/api/users/listing-fees/listings/${listingId}/quotes`, { method: "POST" });
}

export function createListingFeeCheckoutSession(payload: {
  quoteId: number;
  idempotencyKey: string;
  billingCountry: string;
}): Promise<ListingFeeCheckoutSession> {
  return apiClientFetch<ListingFeeCheckoutSession>("/api/users/listing-fees/checkout-sessions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** ZR-PAY-002 Section 3.2: 'Listing fee receipts | View.' */
export function listMyListingFeePayments(): Promise<ListingFeePayment[]> {
  return apiClientFetch<ListingFeePayment[]>("/api/users/listing-fees/payments");
}

/** ZR-PAY-002 Section 8.4: the lister's own view of refund status against
 *  their payment -- issuing one stays admin-restricted. */
export function listListingFeeRefundsForPayment(paymentId: number): Promise<ListingFeeRefund[]> {
  return apiClientFetch<ListingFeeRefund[]>(`/api/users/listing-fees/payments/${paymentId}/refunds`);
}

export function getListingFeePayment(paymentId: number): Promise<ListingFeePayment> {
  return apiClientFetch<ListingFeePayment>(`/api/users/listing-fees/payments/${paymentId}`);
}

/** Returns the receipt PDF as a Blob (not JSON) -- only exists once the
 *  payment has SUCCEEDED. Caller is responsible for turning this into a
 *  download (e.g. via URL.createObjectURL). */
export async function downloadListingFeeReceipt(paymentId: number): Promise<Blob> {
  const res = await fetch(`${API_URL}/api/users/listing-fees/payments/${paymentId}/receipt`, { credentials: "include" });
  if (!res.ok) throw new ApiError(res.status, "Could not download the Listing Fee receipt.");
  return res.blob();
}

// --- Rental payments: tenant view (ZR-PAY-002 Section 4) ---------------------
// Evidence/workflow only -- Zoiko Rooms never receives or holds this money.

export function listMyRentalPaymentObligations(obligationType?: RentalPaymentObligation["obligationType"]): Promise<RentalPaymentObligation[]> {
  const query = obligationType ? `?obligationType=${obligationType}` : "";
  return apiClientFetch<RentalPaymentObligation[]>(`/api/users/rental-payments/obligations${query}`);
}

export function getMyRentalPaymentObligation(obligationId: number): Promise<RentalPaymentObligation> {
  return apiClientFetch<RentalPaymentObligation>(`/api/users/rental-payments/obligations/${obligationId}`);
}

export function markRentalPaymentPaid(
  obligationId: number,
  payload: {
    amount: number;
    currency: string;
    declaredDate: string;
    paymentMethodCategory: RentalPaymentMethodCategory;
    externalReference?: string;
  }
): Promise<RentalPaymentObligation> {
  return apiClientFetch<RentalPaymentObligation>(`/api/users/rental-payments/obligations/${obligationId}/mark-paid`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function uploadRentalPaymentEvidence(recordId: number, file: File): Promise<EvidenceArtifact> {
  const form = new FormData();
  form.append("file", file);
  return apiClientFetch<EvidenceArtifact>(`/api/users/rental-payments/records/${recordId}/evidence`, {
    method: "POST",
    body: form,
  });
}

export function reportRentalPaymentDiscrepancyAsTenant(
  recordId: number,
  payload: { reasonCode: RentalPaymentDiscrepancyReason; details?: string }
): Promise<RentalPaymentDispute> {
  return apiClientFetch<RentalPaymentDispute>(`/api/users/rental-payments/records/${recordId}/disputes`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** field's *value* is the backend's own snake_case column name
 *  ("external_reference" / "payment_method_category"), not a camelCase JS
 *  field name -- it's used directly server-side in getattr/setattr. */
export function correctOwnRentalPaymentRecord(
  recordId: number,
  payload: { fieldName: "external_reference" | "payment_method_category"; newValue: string; reason?: string }
): Promise<RentalPaymentCorrection> {
  return apiClientFetch<RentalPaymentCorrection>(`/api/users/rental-payments/records/${recordId}/self-correct`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getMyRentalPaymentInstructions(obligationId: number): Promise<RentalPaymentInstruction> {
  return apiClientFetch<RentalPaymentInstruction>(`/api/users/rental-payments/obligations/${obligationId}/instructions`);
}

export function listRentalPaymentEvidence(recordId: number): Promise<EvidenceArtifact[]> {
  return apiClientFetch<EvidenceArtifact[]>(`/api/users/rental-payments/records/${recordId}/evidence`);
}

export function downloadRentalPaymentEvidence(recordId: number, artifactId: number): Promise<Blob> {
  return fetch(`${API_URL}/api/users/rental-payments/records/${recordId}/evidence/${artifactId}`, {
    credentials: "include",
  }).then((res) => {
    if (!res.ok) throw new ApiError(res.status, "Could not open this evidence file.");
    return res.blob();
  });
}

// --- Rental payments: recipient view (ZR-PAY-002 Section 5/9) ---------------
// The landlord/agent's own side -- confirming receipt, disputing, and
// managing payment instructions. Same evidence/workflow-only boundary.

export function listRecipientRentalPaymentObligations(
  obligationType?: RentalPaymentObligation["obligationType"]
): Promise<RentalPaymentObligation[]> {
  const query = obligationType ? `?obligationType=${obligationType}` : "";
  return apiClientFetch<RentalPaymentObligation[]>(`/api/users/rental-payments/recipient/obligations${query}`);
}

/** amount omitted means 'confirm the full declared amount'. A lesser
 *  amount records a partial confirmation -- ZR-PAY-002 Section 6
 *  PARTIALLY_PAID -- and can never exceed the record's own declared amount. */
export function confirmRentalPaymentReceipt(recordId: number, amount?: number, note?: string): Promise<RentalPaymentObligation> {
  return apiClientFetch<RentalPaymentObligation>(`/api/users/rental-payments/recipient/records/${recordId}/confirm-receipt`, {
    method: "POST",
    body: JSON.stringify({ amount: amount ?? null, note: note ?? "" }),
  });
}

export function reportRentalPaymentDiscrepancyAsRecipient(
  recordId: number,
  payload: { reasonCode: RentalPaymentDiscrepancyReason; details?: string }
): Promise<RentalPaymentDispute> {
  return apiClientFetch<RentalPaymentDispute>(`/api/users/rental-payments/recipient/records/${recordId}/disputes`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateOwnOpenRentalPaymentDispute(
  disputeId: number,
  payload: { reasonCode?: RentalPaymentDiscrepancyReason; details?: string }
): Promise<RentalPaymentDispute> {
  return apiClientFetch<RentalPaymentDispute>(`/api/users/rental-payments/recipient/disputes/${disputeId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function uploadRentalPaymentEvidenceAsRecipient(recordId: number, file: File): Promise<EvidenceArtifact> {
  const form = new FormData();
  form.append("file", file);
  return apiClientFetch<EvidenceArtifact>(`/api/users/rental-payments/recipient/records/${recordId}/evidence`, {
    method: "POST",
    body: form,
  });
}

export function listMyRentalPaymentInstructions(): Promise<RentalPaymentInstruction[]> {
  return apiClientFetch<RentalPaymentInstruction[]>("/api/users/rental-payments/recipient/instructions");
}

export function submitRentalPaymentInstruction(payload: {
  method: RentalPaymentMethodCategory;
  recipientName: string;
  accountIdentifier: string;
  referenceFormat?: string;
  additionalInstructions?: string;
}): Promise<RentalPaymentInstruction> {
  return apiClientFetch<RentalPaymentInstruction>("/api/users/rental-payments/recipient/instructions", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function resendRentalPaymentInstructionCode(instructionId: number): Promise<void> {
  return apiClientFetch<void>(`/api/users/rental-payments/recipient/instructions/${instructionId}/resend-code`, {
    method: "POST",
  });
}

export function confirmRentalPaymentInstruction(instructionId: number, code: string): Promise<RentalPaymentInstruction> {
  return apiClientFetch<RentalPaymentInstruction>(`/api/users/rental-payments/recipient/instructions/${instructionId}/confirm`, {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}
