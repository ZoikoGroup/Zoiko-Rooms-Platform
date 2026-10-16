"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowRight,
  Building2,
  Calendar,
  Check,
  ChevronRight,
  ClipboardList,
  Clock,
  CreditCard,
  DoorOpen,
  Download,
  ExternalLink,
  Eye,
  FileText,
  Home,
  Info,
  Key,
  MessageSquare,
  Plus,
  Repeat,
  Search,
  Shield,
  ShieldCheck,
  Upload,
  UserCheck,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { ApiError } from "@/lib/api-client";
import {
  HabitabilityIncident,
  HabitabilitySeverity,
  HostRefund,
  AutopayMandate,
  BookingChangeRequest,
  ConditionRating,
  ConditionReportItem,
  ConditionReportType,
  PaymentPreview,
  PublicListing,
  RentalPaymentObligation,
  SubletArrangementType,
  SubletRequest,
  SubletRenterLookup,
  TerminationCase,
  TerminationCasePreview,
  UserOccupancy,
  DisputeCaseRead,
} from "@/lib/types";
import {
  CO_TENANCY_ARRANGEMENT_TYPES,
  RENTER_TERMINATION_CAUSE_CODES,
  subletArrangementTypeLabel,
  terminationCauseCodeLabel,
} from "@/lib/status";
import { addMonths, formatCurrency, formatDate } from "@/lib/utils";
import { matchRentalPaymentObligation } from "@/lib/rentalPaymentMatch";
import { SingleObligationPaymentCard } from "@/components/user/RentalPaymentsManager";
import {
  addOwnConditionReportItem,
  cancelOwnBookingBeforeMoveIn,
  confirmHandoverReceipt,
  reviewPaymentRecipient,
  confirmMoveOutReady,
  errorMessage,
  getObligationAvailableMethods,
  getOwnAgreementPaymentPreview,
  getOwnRefundEntitlement,
  getOwnSubletTerminology,
  giveMoveOutNotice,
  listMyAutopayMandates,
  listMyChangeRequests,
  listMyRentalPaymentObligations,
  listOccupancies,
  listOwnConditionReport,
  listOwnTerminationCases,
  listPublicListings,
  listSubletRequests,
  lookupSubletRenter,
  previewOwnTermination,
  requestOwnTermination,
  renterTermination,
  listMyRefunds,
  listMyHabitabilityIssues,
  reportHabitabilityIssue,
  confirmHabitabilityFixed,
  rejectHabitabilityFix,
  confirmRefundReceived,
  reportRefundNotReceived,
  renterProceedDespiteDispute,
  acceptOwnMutualSurrender,
  declineOwnMutualSurrender,
  submitPremisesChangeRequest,
  submitSubletRequest,
  tenantAgreementPdfUrl,
  withdrawOwnTerminationCase,
  renterDisputes,
} from "@/lib/user-api";
import { DisputeCasesSection } from "@/components/user/DisputeCasePanel";
import { useToast } from "@/components/user/ui";
import { DepositClaimsSection } from "@/components/user/DepositClaimsSection";
import { RentalTransactionRecord } from "@/components/user/RentalTransactionRecord";
import { InstructionsModalView } from "@/components/user/RentalPaymentsManager";
import { Modal } from "@/components/ui/Modal";

type DrawerTab =
  | "movein"
  | "overview"
  | "agreement"
  | "payments"
  | "deposit"
  | "sublet"
  | "notices"
  | "documents"
  | "disputes"
  | "record";

interface RentalCardItem {
  occupancy: UserOccupancy;
  id: string; // e.g. 'ZR-R-10491'
  p: string; // Property Name
  room: string; // Room #7
  type: string; // Studio / En-suite
  city: string; // Hyderabad
  from: string; // Nov 1, 2026
  to: string; // Oct 31, 2027
  start: string; // YYYY-MM-DD
  end: string; // YYYY-MM-DD
  rent: string; // ₹26,000
  rentAmount: number;
  currency: string;
  agr: string; // ZR-AGR-28542 · v1
  agrSt: string; // Executed / Executed / Effective
  st: "upcoming" | "active" | "notice" | "ending" | "ended" | "cancelled";
  ms: string; // Next milestone text
  cta: string; // Review Move-in / Manage Rental / View Rental Record
  ctaK: "movein" | "open";
  action: string | null;
  owner: string;
  agent: string;
  recipient: string;
  payNext: string;
  payStatus: string;
  depositAmt: string;
  depositStatus: string;
  depositHolder: string;
  hasSublet: boolean;
  subletText: string;
}

// An active rental is labelled "Ending soon" only in its final month.
const ENDING_SOON_DAYS = 30;
const PAID_OBLIGATION_STATUSES = new Set(["PAYER_RECORDED", "RECIPIENT_CONFIRMATION_PENDING", "CONFIRMED", "PARTIALLY_PAID"]);

const ISSUE_SEVERITY: Record<HabitabilitySeverity, string> = {
  H0: "Minor — annoying but livable",
  H1: "Affects normal use",
  H2: "Serious — part of the room unusable",
  H3: "Unsafe or unlivable",
};

export function RentalsManager() {
  const router = useRouter();
  // Captured once per mount so render stays pure (sublet nights-remaining check).
  const [nowMs] = useState(() => Date.now());
  const { toast, showToast } = useToast();

  const [mounted, setMounted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [occupancies, setOccupancies] = useState<UserOccupancy[]>([]);
  const [changeRequests, setChangeRequests] = useState<BookingChangeRequest[]>([]);
  const [autopayMandates, setAutopayMandates] = useState<AutopayMandate[]>([]);
  const [paymentPreviews, setPaymentPreviews] = useState<Record<number, PaymentPreview>>({});
  const [rentalPaymentObligations, setRentalPaymentObligations] = useState<Record<number, RentalPaymentObligation[]>>({});
  const [terminationCases, setTerminationCases] = useState<Record<number, TerminationCase>>({});
  // Dispute cases the renter is a party to -- including ones the host opened
  // (e.g. a payment the host says never arrived).
  const [disputeCases, setDisputeCases] = useState<DisputeCaseRead[]>([]);
  const [refunds, setRefunds] = useState<HostRefund[]>([]);
  const [issues, setIssues] = useState<HabitabilityIncident[]>([]);
  const [issueSeverity, setIssueSeverity] = useState<HabitabilitySeverity>("H1");
  const [issueText, setIssueText] = useState("");
  const [publicListings, setPublicListings] = useState<PublicListing[]>([]);
  const [subletRequests, setSubletRequests] = useState<SubletRequest[]>([]);

  // User review state for payment recipient per occupancy (persisted in session)
  const [receiptConfirmedIds, setReceiptConfirmedIds] = useState<Set<number>>(new Set());

  // Filters & Tabs
  const [searchQuery, setSearchQuery] = useState("");
  const [activeCard, setActiveCard] = useState<"" | "upcoming" | "active" | "ending" | "action">("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [locFilter, setLocFilter] = useState("all");
  const [dateFilter, setDateFilter] = useState("all");
  // Captured once so the date filters below stay pure during render.
  const [now] = useState(() => Date.now());
  const [subletFilter, setSubletFilter] = useState("all");
  const [sortFilter, setSortFilter] = useState<"action" | "start" | "end">("action");
  const [viewTab, setViewTab] = useState<"current" | "history">("current");
  const [mobileFilterOpen, setMobileFilterOpen] = useState(false);

  // Drawer state
  const [selectedOccupancy, setSelectedOccupancy] = useState<UserOccupancy | null>(null);
  const [drawerTab, setDrawerTab] = useState<DrawerTab>("overview");
  const drawerRef = useRef<HTMLDivElement>(null);

  // Modals state
  const [activeModal, setActiveModal] = useState<
    | null
    | "recip"
    | "instr"
    | "early"
    | "sublet"
    | "condition"
    | "cancel"
  >(null);
  const [modalOccupancy, setModalOccupancy] = useState<UserOccupancy | null>(null);

  // Sublet Request Modal state
  const [subletStep, setSubletStep] = useState<1 | 2 | 3>(1);
  const [proposedEmail, setProposedEmail] = useState("");
  const [lookupResult, setLookupResult] = useState<SubletRenterLookup | null>(null);
  const [lookingUp, setLookingUp] = useState(false);
  const [evidenceRef, setEvidenceRef] = useState("");
  const [subletReason, setSubletReason] = useState("");
  const [arrangementType, setArrangementType] = useState<SubletArrangementType>("ASSIGNMENT_FULL");
  const [proposedMonthlyRent, setProposedMonthlyRent] = useState("");
  const [subletStartDate, setSubletStartDate] = useState("");
  const [subletEndDate, setSubletEndDate] = useState("");
  const [confirmNoPermissionYet, setConfirmNoPermissionYet] = useState(false);
  const [subletSubmitting, setSubletSubmitting] = useState(false);
  const [subletError, setSubletError] = useState("");

  // Condition Report Modal state
  const [conditionItems, setConditionItems] = useState<ConditionReportItem[]>([]);
  const [conditionReportType, setConditionReportType] = useState<ConditionReportType>("MOVE_IN");
  const [conditionArea, setConditionArea] = useState("");
  const [conditionRating, setConditionRating] = useState<ConditionRating | "">("");
  const [conditionNotes, setConditionNotes] = useState("");
  const [conditionFile, setConditionFile] = useState<File | null>(null);
  const [conditionSubmitting, setConditionSubmitting] = useState(false);
  const [conditionError, setConditionError] = useState("");

  // Move-out / Notice state
  const [earlyEndDate, setEarlyEndDate] = useState("");
  const [earlyEndReason, setEarlyEndReason] = useState("");
  const [earlyEndSubmitting, setEarlyEndSubmitting] = useState(false);
  // "endOfTerm" = leaving when the term ends (move-out notice);
  // "notice" = end early with the notice the rules require;
  // "agree" = propose a date the host (and any co-tenant) must agree to.
  const [earlyEndPath, setEarlyEndPath] = useState<"endOfTerm" | "notice" | "agree">("notice");
  const [caseBusy, setCaseBusy] = useState(false);
  const [counterDate, setCounterDate] = useState("");
  const [orderRef, setOrderRef] = useState("");
  const [orderOutcome, setOrderOutcome] = useState<"ENDS_ON_DATE" | "TENANCY_CONTINUES">("ENDS_ON_DATE");
  const [orderDate, setOrderDate] = useState("");

  // Pre-move-in Cancellation state
  const [cancelReason, setCancelReason] = useState("");
  const [cancelSubmitting, setCancelSubmitting] = useState(false);
  const [cancelError, setCancelError] = useState("");

  useEffect(() => {
    setMounted(true);
  }, []);

  // Lock scroll when drawer or modal is active
  useEffect(() => {
    if (selectedOccupancy || activeModal) {
      const prev = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      return () => {
        document.body.style.overflow = prev;
      };
    }
  }, [selectedOccupancy, activeModal]);

  // Handle escape key
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (activeModal) setActiveModal(null);
        else if (selectedOccupancy) setSelectedOccupancy(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [activeModal, selectedOccupancy]);

  const loadRentalPaymentObligationsFor = useCallback((agreementId: number, occupancyId: number) => {
    Promise.all([
      listMyRentalPaymentObligations(undefined, { agreementId, limit: 100 }),
      listMyRentalPaymentObligations(undefined, { occupancyId, limit: 100 }),
    ])
      .then(([agreementPage, occupancyPage]) => {
        // The initial rent/deposit carry the agreement id, later rent the
        // occupancy id; some carry both, so merge without duplicates.
        const byId = new Map([...agreementPage.items, ...occupancyPage.items].map((o) => [o.id, o]));
        setRentalPaymentObligations((prev) => ({
          ...prev,
          [agreementId]: [...byId.values()],
        }));
      })
      .catch(() => {});
  }, []);

  const load = useCallback(async () => {
    try {
      const [occupanciesData, changeRequestsData, mandatesData, publicListingsData, subletRequestsData] = await Promise.all([
        listOccupancies(),
        listMyChangeRequests(),
        listMyAutopayMandates().catch(() => []),
        listPublicListings({ limit: 100 }).then((p) => p.items).catch(() => []),
        listSubletRequests().catch(() => []),
      ]);
      setOccupancies(occupanciesData);
      setChangeRequests(changeRequestsData);
      setAutopayMandates(mandatesData);
      setPublicListings(publicListingsData);
      setSubletRequests(subletRequestsData);
      renterDisputes.listCases().then(setDisputeCases).catch(() => {});
      listMyRefunds().then(setRefunds).catch(() => {});

      const active = occupanciesData.filter((o) => o.agreementId);
      await Promise.all(
        active.map(async (occupancy) => {
          const agreementId = occupancy.agreementId!;
          loadRentalPaymentObligationsFor(agreementId, occupancy.id);
          try {
            const preview = await getOwnAgreementPaymentPreview(agreementId);
            setPaymentPreviews((prev) => ({ ...prev, [agreementId]: preview }));
          } catch {}

          try {
            const cases = await listOwnTerminationCases(occupancy.id);
            const openCase =
              cases.find((c) => !["TERMINATED", "WITHDRAWN", "REJECTED_PATHWAY", "SURRENDER_DECLINED"].includes(c.status)) ?? cases[0];
            if (openCase) {
              setTerminationCases((prev) => ({ ...prev, [occupancy.id]: openCase }));
            }
          } catch {}
        }),
      );
    } catch (err) {
      showToast(errorMessage(err, "Could not load your rentals."), "error");
    } finally {
      setLoading(false);
    }
  }, [loadRentalPaymentObligationsFor, showToast]);

  useEffect(() => {
    load();
  }, [load]);

  // Transform occupancies into rich Figma-accurate rental cards
  const isRecipientReviewed = useCallback(
    (occ: UserOccupancy) =>
      Boolean(occ.paymentRecipientReviewedAt) ||
      (occ.agreementId !== null &&
        (rentalPaymentObligations[occ.agreementId] ?? []).some((o) => PAID_OBLIGATION_STATUSES.has(o.status))),
    [rentalPaymentObligations]
  );

  const rentalItems = useMemo<RentalCardItem[]>(() => {
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    return occupancies.map((occ) => {
      const listing = publicListings.find((l) => l.id === occ.listingId);
      const preview = occ.agreementId ? paymentPreviews[occ.agreementId] : null;

      const propertyName =
        occ.listingName ||
        listing?.name ||
        occ.propertyAddress ||
        `Listing #${occ.listingId}`;
      const roomLabel = `Room #${occ.roomId}`;
      const roomType = listing?.roomType || "Private Room";
      const city = occ.propertyCity || listing?.city || "Hyderabad";

      const startDateIso = occ.moveInDate || occ.leaseStartDate;
      const fromStr = startDateIso ? formatDate(startDateIso) : "—";
      const toStr = occ.expectedEndDate ? formatDate(occ.expectedEndDate) : "—";
      const startIso = startDateIso || occ.createdAt.split("T")[0] || "";
      const endIso = occ.expectedEndDate || occ.moveOutDate || occ.endedAt?.split("T")[0] || "";

      // Determine rent amount
      const rentObl = preview?.amountDueNow.find((o) => o.obligationType === "RENT");
      const rentAmt = occ.monthlyRent || rentObl?.amount || (listing?.pricePerNight ? listing.pricePerNight * 30 : 0);
      const currency = occ.currency || listing?.currency || "INR";
      const rentFormatted = rentAmt > 0 ? formatCurrency(rentAmt, currency) : "₹—";

      // Determine agreement string & status
      const agrCode = occ.agreementId ? `ZR-AGR-${occ.agreementId} · v1` : "ZR-AGR-Draft";
      const agrStatus = occ.agreementId ? (occ.status === "ACTIVE" ? "Executed / Effective" : "Executed") : "Pending";

      // Determine timeline & milestone
      const endDate = occ.expectedEndDate ? new Date(occ.expectedEndDate) : null;
      const isPastEnd = endDate ? endDate < today : false;
      const daysUntilEnd = endDate ? Math.round((endDate.getTime() - today.getTime()) / 86400000) : null;

      let st: RentalCardItem["st"] = "active";
      if (occ.status === "ENDED") st = "ended";
      else if (occ.status === "CANCELLED") st = "cancelled";
      // Upcoming means move-in not yet confirmed; once the host confirms, the
      // backend status is ACTIVE regardless of how the date parses locally.
      else if (occ.status === "PENDING_MOVE_IN") st = "upcoming";
      else if (occ.moveOutDate || terminationCases[occ.id]) st = "notice";
      else if (daysUntilEnd !== null && daysUntilEnd <= ENDING_SOON_DAYS && daysUntilEnd >= 0) st = "ending";

      let milestone = "Agreement in effect";
      if (st === "upcoming") milestone = occ.moveInDate ? `Move-in ${formatDate(occ.moveInDate)}` : "Pending move-in";
      else if (st === "notice") milestone = `Notice given · Moving out ${occ.moveOutDate ? formatDate(occ.moveOutDate) : toStr}`;
      else if (st === "ending") milestone = `Agreement ends ${toStr}`;
      else if (st === "active") milestone = `Agreement ends ${toStr}`;
      else if (st === "ended") milestone = "Deposit disposition recorded";
      else if (st === "cancelled") milestone = "Cancelled before start";

      // Check if action required
      const recipientReviewed = isRecipientReviewed(occ);
      const receiptConfirmed = receiptConfirmedIds.has(occ.id) || (occ.handoverEventTypes ?? []).includes("RENTER_RECEIPT");
      const possessionDelivered = (occ.handoverEventTypes ?? []).includes("POSSESSION_DELIVERED");
      let action: string | null = null;

      if (occ.status === "PENDING_MOVE_IN") {
        if (!recipientReviewed) {
          action = `${propertyName} · ${roomLabel} has one move-in item outstanding: review the verified payment recipient before your first payment.`;
        } else if (possessionDelivered && !receiptConfirmed) {
          action = `${propertyName} · ${roomLabel}: Confirm key and room receipt once delivered by host.`;
        }
      }

      const ctaLabel = action ? "Review Move-in" : (st === "ended" || st === "cancelled" ? "View Rental Record" : "Manage Rental");
      const ctaKind = action ? "movein" : "open";

      // Host / Owner info
      const owner = occ.hostName || listing?.ownerName || "Verified Property Host";
      const agent = "Zoiko Partner Management";
      const recipient = occ.hostName || listing?.ownerName || "Verified Property Host";

      // Payment info
      const dueObl = preview?.amountDueNow.find((o) => o.status !== "PAID");
      const payNext = dueObl
        ? `${formatCurrency(dueObl.amount, dueObl.currency)} due ${formatDate(dueObl.dueDate)}`
        : "No outstanding obligations";
      // Status from the real payment records, not local UI state.
      const rentalObligations = occ.agreementId !== null ? rentalPaymentObligations[occ.agreementId] ?? [] : [];
      const payStatus = rentalObligations.some((o) => ["PAYER_RECORDED", "RECIPIENT_CONFIRMATION_PENDING"].includes(o.status))
        ? "Marked paid by you · awaiting recipient confirmation"
        : rentalObligations.some((o) => o.status === "OVERDUE")
        ? "Overdue"
        : dueObl ? "Due soon" : "Up to date";

      // Deposit info
      const depObl = preview?.amountDueNow.find((o) => o.obligationType === "DEPOSIT");
      const depAmt = depObl?.amount ? formatCurrency(depObl.amount, depObl.currency) : "Standard Security Deposit";
      const depStatus = depObl?.status === "PAID" ? "Confirmed received" : "Due on agreement execution";

      // Sublet status
      const activeSublet = subletRequests.find(
        (s) => s.currentOccupancyId === occ.id && s.status !== "withdrawn" && s.status !== "expired" && s.status !== "rejected"
      );
      const hasSublet = !!activeSublet || !!occ.reassignedViaSubletRequestId;
      const subletText = activeSublet
        ? `Sublet request (${subletArrangementTypeLabel[activeSublet.arrangementType] || activeSublet.arrangementType}) under host review`
        : occ.reassignedViaSubletRequestId
        ? "Occupancy reassigned via sublet"
        : "No active sublet request";

      return {
        occupancy: occ,
        id: `ZR-R-${occ.id}`,
        p: propertyName,
        room: roomLabel,
        type: roomType,
        city,
        from: fromStr,
        to: toStr,
        start: startIso,
        end: endIso,
        rent: rentFormatted,
        rentAmount: rentAmt,
        currency,
        agr: agrCode,
        agrSt: agrStatus,
        st,
        ms: milestone,
        cta: ctaLabel,
        ctaK: ctaKind,
        action,
        owner,
        agent,
        recipient,
        payNext,
        payStatus,
        depositAmt: depAmt,
        depositStatus: depStatus,
        depositHolder: `Landlord (${recipient})`,
        hasSublet,
        subletText,
      };
    });
  }, [occupancies, publicListings, paymentPreviews, terminationCases, isRecipientReviewed, receiptConfirmedIds, rentalPaymentObligations, subletRequests]);

  // Segregate into Current and History
  const currentRentals = useMemo(
    () => rentalItems.filter((x) => x.st !== "ended" && x.st !== "cancelled"),
    [rentalItems]
  );
  const historyRentals = useMemo(
    () => rentalItems.filter((x) => x.st === "ended" || x.st === "cancelled"),
    [rentalItems]
  );

  // Compute 4 Summary Metric Counts
  const counts = useMemo(() => {
    let upcoming = 0;
    let active = 0;
    let ending = 0;
    let action = 0;

    currentRentals.forEach((x) => {
      if (x.st === "upcoming") upcoming++;
      if (x.st === "active") active++;
      if (x.st === "ending" || x.st === "notice") ending++;
      if (x.action) action++;
    });

    return { upcoming, active, ending, action };
  }, [currentRentals]);

  // Action strip banner items
  const attentionItems = useMemo(
    () => currentRentals.filter((x) => x.action),
    [currentRentals]
  );

  // Unique locations for dropdown
  const uniqueLocations = useMemo(() => {
    const locs = new Set<string>();
    rentalItems.forEach((x) => {
      if (x.p) locs.add(x.p);
    });
    return Array.from(locs);
  }, [rentalItems]);

  // Apply filters & search & sort
  const filteredRentals = useMemo(() => {
    const baseList = viewTab === "history" ? historyRentals : currentRentals;

    const filtered = baseList.filter((x) => {
      // 1. Metric card filter (for Current tab)
      if (viewTab === "current" && activeCard) {
        if (activeCard === "action") {
          if (!x.action) return false;
        } else if (activeCard === "ending") {
          if (x.st !== "ending" && x.st !== "notice") return false;
        } else if (x.st !== activeCard) {
          return false;
        }
      }

      // 2. Status dropdown
      if (statusFilter !== "all") {
        if (statusFilter === "ending") {
          if (x.st !== "ending" && x.st !== "notice") return false;
        } else if (x.st !== statusFilter) {
          return false;
        }
      }

      // 3. Location dropdown
      if (locFilter !== "all" && x.p !== locFilter) {
        return false;
      }

      // 4. Dates dropdown
      if (dateFilter === "starts") {
        if (!x.start) return false;
        const diffDays = (new Date(x.start).getTime() - now) / 86400000;
        if (!(diffDays >= 0 && diffDays <= 30)) return false;
      }
      if (dateFilter === "ends") {
        if (!x.end) return false;
        const diffDays = (new Date(x.end).getTime() - now) / 86400000;
        if (!(diffDays >= 0 && diffDays <= 90)) return false;
      }

      // 5. Sublet dropdown
      if (subletFilter === "yes" && !x.hasSublet) return false;
      if (subletFilter === "no" && x.hasSublet) return false;

      // 6. Search query
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase().trim();
        const hay = `${x.p} ${x.room} ${x.id} ${x.agr} ${x.city} ${x.type}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }

      return true;
    });

    // Sorter
    return filtered.sort((a, b) => {
      if (sortFilter === "start") return (a.start || "").localeCompare(b.start || "");
      if (sortFilter === "end") return (a.end || "").localeCompare(b.end || "");
      // Default: Action required first, then start date
      return (a.action ? 0 : 1) - (b.action ? 0 : 1) || (a.start || "").localeCompare(b.start || "");
    });
  }, [viewTab, currentRentals, historyRentals, activeCard, statusFilter, locFilter, dateFilter, subletFilter, searchQuery, sortFilter, now]);

  const hasActiveFilters = Boolean(
    searchQuery ||
    activeCard ||
    statusFilter !== "all" ||
    locFilter !== "all" ||
    dateFilter !== "all" ||
    subletFilter !== "all"
  );

  const clearFilters = () => {
    setSearchQuery("");
    setActiveCard("");
    setStatusFilter("all");
    setLocFilter("all");
    setDateFilter("all");
    setSubletFilter("all");
  };

  // Drawer open helper
  const handleOpenDrawer = (item: RentalCardItem, initialTab: DrawerTab = "overview") => {
    setSelectedOccupancy(item.occupancy);
    setDrawerTab(item.action ? "movein" : initialTab);
    if (drawerRef.current) {
      drawerRef.current.scrollTop = 0;
    }
  };

  // Modal Handlers
  const openRecipientReviewModal = (occ: UserOccupancy) => {
    setModalOccupancy(occ);
    setActiveModal("recip");
  };

  const openPaymentInstructionsModal = (occ: UserOccupancy) => {
    setModalOccupancy(occ);
    setActiveModal("instr");
  };

  const openEarlyEndModal = (occ: UserOccupancy) => {
    setModalOccupancy(occ);
    setEarlyEndDate("");
    setEarlyEndReason("");
    setEarlyEndPath("notice");
    setActiveModal("early");
  };

  const openSubletWizard = (occ: UserOccupancy) => {
    setModalOccupancy(occ);
    setSubletStep(1);
    setProposedEmail("");
    setLookupResult(null);
    setEvidenceRef("");
    setSubletReason("");
    setArrangementType("ASSIGNMENT_FULL");
    setProposedMonthlyRent("");
    setSubletStartDate(occ.moveInDate || "");
    setSubletEndDate(occ.expectedEndDate || "");
    setConfirmNoPermissionYet(false);
    setSubletError("");
    setActiveModal("sublet");
  };

  const openConditionModal = async (occ: UserOccupancy) => {
    setModalOccupancy(occ);
    setConditionReportType(occ.status === "PENDING_MOVE_IN" ? "MOVE_IN" : "MOVE_OUT");
    setConditionArea("");
    setConditionRating("");
    setConditionNotes("");
    setConditionFile(null);
    setConditionError("");
    setActiveModal("condition");
    try {
      const items = await listOwnConditionReport(occ.id);
      setConditionItems(items);
    } catch {
      setConditionItems([]);
    }
  };

  const openCancelBookingModal = (occ: UserOccupancy) => {
    setModalOccupancy(occ);
    setCancelReason("");
    setCancelError("");
    setActiveModal("cancel");
  };

  // Action Submit Handlers
  const handleConfirmRecipient = async () => {
    if (!modalOccupancy) return;
    try {
      const updated = await reviewPaymentRecipient(modalOccupancy.id);
      setOccupancies((prev) => prev.map((o) => (o.id === updated.id ? updated : o)));
      setActiveModal(null);
      showToast("Payment recipient reviewed. Your rental stays Upcoming until move-in.");
    } catch (err) {
      showToast(errorMessage(err, "Could not record the payment recipient review."), "error");
    }
  };

  const handleConfirmHandoverReceipt = async (occupancyId: number) => {
    try {
      await confirmHandoverReceipt(occupancyId);
      setReceiptConfirmedIds((prev) => new Set(prev).add(occupancyId));
      showToast("Key & room receipt confirmed — host can complete tenancy activation.");
    } catch (err) {
      showToast(errorMessage(err, "Could not confirm handover receipt."), "error");
    }
  };

  const handleSendEarlyEndRequest = async () => {
    if (!modalOccupancy) return;
    setEarlyEndSubmitting(true);
    try {
      if (earlyEndPath === "endOfTerm") {
        await giveMoveOutNotice(modalOccupancy.id, earlyEndReason.trim() || "Leaving at the end of the term");
        showToast("Your host knows you're leaving at the end of the term.");
      } else if (earlyEndPath === "notice") {
        const c = await requestOwnTermination(modalOccupancy.id, {
          causeCode: "RENTER_ORDINARY_EARLY_EXIT",
          notes: earlyEndReason.trim(),
        });
        showToast(
          c.status === "EFFECTIVE_DATE_SET" && c.effectiveTerminationDate
            ? `Notice given. Your tenancy ends on ${c.effectiveTerminationDate}.`
            : "Notice given. Everyone on the tenancy has been asked to confirm the date.",
        );
      } else {
        if (!earlyEndDate) {
          showToast("Pick the date you'd like to leave.", "error");
          return;
        }
        await requestOwnTermination(modalOccupancy.id, {
          causeCode: "MUTUAL_SURRENDER",
          notes: earlyEndReason.trim(),
          proposedEffectiveDate: earlyEndDate,
        });
        showToast("Date proposed. Your host has been asked to agree.");
      }
      setActiveModal(null);
      await load();
    } catch (err) {
      showToast(errorMessage(err, "Could not send early end request."), "error");
    } finally {
      setEarlyEndSubmitting(false);
    }
  };

  const handleLookupSubletEmail = async () => {
    if (!proposedEmail.trim()) {
      setSubletError("Enter the proposed tenant's email address.");
      return;
    }
    setLookingUp(true);
    setSubletError("");
    try {
      const res = await lookupSubletRenter(proposedEmail.trim());
      setLookupResult(res);
      setSubletStep(3);
    } catch (err) {
      setSubletError(errorMessage(err, "Could not look up renter account."));
    } finally {
      setLookingUp(false);
    }
  };

  const handleSubmitSubletRequest = async () => {
    if (!modalOccupancy || !lookupResult?.partyId) {
      setSubletError("Please lookup and verify the proposed replacement renter.");
      return;
    }
    setSubletSubmitting(true);
    setSubletError("");
    try {
      await submitSubletRequest(modalOccupancy.id, {
        proposedRenterPartyId: lookupResult.partyId,
        authorityEvidenceRef: evidenceRef.trim(),
        arrangementType,
        reason: subletReason.trim(),
        ...(proposedMonthlyRent ? { proposedMonthlyRent: parseFloat(proposedMonthlyRent) } : {}),
        ...(subletStartDate ? { proposedStartDate: subletStartDate } : {}),
        ...(subletEndDate ? { proposedEndDate: subletEndDate } : {}),
      });
      setActiveModal(null);
      showToast("Sublet request submitted for host review.");
      await load();
    } catch (err) {
      setSubletError(errorMessage(err, "Could not submit sublet request."));
    } finally {
      setSubletSubmitting(false);
    }
  };

  const handleAddConditionReportItem = async () => {
    if (!modalOccupancy || !conditionArea.trim()) {
      setConditionError("Please specify the area/room part inspected.");
      return;
    }
    setConditionSubmitting(true);
    setConditionError("");
    try {
      await addOwnConditionReportItem(modalOccupancy.id, {
        reportType: conditionReportType,
        area: conditionArea.trim(),
        conditionRating: conditionRating || undefined,
        notes: conditionNotes.trim(),
        file: conditionFile,
      });
      setConditionArea("");
      setConditionRating("");
      setConditionNotes("");
      setConditionFile(null);
      const items = await listOwnConditionReport(modalOccupancy.id);
      setConditionItems(items);
      showToast("Condition report item added.");
    } catch (err) {
      setConditionError(errorMessage(err, "Could not record condition item."));
    } finally {
      setConditionSubmitting(false);
    }
  };

  const handleConfirmCancelBeforeMoveIn = async () => {
    if (!modalOccupancy) return;
    setCancelSubmitting(true);
    setCancelError("");
    try {
      const res = await cancelOwnBookingBeforeMoveIn(modalOccupancy.id, cancelReason.trim());
      setActiveModal(null);
      showToast(
        res.refundedAmount > 0
          ? `Booking cancelled. ${formatCurrency(res.refundedAmount, modalOccupancy.currency)} refunded.`
          : "Booking cancelled. If rent was paid directly to the host, they will return it directly."
      );
      await load();
    } catch (err) {
      setCancelError(errorMessage(err, "Could not cancel booking."));
    } finally {
      setCancelSubmitting(false);
    }
  };

  useEffect(() => {
    if (!selectedOccupancy) return;
    let active = true;
    listMyHabitabilityIssues(selectedOccupancy.id)
      .then((list) => active && setIssues(list))
      .catch(() => active && setIssues([]));
    return () => {
      active = false;
    };
  }, [selectedOccupancy]);

  const selectedItem = useMemo(() => {
    if (!selectedOccupancy) return null;
    return rentalItems.find((r) => r.occupancy.id === selectedOccupancy.id) || null;
  }, [selectedOccupancy, rentalItems]);

  const modalItem = useMemo(() => {
    if (!modalOccupancy) return null;
    return rentalItems.find((r) => r.occupancy.id === modalOccupancy.id) || null;
  }, [modalOccupancy, rentalItems]);

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-3">
        <Loader label="Loading your rentals" />
      </div>
    );
  }

  return (
    <div className="space-y-5 max-w-[1560px] mx-auto pb-12">
      {/* 1. Header Section */}
      <section className="flex justify-between items-end gap-5 flex-wrap pt-2 px-1">
        <div>
          <div className="text-xs font-bold uppercase tracking-wider text-[#D80B0B]">
            Renter workspace
          </div>
          <h1 className="text-3xl sm:text-[38px] leading-[1.05] font-extrabold tracking-tight text-[#0E2F73] mt-1.5">
            My Rentals
          </h1>
          <p className="text-[15px] text-[#4A5878] mt-2 max-w-2xl">
            Manage your upcoming and active rentals, agreements, payments, notices and sublet requests.
          </p>
        </div>
        <Link
          href="/account/rent"
          className="inline-flex items-center gap-2 h-11 px-5 rounded-xl border border-slate-200 bg-white text-sm font-bold text-[#0E2F73] hover:border-[#0E2F73] hover:text-[#D80B0B] transition-colors shadow-sm"
        >
          <Search className="w-4 h-4" />
          Find a Room
        </Link>
      </section>

      {/* 2. Four Summary Metric Cards */}
      <section className="grid grid-cols-2 md:grid-cols-4 gap-3.5" aria-label="Rental summary">
        {/* Card 1: Upcoming */}
        <button
          type="button"
          onClick={() => {
            setActiveCard(activeCard === "upcoming" ? "" : "upcoming");
            setViewTab("current");
          }}
          className={`flex items-center gap-3.5 p-4 rounded-[20px] text-left transition-all border-2 bg-white ${
            activeCard === "upcoming" ? "border-[#0E2F73] bg-[#F5F8FE] shadow-sm" : "border-[#E1E7F1] hover:border-[#B8C6E0]"
          }`}
        >
          <span className="w-[52px] h-[52px] rounded-2xl bg-[#EEF3FF] text-[#1D3F8F] font-extrabold text-2xl flex items-center justify-center flex-shrink-0">
            {counts.upcoming}
          </span>
          <div className="flex flex-col gap-0.5 min-w-0 flex-1">
            <span className="font-extrabold text-base text-[#0B1B3F]">Upcoming</span>
            <span className="text-[12.5px] text-[#4A5878] truncate">Starts in the future</span>
          </div>
          <Calendar className="w-5 h-5 text-[#1D3F8F] opacity-80 flex-shrink-0 self-start mt-1 hidden sm:block" />
        </button>

        {/* Card 2: Active */}
        <button
          type="button"
          onClick={() => {
            setActiveCard(activeCard === "active" ? "" : "active");
            setViewTab("current");
          }}
          className={`flex items-center gap-3.5 p-4 rounded-[20px] text-left transition-all border-2 bg-white ${
            activeCard === "active" ? "border-[#0E2F73] bg-[#F5F8FE] shadow-sm" : "border-[#E1E7F1] hover:border-[#B8C6E0]"
          }`}
        >
          <span className="w-[52px] h-[52px] rounded-2xl bg-[#E7F8EF] text-[#05603A] font-extrabold text-2xl flex items-center justify-center flex-shrink-0">
            {counts.active}
          </span>
          <div className="flex flex-col gap-0.5 min-w-0 flex-1">
            <span className="font-extrabold text-base text-[#0B1B3F]">Active</span>
            <span className="text-[12.5px] text-[#4A5878] truncate">Rental in effect</span>
          </div>
          <Key className="w-5 h-5 text-[#05603A] opacity-80 flex-shrink-0 self-start mt-1 hidden sm:block" />
        </button>

        {/* Card 3: Ending Soon */}
        <button
          type="button"
          onClick={() => {
            setActiveCard(activeCard === "ending" ? "" : "ending");
            setViewTab("current");
          }}
          className={`flex items-center gap-3.5 p-4 rounded-[20px] text-left transition-all border-2 bg-white ${
            activeCard === "ending" ? "border-[#0E2F73] bg-[#F5F8FE] shadow-sm" : "border-[#E1E7F1] hover:border-[#B8C6E0]"
          }`}
        >
          <span className="w-[52px] h-[52px] rounded-2xl bg-[#FFF4E0] text-[#93370D] font-extrabold text-2xl flex items-center justify-center flex-shrink-0">
            {counts.ending}
          </span>
          <div className="flex flex-col gap-0.5 min-w-0 flex-1">
            <span className="font-extrabold text-base text-[#0B1B3F]">Ending Soon</span>
            <span className="text-[12.5px] text-[#4A5878] truncate">Confirmed end ahead</span>
          </div>
          <Clock className="w-5 h-5 text-[#93370D] opacity-80 flex-shrink-0 self-start mt-1 hidden sm:block" />
        </button>

        {/* Card 4: Action Required */}
        <button
          type="button"
          onClick={() => {
            setActiveCard(activeCard === "action" ? "" : "action");
            setViewTab("current");
          }}
          className={`flex items-center gap-3.5 p-4 rounded-[20px] text-left transition-all border-2 bg-white ${
            activeCard === "action" ? "border-[#B42318] bg-[#FFF7F7] shadow-sm" : "border-[#E1E7F1] hover:border-[#B8C6E0]"
          }`}
        >
          <span className="w-[52px] h-[52px] rounded-2xl bg-[#FDECEC] text-[#B42318] font-extrabold text-2xl flex items-center justify-center flex-shrink-0">
            {counts.action}
          </span>
          <div className="flex flex-col gap-0.5 min-w-0 flex-1">
            <span className="font-extrabold text-base text-[#0B1B3F]">Action Required</span>
            <span className="text-[12.5px] text-[#4A5878] truncate">Needs your response</span>
          </div>
          <AlertTriangle className="w-5 h-5 text-[#B42318] opacity-80 flex-shrink-0 self-start mt-1 hidden sm:block" />
        </button>
      </section>

      {/* 3. Action Strip Banner (shown when items need attention) */}
      {attentionItems.length > 0 && (
        <section className="flex items-center justify-between gap-4 p-3.5 sm:px-5 bg-[#FFF5F5] border border-[#F3C2C2] rounded-[18px] flex-wrap">
          <div className="flex items-center gap-2.5 text-[#B42318] font-bold text-sm">
            <AlertTriangle className="w-5 h-5 flex-shrink-0" />
            <span>
              {attentionItems.length} {attentionItems.length === 1 ? "rental needs" : "rentals need"} your attention
            </span>
          </div>
          <div className="flex items-center gap-3 flex-wrap flex-1 justify-end">
            <span className="text-sm text-[#3A1B1B]">
              {attentionItems[0].action}
            </span>
            <button
              type="button"
              onClick={() => handleOpenDrawer(attentionItems[0], "movein")}
              className="px-4 py-2 bg-[#D80B0B] hover:bg-[#B80909] text-white font-bold text-xs sm:text-sm rounded-xl shadow-sm transition-colors whitespace-nowrap"
            >
              Review Move-in
            </button>
          </div>
        </section>
      )}

      {/* 4. Search and Filters Bar */}
      <section className="bg-white border border-[#E1E7F1] rounded-[20px] p-3.5 shadow-sm">
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_auto] gap-3 items-end">
          <div className="flex gap-2">
            <div className="flex-1 flex items-center gap-2.5 h-[46px] px-3.5 bg-[#F4F6FB] border border-[#D5DDEA] rounded-2xl focus-within:border-[#0E2F73]">
              <Search className="w-4 h-4 text-[#4A5878] flex-shrink-0" />
              <input
                type="search"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search property, room, Rental ID or agreement"
                className="w-full bg-transparent text-sm text-[#0B1B3F] placeholder-[#4A5878] outline-none"
              />
            </div>
            <button
              type="button"
              onClick={() => setMobileFilterOpen(!mobileFilterOpen)}
              className="lg:hidden flex items-center gap-2 px-3.5 h-[46px] rounded-2xl border border-[#D5DDEA] bg-white text-sm font-bold text-[#0E2F73]"
            >
              Filters
            </button>
          </div>

          <div
            className={`grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-5 gap-2.5 items-end ${
              mobileFilterOpen ? "grid" : "hidden lg:grid"
            }`}
          >
            {/* Status */}
            <div className="flex flex-col gap-1">
              <label className="text-[11.5px] font-bold text-[#3A4866]">Status</label>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="h-[46px] px-3 rounded-2xl border border-[#D5DDEA] bg-white text-xs font-semibold text-[#0B1B3F] outline-none focus:border-[#0E2F73]"
              >
                <option value="all">All statuses</option>
                <option value="upcoming">Upcoming</option>
                <option value="active">Active</option>
                <option value="notice">Notice Given</option>
                <option value="ending">Ending Soon</option>
              </select>
            </div>

            {/* Location */}
            <div className="flex flex-col gap-1">
              <label className="text-[11.5px] font-bold text-[#3A4866]">Location</label>
              <select
                value={locFilter}
                onChange={(e) => setLocFilter(e.target.value)}
                className="h-[46px] px-3 rounded-2xl border border-[#D5DDEA] bg-white text-xs font-semibold text-[#0B1B3F] outline-none focus:border-[#0E2F73]"
              >
                <option value="all">All locations</option>
                {uniqueLocations.map((loc) => (
                  <option key={loc} value={loc}>
                    {loc}
                  </option>
                ))}
              </select>
            </div>

            {/* Start / End */}
            <div className="flex flex-col gap-1">
              <label className="text-[11.5px] font-bold text-[#3A4866]">Start / End</label>
              <select
                value={dateFilter}
                onChange={(e) => setDateFilter(e.target.value)}
                className="h-[46px] px-3 rounded-2xl border border-[#D5DDEA] bg-white text-xs font-semibold text-[#0B1B3F] outline-none focus:border-[#0E2F73]"
              >
                <option value="all">Any dates</option>
                <option value="starts">Starting in 30 days</option>
                <option value="ends">Ending in 90 days</option>
              </select>
            </div>

            {/* Sublet */}
            <div className="flex flex-col gap-1">
              <label className="text-[11.5px] font-bold text-[#3A4866]">Sublet</label>
              <select
                value={subletFilter}
                onChange={(e) => setSubletFilter(e.target.value)}
                className="h-[46px] px-3 rounded-2xl border border-[#D5DDEA] bg-white text-xs font-semibold text-[#0B1B3F] outline-none focus:border-[#0E2F73]"
              >
                <option value="all">Any</option>
                <option value="yes">Has sublet activity</option>
                <option value="no">No sublet</option>
              </select>
            </div>

            {/* Sort */}
            <div className="flex flex-col gap-1">
              <label className="text-[11.5px] font-bold text-[#3A4866]">Sort</label>
              <select
                value={sortFilter}
                onChange={(e) => setSortFilter(e.target.value as typeof sortFilter)}
                className="h-[46px] px-3 rounded-2xl border border-[#D5DDEA] bg-white text-xs font-semibold text-[#0B1B3F] outline-none focus:border-[#0E2F73]"
              >
                <option value="action">Action required first</option>
                <option value="start">Start date</option>
                <option value="end">End date</option>
              </select>
            </div>
          </div>
        </div>
      </section>

      {/* 5. Workspace Table Container */}
      <section className="bg-white border border-[#E1E7F1] rounded-[24px] overflow-hidden shadow-sm">
        {/* Workspace Bar: Current vs History Tabs */}
        <div className="flex justify-between items-center gap-3 p-4 sm:px-6 border-b border-[#EEF1F7] flex-wrap">
          <div className="inline-flex gap-1 p-1 bg-[#EEF2F9] rounded-xl" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={viewTab === "current"}
              onClick={() => {
                setViewTab("current");
              }}
              className={`h-9 px-4 rounded-lg text-xs sm:text-sm font-bold transition-all ${
                viewTab === "current"
                  ? "bg-white text-[#0E2F73] shadow-sm"
                  : "text-[#3A4866] hover:text-[#0E2F73]"
              }`}
            >
              Current ({currentRentals.length})
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={viewTab === "history"}
              onClick={() => {
                setViewTab("history");
                setActiveCard("");
              }}
              className={`h-9 px-4 rounded-lg text-xs sm:text-sm font-bold transition-all ${
                viewTab === "history"
                  ? "bg-white text-[#0E2F73] shadow-sm"
                  : "text-[#3A4866] hover:text-[#0E2F73]"
              }`}
            >
              History ({historyRentals.length})
            </button>
          </div>

          <div className="flex items-center gap-3">
            <span className="text-xs sm:text-sm text-[#4A5878]">
              {filteredRentals.length} {filteredRentals.length === 1 ? "rental" : "rentals"}
            </span>
            {hasActiveFilters && (
              <button
                type="button"
                onClick={clearFilters}
                className="h-8 px-3 rounded-lg bg-[#EEF2F9] hover:bg-[#E1E8F5] text-xs font-bold text-[#0E2F73] transition-colors"
              >
                Clear filters
              </button>
            )}
          </div>
        </div>

        {/* Table Columns Header (Desktop) */}
        <div className="hidden lg:grid grid-cols-[minmax(210px,1.3fr)_minmax(170px,1.1fr)_minmax(150px,1fr)_130px_minmax(140px,1fr)_170px] gap-3.5 px-6 py-3 bg-[#F7F9FD] border-b border-[#EEF1F7] text-[11px] font-bold tracking-wider uppercase text-[#5B6886]">
          <div>Property / room</div>
          <div>Rental / term</div>
          <div>Agreement</div>
          <div>Status</div>
          <div>Next milestone</div>
          <div className="text-right">Next action</div>
        </div>

        {/* Rental Rows */}
        {filteredRentals.length > 0 ? (
          <div className="divide-y divide-[#EEF1F7]">
            {filteredRentals.map((r) => {
              const isAction = Boolean(r.action);
              const isHistory = viewTab === "history";

              return (
                <article
                  key={r.id}
                  className={`grid grid-cols-1 lg:grid-cols-[minmax(210px,1.3fr)_minmax(170px,1.1fr)_minmax(150px,1fr)_130px_minmax(140px,1fr)_170px] gap-3.5 p-4 sm:px-6 items-center transition-colors hover:bg-slate-50/60 ${
                    isAction ? "bg-[#FFF9F9]" : ""
                  }`}
                >
                  {/* Property / Room */}
                  <button
                    type="button"
                    onClick={() => handleOpenDrawer(r, "overview")}
                    className="flex items-center gap-3.5 text-left group"
                  >
                    <div className="w-12 h-12 rounded-xl bg-[#E3E9F6] text-[#0E2F73] flex items-center justify-center flex-shrink-0 group-hover:bg-[#D9E4FA] transition-colors">
                      <Home className="w-5 h-5" />
                    </div>
                    <div className="flex flex-col gap-0.5 min-w-0">
                      <span className="font-extrabold text-sm sm:text-[15px] text-[#0B1B3F] group-hover:text-[#D80B0B] transition-colors truncate">
                        {r.p} · {r.room}
                      </span>
                      <span className="text-xs text-[#5B6886] truncate">
                        {r.type} · {r.city}
                      </span>
                    </div>
                  </button>

                  {/* Rental / Term */}
                  <div className="flex flex-col gap-0.5 text-xs">
                    <span className="lg:hidden text-[10px] font-bold uppercase tracking-wider text-[#5B6886]">
                      Rental / Term
                    </span>
                    <strong className="font-mono text-[13px] text-[#0B1B3F]">
                      Rental #{r.id}
                    </strong>
                    <span className="text-[#4A5878]">
                      {r.from} – {r.to}
                    </span>
                    <span className="text-sm font-extrabold text-[#0B1B3F]">
                      {r.rent} <small className="text-xs font-normal text-[#4A5878]">/ month</small>
                    </span>
                  </div>

                  {/* Agreement */}
                  <div className="flex flex-col gap-0.5 text-xs">
                    <span className="lg:hidden text-[10px] font-bold uppercase tracking-wider text-[#5B6886]">
                      Agreement
                    </span>
                    <strong className="font-mono text-[13px] text-[#0B1B3F]">
                      #{r.agr}
                    </strong>
                    <span
                      className={`inline-flex items-center gap-1 font-bold text-xs ${
                        isHistory ? "text-[#3A4866]" : "text-[#05603A]"
                      }`}
                    >
                      <Check className="w-3.5 h-3.5" />
                      {r.agrSt}
                    </span>
                  </div>

                  {/* Status Badge */}
                  <div>
                    <span className="lg:hidden text-[10px] font-bold uppercase tracking-wider text-[#5B6886] block mb-1">
                      Status
                    </span>
                    {r.st === "upcoming" && (
                      <span className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-full border border-[#AFC2EE] bg-[#EEF3FF] text-[#1D3F8F] text-xs font-extrabold uppercase">
                        <Calendar className="w-3 h-3" />
                        UPCOMING
                      </span>
                    )}
                    {r.st === "active" && (
                      <span className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-full border border-[#8FD9B4] bg-[#E7F8EF] text-[#05603A] text-xs font-extrabold uppercase">
                        <Key className="w-3 h-3" />
                        ACTIVE
                      </span>
                    )}
                    {r.st === "notice" && (
                      <span className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-full border border-[#0E2F73] bg-[#EAF0FB] text-[#0E2F73] text-xs font-extrabold uppercase">
                        <Calendar className="w-3 h-3" />
                        NOTICE GIVEN
                      </span>
                    )}
                    {r.st === "ending" && (
                      <span className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-full border border-[#F2B866] bg-[#FFF4E0] text-[#93370D] text-xs font-extrabold uppercase">
                        <Clock className="w-3 h-3" />
                        ENDING SOON
                      </span>
                    )}
                    {r.st === "ended" && (
                      <span className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-full border border-[#C3CAD6] bg-[#F0F2F6] text-[#344054] text-xs font-extrabold uppercase">
                        <Check className="w-3 h-3" />
                        ENDED
                      </span>
                    )}
                    {r.st === "cancelled" && (
                      <span className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-full border border-[#C3CAD6] bg-[#F0F2F6] text-[#344054] text-xs font-extrabold uppercase">
                        <X className="w-3 h-3" />
                        CANCELLED
                      </span>
                    )}
                  </div>

                  {/* Next Milestone */}
                  <div className="flex flex-col gap-0.5 text-xs">
                    <span className="lg:hidden text-[10px] font-bold uppercase tracking-wider text-[#5B6886]">
                      Next milestone
                    </span>
                    <span className="font-bold text-[#0B1B3F] text-[13px]">{r.ms}</span>
                  </div>

                  {/* Next Action Button */}
                  <div className="flex lg:justify-end">
                    {r.action ? (
                      <button
                        type="button"
                        onClick={() => handleOpenDrawer(r, "movein")}
                        className="w-full lg:w-auto h-10 px-4 rounded-xl bg-[#D80B0B] hover:bg-[#B80909] text-white font-bold text-xs sm:text-[13px] transition-colors shadow-sm whitespace-nowrap"
                      >
                        Review Move-in
                      </button>
                    ) : isHistory ? (
                      <button
                        type="button"
                        onClick={() => handleOpenDrawer(r, "overview")}
                        className="w-full lg:w-auto h-10 px-4 rounded-xl border border-[#C7D2E6] hover:border-[#0E2F73] text-[#0E2F73] font-bold text-xs sm:text-[13px] bg-white transition-colors whitespace-nowrap"
                      >
                        View Rental Record
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => handleOpenDrawer(r, "overview")}
                        className="w-full lg:w-auto h-10 px-4 rounded-xl border border-[#C7D2E6] hover:border-[#0E2F73] text-[#0E2F73] font-bold text-xs sm:text-[13px] bg-white transition-colors whitespace-nowrap"
                      >
                        Manage Rental
                      </button>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-16 px-4 text-center gap-3">
            <span className="w-12 h-12 rounded-2xl bg-slate-100 flex items-center justify-center text-slate-400">
              <Search className="w-6 h-6" />
            </span>
            <strong className="text-base text-[#0B1B3F]">No rentals match these filters.</strong>
            <p className="text-xs text-[#4A5878]">
              Try clearing your search keyword or selected status and location filters.
            </p>
            <button
              type="button"
              onClick={clearFilters}
              className="mt-2 h-9 px-4 rounded-xl bg-[#0E2F73] hover:bg-[#0B2560] text-white text-xs font-bold transition-colors"
            >
              Clear filters
            </button>
          </div>
        )}
      </section>

      {/* Footnote */}
      <p className="text-xs text-[#4A5878] px-1">
        Rent and deposits go to the verified recipient shown on each rental — not to Zoiko Rooms. Full payment activity lives in Payments.
      </p>

      {/* 6. Empty-state Reference Banner */}
      <section className="relative flex items-center gap-4 p-5 sm:p-6 border-2 border-dashed border-[#C7D2E6] rounded-[22px] bg-[#F8FAFD] mt-4">
        <div className="w-14 h-14 rounded-2xl bg-[#EAF0FB] flex items-center justify-center text-[#0E2F73] flex-shrink-0">
          <Home className="w-7 h-7" />
        </div>
        <div className="flex-1 min-w-0">
          <strong className="text-[#0B1B3F] text-base block">No rental record yet</strong>
          <div className="text-[#4A5878] text-sm mt-0.5">
            Applications that progress through agreement will appear here.
          </div>
        </div>
        <Link
          href="/account/applications"
          className="inline-flex items-center justify-center h-10 px-4 rounded-xl border border-[#C7D2E6] bg-white text-sm font-bold text-[#0E2F73] hover:border-[#0E2F73] hover:text-[#D80B0B] transition-colors shadow-sm whitespace-nowrap flex-shrink-0"
        >
          View My Applications
        </Link>
      </section>

      {/* ======================================================== */}
      {/* 7. Slide-Out Rental Detail Drawer (createPortal)         */}
      {/* ======================================================== */}
      {mounted &&
        selectedItem &&
        createPortal(
          <div className="fixed inset-0 z-50 flex justify-end animate-fade-in" role="dialog" aria-modal="true">
            {/* Scrim */}
            <div
              className="absolute inset-0 bg-[#081638]/45 backdrop-blur-[2px] transition-opacity cursor-default"
              onClick={() => setSelectedOccupancy(null)}
              aria-label="Close drawer"
            />

            {/* Drawer Body */}
            <aside
              ref={drawerRef}
              className="relative w-full max-w-[540px] h-full bg-white shadow-[-20px_0_50px_rgba(8,22,56,0.25)] flex flex-col overflow-y-auto sm:rounded-l-[24px] z-10 transition-transform duration-200"
            >
              {/* Header */}
              <div className="p-6 pb-4 border-b border-[#EEF1F7] flex justify-between items-start gap-4">
                <div className="flex items-start gap-3.5 min-w-0">
                  <div className="w-12 h-12 rounded-2xl bg-[#E3E9F6] text-[#0E2F73] flex items-center justify-center flex-shrink-0">
                    <Home className="w-6 h-6" />
                  </div>
                  <div className="min-w-0">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-[#D80B0B] block">
                      Rental #{selectedItem.id}
                    </span>
                    <h2 className="text-xl font-extrabold text-[#0E2F73] truncate mt-0.5">
                      {selectedItem.p} · {selectedItem.room}
                    </h2>
                    <p className="text-xs text-[#5B6886] mt-0.5 truncate">
                      {selectedItem.type} · {selectedItem.city}, Telangana
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setSelectedOccupancy(null)}
                  className="w-10 h-10 rounded-xl border border-[#D5DDEA] text-[#0E2F73] flex items-center justify-center hover:bg-slate-100 flex-shrink-0 transition-colors"
                  aria-label="Close"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Status info bar */}
              <div className="px-6 py-3 bg-[#F7F9FD] border-b border-[#EEF1F7] flex items-center gap-2.5 flex-wrap text-xs">
                {selectedItem.st === "upcoming" && (
                  <span className="inline-flex items-center gap-1.5 h-6 px-2.5 rounded-full border border-[#AFC2EE] bg-[#EEF3FF] text-[#1D3F8F] font-extrabold uppercase text-[11px]">
                    <Calendar className="w-3 h-3" /> UPCOMING
                  </span>
                )}
                {selectedItem.st === "active" && (
                  <span className="inline-flex items-center gap-1.5 h-6 px-2.5 rounded-full border border-[#8FD9B4] bg-[#E7F8EF] text-[#05603A] font-extrabold uppercase text-[11px]">
                    <Key className="w-3 h-3" /> ACTIVE
                  </span>
                )}
                {selectedItem.st === "notice" && (
                  <span className="inline-flex items-center gap-1.5 h-6 px-2.5 rounded-full border border-[#0E2F73] bg-[#EAF0FB] text-[#0E2F73] font-extrabold uppercase text-[11px]">
                    <Calendar className="w-3 h-3" /> NOTICE GIVEN
                  </span>
                )}
                {selectedItem.st === "ending" && (
                  <span className="inline-flex items-center gap-1.5 h-6 px-2.5 rounded-full border border-[#F2B866] bg-[#FFF4E0] text-[#93370D] font-extrabold uppercase text-[11px]">
                    <Clock className="w-3 h-3" /> ENDING SOON
                  </span>
                )}
                {selectedItem.st === "ended" && (
                  <span className="inline-flex items-center gap-1.5 h-6 px-2.5 rounded-full border border-[#C3CAD6] bg-[#F0F2F6] text-[#344054] font-extrabold uppercase text-[11px]">
                    <Check className="w-3 h-3" /> ENDED
                  </span>
                )}
                {selectedItem.st === "cancelled" && (
                  <span className="inline-flex items-center gap-1.5 h-6 px-2.5 rounded-full border border-[#C3CAD6] bg-[#F0F2F6] text-[#344054] font-extrabold uppercase text-[11px]">
                    <X className="w-3 h-3" /> CANCELLED
                  </span>
                )}
                <span className="text-[#4A5878]">
                  {selectedItem.from} → {selectedItem.to} · {selectedItem.rent} / month · #{selectedItem.agr}
                </span>
              </div>

              {/* Tab Navigation in Drawer -- wraps so every tab stays visible (a
                  sideways-scrolling strip hid Documents/Disputes/Record off-screen). */}
              <div className="flex flex-wrap border-b border-[#E1E7F1] px-4 gap-x-1">
                {(selectedItem.occupancy.status === "PENDING_MOVE_IN" || selectedItem.st === "upcoming") && (
                  <button
                    type="button"
                    onClick={() => setDrawerTab("movein")}
                    className={`h-11 px-3.5 border-b-2 text-xs font-bold whitespace-nowrap transition-colors ${
                      drawerTab === "movein"
                        ? "border-[#0E2F73] text-[#0E2F73]"
                        : "border-transparent text-[#4A5878] hover:text-[#0E2F73]"
                    }`}
                  >
                    Move-in
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setDrawerTab("overview")}
                  className={`h-11 px-3.5 border-b-2 text-xs font-bold whitespace-nowrap transition-colors ${
                    drawerTab === "overview"
                      ? "border-[#0E2F73] text-[#0E2F73]"
                      : "border-transparent text-[#4A5878] hover:text-[#0E2F73]"
                  }`}
                >
                  Overview
                </button>
                <button
                  type="button"
                  onClick={() => setDrawerTab("agreement")}
                  className={`h-11 px-3.5 border-b-2 text-xs font-bold whitespace-nowrap transition-colors ${
                    drawerTab === "agreement"
                      ? "border-[#0E2F73] text-[#0E2F73]"
                      : "border-transparent text-[#4A5878] hover:text-[#0E2F73]"
                  }`}
                >
                  Agreement
                </button>
                <button
                  type="button"
                  onClick={() => setDrawerTab("payments")}
                  className={`h-11 px-3.5 border-b-2 text-xs font-bold whitespace-nowrap transition-colors ${
                    drawerTab === "payments"
                      ? "border-[#0E2F73] text-[#0E2F73]"
                      : "border-transparent text-[#4A5878] hover:text-[#0E2F73]"
                  }`}
                >
                  Payments
                </button>
                <button
                  type="button"
                  onClick={() => setDrawerTab("deposit")}
                  className={`h-11 px-3.5 border-b-2 text-xs font-bold whitespace-nowrap transition-colors ${
                    drawerTab === "deposit"
                      ? "border-[#0E2F73] text-[#0E2F73]"
                      : "border-transparent text-[#4A5878] hover:text-[#0E2F73]"
                  }`}
                >
                  Deposit
                </button>
                <button
                  type="button"
                  onClick={() => setDrawerTab("sublet")}
                  className={`h-11 px-3.5 border-b-2 text-xs font-bold whitespace-nowrap transition-colors ${
                    drawerTab === "sublet"
                      ? "border-[#0E2F73] text-[#0E2F73]"
                      : "border-transparent text-[#4A5878] hover:text-[#0E2F73]"
                  }`}
                >
                  Sublet
                </button>
                <button
                  type="button"
                  onClick={() => setDrawerTab("notices")}
                  className={`h-11 px-3.5 border-b-2 text-xs font-bold whitespace-nowrap transition-colors ${
                    drawerTab === "notices"
                      ? "border-[#0E2F73] text-[#0E2F73]"
                      : "border-transparent text-[#4A5878] hover:text-[#0E2F73]"
                  }`}
                >
                  Notices & Requests
                </button>
                <button
                  type="button"
                  onClick={() => setDrawerTab("documents")}
                  className={`h-11 px-3.5 border-b-2 text-xs font-bold whitespace-nowrap transition-colors ${
                    drawerTab === "documents"
                      ? "border-[#0E2F73] text-[#0E2F73]"
                      : "border-transparent text-[#4A5878] hover:text-[#0E2F73]"
                  }`}
                >
                  Documents
                </button>
                <button
                  type="button"
                  onClick={() => setDrawerTab("disputes")}
                  className={`h-11 px-3.5 border-b-2 text-xs font-bold whitespace-nowrap transition-colors ${
                    drawerTab === "disputes"
                      ? "border-[#0E2F73] text-[#0E2F73]"
                      : "border-transparent text-[#4A5878] hover:text-[#0E2F73]"
                  }`}
                >
                  Disputes
                  {disputeCases.filter((c) => c.occupancyId === selectedItem.occupancy.id).length > 0 && (
                    <span className="ml-1.5 inline-flex min-w-4 h-4 px-1 items-center justify-center rounded-full bg-[#D80B0B] text-white text-[10px]">
                      {disputeCases.filter((c) => c.occupancyId === selectedItem.occupancy.id).length}
                    </span>
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => setDrawerTab("record")}
                  className={`h-11 px-3.5 border-b-2 text-xs font-bold whitespace-nowrap transition-colors ${
                    drawerTab === "record"
                      ? "border-[#0E2F73] text-[#0E2F73]"
                      : "border-transparent text-[#4A5878] hover:text-[#0E2F73]"
                  }`}
                >
                  Transaction Record
                </button>
              </div>

              {/* Drawer Content */}
              <div className="p-6 flex-1 space-y-5">
                {/* TAB: Move-in */}
                {drawerTab === "movein" && (
                  <div className="space-y-4">
                    {selectedItem.occupancy.status === "PENDING_MOVE_IN" &&
                      disputeCases.some((c) => c.occupancyId === selectedItem.occupancy.id && c.status !== "RESOLVED" && c.status !== "CLOSED") && (
                        <div className="space-y-2 rounded-xl border border-amber-200 bg-amber-50 p-3.5 text-xs text-amber-900">
                          <p>
                            A dispute on this rental is open, so move-in waits until it&apos;s resolved — or goes ahead now if you and
                            your host both agree.
                          </p>
                          <button
                            type="button"
                            disabled={caseBusy}
                            onClick={async () => {
                              setCaseBusy(true);
                              try {
                                await renterProceedDespiteDispute(selectedItem.occupancy.id);
                                showToast("Recorded. Move-in can go ahead once your host agrees too.");
                              } catch (err) {
                                showToast(errorMessage(err, "Could not record your agreement."), "error");
                              } finally {
                                setCaseBusy(false);
                              }
                            }}
                            className="px-3 py-1.5 rounded-lg border border-amber-400 text-xs font-bold disabled:opacity-50"
                          >
                            Agree to move in anyway
                          </button>
                        </div>
                      )}
                    <div className="p-3.5 rounded-xl bg-[#EEF3FF] text-[#1D3F8F] text-xs font-medium flex items-start gap-2.5">
                      <Info className="w-4 h-4 flex-shrink-0 mt-0.5" />
                      <span>
                        Completing this checklist doesn’t start the rental. It becomes Active when start conditions are verified on move-in day.
                      </span>
                    </div>

                    <ul className="divide-y divide-[#EEF1F7] border border-[#E1E7F1] rounded-2xl overflow-hidden">
                      <li className="p-3.5 flex items-center justify-between gap-3 bg-white">
                        <div className="flex items-center gap-3">
                          <span className="w-7 h-7 rounded-lg bg-[#E7F8EF] text-[#05603A] flex items-center justify-center flex-shrink-0">
                            <Check className="w-4 h-4" />
                          </span>
                          <div>
                            <div className="font-bold text-xs sm:text-sm text-[#0B1B3F]">Agreement</div>
                            <div className="text-[11px] text-[#5B6886]">
                              #{selectedItem.agr} · {selectedItem.agrSt}
                            </div>
                          </div>
                        </div>
                        <span className="text-xs font-bold text-[#05603A]">Done</span>
                      </li>

                      <li className={`p-3.5 flex items-center justify-between gap-3 ${
                        isRecipientReviewed(selectedItem.occupancy) ? "bg-white" : "bg-[#FFF7F7]"
                      }`}>
                        <div className="flex items-center gap-3">
                          <span className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 ${
                            isRecipientReviewed(selectedItem.occupancy)
                              ? "bg-[#E7F8EF] text-[#05603A]"
                              : "bg-[#FDECEC] text-[#B42318]"
                          }`}>
                            {isRecipientReviewed(selectedItem.occupancy) ? (
                              <Check className="w-4 h-4" />
                            ) : (
                              <AlertTriangle className="w-4 h-4" />
                            )}
                          </span>
                          <div>
                            <div className="font-bold text-xs sm:text-sm text-[#0B1B3F]">First payment & recipient</div>
                            <div className="text-[11px] text-[#5B6886]">
                              {isRecipientReviewed(selectedItem.occupancy)
                                ? `Recipient reviewed · ${selectedItem.recipient}`
                                : "Review verified payment recipient before paying"}
                            </div>
                          </div>
                        </div>
                        {isRecipientReviewed(selectedItem.occupancy) ? (
                          <span className="text-xs font-bold text-[#05603A]">Done</span>
                        ) : (
                          <button
                            type="button"
                            onClick={() => openRecipientReviewModal(selectedItem.occupancy)}
                            className="px-3 py-1.5 rounded-lg bg-[#0E2F73] hover:bg-[#0B2560] text-white text-xs font-bold transition-colors"
                          >
                            Review recipient
                          </button>
                        )}
                      </li>

                      <li className="p-3.5 flex items-center justify-between gap-3 bg-white">
                        <div className="flex items-center gap-3">
                          <span className="w-7 h-7 rounded-lg bg-[#EEF3FF] text-[#1D3F8F] flex items-center justify-center flex-shrink-0">
                            <Info className="w-4 h-4" />
                          </span>
                          <div>
                            <div className="font-bold text-xs sm:text-sm text-[#0B1B3F]">Security Deposit</div>
                            <div className="text-[11px] text-[#5B6886]">{selectedItem.depositAmt} · paid directly to landlord</div>
                          </div>
                        </div>
                        <span className="text-xs font-bold text-[#1D3F8F]">Scheduled</span>
                      </li>

                      <li className="p-3.5 flex items-center justify-between gap-3 bg-white">
                        <div className="flex items-center gap-3">
                          <span className="w-7 h-7 rounded-lg bg-[#EEF3FF] text-[#1D3F8F] flex items-center justify-center flex-shrink-0">
                            <Key className="w-4 h-4" />
                          </span>
                          <div>
                            <div className="font-bold text-xs sm:text-sm text-[#0B1B3F]">Access & Keys</div>
                            <div className="text-[11px] text-[#5B6886]">Collect on move-in day: {selectedItem.from}</div>
                          </div>
                        </div>
                        {receiptConfirmedIds.has(selectedItem.occupancy.id) ||
                        (selectedItem.occupancy.handoverEventTypes ?? []).includes("RENTER_RECEIPT") ? (
                          <span className="text-xs font-bold text-[#05603A]">Confirmed</span>
                        ) : !(selectedItem.occupancy.handoverEventTypes ?? []).includes("POSSESSION_DELIVERED") ? (
                          <span className="text-xs font-bold text-[#5B6886]">Waiting for host handover</span>
                        ) : (
                          <button
                            type="button"
                            onClick={() => handleConfirmHandoverReceipt(selectedItem.occupancy.id)}
                            className="px-3 py-1.5 rounded-lg border border-[#0E2F73] text-[#0E2F73] hover:bg-[#0E2F73] hover:text-white text-xs font-bold transition-colors"
                          >
                            Confirm receipt
                          </button>
                        )}
                      </li>

                      <li className="p-3.5 flex items-center justify-between gap-3 bg-white">
                        <div className="flex items-center gap-3">
                          <span className="w-7 h-7 rounded-lg bg-[#EEF3FF] text-[#1D3F8F] flex items-center justify-center flex-shrink-0">
                            <ClipboardList className="w-4 h-4" />
                          </span>
                          <div>
                            <div className="font-bold text-xs sm:text-sm text-[#0B1B3F]">Move-in condition record</div>
                            <div className="text-[11px] text-[#5B6886]">Document room state and photos on move-in day</div>
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => openConditionModal(selectedItem.occupancy)}
                          className="px-3 py-1.5 rounded-lg border border-slate-300 hover:border-[#0E2F73] text-[#0E2F73] text-xs font-bold transition-colors"
                        >
                          Report
                        </button>
                      </li>
                    </ul>
                  </div>
                )}

                {/* TAB: Overview */}
                {drawerTab === "overview" && (
                  <div className="space-y-4">
                    {selectedItem.action && (
                      <div className="p-3.5 rounded-xl bg-[#FDECEC] text-[#B42318] text-xs font-medium flex items-start gap-2.5">
                        <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                        <span>{selectedItem.action}</span>
                      </div>
                    )}

                    <div className="grid grid-cols-2 gap-2.5">
                      <div className="p-3.5 border border-[#E1E7F1] rounded-2xl flex flex-col gap-1">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-[#5B6886]">
                          Rental status
                        </span>
                        <strong className="text-sm text-[#0B1B3F]">
                          {selectedItem.st.toUpperCase()}
                        </strong>
                      </div>
                      <div className="p-3.5 border border-[#E1E7F1] rounded-2xl flex flex-col gap-1">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-[#5B6886]">Term</span>
                        <strong className="text-sm text-[#0B1B3F]">
                          {selectedItem.from} → {selectedItem.to}
                        </strong>
                      </div>
                      <div className="p-3.5 border border-[#E1E7F1] rounded-2xl flex flex-col gap-1">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-[#5B6886]">Current rent</span>
                        <strong className="text-sm text-[#0B1B3F]">{selectedItem.rent} / month</strong>
                      </div>
                      <div className="p-3.5 border border-[#E1E7F1] rounded-2xl flex flex-col gap-1">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-[#5B6886]">Agreement</span>
                        <strong className="text-sm text-[#0B1B3F]">#{selectedItem.agr}</strong>
                      </div>
                      <div className="p-3.5 border border-[#E1E7F1] rounded-2xl flex flex-col gap-1">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-[#5B6886]">Host / Owner</span>
                        <strong className="text-sm text-[#0B1B3F] truncate">{selectedItem.owner}</strong>
                      </div>
                      <div className="p-3.5 border border-[#E1E7F1] rounded-2xl flex flex-col gap-1">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-[#5B6886]">Managing agent</span>
                        <strong className="text-sm text-[#0B1B3F] truncate">{selectedItem.agent}</strong>
                      </div>
                      <div className="p-3.5 border border-[#E1E7F1] rounded-2xl flex flex-col gap-1">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-[#5B6886]">Payment</span>
                        <strong className="text-sm text-[#0B1B3F]">{selectedItem.payStatus}</strong>
                      </div>
                      <div className="p-3.5 border border-[#E1E7F1] rounded-2xl flex flex-col gap-1">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-[#5B6886]">Deposit</span>
                        <strong className="text-sm text-[#0B1B3F]">{selectedItem.depositAmt}</strong>
                      </div>
                      <div className="p-3.5 border border-[#E1E7F1] rounded-2xl flex flex-col gap-1 col-span-2">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-[#5B6886]">Sublet</span>
                        <strong className="text-sm text-[#0B1B3F]">{selectedItem.subletText}</strong>
                      </div>
                      <div className="p-3.5 border border-[#E1E7F1] rounded-2xl flex flex-col gap-1 col-span-2">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-[#5B6886]">Next milestone</span>
                        <strong className="text-sm text-[#0B1B3F]">{selectedItem.ms}</strong>
                      </div>
                    </div>
                  </div>
                )}

                {/* TAB: Agreement */}
                {drawerTab === "agreement" && (
                  <div className="space-y-4">
                    <div className="p-4 rounded-2xl border border-[#E1E7F1] flex items-center justify-between gap-4">
                      <div className="flex items-center gap-3">
                        <FileText className="w-6 h-6 text-[#0E2F73]" />
                        <div>
                          <strong className="text-sm text-[#0B1B3F] block">Agreement #{selectedItem.agr}</strong>
                          <span className="text-xs text-[#5B6886]">Current executed version</span>
                        </div>
                      </div>
                      {selectedItem.occupancy.agreementId && (
                        <a
                          href={tenantAgreementPdfUrl(selectedItem.occupancy.agreementId)}
                          download
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#0E2F73] text-[#0E2F73] text-xs font-bold hover:bg-[#0E2F73] hover:text-white transition-colors"
                        >
                          <Download className="w-3.5 h-3.5" />
                          Download
                        </a>
                      )}
                    </div>
                    <p className="text-xs text-[#5B6886]">
                      Executed agreements are never replaced. Any amendments, extensions, or renewals create an append-only new version.
                    </p>
                  </div>
                )}

                {/* TAB: Payments */}
                {drawerTab === "payments" && (
                  <div className="space-y-4">
                    {refunds
                      .filter((rf) => rf.occupancyId === selectedItem.occupancy.id && rf.status !== "CONFIRMED")
                      .map((rf) => (
                        <div key={rf.id} className="space-y-2 rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-xs text-emerald-950">
                          <strong className="block text-sm">
                            Your host owes you {rf.currency} {rf.amount.toFixed(2)}
                          </strong>
                          <p>
                            {rf.reason}. Due by {new Date(rf.dueBy).toLocaleDateString()}
                            {rf.status === "OVERDUE" ? " — overdue." : "."}
                          </p>
                          {rf.status === "MARKED_PAID" && (
                            <>
                              <p>
                                Your host says it&apos;s paid{rf.paymentReference ? ` (ref ${rf.paymentReference})` : ""}. If you
                                don&apos;t answer within a week, it&apos;s treated as received.
                              </p>
                              <div className="flex gap-2">
                                <button
                                  type="button"
                                  disabled={caseBusy}
                                  onClick={async () => {
                                    setCaseBusy(true);
                                    try {
                                      const updated = await confirmRefundReceived(rf.id);
                                      setRefunds((prev) => prev.map((x) => (x.id === rf.id ? updated : x)));
                                      showToast("Thanks — refund confirmed.");
                                    } catch (err) {
                                      showToast(errorMessage(err, "Could not confirm."), "error");
                                    } finally {
                                      setCaseBusy(false);
                                    }
                                  }}
                                  className="px-3 py-1.5 rounded-lg bg-[#05603A] text-white font-bold disabled:opacity-50"
                                >
                                  I received it
                                </button>
                                <button
                                  type="button"
                                  disabled={caseBusy}
                                  onClick={async () => {
                                    setCaseBusy(true);
                                    try {
                                      const updated = await reportRefundNotReceived(rf.id, "");
                                      setRefunds((prev) => prev.map((x) => (x.id === rf.id ? updated : x)));
                                      showToast("Your host has been told it hasn't arrived.");
                                    } catch (err) {
                                      showToast(errorMessage(err, "Could not send."), "error");
                                    } finally {
                                      setCaseBusy(false);
                                    }
                                  }}
                                  className="px-3 py-1.5 rounded-lg border border-emerald-400 font-bold disabled:opacity-50"
                                >
                                  It hasn&apos;t arrived
                                </button>
                              </div>
                            </>
                          )}
                        </div>
                      ))}
                    <div className="grid grid-cols-2 gap-2.5">
                      <div className="p-3.5 border border-[#E1E7F1] rounded-2xl flex flex-col gap-1">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-[#5B6886]">
                          Next obligation
                        </span>
                        <strong className="text-sm text-[#0B1B3F]">{selectedItem.payNext}</strong>
                      </div>
                      <div className="p-3.5 border border-[#E1E7F1] rounded-2xl flex flex-col gap-1">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-[#5B6886]">
                          Latest status
                        </span>
                        <strong className="text-sm text-[#0B1B3F]">{selectedItem.payStatus}</strong>
                      </div>
                    </div>

                    {/* Verified Recipient Box */}
                    <div className="p-4 rounded-2xl bg-[#E7F8EF] border border-[#8FD9B4] flex items-start gap-3">
                      <ShieldCheck className="w-6 h-6 text-[#05603A] flex-shrink-0" />
                      <div>
                        <span className="text-[10px] font-extrabold uppercase tracking-wider text-[#05603A] block">
                          Verified Payment Recipient
                        </span>
                        <strong className="text-sm text-[#0B1B3F] block mt-0.5">
                          {selectedItem.recipient}
                        </strong>
                        <p className="text-xs text-[#05603A] mt-1">
                          Payment Receipt Authority verified by Zoiko Rooms. Zoiko Rooms is not the payee.
                        </p>
                      </div>
                    </div>

                    <div className="flex gap-2.5 flex-wrap">
                      <button
                        type="button"
                        onClick={() => openPaymentInstructionsModal(selectedItem.occupancy)}
                        className="px-4 py-2.5 rounded-xl bg-[#0E2F73] hover:bg-[#0B2560] text-white text-xs font-bold transition-colors"
                      >
                        View payment method
                      </button>
                      <Link
                        href="/account/payments"
                        className="inline-flex items-center gap-1.5 px-4 py-2.5 rounded-xl border border-slate-300 hover:border-[#0E2F73] text-[#0E2F73] text-xs font-bold transition-colors"
                      >
                        Open Payments <ArrowRight className="w-3.5 h-3.5" />
                      </Link>
                    </div>
                  </div>
                )}

                {/* TAB: Deposit */}
                {drawerTab === "deposit" && (
                  <div className="space-y-4">
                    <div className="grid grid-cols-2 gap-2.5">
                      <div className="p-3.5 border border-[#E1E7F1] rounded-2xl flex flex-col gap-1">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-[#5B6886]">Deposit amount</span>
                        <strong className="text-sm text-[#0B1B3F]">{selectedItem.depositAmt}</strong>
                      </div>
                      <div className="p-3.5 border border-[#E1E7F1] rounded-2xl flex flex-col gap-1">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-[#5B6886]">Status</span>
                        <strong className="text-sm text-[#0B1B3F]">{selectedItem.depositStatus}</strong>
                      </div>
                      <div className="p-3.5 border border-[#E1E7F1] rounded-2xl flex flex-col gap-1 col-span-2">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-[#5B6886]">Held by</span>
                        <strong className="text-sm text-[#0B1B3F]">{selectedItem.depositHolder}</strong>
                      </div>
                    </div>
                    <p className="text-xs text-[#5B6886] flex items-center gap-1.5">
                      <Shield className="w-4 h-4 text-[#0E2F73]" /> Zoiko Rooms does not hold your deposit.
                    </p>
                  </div>
                )}

                {/* TAB: Sublet */}
                {drawerTab === "sublet" && (
                  <div className="space-y-4">
                    <div className="p-4 border border-[#E1E7F1] rounded-2xl space-y-2">
                      <strong className="text-sm text-[#0B1B3F] block">Sublet Status</strong>
                      <p className="text-xs text-[#4A5878]">{selectedItem.subletText}</p>
                      {(() => {
                        // Mirrors crud/sublet.py:_assert_sublet_permitted so the button only
                        // shows when the request can actually be accepted.
                        const occ = selectedItem.occupancy;
                        const daysLeft = occ.expectedEndDate
                          ? Math.round((new Date(occ.expectedEndDate).getTime() - nowMs) / 86400000)
                          : null;
                        let blocked: string | null = null;
                        if (occ.status !== "ACTIVE") blocked = "Subletting opens once your move-in is confirmed and the rental is active.";
                        else if (occ.reassignedViaSubletRequestId) blocked = "This rental has already been reassigned through a sublet.";
                        else if (selectedItem.hasSublet) blocked = "You already have a sublet request awaiting your host's decision.";
                        else if (daysLeft === null || daysLeft < 30) blocked = "A sublet needs at least 30 nights left on the agreement.";
                        return blocked ? (
                          <p className="mt-1 text-xs text-[#5B6886]">{blocked}</p>
                        ) : (
                          <button
                            type="button"
                            onClick={() => openSubletWizard(occ)}
                            className="mt-2 px-4 py-2 rounded-xl bg-[#0E2F73] hover:bg-[#0B2560] text-white text-xs font-bold transition-colors"
                          >
                            Request to Sublet
                          </button>
                        );
                      })()}
                    </div>
                    <p className="text-xs text-[#5B6886]">
                      Your host or their authorized agent decides. Zoiko Rooms routes and records the request — it doesn’t grant consent.
                    </p>
                  </div>
                )}

                {/* TAB: Notices & Requests */}
                {drawerTab === "notices" && (
                  <div className="space-y-4">
                    {selectedItem.occupancy.status === "ACTIVE" && (
                      <div className="space-y-3 rounded-2xl border border-[#E1E7F1] p-4 text-xs text-[#0B1B3F]">
                        <strong className="block text-sm">Problem with the room?</strong>
                        {issues.filter((i) => i.status !== "RESOLVED").map((i) => (
                          <div key={i.id} className="space-y-2 rounded-xl bg-[#FFF7F7] p-3">
                            <p>
                              <strong>{ISSUE_SEVERITY[i.severity]}</strong> · {i.description || "No details"} ·{" "}
                              {i.status === "OPEN" ? "reported" : i.status === "ACKNOWLEDGED" ? "host is working on it" : "host says it's fixed"}
                              {i.fixDueAt ? ` · fix due ${new Date(i.fixDueAt).toLocaleDateString()}` : ""}
                            </p>
                            {i.creditedAmount > 0 && <p>Rent reduced by {i.creditedAmount.toFixed(2)} because it wasn&apos;t fixed in time.</p>}
                            {i.status === "FIX_REPORTED" && (
                              <div className="flex gap-2">
                                <button type="button" disabled={caseBusy} className="px-3 py-1.5 rounded-lg bg-[#05603A] text-white font-bold disabled:opacity-50"
                                  onClick={async () => {
                                    setCaseBusy(true);
                                    try {
                                      const u = await confirmHabitabilityFixed(i.id);
                                      setIssues((prev) => prev.map((x) => (x.id === i.id ? u : x)));
                                      showToast("Thanks — marked as fixed.");
                                    } catch (err) {
                                      showToast(errorMessage(err, "Could not confirm."), "error");
                                    } finally {
                                      setCaseBusy(false);
                                    }
                                  }}>It&apos;s fixed</button>
                                <button type="button" disabled={caseBusy} className="px-3 py-1.5 rounded-lg border border-slate-300 font-bold disabled:opacity-50"
                                  onClick={async () => {
                                    setCaseBusy(true);
                                    try {
                                      const u = await rejectHabitabilityFix(i.id, "");
                                      setIssues((prev) => prev.map((x) => (x.id === i.id ? u : x)));
                                      showToast("Your host has been told it isn't fixed.");
                                    } catch (err) {
                                      showToast(errorMessage(err, "Could not send."), "error");
                                    } finally {
                                      setCaseBusy(false);
                                    }
                                  }}>Not fixed</button>
                              </div>
                            )}
                          </div>
                        ))}
                        <select value={issueSeverity} onChange={(e) => setIssueSeverity(e.target.value as HabitabilitySeverity)}
                          className="h-10 w-full rounded-xl border border-slate-300 px-3" aria-label="How serious is it">
                          {(Object.keys(ISSUE_SEVERITY) as HabitabilitySeverity[]).map((k) => (
                            <option key={k} value={k}>{ISSUE_SEVERITY[k]}</option>
                          ))}
                        </select>
                        <textarea rows={2} value={issueText} onChange={(e) => setIssueText(e.target.value)} placeholder="What's wrong?"
                          className="w-full rounded-xl border border-slate-300 p-3" />
                        <button type="button" disabled={caseBusy || !issueText.trim()} className="px-3 py-1.5 rounded-lg bg-[#0E2F73] text-white font-bold disabled:opacity-50"
                          onClick={async () => {
                            setCaseBusy(true);
                            try {
                              const created = await reportHabitabilityIssue(selectedItem.occupancy.id, issueSeverity, issueText.trim());
                              setIssues((prev) => [created, ...prev]);
                              setIssueText("");
                              showToast("Reported. Your host has been told and has a deadline to fix it.");
                            } catch (err) {
                              showToast(errorMessage(err, "Could not report the problem."), "error");
                            } finally {
                              setCaseBusy(false);
                            }
                          }}>Report problem</button>
                      </div>
                    )}
                    {(() => {
                      const tc = terminationCases[selectedItem.occupancy.id];
                      if (!tc || ["TERMINATED", "WITHDRAWN", "REJECTED_PATHWAY", "SURRENDER_DECLINED"].includes(tc.status)) return null;
                      const me = selectedItem.occupancy.guestId ?? "";
                      const mineToAnswer = tc.status === "AWAITING_RESPONSE" && (tc.pendingResponders ?? []).includes(me);
                      const iOpened = tc.initiatorGuestId === me;
                      const due = tc.responseDueAt ? new Date(tc.responseDueAt).toLocaleDateString() : null;
                      const act = async (fn: () => Promise<unknown>, msg: string) => {
                        setCaseBusy(true);
                        try {
                          await fn();
                          showToast(msg);
                          setCounterDate("");
                          setOrderRef("");
                          setOrderDate("");
                          await load();
                        } catch (err) {
                          showToast(errorMessage(err, "That didn't work. Please try again."), "error");
                        } finally {
                          setCaseBusy(false);
                        }
                      };
                      const btn = "px-3 py-1.5 rounded-lg text-xs font-bold disabled:opacity-50";
                      return (
                        <div className="space-y-3 rounded-2xl border border-amber-200 bg-amber-50/60 p-4 text-xs text-[#0B1B3F]">
                          <strong className="block text-sm">
                            {iOpened ? "Your request to end the tenancy" : "Request to end your tenancy"}
                          </strong>
                          <p>
                            {tc.status === "EFFECTIVE_DATE_SET" && tc.effectiveTerminationDate
                              ? `Ends on ${tc.effectiveTerminationDate}. Move out by then and confirm it here so your tenancy closes.`
                              : tc.status === "CONTESTED"
                                ? "Contested. The tenancy continues unless a court or tribunal orders otherwise."
                                : tc.earliestEffectiveDate
                                  ? `Proposed end date: ${tc.earliestEffectiveDate}${due ? ` · answers due ${due}` : ""}.`
                                  : "Waiting for a response."}
                          </p>

                          {mineToAnswer && (
                            <div className="space-y-2">
                              <p>
                                {tc.deemedOnSilence ? "If you don't answer by the deadline, the date is accepted." : "Nothing changes unless you agree."}
                              </p>
                              <input
                                type="date"
                                value={counterDate}
                                min={new Date().toISOString().slice(0, 10)}
                                onChange={(e) => setCounterDate(e.target.value)}
                                className="h-10 w-full rounded-xl border border-slate-300 px-3"
                                aria-label="Propose a different date"
                              />
                              <div className="flex flex-wrap gap-2">
                                <button type="button" disabled={caseBusy} className={`${btn} bg-[#0E2F73] text-white`}
                                  onClick={() => act(() => renterTermination.respond(tc.id, { response: "ACCEPT" }), "Accepted.")}>Accept date</button>
                                <button type="button" disabled={caseBusy || !counterDate} className={`${btn} border border-slate-300`}
                                  onClick={() => act(() => renterTermination.respond(tc.id, { response: "COUNTER", proposedDate: counterDate }), "Your date was sent.")}>Propose this date</button>
                                <button type="button" disabled={caseBusy} className={`${btn} border border-rose-300 text-rose-700`}
                                  onClick={() => act(() => renterTermination.respond(tc.id, { response: "CONTEST" }), "Contested.")}>Contest</button>
                              </div>
                            </div>
                          )}

                          {tc.status === "SURRENDER_PROPOSED" && !iOpened && (
                            <div className="flex gap-2">
                              <button type="button" disabled={caseBusy} className={`${btn} bg-[#0E2F73] text-white`}
                                onClick={() => act(() => acceptOwnMutualSurrender(tc.id), "Accepted. The end date is set.")}>Agree</button>
                              <button type="button" disabled={caseBusy} className={`${btn} border border-slate-300`}
                                onClick={() => act(() => declineOwnMutualSurrender(tc.id), "Declined. Your tenancy continues.")}>Decline</button>
                            </div>
                          )}

                          {tc.status === "CONTESTED" && (
                            <div className="space-y-2">
                              <select value={orderOutcome} onChange={(e) => setOrderOutcome(e.target.value as typeof orderOutcome)} className="h-10 w-full rounded-xl border border-slate-300 px-3">
                                <option value="ENDS_ON_DATE">A court/tribunal order ends the tenancy</option>
                                <option value="TENANCY_CONTINUES">A court/tribunal order says it continues</option>
                              </select>
                              <input value={orderRef} onChange={(e) => setOrderRef(e.target.value)} placeholder="Order reference" className="h-10 w-full rounded-xl border border-slate-300 px-3" />
                              {orderOutcome === "ENDS_ON_DATE" && (
                                <input type="date" value={orderDate} onChange={(e) => setOrderDate(e.target.value)} className="h-10 w-full rounded-xl border border-slate-300 px-3" aria-label="End date in the order" />
                              )}
                              <button type="button" disabled={caseBusy || !orderRef.trim() || (orderOutcome === "ENDS_ON_DATE" && !orderDate)} className={`${btn} bg-[#0E2F73] text-white`}
                                onClick={() => act(() => renterTermination.recordExternalDecision(tc.id, {
                                  outcome: orderOutcome, orderRef: orderRef.trim(), effectiveDate: orderOutcome === "ENDS_ON_DATE" ? orderDate : undefined,
                                }), "Order recorded.")}>Record order</button>
                            </div>
                          )}

                          <div className="flex flex-wrap gap-2">
                            {iOpened && ["AWAITING_RESPONSE", "CONTESTED", "SURRENDER_PROPOSED", "EFFECTIVE_DATE_SET", "OPENED"].includes(tc.status) && (
                              <button type="button" disabled={caseBusy} className={`${btn} border border-slate-300`}
                                onClick={() => act(() => withdrawOwnTerminationCase(tc.id), "Withdrawn.")}>Withdraw</button>
                            )}
                            {!iOpened && !tc.noticeAcknowledgedAt && (
                              <button type="button" disabled={caseBusy} className={`${btn} border border-slate-300`}
                                onClick={() => act(() => renterTermination.acknowledge(tc.id), "Thanks — your host can see you received it.")}>I received this notice</button>
                            )}
                          </div>
                        </div>
                      );
                    })()}
                    <div className="border border-[#E1E7F1] rounded-2xl divide-y divide-[#EEF1F7]">
                      <div className="p-4 flex items-center justify-between gap-3">
                        <div>
                          <strong className="text-sm text-[#0B1B3F] block">End of rental & renewals</strong>
                          <span className="text-xs text-[#5B6886]">
                            Fixed term ends {selectedItem.to}. No automatic renewal.
                          </span>
                        </div>
                        <button
                          type="button"
                          onClick={() => setDrawerTab("agreement")}
                          className="px-3 py-1.5 rounded-lg border border-slate-300 text-xs font-bold text-[#0E2F73] hover:border-[#0E2F73]"
                        >
                          View agreement
                        </button>
                      </div>

                      <div className="p-4 flex items-center justify-between gap-3">
                        <div>
                          <strong className="text-sm text-[#0B1B3F] block">Request early end / Move-out notice</strong>
                          <span className="text-xs text-[#5B6886]">
                            Ending a fixed term early needs host agreement or notice under agreement #{selectedItem.agr}
                          </span>
                        </div>
                        <button
                          type="button"
                          onClick={() => openEarlyEndModal(selectedItem.occupancy)}
                          className="px-3 py-1.5 rounded-lg border border-[#0E2F73] text-xs font-bold text-[#0E2F73] hover:bg-[#0E2F73] hover:text-white transition-colors"
                        >
                          Request
                        </button>
                      </div>

                      {selectedItem.st === "upcoming" && (
                        <div className="p-4 flex items-center justify-between gap-3 bg-[#FFF7F7]">
                          <div>
                            <strong className="text-sm text-[#B42318] block">Cancel before move-in</strong>
                            <span className="text-xs text-[#5B6886]">
                              Cancel a signed agreement before occupying the room.
                            </span>
                          </div>
                          <button
                            type="button"
                            onClick={() => openCancelBookingModal(selectedItem.occupancy)}
                            className="px-3 py-1.5 rounded-lg bg-[#B42318] text-xs font-bold text-white hover:bg-[#8F1B12] transition-colors"
                          >
                            Cancel
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* TAB: Documents */}
                {drawerTab === "documents" && (
                  <div className="space-y-3">
                    <div className="p-4 border border-[#E1E7F1] rounded-2xl flex items-center justify-between gap-3">
                      <div className="flex items-center gap-3">
                        <FileText className="w-5 h-5 text-[#0E2F73]" />
                        <div>
                          <strong className="text-sm text-[#0B1B3F] block">Agreement #{selectedItem.agr}</strong>
                          <span className="text-xs text-[#5B6886]">Current executed version</span>
                        </div>
                      </div>
                      {selectedItem.occupancy.agreementId && (
                        <a
                          href={tenantAgreementPdfUrl(selectedItem.occupancy.agreementId)}
                          download
                          className="text-xs font-bold text-[#0E2F73] hover:underline"
                        >
                          Download
                        </a>
                      )}
                    </div>

                    <div className="p-4 border border-[#E1E7F1] rounded-2xl flex items-center justify-between gap-3">
                      <div className="flex items-center gap-3">
                        <FileText className="w-5 h-5 text-[#0E2F73]" />
                        <div>
                          <strong className="text-sm text-[#0B1B3F] block">Move-in condition record</strong>
                          <span className="text-xs text-[#5B6886]">Inspected room conditions and photos</span>
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => openConditionModal(selectedItem.occupancy)}
                        className="text-xs font-bold text-[#0E2F73] hover:underline"
                      >
                        View / Edit
                      </button>
                    </div>

                    <div className="p-4 border border-[#E1E7F1] rounded-2xl flex items-center justify-between gap-3">
                      <div className="flex items-center gap-3">
                        <FileText className="w-5 h-5 text-[#0E2F73]" />
                        <div>
                          <strong className="text-sm text-[#0B1B3F] block">Required disclosures</strong>
                          <span className="text-xs text-[#5B6886]">House rules · occupancy limits</span>
                        </div>
                      </div>
                      <span className="text-xs font-bold text-[#05603A]">Acknowledged</span>
                    </div>
                  </div>
                )}

                {/* TAB: Disputes */}
                {drawerTab === "disputes" && (
                  <div className="space-y-4">
                    <DisputeCasesSection
                      client={renterDisputes}
                      viewerRole="RENTER"
                      cases={disputeCases.filter((c) => c.occupancyId === selectedItem.occupancy.id)}
                      showToast={showToast}
                      onChanged={load}
                      occupancyId={selectedItem.occupancy.id}
                      emptyMessage="No disputes on this rental."
                    />
                  </div>
                )}

                {/* TAB: Transaction Record */}
                {drawerTab === "record" && (
                  <div className="space-y-4">
                    <RentalTransactionRecord occupancyId={selectedItem.occupancy.id} role="renter" />
                  </div>
                )}
              </div>

              {/* Drawer Footer */}
              <div className="p-6 border-t border-[#EEF1F7] flex items-center justify-between gap-3 bg-[#F7F9FD]">
                <Link
                  href="/account/applications"
                  className="w-full inline-flex items-center justify-center gap-2 h-11 rounded-xl border border-[#0E2F73] text-[#0E2F73] hover:bg-[#0E2F73] hover:text-white text-xs sm:text-sm font-bold transition-colors"
                >
                  <MessageSquare className="w-4 h-4" /> Message Host / Manager
                </Link>
              </div>
            </aside>
          </div>,
          document.body
        )}

      {/* ======================================================== */}
      {/* 8. Modals (Review Recipient, Payment Instructions, etc.) */}
      {/* ======================================================== */}

      {/* Recipient Review Modal */}
      {mounted &&
        activeModal === "recip" &&
        modalItem &&
        createPortal(
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
            <div className="absolute inset-0 bg-[#081638]/45 backdrop-blur-[2px]" onClick={() => setActiveModal(null)} />
            <div className="relative w-full max-w-lg bg-white rounded-[24px] p-6 shadow-2xl space-y-4 z-10">
              <h2 className="text-xl font-extrabold text-[#0E2F73]">Review your payment recipient</h2>

              <div className="p-4 rounded-2xl bg-[#E7F8EF] border border-[#8FD9B4] flex items-start gap-3">
                <ShieldCheck className="w-6 h-6 text-[#05603A] flex-shrink-0" />
                <div>
                  <span className="text-[10px] font-extrabold uppercase tracking-wider text-[#05603A] block">
                    Verified Payment Recipient
                  </span>
                  <strong className="text-sm text-[#0B1B3F] block mt-0.5">{modalItem.recipient}</strong>
                  <p className="text-xs text-[#05603A] mt-1">
                    Payment Receipt Authority verified by Zoiko Rooms. Zoiko Rooms is not the payee.
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="p-3 border border-[#E1E7F1] rounded-xl flex flex-col gap-0.5">
                  <span className="text-[10px] font-bold text-[#5B6886] uppercase">Recipient role</span>
                  <strong className="text-[#0B1B3F]">Landlord / Owner</strong>
                </div>
                <div className="p-3 border border-[#E1E7F1] rounded-xl flex flex-col gap-0.5">
                  <span className="text-[10px] font-bold text-[#5B6886] uppercase">Verified status</span>
                  <strong className="text-[#05603A]">Verified on agreement</strong>
                </div>
                <div className="p-3 border border-[#E1E7F1] rounded-xl flex flex-col gap-0.5">
                  <span className="text-[10px] font-bold text-[#5B6886] uppercase">First payment</span>
                  <strong className="text-[#0B1B3F]">{modalItem.payNext}</strong>
                </div>
                <div className="p-3 border border-[#E1E7F1] rounded-xl flex flex-col gap-0.5">
                  <span className="text-[10px] font-bold text-[#5B6886] uppercase">Method</span>
                  <strong className="text-[#0B1B3F]">Bank transfer to verified account</strong>
                </div>
              </div>

              <p className="text-xs text-[#B42318] font-medium bg-[#FFF5F5] p-3 rounded-xl border border-[#F3C2C2]">
                Never send rent using details from chat messages or external emails. Only use recipient payment methods confirmed on this portal.
              </p>

              <div className="flex justify-end gap-2.5 pt-2">
                <button
                  type="button"
                  onClick={() => setActiveModal(null)}
                  className="px-4 py-2.5 rounded-xl border border-slate-300 text-xs font-bold text-[#0E2F73] hover:bg-slate-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleConfirmRecipient}
                  className="px-4 py-2.5 rounded-xl bg-[#0E2F73] hover:bg-[#0B2560] text-xs font-bold text-white transition-colors"
                >
                  I’ve reviewed the recipient
                </button>
              </div>
            </div>
          </div>,
          document.body
        )}

      {/* Payment Instructions -- the real, verified details for the next open payment */}
      {activeModal === "instr" && modalItem && (() => {
        const open = (modalItem.occupancy.agreementId !== null ? rentalPaymentObligations[modalItem.occupancy.agreementId] ?? [] : [])
          .filter((o) => ["OVERDUE", "DUE", "UPCOMING", "PARTIALLY_PAID"].includes(o.status))
          .sort((x, y) => x.dueDate.localeCompare(y.dueDate));
        return open.length > 0 ? (
          <InstructionsModalView
            obligation={open[0]}
            onClose={() => setActiveModal(null)}
            onRecordPayment={() => {
              setActiveModal(null);
              router.push("/account/rent-payments");
            }}
          />
        ) : (
          <Modal open onClose={() => setActiveModal(null)} title="Payment method">
            <p className="text-sm text-slate-600">Nothing is due on this rental right now.</p>
          </Modal>
        );
      })()}

      {/* Early End / Move-out Modal */}
      {mounted &&
        activeModal === "early" &&
        modalItem &&
        createPortal(
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
            <div className="absolute inset-0 bg-[#081638]/45 backdrop-blur-[2px]" onClick={() => setActiveModal(null)} />
            <div className="relative w-full max-w-lg bg-white rounded-[24px] p-6 shadow-2xl space-y-4 z-10">
              <h2 className="text-xl font-extrabold text-[#0E2F73]">End your tenancy</h2>
              <p className="text-xs text-[#5B6886]">Your agreement (#{modalItem.agr}) runs until {modalItem.to}.</p>

              <div className="space-y-3 text-xs">
                <fieldset className="space-y-2">
                  {(
                    [
                      ["notice", "Give notice", "Ends on the earliest date your market's notice rules allow."],
                      ["agree", "Agree a date with my host", "Your host (and any co-tenant) must agree to the date."],
                      ["endOfTerm", "I'm leaving when the term ends", `Lets your host know you'll move out on ${modalItem.to}.`],
                    ] as const
                  ).map(([value, title, help]) => (
                    <label key={value} className="flex cursor-pointer items-start gap-2 rounded-xl border border-slate-200 p-2.5">
                      <input type="radio" name="early-end-path" checked={earlyEndPath === value} onChange={() => setEarlyEndPath(value)} className="mt-0.5" />
                      <span>
                        <span className="block font-bold text-[#0B1B3F]">{title}</span>
                        <span className="text-[#5B6886]">{help}</span>
                      </span>
                    </label>
                  ))}
                </fieldset>
                {earlyEndPath === "agree" && (
                  <div>
                    <label className="font-bold text-[#3A4866] block mb-1">Proposed end date</label>
                    <input
                      type="date"
                      value={earlyEndDate}
                      min={new Date().toISOString().slice(0, 10)}
                      onChange={(e) => setEarlyEndDate(e.target.value)}
                      className="w-full h-11 px-3 border border-slate-300 rounded-xl text-sm"
                    />
                  </div>
                )}
                <div>
                  <label className="font-bold text-[#3A4866] block mb-1">Reason for request</label>
                  <textarea
                    rows={3}
                    value={earlyEndReason}
                    onChange={(e) => setEarlyEndReason(e.target.value)}
                    placeholder="Describe your circumstances or reason for early surrender"
                    className="w-full p-3 border border-slate-300 rounded-xl text-sm outline-none focus:border-[#0E2F73]"
                  />
                </div>
              </div>

              <div className="flex justify-end gap-2.5 pt-2">
                <button
                  type="button"
                  onClick={() => setActiveModal(null)}
                  className="px-4 py-2.5 rounded-xl border border-slate-300 text-xs font-bold text-[#0E2F73]"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={earlyEndSubmitting}
                  onClick={handleSendEarlyEndRequest}
                  className="px-4 py-2.5 rounded-xl bg-[#0E2F73] hover:bg-[#0B2560] text-xs font-bold text-white transition-colors"
                >
                  {earlyEndSubmitting ? "Sending..." : "Send request"}
                </button>
              </div>
            </div>
          </div>,
          document.body
        )}

      {/* Sublet Request Modal */}
      {mounted &&
        activeModal === "sublet" &&
        modalItem &&
        createPortal(
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
            <div className="absolute inset-0 bg-[#081638]/45 backdrop-blur-[2px]" onClick={() => setActiveModal(null)} />
            <div className="relative w-full max-w-xl bg-white rounded-[24px] p-6 shadow-2xl space-y-4 z-10 max-h-[90vh] overflow-y-auto">
              <div className="flex justify-between items-center">
                <h2 className="text-xl font-extrabold text-[#0E2F73]">Request to Sublet</h2>
                <span className="text-xs font-bold text-[#5B6886]">Step {subletStep} of 3</span>
              </div>

              {subletError && (
                <div className="p-3 rounded-xl bg-[#FFF5F5] border border-[#F3C2C2] text-xs text-[#B42318] font-semibold">
                  {subletError}
                </div>
              )}

              {subletStep === 1 && (
                <div className="space-y-3 text-xs">
                  <div>
                    <label className="font-bold text-[#3A4866] block mb-1">Arrangement Type</label>
                    <select
                      value={arrangementType}
                      onChange={(e) => setArrangementType(e.target.value as SubletArrangementType)}
                      className="w-full h-11 px-3 border border-slate-300 rounded-xl text-sm"
                    >
                      <option value="ASSIGNMENT_FULL">Full Assignment (Swap renter)</option>
                      <option value="REPLACEMENT_OCCUPANT">Replacement Occupant</option>
                      <option value="SUBLEASE_PARTIAL" disabled={!modalOccupancy?.roomHasSpaceForCoTenant}>
                        Sublease Partial{modalOccupancy?.roomHasSpaceForCoTenant ? "" : " (room is full)"}
                      </option>
                      <option value="ADD_CO_TENANT" disabled={!modalOccupancy?.roomHasSpaceForCoTenant}>
                        Add Co-Tenant{modalOccupancy?.roomHasSpaceForCoTenant ? "" : " (room is full)"}
                      </option>
                    </select>
                    {!modalOccupancy?.roomHasSpaceForCoTenant && (
                      <p className="mt-1 text-[11px] text-[#5B6886]">
                        This room is already at its occupant limit, so only a full handover is possible. Your host can
                        raise the room&apos;s occupant limit if it can be shared.
                      </p>
                    )}
                  </div>
                  <div>
                    <label className="font-bold text-[#3A4866] block mb-1">Reason for subletting</label>
                    <textarea
                      rows={3}
                      value={subletReason}
                      onChange={(e) => setSubletReason(e.target.value)}
                      placeholder="Why do you wish to sublet or replace your occupancy?"
                      className="w-full p-3 border border-slate-300 rounded-xl text-sm outline-none focus:border-[#0E2F73]"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="font-bold text-[#3A4866] block mb-1">Proposed Start Date</label>
                      <input
                        type="date"
                        value={subletStartDate}
                        onChange={(e) => setSubletStartDate(e.target.value)}
                        className="w-full h-11 px-3 border border-slate-300 rounded-xl text-sm"
                      />
                    </div>
                    <div>
                      <label className="font-bold text-[#3A4866] block mb-1">Proposed End Date</label>
                      <input
                        type="date"
                        value={subletEndDate}
                        onChange={(e) => setSubletEndDate(e.target.value)}
                        className="w-full h-11 px-3 border border-slate-300 rounded-xl text-sm"
                      />
                    </div>
                  </div>
                  <div className="flex justify-end gap-2 pt-2">
                    <button
                      type="button"
                      onClick={() => setActiveModal(null)}
                      className="px-4 py-2 rounded-xl border border-slate-300 font-bold"
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      onClick={() => setSubletStep(2)}
                      className="px-4 py-2 rounded-xl bg-[#0E2F73] text-white font-bold"
                    >
                      Continue
                    </button>
                  </div>
                </div>
              )}

              {subletStep === 2 && (
                <div className="space-y-3 text-xs">
                  <p className="text-slate-600">
                    Enter the email address of the prospective replacement renter. They must have an active Zoiko account.
                  </p>
                  <div>
                    <label className="font-bold text-[#3A4866] block mb-1">Proposed Renter Email</label>
                    <input
                      type="email"
                      value={proposedEmail}
                      onChange={(e) => setProposedEmail(e.target.value)}
                      placeholder="renter@example.com"
                      className="w-full h-11 px-3 border border-slate-300 rounded-xl text-sm"
                    />
                  </div>
                  <div className="flex justify-between items-center pt-2">
                    <button
                      type="button"
                      onClick={() => setSubletStep(1)}
                      className="px-4 py-2 rounded-xl border border-slate-300 font-bold"
                    >
                      Back
                    </button>
                    <button
                      type="button"
                      disabled={lookingUp}
                      onClick={handleLookupSubletEmail}
                      className="px-4 py-2 rounded-xl bg-[#0E2F73] text-white font-bold"
                    >
                      {lookingUp ? "Verifying..." : "Verify Renter"}
                    </button>
                  </div>
                </div>
              )}

              {subletStep === 3 && (
                <div className="space-y-3 text-xs">
                  <div className="p-3 bg-emerald-50 text-emerald-800 rounded-xl border border-emerald-200">
                    <UserCheck className="w-4 h-4 inline mr-1" />
                    Proposed Renter: <strong>{lookupResult?.name || proposedEmail}</strong>
                  </div>
                  <div>
                    <label className="font-bold text-[#3A4866] block mb-1">Authority Evidence / Reference (Optional)</label>
                    <input
                      type="text"
                      value={evidenceRef}
                      onChange={(e) => setEvidenceRef(e.target.value)}
                      placeholder="e.g. Host written consent reference"
                      className="w-full h-11 px-3 border border-slate-300 rounded-xl text-sm"
                    />
                  </div>
                  <div className="flex items-center gap-2 pt-2">
                    <input
                      type="checkbox"
                      id="confirm-sublet"
                      checked={confirmNoPermissionYet}
                      onChange={(e) => setConfirmNoPermissionYet(e.target.checked)}
                      className="rounded"
                    />
                    <label htmlFor="confirm-sublet" className="text-slate-600">
                      I understand this is a request and the host has full discretion to approve or decline.
                    </label>
                  </div>
                  <div className="flex justify-between items-center pt-2">
                    <button
                      type="button"
                      onClick={() => setSubletStep(2)}
                      className="px-4 py-2 rounded-xl border border-slate-300 font-bold"
                    >
                      Back
                    </button>
                    <button
                      type="button"
                      disabled={!confirmNoPermissionYet || subletSubmitting}
                      onClick={handleSubmitSubletRequest}
                      className="px-4 py-2 rounded-xl bg-[#0E2F73] text-white font-bold disabled:opacity-50"
                    >
                      {subletSubmitting ? "Submitting..." : "Submit Sublet Request"}
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>,
          document.body
        )}

      {/* Condition Report Modal */}
      {mounted &&
        activeModal === "condition" &&
        modalItem &&
        createPortal(
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
            <div className="absolute inset-0 bg-[#081638]/45 backdrop-blur-[2px]" onClick={() => setActiveModal(null)} />
            <div className="relative w-full max-w-lg bg-white rounded-[24px] p-6 shadow-2xl space-y-4 z-10 max-h-[90vh] overflow-y-auto">
              <h2 className="text-xl font-extrabold text-[#0E2F73]">Condition report items</h2>

              {conditionError && (
                <div className="p-3 rounded-xl bg-[#FFF5F5] border border-[#F3C2C2] text-xs text-[#B42318] font-semibold">
                  {conditionError}
                </div>
              )}

              {/* Recorded Items List */}
              {conditionItems.length > 0 && (
                <div className="space-y-2">
                  <span className="text-xs font-bold text-[#5B6886] uppercase">Existing items</span>
                  <div className="border border-slate-200 rounded-xl divide-y divide-slate-100 max-h-40 overflow-y-auto">
                    {conditionItems.map((ci) => (
                      <div key={ci.id} className="p-3 text-xs flex justify-between items-center">
                        <div>
                          <strong className="text-[#0B1B3F]">{ci.area}</strong> ({ci.reportType})
                          <p className="text-slate-500">{ci.notes}</p>
                        </div>
                        <Badge tone={ci.conditionRating === "DAMAGED" ? "danger" : "neutral"}>
                          {ci.conditionRating || "Recorded"}
                        </Badge>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* New item form */}
              <div className="space-y-3 text-xs border-t border-slate-200 pt-3">
                <strong className="text-sm text-[#0B1B3F] block">Add inspection observation</strong>
                <div>
                  <label className="font-bold text-[#3A4866] block mb-1">Area / Component</label>
                  <input
                    type="text"
                    value={conditionArea}
                    onChange={(e) => setConditionArea(e.target.value)}
                    placeholder="e.g. Master Bedroom walls, Bathroom fixtures"
                    className="w-full h-11 px-3 border border-slate-300 rounded-xl text-sm"
                  />
                </div>
                <div>
                  <label className="font-bold text-[#3A4866] block mb-1">Rating</label>
                  <select
                    value={conditionRating}
                    onChange={(e) => setConditionRating(e.target.value as ConditionRating | "")}
                    className="w-full h-11 px-3 border border-slate-300 rounded-xl text-sm"
                  >
                    <option value="">Select rating (optional)</option>
                    <option value="GOOD">Good</option>
                    <option value="FAIR">Fair</option>
                    <option value="DAMAGED">Damaged / Issue</option>
                  </select>
                </div>
                <div>
                  <label className="font-bold text-[#3A4866] block mb-1">Notes / Description</label>
                  <textarea
                    rows={2}
                    value={conditionNotes}
                    onChange={(e) => setConditionNotes(e.target.value)}
                    placeholder="Specific notes or scratches observed"
                    className="w-full p-3 border border-slate-300 rounded-xl text-sm outline-none"
                  />
                </div>
                <div>
                  <label className="font-bold text-[#3A4866] block mb-1">Attach Photo Evidence</label>
                  <input
                    type="file"
                    accept="image/*"
                    onChange={(e) => setConditionFile(e.target.files?.[0] || null)}
                    className="text-xs"
                  />
                </div>
              </div>

              <div className="flex justify-end gap-2.5 pt-2">
                <button
                  type="button"
                  onClick={() => setActiveModal(null)}
                  className="px-4 py-2.5 rounded-xl border border-slate-300 text-xs font-bold text-[#0E2F73]"
                >
                  Close
                </button>
                <button
                  type="button"
                  disabled={conditionSubmitting}
                  onClick={handleAddConditionReportItem}
                  className="px-4 py-2.5 rounded-xl bg-[#0E2F73] text-white text-xs font-bold hover:bg-[#0B2560]"
                >
                  {conditionSubmitting ? "Saving..." : "Save item"}
                </button>
              </div>
            </div>
          </div>,
          document.body
        )}

      {/* Cancel Before Move-in Modal */}
      {mounted &&
        activeModal === "cancel" &&
        modalItem &&
        createPortal(
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
            <div className="absolute inset-0 bg-[#081638]/45 backdrop-blur-[2px]" onClick={() => setActiveModal(null)} />
            <div className="relative w-full max-w-lg bg-white rounded-[24px] p-6 shadow-2xl space-y-4 z-10">
              <h2 className="text-xl font-extrabold text-[#B42318]">Cancel booking before move-in</h2>
              <p className="text-xs text-[#5B6886]">
                You have not moved into this room yet. You can cancel your occupancy subject to the cancellation policy on agreement #{modalItem.agr}.
              </p>

              {cancelError && (
                <div className="p-3 rounded-xl bg-[#FFF5F5] border border-[#F3C2C2] text-xs text-[#B42318] font-semibold">
                  {cancelError}
                </div>
              )}

              <div>
                <label className="font-bold text-[#3A4866] block mb-1 text-xs">Reason for cancellation</label>
                <textarea
                  rows={3}
                  value={cancelReason}
                  onChange={(e) => setCancelReason(e.target.value)}
                  placeholder="Tell your host why you cannot proceed with move-in"
                  className="w-full p-3 border border-slate-300 rounded-xl text-sm outline-none focus:border-[#B42318]"
                />
              </div>

              <div className="flex justify-end gap-2.5 pt-2">
                <button
                  type="button"
                  onClick={() => setActiveModal(null)}
                  className="px-4 py-2.5 rounded-xl border border-slate-300 text-xs font-bold text-[#0E2F73]"
                >
                  Keep booking
                </button>
                <button
                  type="button"
                  disabled={cancelSubmitting}
                  onClick={handleConfirmCancelBeforeMoveIn}
                  className="px-4 py-2.5 rounded-xl bg-[#B42318] hover:bg-[#8F1B12] text-xs font-bold text-white transition-colors"
                >
                  {cancelSubmitting ? "Cancelling..." : "Confirm cancellation"}
                </button>
              </div>
            </div>
          </div>,
          document.body
        )}
      <DepositClaimsSection side="renter" showToast={showToast} />
    </div>
  );
}
