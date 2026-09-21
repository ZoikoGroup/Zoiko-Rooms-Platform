import { ApiError, apiClientFetch } from "@/lib/api-client";
import {
  Agreement,
  Application,
  AutopayMandate,
  BookingChangeRequest,
  ConditionRating,
  ConditionReportItem,
  ConditionReportType,
  DisclosureRequirement,
  DisputeCaseCreate,
  DisputeCaseExportRead,
  DisputeCaseRead,
  DisputeClaimCreate,
  DisputeClaimRead,
  DisputeDeadlineRead,
  DisputeCaseMessageRead,
  DisputeEvidenceRead,
  DisputeExternalProceedingRead,
  DisputePartyRead,
  DisputeSettlementRead,
  DisputeSettlementRespondAction,
  HandoverEvent,
  HostedListing,
  IdentityDocumentType,
  IdentityVerificationRecord,
  Offer,
  PaymentPreview,
  PreMoveInCancellationResult,
  Property,
  PublicListing,
  PublicListingsPage,
  PublishEligibility,
  RefundEntitlement,
  RenterVerificationStatus,
  Room,
  SimulatedPayment,
  SubletArrangementType,
  SubletRenterLookup,
  SubletRequest,
  TerminationCase,
  TerminationCasePreview,
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

/** Section 9 gap: the move-out mirror of confirmHandoverReceipt above --
 *  previously a naturally-expiring tenancy had no renter-notice step. */
export function giveMoveOutNotice(occupancyId: number, notes: string): Promise<HandoverEvent> {
  return apiClientFetch<HandoverEvent>(`/api/users/rentals/occupancies/${occupancyId}/move-out/notice`, {
    method: "POST",
    body: JSON.stringify({ notes }),
  });
}

export function confirmMoveOutReady(occupancyId: number, notes: string): Promise<HandoverEvent> {
  return apiClientFetch<HandoverEvent>(`/api/users/rentals/occupancies/${occupancyId}/move-out/ready`, {
    method: "POST",
    body: JSON.stringify({ notes }),
  });
}

/** Section 9 gap: move-in/move-out condition report (photos + notes). */
export function listOwnConditionReport(occupancyId: number, reportType?: ConditionReportType): Promise<ConditionReportItem[]> {
  const query = reportType ? `?report_type=${reportType}` : "";
  return apiClientFetch<ConditionReportItem[]>(`/api/users/rentals/occupancies/${occupancyId}/condition-report${query}`);
}

export function addOwnConditionReportItem(
  occupancyId: number,
  payload: { reportType: ConditionReportType; area?: string; conditionRating?: ConditionRating; notes?: string; file?: File | null },
): Promise<ConditionReportItem> {
  const form = new FormData();
  form.set("report_type", payload.reportType);
  if (payload.area) form.set("area", payload.area);
  if (payload.conditionRating) form.set("condition_rating", payload.conditionRating);
  if (payload.notes) form.set("notes", payload.notes);
  if (payload.file) form.set("file", payload.file);
  return apiClientFetch<ConditionReportItem>(`/api/users/rentals/occupancies/${occupancyId}/condition-report`, {
    method: "POST",
    body: form,
  });
}

export function lookupSubletRenter(email: string): Promise<SubletRenterLookup> {
  return apiClientFetch<SubletRenterLookup>(`/api/users/rentals/sublet-lookup?email=${encodeURIComponent(email)}`);
}

/** ZR-SUB-003 Section 8 sublet.uiTerm -- the jurisdiction-resolved word for
 *  "sublet" (e.g. "sublease" elsewhere), fetched before the create wizard
 *  renders. Every jurisdiction defaults to "sublet" today, so this is real,
 *  wired infrastructure with no visible effect yet -- see its own schema
 *  docstring. */
export function getOwnSubletTerminology(occupancyId: number): Promise<{ uiTerm: string }> {
  return apiClientFetch<{ uiTerm: string }>(`/api/users/rentals/occupancies/${occupancyId}/sublet-terminology`);
}

/** Section 7 gap: cancel a signed-but-not-moved-in booking, with a real
 *  refund (minus any cancellation fee outside the free-cancellation
 *  window) -- not just a status flip. */
export function cancelOwnBookingBeforeMoveIn(
  occupancyId: number, reason: string
): Promise<PreMoveInCancellationResult> {
  return apiClientFetch<PreMoveInCancellationResult>(`/api/users/rentals/occupancies/${occupancyId}/cancel-before-move-in`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

// -- Section 6 gap: termination cases + refund entitlements -- previously
// had a complete backend and zero renter-facing UI anywhere.

export function previewOwnTermination(
  occupancyId: number, payload: { causeCode: string; proposedEffectiveDate?: string; evidenceRefs?: string[] }
): Promise<TerminationCasePreview> {
  return apiClientFetch<TerminationCasePreview>(`/api/users/rentals/occupancies/${occupancyId}/termination-cases/preview`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function requestOwnTermination(
  occupancyId: number, payload: { causeCode: string; notes?: string; proposedEffectiveDate?: string; evidenceRefs?: string[] }
): Promise<TerminationCase> {
  return apiClientFetch<TerminationCase>(`/api/users/rentals/occupancies/${occupancyId}/termination-cases`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listOwnTerminationCases(occupancyId: number): Promise<TerminationCase[]> {
  return apiClientFetch<TerminationCase[]>(`/api/users/rentals/occupancies/${occupancyId}/termination-cases`);
}

export function withdrawOwnTerminationCase(caseId: number): Promise<TerminationCase> {
  return apiClientFetch<TerminationCase>(`/api/users/rentals/termination-cases/${caseId}/withdraw`, { method: "POST" });
}

export function acceptOwnMutualSurrender(caseId: number): Promise<TerminationCase> {
  return apiClientFetch<TerminationCase>(`/api/users/rentals/termination-cases/${caseId}/accept-surrender`, { method: "POST" });
}

export function declineOwnMutualSurrender(caseId: number): Promise<TerminationCase> {
  return apiClientFetch<TerminationCase>(`/api/users/rentals/termination-cases/${caseId}/decline-surrender`, { method: "POST" });
}

export function getOwnRefundEntitlement(caseId: number): Promise<RefundEntitlement> {
  return apiClientFetch<RefundEntitlement>(`/api/users/rentals/termination-cases/${caseId}/refund-entitlement`);
}

export function submitSubletRequest(
  occupancyId: number,
  payload: {
    proposedRenterPartyId: number;
    authorityEvidenceRef?: string;
    arrangementType?: SubletArrangementType;
    proposedMonthlyRent?: number;
    reason?: string;
    proposedStartDate?: string;
    proposedEndDate?: string;
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

/** Answers the Host's "more information" request on the tenant's own sublet request. */
export function respondToSubletInfoRequest(subletRequestId: number, notes: string): Promise<SubletRequest> {
  return apiClientFetch<SubletRequest>(`/api/users/rentals/sublet-requests/${subletRequestId}/respond`, {
    method: "POST",
    body: JSON.stringify({ notes }),
  });
}

/** Tenant withdraws their own sublet request before a decision is made. */
export function withdrawSubletRequest(subletRequestId: number): Promise<SubletRequest> {
  return apiClientFetch<SubletRequest>(`/api/users/rentals/sublet-requests/${subletRequestId}/withdraw`, { method: "POST" });
}

/** The tenant's own downloadable decision record (ZR-SUB-003 Wireframe J) --
 *  a direct link, same pattern as identityDocumentUrl above. 409s until the
 *  request reaches a completed state (approved/rejected/withdrawn). */
export function tenantSubletDecisionRecordUrl(subletRequestId: number): string {
  return `${API_URL}/api/users/rentals/sublet-requests/${subletRequestId}/record`;
}

/** ZR-ENG-CLR-004 Section 4.10: "All contractual parties must have continuing
 *  access." The renter's own copy of their agreement PDF -- the backend route
 *  already existed with no frontend caller anywhere. */
export function tenantAgreementPdfUrl(agreementId: number): string {
  return `${API_URL}/api/users/rentals/agreements/${agreementId}/pdf`;
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
  payload: { proposedMonthlyRent: number; proposedDepositAmount?: number; reason?: string }
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

// --- Sublet requests (ZR-SUB-003: the Host, not Zoiko Admin, decides) -------

/** Sublet requests routed to any of the host's own party-owned listings. */
export function listHostedSubletRequests(): Promise<SubletRequest[]> {
  return apiClientFetch<SubletRequest[]>("/api/users/hosting/sublet-requests");
}

export function requestHostedSubletMoreInfo(
  subletRequestId: number,
  notes: string,
  payload: { requestedDocumentTypes?: string[]; dueAt?: string | null } = {}
): Promise<SubletRequest> {
  return apiClientFetch<SubletRequest>(`/api/users/hosting/sublet-requests/${subletRequestId}/request-info`, {
    method: "POST",
    body: JSON.stringify({ notes, requestedDocumentTypes: [], dueAt: null, ...payload }),
  });
}

export function approveHostedSubletRequest(
  subletRequestId: number,
  payload: {
    notes?: string; conditions?: string; expiresAt?: string | null; conditionList?: string[];
    authorityConfirmed?: boolean; stepUpPassword?: string;
  } = {}
): Promise<SubletRequest> {
  return apiClientFetch<SubletRequest>(`/api/users/hosting/sublet-requests/${subletRequestId}/approve`, {
    method: "POST",
    body: JSON.stringify({
      notes: "", conditions: "", expiresAt: null, conditionList: [], authorityConfirmed: false, stepUpPassword: "", ...payload,
    }),
  });
}

export function declineHostedSubletRequest(subletRequestId: number, notes = "", declineReasonCode = ""): Promise<SubletRequest> {
  return apiClientFetch<SubletRequest>(`/api/users/hosting/sublet-requests/${subletRequestId}/decline`, {
    method: "POST",
    body: JSON.stringify({ notes, declineReasonCode }),
  });
}

/** The Host's own copy of the same downloadable decision record. */
export function hostSubletDecisionRecordUrl(subletRequestId: number): string {
  return `${API_URL}/api/users/hosting/sublet-requests/${subletRequestId}/record`;
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

/** Section 5 gap: autopay mandates existed only in the admin/backend --
 *  these are the renter's own self-service opt-in/list/revoke calls. */
export function listMyAutopayMandates(): Promise<AutopayMandate[]> {
  return apiClientFetch<AutopayMandate[]>("/api/users/payments/mandates");
}

export function createAutopayMandate(occupancyId: number): Promise<AutopayMandate> {
  return apiClientFetch<AutopayMandate>("/api/users/payments/mandates", {
    method: "POST",
    body: JSON.stringify({ occupancyId }),
  });
}

export function revokeAutopayMandate(mandateId: number): Promise<AutopayMandate> {
  return apiClientFetch<AutopayMandate>(`/api/users/payments/mandates/${mandateId}`, {
    method: "DELETE",
  });
}

// -- Section 10 gap: ZR-ENG-CLR-010 general-purpose Dispute Resolution
// engine -- previously had zero frontend anywhere despite a fully-built
// backend (backend/app/api/routes/disputes.py's renter_router/host_router,
// each ~30 identical endpoints mirrored one for RENTER, one for HOST). This
// factory builds one identical client per side rather than hand-duplicating
// every function twice -- the two backend routers really are byte-for-byte
// mirrors of each other (guest-scoped vs party-scoped), so this isn't a
// premature abstraction, it's just not retyping the same 20 functions twice.
function makeDisputeClient(basePath: string) {
  return {
    openCase(payload: DisputeCaseCreate): Promise<DisputeCaseRead> {
      return apiClientFetch<DisputeCaseRead>(basePath, { method: "POST", body: JSON.stringify(payload) });
    },
    listCases(): Promise<DisputeCaseRead[]> {
      return apiClientFetch<DisputeCaseRead[]>(basePath);
    },
    getCase(caseId: number): Promise<DisputeCaseRead> {
      return apiClientFetch<DisputeCaseRead>(`${basePath}/${caseId}`);
    },
    getCaseExport(caseId: number): Promise<DisputeCaseExportRead> {
      return apiClientFetch<DisputeCaseExportRead>(`${basePath}/${caseId}/export`);
    },
    uploadEvidence(
      caseId: number,
      payload: { file?: File | null; noteText?: string; claimIds?: number[]; capturedAt?: string | null },
    ): Promise<DisputeEvidenceRead> {
      const form = new FormData();
      if (payload.file) form.set("file", payload.file);
      if (payload.noteText) form.set("note_text", payload.noteText);
      for (const id of payload.claimIds ?? []) form.append("claim_ids", String(id));
      if (payload.capturedAt) form.set("captured_at", payload.capturedAt);
      return apiClientFetch<DisputeEvidenceRead>(`${basePath}/${caseId}/evidence`, { method: "POST", body: form });
    },
    listEvidence(caseId: number): Promise<DisputeEvidenceRead[]> {
      return apiClientFetch<DisputeEvidenceRead[]>(`${basePath}/${caseId}/evidence`);
    },
    evidenceFileUrl(caseId: number, evidenceId: number): string {
      return `${API_URL}${basePath}/${caseId}/evidence/${evidenceId}/file`;
    },
    requestEvidenceDeletion(caseId: number, evidenceId: number): Promise<DisputeEvidenceRead> {
      return apiClientFetch<DisputeEvidenceRead>(`${basePath}/${caseId}/evidence/${evidenceId}/delete`, { method: "POST" });
    },
    listExternalProceedings(caseId: number): Promise<DisputeExternalProceedingRead[]> {
      return apiClientFetch<DisputeExternalProceedingRead[]>(`${basePath}/${caseId}/external-proceedings`);
    },
    proposeSettlement(
      caseId: number,
      payload: { claimIds: number[]; termsText: string; amount?: number | null; currency?: string | null; expiresAt?: string | null; acknowledgesNoNonwaivableWaiver?: boolean },
    ): Promise<DisputeSettlementRead> {
      return apiClientFetch<DisputeSettlementRead>(`${basePath}/${caseId}/settlements`, { method: "POST", body: JSON.stringify(payload) });
    },
    listSettlements(caseId: number): Promise<DisputeSettlementRead[]> {
      return apiClientFetch<DisputeSettlementRead[]>(`${basePath}/${caseId}/settlements`);
    },
    listDeadlines(caseId: number): Promise<DisputeDeadlineRead[]> {
      return apiClientFetch<DisputeDeadlineRead[]>(`${basePath}/${caseId}/deadlines`);
    },
    postMessage(caseId: number, body: string): Promise<DisputeCaseMessageRead> {
      return apiClientFetch<DisputeCaseMessageRead>(`${basePath}/${caseId}/messages`, { method: "POST", body: JSON.stringify({ body }) });
    },
    listMessages(caseId: number): Promise<DisputeCaseMessageRead[]> {
      return apiClientFetch<DisputeCaseMessageRead[]>(`${basePath}/${caseId}/messages`);
    },
    listParties(caseId: number): Promise<DisputePartyRead[]> {
      return apiClientFetch<DisputePartyRead[]>(`${basePath}/${caseId}/parties`);
    },
    respondSettlement(
      caseId: number, settlementId: number,
      payload: { action: DisputeSettlementRespondAction; counterTermsText?: string | null; counterAmount?: number | null; counterCurrency?: string | null; counterExpiresAt?: string | null },
    ): Promise<DisputeSettlementRead> {
      return apiClientFetch<DisputeSettlementRead>(`${basePath}/${caseId}/settlements/${settlementId}/respond`, { method: "POST", body: JSON.stringify(payload) });
    },
    voidSettlement(caseId: number, settlementId: number): Promise<DisputeSettlementRead> {
      return apiClientFetch<DisputeSettlementRead>(`${basePath}/${caseId}/settlements/${settlementId}/void`, { method: "POST" });
    },
    addClaim(caseId: number, payload: DisputeClaimCreate): Promise<DisputeClaimRead> {
      return apiClientFetch<DisputeClaimRead>(`${basePath}/${caseId}/claims`, { method: "POST", body: JSON.stringify(payload) });
    },
    requestClaimReview(caseId: number, claimId: number, reason: string): Promise<DisputeClaimRead> {
      return apiClientFetch<DisputeClaimRead>(`${basePath}/${caseId}/claims/${claimId}/request-review`, { method: "POST", body: JSON.stringify({ reason }) });
    },
  };
}

export const renterDisputes = makeDisputeClient("/api/users/rentals/disputes");
export const hostDisputes = makeDisputeClient("/api/users/hosting/disputes");

