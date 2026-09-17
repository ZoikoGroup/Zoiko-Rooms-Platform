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
}

export interface PublishEligibility {
  eligible: boolean;
  reasons: string[];
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
  | "review_required";

export interface AuthorityRecord {
  id: number;
  partyId: number;
  roomId: number;
  authorityType: string;
  evidenceRef: string;
  verifiedAt: string | null;
  expiresAt: string | null;
  status: AuthorityStatus;
  createdAt: string;
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
  decidedByAdminId: number;
  decidedAt: string;
}

export type OfferStatus = "DRAFT" | "SENT" | "ACCEPTED" | "DECLINED" | "EXPIRED" | "WITHDRAWN";

export interface OfferTermsRecord {
  id: number;
  version: number;
  monthlyRent: number;
  depositAmount: number;
  startDate: string;
  termMonths: number;
  createdAt: string;
}

export type AgreementStatus = "DRAFT" | "SENT" | "SIGNED" | "VOID";

export interface Agreement {
  id: number;
  offerId: number;
  version: number;
  status: AgreementStatus;
  contentRef: string;
  signedByProviderAt: string | null;
  signedByRenterAt: string | null;
  signatureRef: string;
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
}

export interface Application {
  id: number;
  listingId: string;
  listingName: string;
  guestId: string;
  guestName: string;
  guestEmail: string;
  status: ApplicationStatus;
  message: string;
  desiredMoveIn: string | null;
  submittedAt: string;
  updatedAt: string;
  decisions: ApplicationDecisionRecord[];
  offer: Offer | null;
}

// --- Occupancy ---

export type OccupancyStatus = "PENDING_MOVE_IN" | "ACTIVE" | "ENDED";

export interface Occupancy {
  id: number;
  offerId: number;
  listingId: string;
  roomId: number;
  guestId: string;
  guestName: string;
  status: OccupancyStatus;
  moveInDate: string | null;
  expectedEndDate: string | null;
  moveOutDate: string | null;
  createdAt: string;
  endedAt: string | null;
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

export type DepositStatus = "HELD" | "RELEASED" | "FORFEITED" | "PARTIALLY_RELEASED";

export interface DepositRecord {
  id: number;
  obligationId: number;
  status: DepositStatus;
  heldAmount: number;
  releasedAmount: number;
  releasedAt: string | null;
  notes: string;
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
}

export type SubletRequestStatus =
  | "pending_verification"
  | "pending_admin_review"
  | "approved"
  | "rejected";

export interface SubletRequest {
  id: number;
  currentOccupancyId: number;
  proposedRenterPartyId: number;
  status: SubletRequestStatus;
  authorityEvidenceRef: string;
  adminDecision: string;
  adminNotes: string;
  decidedByAdminId: number | null;
  createdAt: string;
  decidedAt: string | null;
  listingName: string;
  listingCity: string;
  roomType: string;
  guests: number;
  bedrooms: number;
  bathrooms: number;
  currentTenantName: string;
  proposedRenterName: string;
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

export interface AppNotification {
  id: number;
  title: string;
  message: string;
  notificationType: string;
  relatedEntityType: string;
  relatedEntityId: string;
  isRead: boolean;
  createdAt: string;
  readAt: string | null;
}
