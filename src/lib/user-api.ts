import { ApiError, apiClientFetch } from "@/lib/api-client";
import {
  Agreement,
  Application,
  AuthorityRecord,
  AuthorityRelationshipType,
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
  EvidenceArtifact,
  ExternalPaymentSession,
  ExternalPaymentSessionCreateResult,
  HandoverEvent,
  HostedListing,
  IdentityDocumentType,
  IdentityVerificationRecord,
  ListingFeeCheckoutSession,
  ListingFeePayment,
  ListingFeeQuote,
  ListingFeeRefund,
  Occupancy,
  Offer,
  PaymentConnection,
  PaymentPreview,
  PaymentRecipientAuthority,
  PaymentRecipientRelationshipType,
  PreMoveInCancellationResult,
  Property,
  PropertyVerification,
  PublicListing,
  PublicListingsPage,
  PublishEligibility,
  RefundEntitlement,
  RentalPaymentCorrection,
  RentalPaymentDiscrepancyReason,
  RentalPaymentDispute,
  RentalPaymentInstruction,
  RentalPaymentMethodCategory,
  RentalPaymentObligation,
  RentalPaymentObligationsPage,
  RentalPaymentProviderAccount,
  RentalPaymentProviderAccountConnectResult,
  RentalPaymentTimelinePage,
  RentalTransactionRecord,
  RentalTransactionTimelineEntry,
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

/** Rental Transaction Record wireframe: a computed, read-only composite over
 *  this occupancy's own Application/Offer/Agreement, payments, handover,
 *  sublet and termination records -- see backend/app/crud/
 *  rental_transaction_record.py. Renter-scoped: 403s for another renter's
 *  occupancy, same as getOccupancy above. */
export function getRentalTransactionRecord(occupancyId: number): Promise<RentalTransactionRecord> {
  return apiClientFetch<RentalTransactionRecord>(`/api/users/rentals/occupancies/${occupancyId}/transaction-record`);
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

// Lister, Property & Authority Verification wireframe: host self-service
// submission of authority-to-list and property evidence for a room they
// own -- separate claims from identity (see VerificationStatusSummary).

export function listHostedRoomAuthorityRecords(roomId: number): Promise<AuthorityRecord[]> {
  return apiClientFetch<AuthorityRecord[]>(`/api/users/hosting/rooms/${roomId}/authority-records`);
}

export function declareHostedAuthorityRecord(
  roomId: number,
  payload: { relationshipType: AuthorityRelationshipType; evidenceRef: string }
): Promise<AuthorityRecord> {
  return apiClientFetch<AuthorityRecord>(`/api/users/hosting/rooms/${roomId}/authority-records`, {
    method: "POST",
    body: JSON.stringify({ roomId, ...payload }),
  });
}

export function listHostedRoomPropertyVerifications(roomId: number): Promise<PropertyVerification[]> {
  return apiClientFetch<PropertyVerification[]>(`/api/users/hosting/rooms/${roomId}/property-verifications`);
}

export function declareHostedPropertyVerification(roomId: number, payload: { evidenceRef: string }): Promise<PropertyVerification> {
  return apiClientFetch<PropertyVerification>(`/api/users/hosting/rooms/${roomId}/property-verifications`, {
    method: "POST",
    body: JSON.stringify({ roomId, ...payload }),
  });
}

// ZR-PAY-LINK-003 Section 1.1/2: "authority to list" and "authority to
// receive payments" are separate claims -- a distinct submission from
// declareHostedAuthorityRecord above, and the recipient may be a different
// party than the submitting host (an authorized agent/manager).

export function listHostedRoomPaymentRecipientAuthorities(roomId: number): Promise<PaymentRecipientAuthority[]> {
  return apiClientFetch<PaymentRecipientAuthority[]>(`/api/users/hosting/rooms/${roomId}/payment-recipient-authorities`);
}

/** ZR-PAY-LINK-003 Section 3.1: the consolidated recipient+destination
 *  status view for a room. */
export function getHostedRoomPaymentConnection(roomId: number): Promise<PaymentConnection> {
  return apiClientFetch<PaymentConnection>(`/api/users/hosting/rooms/${roomId}/payment-connection`);
}

export function declareHostedPaymentRecipientAuthority(
  roomId: number,
  payload: { recipientPartyId?: number; relationshipType: PaymentRecipientRelationshipType; evidenceRef: string }
): Promise<PaymentRecipientAuthority> {
  return apiClientFetch<PaymentRecipientAuthority>(`/api/users/hosting/rooms/${roomId}/payment-recipient-authorities`, {
    method: "POST",
    body: JSON.stringify({ roomId, ...payload }),
  });
}

/** ZR-PAY-LINK-003 Section 14.1: step-up confirmation for a recipient
 *  CHANGE -- only ever needed when declareHostedPaymentRecipientAuthority
 *  returns a "pending_step_up" row. */
export function resendPaymentRecipientAuthorityChangeCode(roomId: number, authorityId: number): Promise<{ sent: boolean }> {
  return apiClientFetch<{ sent: boolean }>(
    `/api/users/hosting/rooms/${roomId}/payment-recipient-authorities/${authorityId}/resend-change-code`,
    { method: "POST" }
  );
}

export function confirmPaymentRecipientAuthorityChange(
  roomId: number,
  authorityId: number,
  code: string
): Promise<PaymentRecipientAuthority> {
  return apiClientFetch<PaymentRecipientAuthority>(
    `/api/users/hosting/rooms/${roomId}/payment-recipient-authorities/${authorityId}/confirm-change`,
    { method: "POST", body: JSON.stringify({ code }) }
  );
}

/** Rental Transaction Record wireframe, host view -- lists the occupancies
 *  (current and past tenancies) for a room the calling host's own party
 *  owns, so the host UI has an occupancy id to request a transaction
 *  record for. Same room ownership scoping as listHostedRoomAuthorityRecords
 *  above. */
export function listHostedRoomOccupancies(roomId: number): Promise<Occupancy[]> {
  return apiClientFetch<Occupancy[]>(`/api/users/hosting/rooms/${roomId}/occupancies`);
}

/** Rental Transaction Record wireframe, host view -- scoped to a room the
 *  calling host's own party owns (room.property.ownerPartyId). Never
 *  includes the renter's own identity-verification claim -- see
 *  backend/app/api/routes/user_hosting.py's own route docstring. */
export function getHostedRentalTransactionRecord(occupancyId: number): Promise<RentalTransactionRecord> {
  return apiClientFetch<RentalTransactionRecord>(`/api/users/hosting/occupancies/${occupancyId}/transaction-record`);
}

/** ZR-ENG-CLR-011 Section 10/ZR-ENG-CLR-004 Section 4.3: confirming move-in
 *  is a Host commercial action -- previously reachable only from Zoiko's
 *  internal admin console. Mirrors the gate this action itself evaluates:
 *  the host must record HANDOVER_READY and POSSESSION_DELIVERED first (the
 *  renter's own RENTER_RECEIPT confirmation already lives on their Rentals
 *  page), then move-in-eligibility/confirm-move-in become available. */
export function getHostedMoveInEligibility(occupancyId: number): Promise<{ eligible: boolean; reasons: string[] }> {
  return apiClientFetch(`/api/users/hosting/occupancies/${occupancyId}/move-in-eligibility`);
}

export function confirmHostedMoveIn(occupancyId: number): Promise<Occupancy> {
  return apiClientFetch<Occupancy>(`/api/users/hosting/occupancies/${occupancyId}/confirm-move-in`, { method: "POST" });
}

export function prepareHostedHandover(occupancyId: number): Promise<void> {
  return apiClientFetch(`/api/users/hosting/occupancies/${occupancyId}/handover/prepare`, {
    method: "POST", body: JSON.stringify({}),
  });
}

export function confirmHostedPossessionDelivered(occupancyId: number): Promise<void> {
  return apiClientFetch(`/api/users/hosting/occupancies/${occupancyId}/handover/possession-delivered`, {
    method: "POST", body: JSON.stringify({}),
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
  defaultMonthlyRent?: number | null;
  defaultDepositAmount?: number | null;
  defaultTermMonths?: number | null;
  defaultCadence?: string;
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

/** The return leg once Stripe redirects back from its own hosted checkout
 *  page -- resolves the `checkoutSessionId` query param (Stripe's own
 *  {CHECKOUT_SESSION_ID} placeholder, substituted server-side) to the
 *  ListingFeePayment it belongs to. */
export function resolveListingFeeCheckoutSession(checkoutSessionId: string): Promise<ListingFeePayment> {
  return apiClientFetch<ListingFeePayment>(`/api/users/listing-fees/checkout-sessions/${checkoutSessionId}/resolve`);
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

export function listMyRentalPaymentObligations(
  obligationType?: RentalPaymentObligation["obligationType"],
  page?: { limit?: number; offset?: number; agreementId?: number; occupancyId?: number }
): Promise<RentalPaymentObligationsPage> {
  const params = new URLSearchParams();
  if (obligationType) params.set("obligationType", obligationType);
  if (page?.limit != null) params.set("limit", String(page.limit));
  if (page?.offset != null) params.set("offset", String(page.offset));
  if (page?.agreementId != null) params.set("agreementId", String(page.agreementId));
  if (page?.occupancyId != null) params.set("occupancyId", String(page.occupancyId));
  const query = params.toString() ? `?${params.toString()}` : "";
  return apiClientFetch<RentalPaymentObligationsPage>(`/api/users/rental-payments/obligations${query}`);
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

/** ZR-PAY-LINK-003 Section 19: immutable declare/confirm/dispute/correction
 *  timeline for one record -- available to either side of it (tenant or
 *  the authorized recipient), same access rule as the evidence routes. */
export function getRentalPaymentRecordTimeline(
  recordId: number,
  page?: { limit?: number; offset?: number }
): Promise<RentalPaymentTimelinePage> {
  const params = new URLSearchParams();
  if (page?.limit != null) params.set("limit", String(page.limit));
  if (page?.offset != null) params.set("offset", String(page.offset));
  const query = params.toString() ? `?${params.toString()}` : "";
  return apiClientFetch<RentalPaymentTimelinePage>(`/api/users/rental-payments/records/${recordId}/timeline${query}`);
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
  obligationType?: RentalPaymentObligation["obligationType"],
  page?: { limit?: number; offset?: number; agreementId?: number }
): Promise<RentalPaymentObligationsPage> {
  const params = new URLSearchParams();
  if (obligationType) params.set("obligationType", obligationType);
  if (page?.limit != null) params.set("limit", String(page.limit));
  if (page?.offset != null) params.set("offset", String(page.offset));
  if (page?.agreementId != null) params.set("agreementId", String(page.agreementId));
  const query = params.toString() ? `?${params.toString()}` : "";
  return apiClientFetch<RentalPaymentObligationsPage>(`/api/users/rental-payments/recipient/obligations${query}`);
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
  countryCode: string;
  bankDetails: Record<string, string>;
  authorizedRecipientConfirmed: boolean;
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

/** ZR-PAY-LINK-003 Section 6/Wireframe C: the recipient's own connected
 *  Stripe account for the online-payment rail -- a second, independent
 *  destination alongside the direct-instruction functions above. */
export function getRentalPaymentProviderAccount(): Promise<RentalPaymentProviderAccount> {
  return apiClientFetch<RentalPaymentProviderAccount>("/api/users/rental-payments/recipient/provider-account");
}

export function connectRentalPaymentProviderAccount(payload: {
  country: string;
  email: string;
}): Promise<RentalPaymentProviderAccountConnectResult> {
  return apiClientFetch<RentalPaymentProviderAccountConnectResult>("/api/users/rental-payments/recipient/provider-account", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function refreshRentalPaymentProviderAccount(): Promise<RentalPaymentProviderAccount> {
  return apiClientFetch<RentalPaymentProviderAccount>("/api/users/rental-payments/recipient/provider-account/refresh", {
    method: "POST",
  });
}

/** A fresh hosted onboarding link for the account already on file -- for a
 *  host who closed the Stripe tab before finishing. Never creates a new
 *  account (unlike connectRentalPaymentProviderAccount). */
export function resumeRentalPaymentProviderAccountOnboarding(): Promise<RentalPaymentProviderAccountConnectResult> {
  return apiClientFetch<RentalPaymentProviderAccountConnectResult>(
    "/api/users/rental-payments/recipient/provider-account/resume-onboarding",
    { method: "POST" }
  );
}

/** Dev/test-only -- refuses once real Stripe credentials are configured
 *  server-side, see crud/rental_payment_provider_account.py's own guard. */
export function simulateRentalPaymentProviderAccountOnboardingComplete(): Promise<RentalPaymentProviderAccount> {
  return apiClientFetch<RentalPaymentProviderAccount>(
    "/api/users/rental-payments/recipient/provider-account/simulate-onboarding-complete",
    { method: "POST" }
  );
}

/** ZR-PAY-LINK-003 Section 14.1: the governed "change payment account" flow
 *  -- mailed step-up code, same shape as
 *  resendRentalPaymentInstructionCode/confirmRentalPaymentInstruction. The
 *  current account stays fully usable while a change is only requested,
 *  never confirmed. */
export function requestRentalPaymentProviderAccountChange(): Promise<{ sent: boolean }> {
  return apiClientFetch<{ sent: boolean }>("/api/users/rental-payments/recipient/provider-account/request-change", {
    method: "POST",
  });
}

export function resendRentalPaymentProviderAccountChangeCode(): Promise<{ sent: boolean }> {
  return apiClientFetch<{ sent: boolean }>("/api/users/rental-payments/recipient/provider-account/resend-change-code", {
    method: "POST",
  });
}

export function confirmRentalPaymentProviderAccountChange(payload: {
  code: string;
  country: string;
  email: string;
}): Promise<RentalPaymentProviderAccountConnectResult> {
  return apiClientFetch<RentalPaymentProviderAccountConnectResult>(
    "/api/users/rental-payments/recipient/provider-account/confirm-change",
    { method: "POST", body: JSON.stringify(payload) }
  );
}

/** ZR-PAY-LINK-003 Section 3.1: the tenant-facing counterpart to
 *  getHostedRoomPaymentConnection -- lets the tenant Payments UI warn when
 *  their room's connection is SUSPENDED instead of silently offering
 *  payment actions against it. */
export function getRentalPaymentObligationConnection(obligationId: number): Promise<PaymentConnection> {
  return apiClientFetch<PaymentConnection>(`/api/users/rental-payments/obligations/${obligationId}/connection`);
}

/** ZR-PAY-LINK-003 Section 19/Wireframe F: starts a provider-hosted checkout
 *  for an obligation -- the tenant's own "Continue to secure payment." */
export function startRentalPaymentSession(obligationId: number): Promise<ExternalPaymentSessionCreateResult> {
  return apiClientFetch<ExternalPaymentSessionCreateResult>(
    `/api/users/rental-payments/obligations/${obligationId}/payment-session`,
    { method: "POST" }
  );
}

/** The return-page resolve -- self-heals via the backend's own
 *  resolve_session rather than only waiting on the webhook. */
export function getRentalPaymentSession(sessionId: number): Promise<ExternalPaymentSession> {
  return apiClientFetch<ExternalPaymentSession>(`/api/users/rental-payments/payment-sessions/${sessionId}`);
}

/** Same role as resolveListingFeeCheckoutSession plays for the Listing Fee
 *  return leg -- looks up which of the tenant's own sessions a Stripe
 *  `checkoutSessionId` query param refers to, self-healing its status. */
export function resolveRentalPaymentCheckoutSession(checkoutSessionId: string): Promise<ExternalPaymentSession> {
  return apiClientFetch<ExternalPaymentSession>(
    `/api/users/rental-payments/payment-sessions/by-checkout-session/${checkoutSessionId}`
  );
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

