// Private-room-only taxonomy per the marketplace standard -- whole-home, studio, hotel,
// nightly/vacation, hostel and dorm/shared-bed inventory is explicitly rejected.
export type PropertyType = "private_room";

export type ListingState =
  | "DRAFT"
  | "EVIDENCE_PENDING"
  | "REVIEW"
  | "REJECTED"
  | "APPROVED"
  | "PUBLISHED"
  | "PAUSED"
  | "SUSPENDED"
  | "WITHDRAWN"
  | "ARCHIVED";

export type BookingStatus = "confirmed" | "pending" | "cancelled" | "completed";
export type PaymentStatus = "paid" | "unpaid" | "refunded";
export type AdminRole = "admin" | "super_admin";
export type ApprovalStatus = "pending" | "approved" | "rejected";

export interface Listing {
  id: string;
  slug: string;
  name: string;
  propertyType: PropertyType;
  roomType: string;
  city: string;
  location: string;
  latitude?: number | null;
  longitude?: number | null;
  pricePerNight: number;
  // ISO-4217-style 3-letter code, e.g. "INR"/"GBP"/"USD". Always present on
  // current API responses (defaults to "INR" server-side); still worth an "?? INR"
  // fallback at display sites in case older cached data is ever read.
  currency: string;
  rating: number;
  reviewCount: number;
  guests: number;
  bedrooms: number;
  bathrooms: number;
  size: number;
  images: string[];
  amenities: string[];
  description: string;
  tags: string[];
  featured?: boolean;
  ownerId: number;
  partyId: number | null;
  roomId: number | null;
  minStayNights: number;
  marketReleaseId: number | null;
  state: ListingState;
  rejectionReason: string;
  contactName: string;
  contactPhone: string;
  contactEmail: string;
  // Real-time: state === "PUBLISHED" alone doesn't mean a renter hasn't since
  // moved in. Computed server-side (crud.listing.annotate_availability) --
  // never infer "is this actually live" from state alone.
  available: boolean;
  // Reusable offer terms, consulted only when a market release has opted
  // into automatic offer creation. Leaving these unset keeps this listing
  // on the manual offer flow regardless of that setting.
  defaultMonthlyRent: number | null;
  defaultDepositAmount: number | null;
  defaultTermMonths: number | null;
  defaultCadence: string;
}

export interface PublishEligibility {
  eligible: boolean;
  reasons: string[];
}

export interface OptionalClauseChoice {
  clauseId: string;
  title: string;
}

export type PartyType = "provider" | "renter" | "institution" | "zoiko_operator";

export interface Party {
  id: number;
  partyType: PartyType;
  status: "active" | "suspended" | "closed";
  jurisdiction: string;
  createdAt: string;
}

export interface MarketRelease {
  id: number;
  jurisdiction: string;
  status: "draft" | "active" | "disabled";
  minStayNights: number;
  effectiveFrom: string | null;
  approvedAt: string | null;
  createdAt: string;
  // See backend/app/services/policy.py for the known keys/defaults. Empty
  // means every policy uses the platform-wide default (manual/on).
  policyOverrides: Record<string, unknown>;
}

export interface Property {
  id: number;
  ownerPartyId: number;
  address: string;
  city: string;
  status: "active" | "inactive";
  createdAt: string;
}

export interface Room {
  id: number;
  propertyId: number;
  roomType: string;
  size: number;
  hasEnsuite: boolean;
  status: "active" | "inactive";
  createdAt: string;
}

export type AuthorityStatus =
  | "not_started"
  | "pending"
  | "verified"
  | "expiring"
  | "expired"
  | "failed"
  | "conflict"
  | "review_required"
  | "revoked";

export type AuthorityRelationshipType = "OWNER" | "AGENT" | "MANAGER";

export interface AuthorityRecord {
  id: number;
  partyId: number;
  roomId: number;
  authorityType: string;
  relationshipType: AuthorityRelationshipType | null;
  evidenceRef: string;
  verifiedAt: string | null;
  expiresAt: string | null;
  status: AuthorityStatus;
  createdAt: string;
}

/** ZR-PAY-LINK-003 Section 1.1/2: a separate claim from AuthorityRecord --
 *  "authority to list" and "authority to receive payments" are separate
 *  claims. partyId here is who actually receives rent for this room, which
 *  may be a different party than whoever holds the room's own list-authority. */
/** "pending_step_up" (ZR-PAY-LINK-003 Section 14.1) only appears for a
 *  CHANGE -- a room that already has a live verified recipient getting a
 *  new one -- never for a room's first-ever declaration, which goes
 *  straight to "pending". */
export type PaymentRecipientAuthorityStatus = "pending_step_up" | "pending" | "verified" | "failed" | "revoked";
export type PaymentRecipientRelationshipType = "OWNER" | "AGENT" | "MANAGER" | "OTHER";

export interface PaymentRecipientAuthority {
  id: number;
  partyId: number;
  roomId: number;
  relationshipType: PaymentRecipientRelationshipType;
  evidenceRef: string;
  verifiedAt: string | null;
  expiresAt: string | null;
  status: PaymentRecipientAuthorityStatus;
  isHighRisk: boolean;
  highRiskReason: string;
  createdAt: string;
}

/** ZR-PAY-LINK-003 Section 3.1: the consolidated recipient+destination
 *  status for a room -- what backs the "is rent collection actually usable
 *  yet, and why not if not" banner. CLOSED is not yet derived server-side
 *  (see crud/payment_connection.py's own docstring), so it never appears
 *  here today. */
export type PaymentConnectionState =
  | "DRAFT"
  | "RECIPIENT_SETUP_REQUIRED"
  | "PENDING_VERIFICATION"
  | "ACTIVE"
  | "SUSPENDED";

export interface PaymentConnection {
  roomId: number;
  state: PaymentConnectionState;
  recipientPartyId: number | null;
  recipientAuthorityId: number | null;
  recipientRelationshipType: PaymentRecipientRelationshipType | null;
  recipientAuthorityStatus: PaymentRecipientAuthorityStatus | null;
  recipientVerifiedAt: string | null;
  recipientExpiresAt: string | null;
  destinationMethod: string | null;
  destinationStatus: string | null;
  destinationAccountIdentifierMasked: string | null;
}

export interface RoomPassportClaim {
  id: number;
  roomId: number;
  claimType: string;
  value: string;
  evidenceTier: string;
  verifiedAt: string | null;
  expiresAt: string | null;
  createdAt: string;
}

export type OccupancyReviewState = "UNKNOWN" | "UNSUPPORTED" | "APPROVED";

export interface OccupancyClassification {
  id: number;
  roomId: number;
  classification: string;
  confidence: number;
  evidenceRef: string;
  jurisdiction: string;
  ruleVersion: number;
  reviewState: OccupancyReviewState;
  updatedAt: string;
}

export interface Booking {
  id: string;
  listingId: string;
  listingName: string;
  propertyType: PropertyType;
  guestName: string;
  guestEmail: string;
  guestAvatar: string;
  checkIn: string;
  checkOut: string;
  nights: number;
  guests: number;
  totalAmount: number;
  status: BookingStatus;
  paymentStatus: PaymentStatus;
  createdAt: string;
}

export interface Guest {
  id: string;
  name: string;
  email: string;
  phone: string;
  avatar: string;
  location: string;
  totalBookings: number;
  totalSpent: number;
  joinedAt: string;
  status: "active" | "inactive";
  /** Null when this guest has no linked Zoiko login (e.g. an admin-recorded
   *  walk-in) -- Party-keyed features like Occupancy Eligibility can't target them. */
  partyId: number | null;
}

export interface Review {
  id: string;
  listingId: string;
  listingName: string;
  guestName: string;
  guestAvatar: string;
  rating: number;
  comment: string;
  date: string;
  propertyType: PropertyType;
}

export interface Payment {
  id: string;
  bookingId: string;
  guestName: string;
  amount: number;
  method: "Credit Card" | "UPI" | "Net Banking" | "PayPal" | "Wallet";
  status: PaymentStatus;
  date: string;
}

export interface AdminUserSummary {
  id: number;
  email: string;
  fullName: string;
  phone: string;
  role: AdminRole;
  isActive: boolean;
  approvalStatus: ApprovalStatus;
  createdAt: string;
}

export interface SearchResult {
  id: string;
  type: "listing" | "guest" | "booking";
  title: string;
  subtitle: string;
  href: string;
}

// --- Leasing pipeline (application -> offer -> agreement) ---

export type ApplicationStatus = "SUBMITTED" | "WITHDRAWN" | "DECIDED";

export interface ApplicationDecisionRecord {
  id: number;
  decision: "APPROVED" | "REJECTED";
  reasonCode: string;
  note: string;
  decidedByAdminId: number | null;
  decidedByUserId: number | null;
  decidedAt: string;
}

export type OfferStatus = "DRAFT" | "SENT" | "ACCEPTED" | "DECLINED" | "EXPIRED" | "WITHDRAWN";

export interface OfferTermsRecord {
  id: number;
  version: number;
  monthlyRent: number;
  depositAmount: number;
  currency: string;
  startDate: string;
  termMonths: number;
  createdAt: string;
}

export type AgreementStatus =
  | "DRAFT"
  | "SENT"
  | "PARTIALLY_EXECUTED"
  | "PAYMENT_IN_PROGRESS"
  | "PAYMENT_PENDING"
  | "SIGNED"
  | "EXPIRED"
  | "VOID"
  | "AMENDMENT_PENDING";

export interface Agreement {
  id: number;
  offerId: number;
  version: number;
  status: AgreementStatus;
  contentRef: string;
  signedByProviderAt: string | null;
  signedByRenterAt: string | null;
  signatureRef: string;
  paymentSessionExpiresAt: string | null;
  createdAt: string;
}

export type DisclosureStatus = "REQUIRED_MISSING" | "DELIVERED" | "ACKNOWLEDGED";

export interface DisclosureRequirement {
  id: number;
  agreementId: number;
  disclosureType: string;
  title: string;
  required: boolean;
  status: DisclosureStatus;
  deliveredAt: string | null;
  deliveredToParty: string;
  deliveryChannel: string;
  acknowledgedAt: string | null;
  documentContentHash: string;
  createdAt: string;
}

export interface Offer {
  id: number;
  applicationId: number;
  listingId: string;
  guestId: string;
  status: OfferStatus;
  currentVersion: number;
  createdAt: string;
  terms: OfferTermsRecord[];
  agreement: Agreement | null;
  guestHasAccount: boolean;
}

export interface Application {
  id: number;
  listingId: string;
  listingName: string;
  guestId: string;
  guestName: string;
  guestEmail: string;
  guestPartyId: number | null;
  status: ApplicationStatus;
  message: string;
  desiredMoveIn: string | null;
  submittedAt: string;
  updatedAt: string;
  decisions: ApplicationDecisionRecord[];
  offer: Offer | null;
}

// --- Occupancy ---

export type OccupancyStatus = "PENDING_MOVE_IN" | "ACTIVE" | "ENDED" | "CANCELLED";

export interface Occupancy {
  id: number;
  offerId: number;
  listingId: string;
  listingName: string;
  roomId: number;
  propertyAddress: string;
  propertyCity: string;
  guestId: string;
  guestName: string;
  status: OccupancyStatus;
  moveInDate: string | null;
  expectedEndDate: string | null;
  moveOutDate: string | null;
  createdAt: string;
  endedAt: string | null;
  reassignedViaSubletRequestId: number | null;
}

export type HandoverEventType =
  | "HANDOVER_READY" | "POSSESSION_DELIVERED" | "RENTER_RECEIPT"
  | "MOVE_OUT_NOTICE_GIVEN" | "MOVE_OUT_READY" | "HOST_MOVE_OUT_CONFIRMED";

export interface HandoverEvent {
  id: number;
  occupancyId: number;
  eventType: HandoverEventType;
  actorKind: string;
  createdAt: string;
}

export type ConditionReportType = "MOVE_IN" | "MOVE_OUT";
export type ConditionRating = "GOOD" | "FAIR" | "DAMAGED";

export interface ConditionReportItem {
  id: number;
  occupancyId: number;
  reportType: ConditionReportType;
  area: string;
  conditionRating: ConditionRating | null;
  notes: string;
  originalFilename: string;
  contentType: string;
  sizeBytes: number;
  recordedByGuestId: string | null;
  recordedByAdminId: number | null;
  createdAt: string;
  hasFile: boolean;
}

export interface ActivationGateStatus {
  occupancyId: number;
  latestDecision: { outcome: string; reasonCodes: string[] } | null;
  handoverEvents: HandoverEvent[];
}

// --- Finance ledger ---

export type ObligationType = "RENT" | "DEPOSIT" | "FEE" | "TAX";
export type MoneyPlane = "OCCUPANCY" | "SAFEGUARDED" | "REVENUE";
export type ObligationStatus = "PENDING" | "PARTIALLY_PAID" | "PAID" | "WAIVED" | "FAILED" | "REFUNDED";

export interface Obligation {
  id: number;
  obligationType: ObligationType;
  moneyPlane: MoneyPlane;
  amount: number;
  currency: string;
  dueDate: string;
  status: ObligationStatus;
  guestId: string;
  agreementId: number | null;
  occupancyId: number | null;
  payoutId: number | null;
  createdAt: string;
}

export type SimulatedPaymentStatus = "PENDING" | "SUCCEEDED" | "FAILED";

export interface PaymentAllocation {
  id: number;
  paymentId: number;
  obligationId: number;
  amountAllocated: number;
  createdAt: string;
}

export interface SimulatedPayment {
  id: number;
  guestId: string;
  amount: number;
  currency: string;
  idempotencyKey: string;
  status: SimulatedPaymentStatus;
  createdAt: string;
  confirmedAt: string | null;
  allocations: PaymentAllocation[];
}

export interface ObligationRead {
  id: number;
  obligationType: string;
  moneyPlane: string;
  amount: number;
  currency: string;
  dueDate: string;
  status: string;
  guestId: string;
  agreementId: number | null;
  occupancyId: number | null;
  payoutId: number | null;
  createdAt: string;
}

export interface PaymentPreview {
  amountDueNow: ObligationRead[];
  cadence: string;
  remainingScheduledCount: number;
}

export interface AutopayMandate {
  id: number;
  occupancyId: number;
  payerGuestId: string;
  providerRef: string;
  status: string;
  consentSnapshot: Record<string, unknown>;
  createdAt: string;
  revokedAt: string | null;
}

export type DepositStatus = "HELD" | "RELEASED" | "FORFEITED" | "PARTIALLY_RELEASED";

export interface DepositRecord {
  id: number;
  obligationId: number;
  status: DepositStatus;
  heldAmount: number;
  releasedAmount: number;
  releasedAt: string | null;
  notes: string;
  currency: string;
}

export type PayoutStatus = "PENDING" | "PAID" | "FAILED" | "HELD";

export interface PayoutRecord {
  id: number;
  partyId: number;
  periodKey: string;
  amount: number;
  currency: string;
  status: PayoutStatus;
  holdReason: string;
  createdAt: string;
  paidAt: string | null;
}

export type RefundStatus = "REQUESTED" | "APPROVED" | "REJECTED" | "COMPLETED";

export interface RefundRequest {
  id: number;
  paymentId: number;
  obligationId: number;
  amount: number;
  reason: string;
  status: RefundStatus;
  requestedByAdminId: number;
  decidedByAdminId: number | null;
  createdAt: string;
  decidedAt: string | null;
  currency: string;
}

export type DisputeCategory = "CHARGEBACK" | "COMPENSATION" | "OTHER";
export type DisputeStatus = "OPEN" | "RESOLVED" | "REJECTED";

export interface DisputeCase {
  id: number;
  paymentId: number | null;
  occupancyId: number | null;
  category: DisputeCategory;
  description: string;
  status: DisputeStatus;
  openedAt: string;
  resolvedAt: string | null;
  resolutionNotes: string;
}

export type ReconciliationStatus = "CLEAN" | "DISCREPANCIES_FOUND";

export interface ReconciliationRun {
  id: number;
  runAt: string;
  totals: Record<string, number>;
  mismatches: string[];
  status: ReconciliationStatus;
}

// --- Listing Fee (ZR-PAY-002 Section 8) ---
// The only payment Zoiko Rooms collects for itself -- architecturally
// separate from the "Finance ledger" domain above (rent/deposit custody).
// Mirrors /api/users/listing-fees/* and /api/finance/listing-fees/*.

export interface ListingFeePolicy {
  id: number;
  jurisdictionCode: string;
  version: number;
  effectiveFrom: string;
  effectiveTo: string | null;
  amount: number;
  currency: string;
  taxRate: number;
  quoteValidityMinutes: number;
  legalEntityName: string;
  taxRegistrationNumber: string;
  disclosureText: string;
  refundEligible: boolean;
  refundWindowDays: number | null;
  createdAt: string;
}

export interface ListingFeeQuote {
  id: number;
  listingId: string;
  amount: number;
  taxAmount: number;
  totalAmount: number;
  currency: string;
  expiresAt: string;
  createdAt: string;
}

export interface ListingFeeCheckoutSession {
  id: number;
  quoteId: number;
  listingId: string;
  amount: number;
  currency: string;
  status: ListingFeePaymentStatus;
  /** Stripe's own hosted payment page -- redirect the browser here directly
   *  (window.location.href), never render a custom form for it. Empty when
   *  Stripe isn't configured server-side -- the payment already completed
   *  synchronously in that case; nothing to redirect to. */
  checkoutUrl: string;
  createdAt: string;
}

export type ListingFeePaymentStatus = "PENDING" | "SUCCEEDED" | "FAILED";

export interface ListingFeePayment {
  id: number;
  quoteId: number;
  listingId: string;
  amount: number;
  currency: string;
  status: ListingFeePaymentStatus;
  billingCountry: string;
  failureMessage: string;
  createdAt: string;
  paidAt: string | null;
  failedAt: string | null;
  refundEligible: boolean;
}

export interface ListingFeeReceipt {
  id: number;
  paymentId: number;
  receiptNumber: string;
  legalEntityName: string;
  taxRegistrationNumber: string;
  amount: number;
  taxRate: number;
  taxAmount: number;
  totalAmount: number;
  currency: string;
  issuedAt: string;
}

export type ListingFeeRefundStatus = "REQUESTED" | "PROCESSING" | "PARTIALLY_REFUNDED" | "REFUNDED" | "FAILED";

export interface ListingFeeRefund {
  id: number;
  paymentId: number;
  amount: number;
  currency: string;
  reason: string;
  status: ListingFeeRefundStatus;
  requestedByAdminId: number;
  failureMessage: string;
  createdAt: string;
  completedAt: string | null;
}

// --- Rental payment records (ZR-PAY-002 Section 4-11) ---
// Zoiko Rooms never collects, holds or moves this money -- these are
// declarations, confirmations, disputes and corrections only. Independent
// of the "Finance ledger" domain above (see backend's own module docstring
// for why the two temporarily coexist).

export type RentalPaymentObligationType = "RENT" | "DEPOSIT" | "OTHER";

/** ZR-PAY-LINK-003 Section 16. CONFIRMED collapses the ZR-PAY-002-era
 *  CONFIRMED_BY_RECIPIENT/CONFIRMED_BY_PROVIDER pair -- "who confirmed" is
 *  still available on RentalPaymentRecord.provenance, rendered as its own
 *  field wherever status is shown. PROVIDER_PROCESSING has no producing
 *  backend code path yet (see models/rental_payment.py's own docstring). */
export type RentalPaymentStatus =
  | "UPCOMING"
  | "DUE"
  | "PAYMENT_SESSION_STARTED"
  | "PROVIDER_PROCESSING"
  | "PAYER_RECORDED"
  | "RECIPIENT_CONFIRMATION_PENDING"
  | "CONFIRMED"
  | "PARTIALLY_PAID"
  | "OVERDUE"
  | "DISPUTED"
  | "REVERSED"
  | "WAIVED"
  | "CANCELLED";

export type RentalPaymentProvenance =
  | "TENANT_DECLARATION"
  | "RECIPIENT_CONFIRMATION"
  | "PROVIDER_CONFIRMATION"
  | "ADMIN_CORRECTION"
  | "SYSTEM_DERIVATION";

export type RentalPaymentMethodCategory = "BANK_TRANSFER" | "CASH" | "CARD" | "OTHER";

export interface RentalPaymentRecord {
  id: number;
  obligationId: number;
  status: RentalPaymentStatus;
  provenance: RentalPaymentProvenance;
  declaredAmount: number;
  declaredCurrency: string;
  declaredDate: string;
  paymentMethodCategory: RentalPaymentMethodCategory;
  externalReference: string;
  declaredByGuestId: string;
  confirmedByPartyId: number | null;
  /** Null until a confirmation exists. Less than declaredAmount means
   *  PARTIALLY_PAID (ZR-PAY-002 Section 6). */
  confirmedAmount: number | null;
  /** Set only when provenance is PROVIDER_CONFIRMATION -- the external
   *  provider's own transaction/reconciliation reference. */
  providerReference: string;
  confirmedAt: string | null;
  createdAt: string;
  /** ZR-PAY-LINK-003 Section 19/Wireframe PAY-18 -- previously only ever
   *  visible to admins; now nested here for the tenant/recipient views. */
  disputes: RentalPaymentDispute[];
  corrections: RentalPaymentCorrection[];
}

export interface RentalPaymentObligation {
  id: number;
  obligationType: RentalPaymentObligationType;
  agreementId: number | null;
  occupancyId: number | null;
  tenantGuestId: string;
  recipientPartyId: number;
  amount: number;
  currency: string;
  dueDate: string;
  status: RentalPaymentStatus;
  /** Jurisdiction-resolved display term -- "rent", or the jurisdiction's own
   *  word for deposit ("tenancy deposit" / "bond" / "security deposit" / ...).
   *  Always use this over obligationType for user-facing copy. */
  displayLabel: string;
  waivedReason: string;
  waivedAt: string | null;
  createdAt: string;
  records: RentalPaymentRecord[];
}

export type RentalPaymentDiscrepancyReason =
  | "NOT_ARRIVED"
  | "AMOUNT_DIFFERENT"
  | "REFERENCE_MISMATCH"
  | "RETURNED_OR_REVERSED"
  | "OTHER";

export interface RentalPaymentDispute {
  id: number;
  recordId: number;
  reasonCode: RentalPaymentDiscrepancyReason;
  details: string;
  status: "OPEN" | "RESOLVED";
  reportedByGuestId: string | null;
  reportedByPartyId: number | null;
  reportedAt: string;
  resolvedByAdminId: number | null;
  resolvedAt: string | null;
  resolutionNotes: string;
}

export interface RentalPaymentCorrection {
  id: number;
  recordId: number;
  fieldName: string;
  previousValue: string;
  newValue: string;
  reason: string;
  actorAdminId: number | null;
  actorGuestId: string | null;
  actorPartyId: number | null;
  createdAt: string;
}

export type RentalPaymentInstructionStatus = "PENDING_VERIFICATION" | "PENDING_REVIEW" | "ACTIVE" | "SUPERSEDED" | "REJECTED";

export interface RentalPaymentInstruction {
  id: number;
  partyId: number;
  status: RentalPaymentInstructionStatus;
  method: RentalPaymentMethodCategory;
  recipientName: string;
  accountIdentifierMasked: string;
  referenceFormat: string;
  additionalInstructions: string;
  verifiedAt: string | null;
  createdAt: string;
  isHighRisk: boolean;
  highRiskReason: string;
  reviewedAt: string | null;
  reviewReason: string;
}

/** SUPERSEDED never appears on the account a recipient's own GET/change
 *  routes return (those always resolve the current row) -- listed here
 *  only because it's a real value the type could carry in principle. */
export type RentalPaymentProviderAccountStatus = "ONBOARDING" | "COMPLETE" | "SUPERSEDED";

/** ZR-PAY-LINK-003 Section 6/Wireframe C: a recipient's own connected Stripe
 *  account for receiving rent/deposit payments directly -- never the raw
 *  account id, same masking posture as RentalPaymentInstruction. */
export interface RentalPaymentProviderAccount {
  id: number;
  status: RentalPaymentProviderAccountStatus;
  detailsSubmitted: boolean;
  chargesEnabled: boolean;
  payoutsEnabled: boolean;
  /** ZR-PAY-LINK-003 Section 14.1: set only on the row created by a
   *  confirmed account change, same as RentalPaymentInstruction's own
   *  isHighRisk/highRiskReason. */
  isHighRisk: boolean;
  highRiskReason: string;
  createdAt: string;
  updatedAt: string;
}

export interface RentalPaymentProviderAccountConnectResult {
  account: RentalPaymentProviderAccount;
  onboardingUrl: string;
}

export type ExternalPaymentSessionStatus = "STARTED" | "SUCCEEDED" | "FAILED";

/** ZR-PAY-LINK-003 Section 6/10.1/Wireframe F: a short-lived,
 *  provider-hosted payment handoff for one obligation. */
export interface ExternalPaymentSession {
  id: number;
  obligationId: number;
  status: ExternalPaymentSessionStatus;
  amount: number;
  currency: string;
  failureMessage: string;
  createdAt: string;
  resolvedAt: string | null;
}

export interface ExternalPaymentSessionCreateResult {
  session: ExternalPaymentSession;
  checkoutUrl: string;
}

export interface EvidenceArtifact {
  id: number;
  relatedEntityType: string;
  relatedEntityId: string;
  originalFilename: string;
  contentType: string;
  fileSize: number;
  scanStatus: string;
  createdAt: string;
}

export interface RentalPaymentEvidenceHold {
  id: number;
  artifactId: number;
  status: "ACTIVE" | "RELEASED";
  reason: string;
  placedByAdminId: number;
  placedAt: string;
  releasedByAdminId: number | null;
  releasedAt: string | null;
}

// --- USER accounts (renters & hosts) ---
// These mirror the /api/users/* schemas. They are deliberately separate from the
// admin types above: a UserAccount authenticates with `zoiko_user_token` and is
// never an AdminUser.

export interface UserProfile {
  id: number;
  email: string;
  fullName: string;
  phone: string;
  partyId: number | null;
  emailVerified: boolean;
  isActive: boolean;
  createdAt: string;
}

export type DocumentCategory = "identity" | "address" | "other";

export type IdentityDocumentType =
  // identity
  | "aadhaar"
  | "pan_card"
  | "passport"
  | "driving_license"
  | "voter_id"
  | "national_id"
  | "residence_permit"
  | "permanent_resident_card"
  | "government_photo_id"
  | "government_employee_id"
  // address / residency
  | "electricity_bill"
  | "water_bill"
  | "gas_bill"
  | "telephone_bill"
  | "internet_bill"
  | "property_tax_bill"
  | "bank_statement"
  | "credit_card_statement"
  | "government_address_certificate"
  | "rental_agreement"
  // other
  | "birth_certificate"
  | "marriage_certificate"
  | "other_government_document"
  | "other";

export type IdentityVerificationStatus =
  | "pending"
  | "verified"
  | "rejected"
  | "expired"
  | "additional_evidence_required";

/** User-facing shape, returned by /api/users/identity-verifications. */
export interface IdentityVerificationRecord {
  id: number;
  documentType: IdentityDocumentType;
  documentCategory: DocumentCategory;
  customDocumentName: string;
  documentNumber: string;
  evidenceRef: string;
  status: IdentityVerificationStatus;
  hasDocument: boolean;
  documentOriginalName: string;
  documentContentType: string;
  verifiedAt: string | null;
  expiresAt: string | null;
  createdAt: string;
  updatedAt: string;
  verifierNotes: string;
}

/** Admin-facing shape, returned by /api/identity-verifications -- field names
 *  mirror the backend's ORM-passthrough schema, which is why this differs
 *  slightly from IdentityVerificationRecord above (e.g. encryptedReference
 *  instead of documentNumber). */
export interface AdminIdentityVerification {
  id: number;
  partyId: number;
  documentType: IdentityDocumentType;
  documentCategory: DocumentCategory;
  customDocumentName: string;
  encryptedReference: string | null;
  evidenceRef: string;
  verifiedAt: string | null;
  expiresAt: string | null;
  verifierAdminId: number | null;
  verifierNotes: string;
  status: IdentityVerificationStatus;
  hasDocument: boolean;
  documentFileOriginalName: string;
  documentFileContentType: string;
  createdAt: string;
  updatedAt: string;
}

export interface UserApplication {
  id: number;
  listingId: string;
  /** May be empty if the listing was since deleted -- render listingId as a
   *  fallback in that case rather than a blank heading. */
  listingName: string;
  status: ApplicationStatus;
  message: string;
  desiredMoveIn: string | null;
  submittedAt: string;
  updatedAt: string;
  offerId: number | null;
  offerStatus: string | null;
  agreementId: number | null;
  agreementStatus: string | null;
}

export interface UserOccupancy {
  id: number;
  listingId: string;
  roomId: number;
  status: OccupancyStatus;
  moveInDate: string | null;
  expectedEndDate: string | null;
  moveOutDate: string | null;
  createdAt: string;
  endedAt: string | null;
  agreementId: number | null;
  currency: string;
  reassignedViaSubletRequestId: number | null;
}

export interface PreMoveInCancellationResult {
  occupancy: { id: number; status: string; moveOutDate: string | null; endedAt: string | null };
  feeAmount: number;
  feeNote: string;
  refundedAmount: number;
}

// -- Section 6 gap: termination cases + refund entitlements (ZR-ENG-CLR-006) --

export type TerminationCauseCode =
  | "RENTER_ORDINARY_EARLY_EXIT"
  | "RENTER_CONTRACT_BREAK"
  | "RENTER_STATUTORY_RIGHT"
  | "MUTUAL_SURRENDER"
  | "ASSIGNMENT_OR_REPLACEMENT"
  | "HOST_FAULT_OR_NONPERFORMANCE"
  | "HOST_LAWFUL_POSSESSION_ACTION"
  | "RENTER_BREACH"
  | "PROPERTY_UNINHABITABLE"
  | "CASUALTY_OR_FORCE_EVENT"
  | "ABANDONMENT_REPORTED"
  | "PLATFORM_SAFETY_INTERVENTION"
  | "LEGAL_OR_REGULATORY_ORDER"
  | "OTHER_COUNSEL_APPROVED";

export type TerminationCaseStatus =
  | "OPENED"
  | "SURRENDER_PROPOSED"
  | "SURRENDER_DECLINED"
  | "PENDING_REVIEW"
  | "REJECTED_PATHWAY"
  | "EFFECTIVE_DATE_SET"
  | "TERMINATED"
  | "WITHDRAWN";

export interface TerminationCase {
  id: number;
  occupancyId: number;
  agreementId: number;
  initiatorGuestId: string | null;
  initiatorAdminId: number | null;
  causeCode: TerminationCauseCode;
  status: TerminationCaseStatus;
  notes: string;
  noticeCreatedAt: string;
  noticeServedAt: string | null;
  noticeMethod: string;
  evidenceRefs: string[];
  earliestEffectiveDate: string | null;
  effectiveTerminationDate: string | null;
  withdrawnAt: string | null;
  tribunalLiabilityAmount: number;
  tribunalLiabilityReason: string;
  adjudicatedEffectiveDate: string | null;
  adjudicatedEffectiveDateReason: string;
  createdAt: string;
}

export interface TerminationCasePreview {
  causeCode: TerminationCauseCode;
  resolvedStatus: string;
  requiresHostConsent: boolean;
  requiresEvidenceToResolveNow: boolean;
  earliestEffectiveDate: string | null;
  estimatedEarnedRent: number | null;
  estimatedRefundableUnearnedRent: number | null;
  estimatedLiabilityAmount: number | null;
  estimatedLiabilityNote: string;
  estimatedMitigationCredit: number | null;
  estimatedNetRefund: number | null;
  depositDisclaimer: string;
  alternativesNote: string;
}

export interface MitigationRecord {
  id: number;
  terminationCaseId: number;
  marketedForRelettingAt: string | null;
  listingChannels: string[];
  replacementBookingId: number | null;
  replacementOccupancyStart: string | null;
  replacementRentAmount: number | null;
  reasonableRelettingCosts: number | null;
  evidenceRefs: string[];
  notes: string;
  recordedByAdminId: number | null;
  createdAt: string;
}

export type RefundEntitlementStatus = "CALCULATED" | "APPROVED" | "EXECUTED";

export interface RefundEntitlementLineItem {
  id: number;
  type: string;
  sourceObligationId: number | null;
  periodDueDate: string | null;
  amount: number;
  basisNote: string;
  refundRequestId: number | null;
}

export interface RefundEntitlement {
  id: number;
  terminationCaseId: number;
  version: number;
  currency: string;
  grossRefundable: number;
  netRefund: number;
  status: RefundEntitlementStatus;
  calculatedByAdminId: number | null;
  calculatedAt: string;
  approvedByAdminId: number | null;
  approvedAt: string | null;
  executedByAdminId: number | null;
  executedAt: string | null;
  lineItems: RefundEntitlementLineItem[];
}

// -- Section 10 gap: ZR-ENG-CLR-010 general-purpose Dispute Resolution
// engine (DisputeResolutionCase et al, backend/app/models/dispute*.py) --
// entirely separate from the older, simpler finance.DisputeCase
// (chargeback/PSP-reversal-adjacent) already surfaced in
// FinanceOpsManager.tsx's "Disputes" section. Field names mirror the
// backend CamelModel schemas exactly (backend/app/schemas/disputes.py and
// dispute_*.py).
export type DisputeClaimFamily =
  | "DEPOSIT" | "PAYMENT" | "REFUND_PAYOUT" | "PROPERTY_CONDITION" | "BOOKING_AGREEMENT"
  | "SUBLET_OCCUPANCY" | "MARKETPLACE_CONDUCT" | "PROTECTED_SAFETY" | "VERIFICATION_FRAUD" | "ZOIKO_SERVICE";

export type DisputeAuthorityClass = "A0" | "A1" | "A2" | "A3" | "A4" | "A5" | "A6";
export type DisputeClaimantRole = "RENTER" | "HOST";
export type DisputeResolverConfidence = "RESOLVED" | "LEGAL_REVIEW_REQUIRED";

export type DisputeCaseStatus =
  | "SUBMITTED" | "TRIAGED" | "LEGAL_REVIEW_REQUIRED" | "IN_PROGRESS"
  | "PARTIALLY_RESOLVED" | "RESOLVED" | "CLOSED" | "ON_HOLD" | "EXTERNAL_PENDING" | "REOPENED";

export type DisputeClaimStatus =
  | "OPEN" | "RESPONSE_DUE" | "EVIDENCE" | "NEGOTIATION" | "INTERNAL_REVIEW"
  | "EXTERNAL_REFERRAL" | "UPHELD" | "PARTLY_UPHELD" | "NOT_UPHELD" | "SETTLED" | "WITHDRAWN";

export type DisputeCaseReopenGrounds =
  | "MATERIAL_NEW_EVIDENCE" | "PROCESSING_ERROR" | "EXTERNAL_DECISION" | "FRAUD_FINDING"
  | "INTERNAL_REVIEW_REQUESTED" | "OTHER";

export type DisputeCaseTeam = "DISPUTE_OPERATIONS" | "TRUST_AND_SAFETY" | "LEGAL_COMPLIANCE" | "FINANCE";

export type DisputeFinancialHoldStatus = "PROPOSED" | "ACTIVE" | "RELEASE_PENDING" | "RELEASED" | "CLOSED";

export type DisputeEvidenceProvenance = "RENTER_SUBMITTED" | "HOST_SUBMITTED" | "ADMIN_COLLECTED" | "SYSTEM_GENERATED";
export type DisputeEvidenceDisclosureClass = "ALL_PARTIES" | "HOST_VISIBLE_ONLY" | "RENTER_VISIBLE_ONLY" | "INTERNAL_ONLY";
export type DisputeEvidenceVerificationStatus = "RECEIVED" | "VERIFIED" | "UNVERIFIED" | "ARCHIVED";

export type DisputeSettlementStatus = "SENT" | "COUNTERED" | "ACCEPTED" | "REJECTED" | "EXPIRED" | "EFFECTIVE" | "VOID";
export type DisputeSettlementProposerRole = "RENTER" | "HOST";
export type DisputeSettlementRespondAction = "ACCEPT" | "REJECT" | "COUNTER";

export type DisputeDeadlineType = "PARTY_RESPONSE" | "EVIDENCE_CLOSE" | "INTERNAL_REVIEW";
export type DisputeDeadlineStatus = "PENDING" | "MET" | "EXTENDED" | "CANCELLED";
export type DisputeDeadlineSource = "SYSTEM_DEFAULT" | "ADMIN_SET";

export type DisputePartyRole = "RENTER" | "HOST" | "REPRESENTATIVE";
export type DisputePartyRepresentationType = "SELF" | "PROPERTY_MANAGER" | "LEGAL_COUNSEL" | "OTHER_AUTHORIZED";

export type DisputeExternalProceedingAuthorityType = string;
export type DisputeExternalProceedingStatus = "FILED" | "ACCEPTED" | "PENDING" | "DISMISSED" | "WITHDRAWN";
export type DisputeExternalProceedingFinalityState = "FINAL" | "UNDER_REVIEW";
export type DisputeExternalProceedingOutcome = "UPHELD" | "PARTLY_UPHELD" | "NOT_UPHELD" | "SETTLED";

export type DisputeMessageSenderRole = "RENTER" | "HOST" | "ADMIN";
export type DisputeMessageVisibilityClass = "PARTY_VISIBLE" | "INTERNAL_ONLY";
export type DisputeMessageModerationState = "VISIBLE" | "HIDDEN";

export type DisputeClaimDecideOutcome = "UPHELD" | "PARTLY_UPHELD" | "NOT_UPHELD";

export interface DisputeClaimCreate {
  claimCode: string;
  claimFamily: DisputeClaimFamily;
  amount?: number | null;
  currency?: string;
  requestedRemedy?: string;
  safetyFlag?: boolean;
}

export interface DisputeCaseCreate {
  occupancyId?: number | null;
  claim: DisputeClaimCreate;
}

export interface DisputeClaimRead {
  id: number;
  caseId: number;
  claimCode: string;
  claimFamily: DisputeClaimFamily;
  claimantRole: DisputeClaimantRole;
  amount: number | null;
  currency: string;
  requestedRemedy: string;
  authorityClass: DisputeAuthorityClass | null;
  resolverConfidence: DisputeResolverConfidence;
  resolverNotes: string;
  policyPackId: number | null;
  policyPackVersion: number | null;
  sourceRecordType: string | null;
  sourceRecordId: string | null;
  sourceRecordSnapshot: Record<string, unknown>;
  status: DisputeClaimStatus;
  outcome: DisputeClaimDecideOutcome | null;
  reasonCode: string;
  createdAt: string;
  decidedAt: string | null;
  decidedByAdminId: number | null;
  version: number;
}

export interface DisputeMoneyStatusByCurrency {
  currency: string;
  amountDisputed: number;
  amountHeld: number;
  amountUndisputed: number;
  amountSettled: number;
}

export interface DisputeCaseRead {
  id: number;
  occupancyId: number | null;
  propertyId: number | null;
  openedByGuestId: string | null;
  openedByPartyId: number | null;
  severity: string;
  status: DisputeCaseStatus;
  primaryClaimFamily: DisputeClaimFamily;
  externalDependencyFlag: boolean;
  openedAt: string;
  closedAt: string | null;
  reopenedAt: string | null;
  reopenedByAdminId: number | null;
  reopenGrounds: string | null;
  reopenNote: string;
  assignedTeam: DisputeCaseTeam | null;
  assignedAdminId: number | null;
  partialClosureReason: string;
  version: number;
  moneyStatus: DisputeMoneyStatusByCurrency[];
  claims: DisputeClaimRead[];
}

export interface DisputeEvidenceRead {
  id: number;
  caseId: number;
  provenance: DisputeEvidenceProvenance;
  uploadedByGuestId: string | null;
  uploadedByPartyId: number | null;
  uploadedByAdminId: number | null;
  originalFilename: string;
  contentType: string;
  sizeBytes: number;
  sha256Hash: string;
  noteText: string;
  disclosureClass: DisputeEvidenceDisclosureClass;
  legalHold: boolean;
  verificationStatus: DisputeEvidenceVerificationStatus;
  redactedOfEvidenceId: number | null;
  claimIds: number[];
  deletionRequestedAt: string | null;
  deletedAt: string | null;
  deletionRefusedReason: string;
  createdAt: string;
  capturedAt: string | null;
}

export interface DisputeLegalHoldRead {
  id: number;
  evidenceId: number;
  caseId: number;
  status: "ACTIVE" | "RELEASED";
  reason: string;
  placedByAdminId: number;
  placedAt: string;
  releasedByAdminId: number | null;
  releasedAt: string | null;
}

export interface DisputeSettlementRead {
  id: number;
  caseId: number;
  proposedByRole: DisputeSettlementProposerRole;
  proposedByGuestId: string | null;
  proposedByPartyId: number | null;
  status: DisputeSettlementStatus;
  termsText: string;
  amount: number | null;
  currency: string;
  termsHash: string;
  acknowledgesNoNonwaivableWaiver: boolean;
  offeredAt: string;
  expiresAt: string | null;
  respondedByGuestId: string | null;
  respondedByPartyId: number | null;
  respondedAt: string | null;
  responseNote: string;
  effectiveAt: string | null;
  supersedesSettlementId: number | null;
  claimIds: number[];
  createdAt: string;
  version: number;
  acceptedPartySnapshot: Record<string, unknown>;
}

export interface DisputeCaseMessageRead {
  id: number;
  caseId: number;
  senderRole: DisputeMessageSenderRole;
  senderGuestId: string | null;
  senderPartyId: number | null;
  senderAdminId: number | null;
  body: string;
  visibilityClass: DisputeMessageVisibilityClass;
  moderationState: DisputeMessageModerationState;
  moderatedByAdminId: number | null;
  moderatedAt: string | null;
  createdAt: string;
}

export interface DisputePartyRead {
  id: number;
  caseId: number;
  partyRole: DisputePartyRole;
  guestId: string | null;
  partyId: number | null;
  represents: string | null;
  representationType: DisputePartyRepresentationType;
  authorityVerifiedAt: string | null;
  authorityEvidenceRef: string;
  communicationRestrictions: string;
  addedAt: string;
  addedByAdminId: number | null;
}

export interface DisputeDeadlineRead {
  id: number;
  caseId: number;
  claimId: number | null;
  deadlineType: DisputeDeadlineType;
  dueAt: string;
  originalDueAt: string | null;
  status: DisputeDeadlineStatus;
  extensionBasis: string;
  source: DisputeDeadlineSource;
  createdByAdminId: number | null;
  createdAt: string;
  reminderAt: string | null;
  isOverdue: boolean;
  isReminderDue: boolean;
}

export interface DisputeExternalProceedingRead {
  id: number;
  caseId: number;
  authorityType: string;
  authorityName: string;
  externalReference: string;
  status: DisputeExternalProceedingStatus;
  finalityState: DisputeExternalProceedingFinalityState | null;
  filedAt: string | null;
  decisionDate: string | null;
  outcomeEvidenceId: number | null;
  outcomeSummary: string;
  filedByAdminId: number;
  decidedByAdminId: number | null;
  claimIds: number[];
  createdAt: string;
  version: number;
  externalDeadlineAt: string | null;
  filedAfterDeadline: boolean;
}

export interface DisputeDecisionRead {
  id: number;
  claimId: number;
  caseId: number;
  outcome: string;
  decisionBasis: string;
  authority: "admin" | "external_proceeding" | "settlement";
  decidedByAdminId: number | null;
  reasonCode: string;
  reasonCategory: string;
  externalProceedingId: number | null;
  settlementId: number | null;
  decidedAt: string;
}

export interface DisputeFinancialHoldRead {
  id: number;
  claimId: number;
  amount: number;
  currency: string;
  authorityBasis: string;
  status: DisputeFinancialHoldStatus;
  version: number;
  reasonCode: string;
  createdByAdminId: number;
  createdAt: string;
  approvedByAdminId: number | null;
  approvedAt: string | null;
  releaseRequestedByAdminId: number | null;
  releaseRequestedAt: string | null;
  releasedAt: string | null;
  releaseReason: string;
  reviewAt: string | null;
  isOverdueForReview: boolean;
}

export interface DisputeChronologyEvent {
  timestamp: string;
  eventType: string;
  summary: string;
}

export interface DisputeCaseExportRead {
  case: DisputeCaseRead;
  evidenceIndex: DisputeEvidenceRead[];
  chronology: DisputeChronologyEvent[];
  generatedAt: string;
  note: string;
}

export type BookingChangeType =
  | "DATE_SHIFT"
  | "EXTENSION"
  | "SHORTENING"
  | "PREMISES_CHANGE"
  | "FINANCIAL_CHANGE"
  | "TERM_SHIFT"
  | "LEGAL_ORDER_CHANGE"
  | "DEPOSIT_CHANGE";

export type BookingChangeRequestStatus =
  | "AWAITING_HOST"
  | "AWAITING_RENTER"
  | "AWAITING_AGREEMENT_ACTION"
  | "EFFECTIVE"
  | "REJECTED"
  | "WITHDRAWN"
  | "EXPIRED"
  | "CONFLICT"
  | "FAILED";

export interface BookingChangeRequest {
  id: number;
  agreementId: number;
  requestedByGuestId: string;
  changeType: BookingChangeType;
  status: BookingChangeRequestStatus;
  originalStartDate: string;
  proposedStartDate: string;
  originalEndDate: string | null;
  proposedEndDate: string | null;
  additionalTermMonths: number | null;
  targetListingId: string | null;
  resultingApplicationId: number | null;
  originalMonthlyRent: number | null;
  proposedMonthlyRent: number | null;
  reason: string;
  decisionNote: string;
  decidedByAdminId: number | null;
  decidedAt: string | null;
  resultingAmendmentId: number | null;
  createdAt: string;
  expiresAt: string;
  listingName: string;
  targetListingName: string;
  guestName: string;
  authorityEvidenceRef: string;
  originalDepositAmount: number | null;
  proposedDepositAmount: number | null;
  currency: string;
}

export type SubletRequestStatus =
  | "draft"
  | "pending_verification"
  | "pending_admin_review"
  | "more_information_requested"
  | "tenant_response_submitted"
  | "approved"
  | "rejected"
  | "withdrawn"
  | "expired"
  | "superseded"
  | "cancelled_by_authority";

export type SubletDeclineReasonCode =
  | "PROPERTY_UNSUITABLE_FOR_ARRANGEMENT"
  | "PROPOSED_OCCUPANT_NOT_ELIGIBLE"
  | "INSUFFICIENT_INFORMATION_PROVIDED"
  | "TERMS_NOT_ACCEPTABLE"
  | "POLICY_OR_JURISDICTION_RESTRICTION"
  | "AUTHORITY_OR_OWNERSHIP_CONCERN"
  | "OTHER";

export type SubletArrangementType =
  | "ASSIGNMENT_FULL"
  | "REPLACEMENT_OCCUPANT"
  | "SUBLEASE_PARTIAL"
  | "ADD_CO_TENANT"
  | "LODGER_OR_LICENSEE"
  | "ADDITIONAL_OCCUPANT";

export interface SubletRequest {
  id: number;
  currentOccupancyId: number;
  proposedRenterPartyId: number | null;
  status: SubletRequestStatus;
  authorityEvidenceRef: string;
  adminDecision: string;
  adminNotes: string;
  decidedByAdminId: number | null;
  decidedByUserId: number | null;
  createdAt: string;
  decidedAt: string | null;
  infoRequestNote: string;
  infoRequestedAt: string | null;
  infoResponseNote: string;
  infoRespondedAt: string | null;
  infoRequestedDocumentTypes: string[];
  infoRequestDueAt: string | null;
  proposedStartDate: string | null;
  proposedEndDate: string | null;
  approvalConditions: string;
  approvalConditionList: string[];
  approvedWithAuthorityConfirmation: boolean;
  approvalExpiresAt: string | null;
  withdrawnAt: string | null;
  reason: string;
  arrangementType: SubletArrangementType;
  listingName: string;
  listingCity: string;
  roomType: string;
  guests: number;
  bedrooms: number;
  bathrooms: number;
  currentTenantName: string;
  proposedRenterName: string;
  version: number;
  declineReasonCode: string;
  supersededBySubletRequestId: number | null;
  expiredAt: string | null;
  cancelledByAuthorityAt: string | null;
  cancelledByAuthorityAdminId: number | null;
  cancelledByAuthorityReason: string;
  // Only set for SUBLEASE_PARTIAL/ADD_CO_TENANT/LODGER_OR_LICENSEE -- the
  // co-tenant's own new agreement. When sublet.signatureMode is
  // E_SIGNATURE for this jurisdiction, it's left unsigned until both
  // parties actually sign it (see signOwnAgreement/signHostedAgreement).
  newAgreementId: number | null;
}

export interface SubletChronologyEvent {
  timestamp: string;
  eventType: string;
  summary: string;
}

export interface SubletRenterLookup {
  found: boolean;
  partyId: number | null;
  name: string | null;
  identityVerified: boolean;
}

/** A listing owned by a user's party rather than an admin -- `ownerId` is always null
 *  for these, ownership is carried by the backend's `party_id` column instead. */
export interface HostedListing extends Omit<Listing, "ownerId"> {
  ownerId: number | null;
}

/** The unauthenticated browse view returned by /api/public/listings. */
export interface PublicListing {
  id: string;
  slug: string;
  name: string;
  propertyType: PropertyType;
  roomType: string;
  city: string;
  location: string;
  latitude?: number | null;
  longitude?: number | null;
  pricePerNight: number;
  currency: string;
  rating: number;
  reviewCount: number;
  guests: number;
  bedrooms: number;
  bathrooms: number;
  size: number;
  images: string[];
  amenities: string[];
  tags: string[];
  description: string;
  featured?: boolean;
  roomId: number | null;
  minStayNights: number;
  // Deliberately no ownerEmail/ownerPhone -- the public endpoint never returns a
  // host's contact details to an unauthenticated caller.
  ownerName: string;
}

export interface PublicListingsPage {
  items: PublicListing[];
  limit: number;
  offset: number;
  total: number;
  hasMore: boolean;
}

// --- Notifications (shared shape for both /api/notifications (admin) and
// /api/users/notifications (user) -- each endpoint only ever returns the
// authenticated caller's own rows). ---

export type NotificationCategory = "PAYMENTS" | "LEASING" | "OCCUPANCY" | "DISPUTES_AND_SAFETY";
export type NotificationPriority = "NORMAL" | "HIGH";

export interface AppNotification {
  id: number;
  title: string;
  message: string;
  notificationType: string;
  relatedEntityType: string;
  relatedEntityId: string;
  category: NotificationCategory;
  priority: NotificationPriority;
  isRead: boolean;
  createdAt: string;
  readAt: string | null;
}

// Section 11 gap: category opt-out + quiet hours -- DISPUTES_AND_SAFETY is
// deliberately not in NOTIFICATION_OPTABLE_CATEGORIES (backend/app/models/
// notification.py) and is filtered out server-side if sent anyway.
export interface NotificationPreference {
  optedOutCategories: NotificationCategory[];
  quietHoursEnabled: boolean;
  quietHoursStartMinute: number;
  quietHoursEndMinute: number;
}

// --- Verification (ZR-ENG-CLR-012) ---

export type OccupancyEligibilityMethod = "DIGITAL_SHARE_CODE" | "MANUAL_DOCUMENT_CHECK";
export type OccupancyEligibilityStatus =
  | "IN_PROGRESS"
  | "PASS"
  | "INCONCLUSIVE"
  | "TECHNICAL_ERROR"
  | "FAIL_INELIGIBLE"
  | "FRAUD_REVIEW"
  | "EXPIRED"
  | "WAIVED_POLICY"
  | "SUSPENDED";

export interface OccupancyEligibilityCheck {
  id: number;
  partyId: number;
  jurisdictionCode: string;
  method: OccupancyEligibilityMethod;
  shareCode: string;
  evidenceRef: string;
  status: OccupancyEligibilityStatus;
  reasonNote: string;
  checkedByAdminId: number | null;
  checkedAt: string | null;
  followUpDueAt: string | null;
  createdAt: string;
}

export type PropertyComplianceCredentialStatus = "UNDER_REVIEW" | "VALID" | "EXPIRING" | "EXPIRED" | "REVOKED" | "SUSPENDED";

export interface PropertyComplianceCredential {
  id: number;
  roomId: number;
  requirementCode: string;
  status: PropertyComplianceCredentialStatus;
  issuerSource: string;
  evidenceRef: string;
  method: string;
  jurisdictionCode: string;
  validFrom: string;
  expiresAt: string | null;
  revokedAt: string | null;
  revokedReason: string;
  createdAt: string;
}

export interface RenterVerificationStatusItem {
  requirementCode: string;
  status: string;
  expiresAt: string | null;
  jurisdictionCode: string;
  explanation: string;
  sharingScope: string;
  retentionNote: string;
  alternativeMethodNote: string;
}

export interface RenterVerificationStatus {
  identity: RenterVerificationStatusItem;
  occupancyEligibility: RenterVerificationStatusItem[];
  // Lister, Property & Authority Verification wireframe: separate claims from
  // identity above -- one item per room the calling user hosts (empty for a
  // renter with no hosted rooms). Never implies identity verification proves
  // either of these.
  propertyVerification: RenterVerificationStatusItem[];
  authorityToList: RenterVerificationStatusItem[];
}

export type PropertyVerificationStatus = "pending" | "verified" | "rejected" | "additional_evidence_required" | "revoked";

export interface PropertyVerification {
  id: number;
  partyId: number;
  roomId: number;
  evidenceRef: string;
  status: PropertyVerificationStatus;
  verifierAdminId: number | null;
  verifierNotes: string;
  verifiedAt: string | null;
  expiresAt: string | null;
  createdAt: string;
}

export type ScreeningDecisionStatus = "AUTHORIZED" | "PASS" | "FAIL" | "INCONCLUSIVE" | "DISPUTED_SOURCE";

export type AmendmentStatus =
  | "REQUESTED"
  | "CLASSIFIED"
  | "TERMS_PROPOSED"
  | "APPROVALS_PENDING"
  | "GENERATED"
  | "EXECUTION_PENDING"
  | "EXECUTED"
  | "EFFECTIVE";

export type AmendmentType = "MATERIAL_CHANGE" | "ADDENDUM" | "ASSIGNMENT_NOVATION" | "RESTATED_AGREEMENT" | "RENEWAL" | "CORRECTION";

export interface AgreementAmendment {
  id: number;
  agreementId: number;
  sourceVersionId: number;
  resultingVersionId: number | null;
  amendmentType: AmendmentType | null;
  status: AmendmentStatus;
  reason: string;
  proposedTerms: Record<string, unknown>;
  proposedGuarantor: { legalName?: string; contactEmail?: string };
  requestedByAdminId: number;
  createdAt: string;
  classifiedAt: string | null;
  termsProposedAt: string | null;
  approvalsPendingAt: string | null;
  generatedAt: string | null;
  executionPendingAt: string | null;
  executedAt: string | null;
  effectiveAt: string | null;
}

export interface AgreementParty {
  id: number;
  agreementId: number;
  role: string;
  legalName: string;
  contactEmail: string;
  partyId: number | null;
  consentMethod: string;
  consentEvidenceRef: string;
  consentedAt: string | null;
}

export interface MarketPolicyPack {
  id: number;
  jurisdictionCode: string;
  version: number;
  effectiveFrom: string;
  effectiveTo: string | null;
  confidence: "VERIFIED" | "REVIEW_REQUIRED" | "DEPRECATED" | "EMERGENCY_BLOCK";
  legalSourceNote: string;
  depositInstrumentAllowed: string;
  depositMaxRentMultiple: number;
  depositCustodyModel: string;
  depositProtectionDeadlineDays: number | null;
  depositReleaseDeadlineDays: number;
  subletConsentStandard: string;
  subletConsentResponseDays: number;
  subletMaxRentMultipleOfOriginal: number;
  subletAssignmentPayeeModel: string;
  subletSubleasePayeeModel: string;
  rentChangeMinIntervalDays: number;
  occupancyEligibilityRequired: boolean;
  occupancyEligibilityMethodNote: string;
  occupancyEligibilityFollowUpDays: number | null;
  identityEvidenceRetentionDays: number;
  requiredPropertyComplianceCodes: string[];
  identityRequiredAtApplication: boolean;
  screeningProhibitedCheckTypes: string[];
  platformFeeRate: number;
  fundsFlowProfile: string;
  permittedPaymentMethodClasses: string[];
  zoikoLegalEntityName: string;
  zoikoTaxRegistrationNumber: string;
  serviceFeeTaxRate: number;
  terminationNoticeDays: number;
  alignTerminationToRentCycle: boolean;
  terminationLiabilityModel: string;
  terminationBreakFeeRentMultiple: number;
  terminationLiabilityCapRentMultiple: number | null;
  disputeDepositAuthorityClass: string;
  disputeBookingAgreementAuthorityClass: string;
  disputePropertyConditionAuthorityClass: string;
  disputeSubletOccupancyAuthorityClass: string;
  disputeResponseWindowDays: number;
  disputeEvidenceWindowDays: number;
  disputeExternalFilingDeadlineDays: number | null;
  disputeConciliationRequirement: string;
  disputeNonWaivableClaimFamilies: string[];
  createdAt: string;
}

export interface ScreeningCheck {
  id: number;
  partyId: number;
  jurisdictionCode: string;
  checkType: string;
  providerName: string;
  permissiblePurpose: string;
  hostPolicyCriteria: string;
  providerResultSummary: string;
  decisionStatus: ScreeningDecisionStatus;
  decisionReason: string;
  adverseActionNoticeSentAt: string | null;
  reviewedByAdminId: number | null;
  reviewedAt: string | null;
  createdAt: string;
  disputeReason: string;
  disputedAt: string | null;
}

// --- Rental Transaction Record ---
// A computed, read-only composite over existing authoritative records
// (Occupancy is the root) -- see backend/app/schemas/rental_transaction_record.py.
// Composed from the interfaces above wherever one already exists, rather
// than redeclaring their fields.

export interface ActivationDecision {
  id: number;
  occupancyId: number;
  decisionVersion: number;
  gateRuleVersion: number;
  outcome: string;
  reasonCodes: string[];
  checks: Record<string, unknown>;
  trigger: string;
  evaluatingAdminId: number | null;
  correlationId: string;
  evaluatedAt: string;
}

export interface TerminationRecord {
  id: number;
  occupancyId: number;
  agreementId: number;
  basis: string;
  noticeGivenAt: string | null;
  liabilityEndDate: string | null;
  terminationEffectiveDate: string | null;
  physicalMoveOutDate: string | null;
  createdAt: string;
}

export interface RentalTransactionTimelineEntry {
  timestamp: string;
  source: string;
  eventType: string;
  detail: Record<string, unknown>;
}

export interface RentalTransactionRecord {
  occupancy: Occupancy;
  application: Application | null;
  amendments: AgreementAmendment[];
  obligations: ObligationRead[];
  payments: SimulatedPayment[];
  deposit: DepositRecord | null;
  /** ZR-PAY-002's own record/evidence-layer obligations (never money-moving),
   *  kept alongside `obligations` above rather than replacing it. */
  rentalPaymentObligations: RentalPaymentObligation[];
  handoverEvents: HandoverEvent[];
  activationDecisions: ActivationDecision[];
  subletRequests: SubletRequest[];
  terminationCases: TerminationCase[];
  terminationRecord: TerminationRecord | null;
  propertyVerification: RenterVerificationStatusItem | null;
  authorityToList: RenterVerificationStatusItem | null;
  identityVerification: RenterVerificationStatusItem | null;
  timeline: RentalTransactionTimelineEntry[];
}
