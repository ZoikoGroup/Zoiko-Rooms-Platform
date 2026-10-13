"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowDownToLine,
  Building2,
  Calendar,
  Check,
  CheckCircle2,
  ChevronDown,
  Clock,
  Copy,
  CreditCard,
  Download,
  ExternalLink,
  Eye,
  FileText,
  Filter,
  HelpCircle,
  Lock,
  Plus,
  Receipt,
  Repeat,
  Search,
  ShieldAlert,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { RoundedFilterDropdown } from "@/components/ui/RoundedFilterDropdown";
import { Toast, useToast, Field, inputClass } from "@/components/user/ui";
import {
  RentalPaymentObligation,
  RentalPaymentRecord,
  RentalPaymentInstruction,
  RentalPaymentMethodCategory,
  RentalPaymentDiscrepancyReason,
  UserOccupancy,
  PublicListing,
} from "@/lib/types";
import { formatCurrency, formatDate, formatMoney } from "@/lib/utils";
import {
  errorMessage,
  getMyRentalPaymentInstructions,
  listMyRentalPaymentObligations,
  listOccupancies,
  listPublicListings,
  markRentalPaymentPaid,
  reportRentalPaymentDiscrepancyAsTenant,
  uploadRentalPaymentEvidence,
} from "@/lib/user-api";
import { rentalPaymentStatusLabel } from "@/lib/status";
import { HowToPay, getHowToPay, payeeTypeLabel } from "@/lib/sublet-payments";
import { resolveBankFieldSchema } from "@/lib/bankFieldSchemas";

const METHOD_OPTIONS: { value: RentalPaymentMethodCategory; label: string }[] = [
  { value: "BANK_TRANSFER", label: "Bank transfer" },
  { value: "UPI", label: "UPI" },
  { value: "CASH", label: "Cash" },
  { value: "OTHER", label: "Other" },
];

const METHOD_LABELS: Record<string, string> = {
  BANK_TRANSFER: "Bank transfer",
  UPI: "UPI",
  CASH: "Cash",
  OTHER: "Other",
};

const DISCREPANCY_OPTIONS: { value: RentalPaymentDiscrepancyReason; label: string }[] = [
  { value: "NOT_ARRIVED", label: "Payment has not arrived" },
  { value: "AMOUNT_DIFFERENT", label: "Amount received is different" },
  { value: "REFERENCE_MISMATCH", label: "Reference cannot be matched" },
  { value: "RETURNED_OR_REVERSED", label: "Payment was returned or reversed" },
  { value: "OTHER", label: "Other" },
];

/** Format short due date like "Nov 1", "Nov 15", "Oct 15" */
function formatShortDueDate(dateStr?: string | null): string {
  if (!dateStr) return "—";
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  } catch {
    return dateStr;
  }
}

/** Check if due date is within the next 7 days */
function isDueSoon(dateStr?: string | null): boolean {
  if (!dateStr) return false;
  try {
    const due = new Date(dateStr).getTime();
    const now = Date.now();
    const diffDays = (due - now) / (1000 * 60 * 60 * 24);
    return diffDays >= 0 && diffDays <= 7;
  } catch {
    return false;
  }
}

export function RentalPaymentsManager() {
  const { toast, showToast } = useToast();

  const [loading, setLoading] = useState(true);
  const [obligations, setObligations] = useState<RentalPaymentObligation[]>([]);
  const [occupancies, setOccupancies] = useState<UserOccupancy[]>([]);
  const [publicListings, setPublicListings] = useState<PublicListing[]>([]);

  // Tab: "RENTAL_PAYMENTS" | "DEPOSITS" | "HISTORY"
  const [activeTab, setActiveTab] = useState<"RENTAL_PAYMENTS" | "DEPOSITS" | "HISTORY">("RENTAL_PAYMENTS");

  // Filter states
  const [searchQuery, setSearchQuery] = useState("");
  const [rentalFilter, setRentalFilter] = useState<string>("ALL");
  const [sortOption, setSortOption] = useState<"NEXT_DUE_FIRST" | "AMOUNT_HIGH" | "AMOUNT_LOW" | "NEWEST">("NEXT_DUE_FIRST");

  // Modal states
  const [viewingInstructions, setViewingInstructions] = useState<RentalPaymentObligation | null>(null);
  const [payingObligation, setPayingObligation] = useState<RentalPaymentObligation | null>(null);
  const [viewingRecord, setViewingRecord] = useState<{
    obligation: RentalPaymentObligation;
    record: RentalPaymentRecord;
  } | null>(null);
  const [disputeModalRecord, setDisputeModalRecord] = useState<RentalPaymentRecord | null>(null);
  const [disputeReason, setDisputeReason] = useState("");
  const [disputeNotes, setDisputeNotes] = useState("");

  // Load real data from backend
  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [obPage, occs, listings] = await Promise.all([
        listMyRentalPaymentObligations(undefined, { limit: 100, offset: 0 }).catch(() => ({
          items: [] as RentalPaymentObligation[],
        })),
        listOccupancies().catch(() => [] as UserOccupancy[]),
        listPublicListings({ limit: 100 }).then((res) => res.items).catch(() => [] as PublicListing[]),
      ]);

      setObligations(obPage.items || []);
      setOccupancies(occs);
      setPublicListings(listings);
    } catch (err) {
      console.warn("Could not load payment obligations:", err);
      showToast(errorMessage(err, "Could not load payments."), "error");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Derive Property name / Rental details for an obligation
  const getObligationPropertyInfo = useCallback(
    (o: RentalPaymentObligation) => {
      // Find matching occupancy or listing
      const occ = occupancies.find((oc) => oc.id === o.occupancyId || oc.listingId === String(o.agreementId));
      const listing = occ ? publicListings.find((l) => l.id === occ.listingId) : undefined;

      const propName = occ?.propertyAddress
        ? `${occ.propertyAddress.split(",")[0]} · Room #${occ.roomId || 1}`
        : occ?.listingName
        ? `${occ.listingName} · Room #${occ.roomId || 1}`
        : listing
        ? `${listing.name} · Room #${occ?.roomId || 1}`
        : o.payeeBasis
        ? `${o.payeeBasis} · Room #${o.occupancyId || 1}`
        : `Rental #${o.occupancyId || o.id}`;

      const rentalCode = `Rental #ZR-R-${o.occupancyId || o.agreementId || o.id}`;
      const recipientName = o.payeeBasis || occ?.hostName || "Verified Landlord";

      return {
        propertyName: propName,
        rentalCode,
        recipientName,
        recipientVerified: true,
      };
    },
    [occupancies, publicListings]
  );

  // Filter tabs
  const tabFilteredObligations = useMemo(() => {
    return obligations.filter((o) => {
      if (activeTab === "RENTAL_PAYMENTS") {
        return o.obligationType === "RENT" || o.obligationType === "OTHER";
      }
      if (activeTab === "DEPOSITS") {
        return o.obligationType === "DEPOSIT";
      }
      if (activeTab === "HISTORY") {
        return o.status === "CONFIRMED" || (o.records && o.records.length > 0);
      }
      return true;
    });
  }, [obligations, activeTab]);

  // Search & Filter
  const filteredObligations = useMemo(() => {
    return tabFilteredObligations
      .filter((o) => {
        const info = getObligationPropertyInfo(o);

        // Search query
        if (searchQuery.trim()) {
          const q = searchQuery.toLowerCase().trim();
          const matchesProp = info.propertyName.toLowerCase().includes(q);
          const matchesCode = info.rentalCode.toLowerCase().includes(q);
          const matchesLabel = o.displayLabel.toLowerCase().includes(q);
          const matchesRecipient = info.recipientName.toLowerCase().includes(q);
          const matchesRef = (o.paymentReference || "").toLowerCase().includes(q);
          if (!matchesProp && !matchesCode && !matchesLabel && !matchesRecipient && !matchesRef) {
            return false;
          }
        }

        // Rental filter
        if (rentalFilter !== "ALL") {
          if (info.propertyName !== rentalFilter && info.rentalCode !== rentalFilter) {
            return false;
          }
        }

        return true;
      })
      .sort((a, b) => {
        if (sortOption === "NEXT_DUE_FIRST") {
          return new Date(a.dueDate).getTime() - new Date(b.dueDate).getTime();
        }
        if (sortOption === "AMOUNT_HIGH") {
          return b.amount - a.amount;
        }
        if (sortOption === "AMOUNT_LOW") {
          return a.amount - b.amount;
        }
        if (sortOption === "NEWEST") {
          return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime();
        }
        return 0;
      });
  }, [tabFilteredObligations, searchQuery, rentalFilter, sortOption, getObligationPropertyInfo]);

  // Derived metrics / KPI cards
  const nextPayment = useMemo(() => {
    const open = obligations
      .filter((o) => o.status === "UPCOMING" || o.status === "DUE" || o.status === "OVERDUE")
      .sort((a, b) => new Date(a.dueDate).getTime() - new Date(b.dueDate).getTime());
    return open[0] || null;
  }, [obligations]);

  const awaitingConfirmationCount = useMemo(() => {
    return obligations.filter(
      (o) =>
        o.status === "RECIPIENT_CONFIRMATION_PENDING" ||
        o.status === "PAYER_RECORDED" ||
        (o.records && o.records.some((r) => r.status === "RECIPIENT_CONFIRMATION_PENDING"))
    ).length;
  }, [obligations]);

  const overdueCount = useMemo(() => {
    return obligations.filter((o) => o.status === "OVERDUE").length;
  }, [obligations]);

  const actionRequiredCount = useMemo(() => {
    return obligations.filter((o) => o.status === "DISPUTED" || o.status === "REVERSED").length;
  }, [obligations]);

  // Tab counts
  const rentCount = useMemo(() => obligations.filter((o) => o.obligationType === "RENT").length, [obligations]);
  const depositCount = useMemo(() => obligations.filter((o) => o.obligationType === "DEPOSIT").length, [obligations]);
  const historyCount = useMemo(
    () => obligations.filter((o) => o.status === "CONFIRMED" || o.records?.length > 0).length,
    [obligations]
  );

  // Distinct property rentals for filter dropdown
  const distinctRentals = useMemo(() => {
    const set = new Set<string>();
    obligations.forEach((o) => {
      const info = getObligationPropertyInfo(o);
      set.add(info.propertyName);
    });
    return Array.from(set);
  }, [obligations, getObligationPropertyInfo]);

  const rentalOptions = useMemo(
    () => [
      { value: "ALL", label: "All rentals" },
      ...distinctRentals.map((r) => ({ value: r, label: r })),
    ],
    [distinctRentals]
  );

  const sortOptions = useMemo(
    () => [
      { value: "NEXT_DUE_FIRST", label: "Next due first" },
      { value: "AMOUNT_HIGH", label: "Amount: high to low" },
      { value: "AMOUNT_LOW", label: "Amount: low to high" },
      { value: "NEWEST", label: "Newest first" },
    ],
    []
  );

  // CSV Export Function
  function handleExportCSV() {
    if (filteredObligations.length === 0) {
      showToast("No payment records to export.", "error");
      return;
    }

    const headers = [
      "Rental / Property",
      "Rental Code",
      "Payment Label",
      "Due Date",
      "Amount",
      "Currency",
      "Status",
      "Verified Recipient",
    ];

    const rows = filteredObligations.map((o) => {
      const info = getObligationPropertyInfo(o);
      return [
        `"${info.propertyName.replace(/"/g, '""')}"`,
        `"${info.rentalCode.replace(/"/g, '""')}"`,
        `"${o.displayLabel.replace(/"/g, '""')}"`,
        `"${o.dueDate}"`,
        o.amount,
        `"${o.currency || "INR"}"`,
        `"${rentalPaymentStatusLabel[o.status] || o.status}"`,
        `"${info.recipientName.replace(/"/g, '""')}"`,
      ];
    });

    const csvContent =
      "data:text/csv;charset=utf-8," + [headers.join(","), ...rows.map((e) => e.join(","))].join("\n");
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `rental_payment_records_${new Date().toISOString().slice(0, 10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    showToast("Payment records exported successfully.", "success");
  }

  // Report Discrepancy Handler (for renter)
  async function handleReportDiscrepancy() {
    if (!disputeModalRecord || !disputeReason) return;
    try {
      await reportRentalPaymentDiscrepancyAsTenant(disputeModalRecord.id, {
        reasonCode: disputeReason as any,
        details: disputeNotes.trim(),
      });
      showToast("Payment issue reported to host.", "success");
      setDisputeModalRecord(null);
      setViewingRecord(null);
      loadData();
    } catch (err) {
      showToast(errorMessage(err, "Could not report discrepancy."), "error");
    }
  }

  if (loading) {
    return (
      <div className="py-12">
        <Loader label="Loading payments..." />
      </div>
    );
  }

  const nextPropInfo = nextPayment ? getObligationPropertyInfo(nextPayment) : null;

  return (
    <div className="w-full space-y-5 font-sans">
      {/* 1. Page Header with RENTER WORKSPACE overline & Export Button */}
      <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4">
        <div>
          <div className="text-xs font-bold text-[#D80B0B] uppercase tracking-wider">
            RENTER WORKSPACE
          </div>
          <h1 className="text-3xl sm:text-4xl font-extrabold text-[#0C2B4E] dark:text-white tracking-tight mt-0.5">
            Payments
          </h1>
          <p className="mt-1 text-xs sm:text-sm text-slate-500 dark:text-slate-400 max-w-3xl leading-relaxed">
            Track rent and deposits, view verified payment instructions, record payments you have made, and see when receipt is confirmed.
          </p>
        </div>

        <div className="flex items-center gap-2 self-start sm:self-auto">
          <button
            onClick={handleExportCSV}
            className="inline-flex items-center gap-2 px-5 py-3 text-xs sm:text-sm font-bold text-[#0C2B4E] dark:text-slate-200 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl hover:bg-slate-50 dark:hover:bg-slate-800/80 shadow-sm transition cursor-pointer"
          >
            <Download className="h-4 w-4 text-[#0C2B4E] dark:text-slate-300" />
            <span>Export Payment Records</span>
          </button>
        </div>
      </div>

      {/* 2. 4 KPI Metric Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
        {/* Card 1: NEXT PAYMENT (Solid Dark Navy Card #0C2B4E) */}
        <div className="bg-[#0C2B4E] text-white rounded-[20px] p-3 shadow-sm flex flex-col justify-between gap-2 border border-[#0C2B4E]">
          <div>
            <div className="text-[11px] font-extrabold text-sky-300 uppercase tracking-wider">
              NEXT PAYMENT
            </div>
            <div className="text-2xl sm:text-xl font-extrabold text-white mt-1">
              {nextPayment
                ? `${formatCurrency(nextPayment.amount, nextPayment.currency)} · ${formatShortDueDate(nextPayment.dueDate)}`
                : "No upcoming payments"}
            </div>
          </div>
          <div className="text-xs text-slate-300 truncate">
            {nextPayment && nextPropInfo
              ? `${nextPayment.displayLabel} · ${nextPropInfo.propertyName}`
              : "All obligations settled"}
          </div>
        </div>

        {/* Card 2: Awaiting Confirmation */}
        <div className="bg-white dark:bg-slate-900 rounded-[20px] p-4 sm:p-5 border border-slate-200/80 dark:border-slate-800 shadow-sm flex items-center gap-3.5">
          <div className="size-12 rounded-2xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center shrink-0">
            <span className="text-2xl font-extrabold text-[#0C2B4E] dark:text-slate-100">
              {awaitingConfirmationCount}
            </span>
          </div>
          <div>
            <div className="text-sm sm:text-base font-extrabold text-[#0C2B4E] dark:text-white leading-tight">
              Awaiting
              <br />
              Confirmation
            </div>
            <div className="text-xs text-slate-400 dark:text-slate-500 mt-0.5">
              Marked paid by you
            </div>
          </div>
        </div>

        {/* Card 3: Overdue */}
        <div className="bg-white dark:bg-slate-900 rounded-[20px] p-4 sm:p-5 border border-slate-200/80 dark:border-slate-800 shadow-sm flex items-center gap-3.5">
          <div className="size-12 rounded-2xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center shrink-0">
            <span
              className={`text-2xl font-extrabold ${
                overdueCount > 0 ? "text-[#D80B0B]" : "text-[#D80B0B]"
              }`}
            >
              {overdueCount}
            </span>
          </div>
          <div>
            <div className="text-sm sm:text-base font-extrabold text-[#0C2B4E] dark:text-white leading-tight">
              Overdue
            </div>
            <div className="text-xs text-slate-400 dark:text-slate-500 mt-0.5">
              Past due, not marked paid
            </div>
          </div>
        </div>

        {/* Card 4: Action Required */}
        <div className="bg-white dark:bg-slate-900 rounded-[20px] p-4 sm:p-5 border border-slate-200/80 dark:border-slate-800 shadow-sm flex items-center gap-3.5">
          <div className="size-12 rounded-2xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center shrink-0">
            <span
              className={`text-2xl font-extrabold ${
                actionRequiredCount > 0 ? "text-[#D80B0B]" : "text-[#D80B0B]"
              }`}
            >
              {actionRequiredCount}
            </span>
          </div>
          <div>
            <div className="text-sm sm:text-base font-extrabold text-[#0C2B4E] dark:text-white leading-tight">
              Action Required
            </div>
            <div className="text-xs text-slate-400 dark:text-slate-500 mt-0.5">
              Mismatch or disputed records
            </div>
          </div>
        </div>
      </div>

      {/* 3. Status Banner */}
      <div className="rounded-2xl bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 px-4 py-3.5 flex items-start sm:items-center gap-3">
        <div className="size-6 rounded-xl bg-white dark:bg-slate-700 border border-slate-200 dark:border-slate-600 flex items-center justify-center text-emerald-600 shrink-0">
          <Check className="h-4 w-4 stroke-[2.5]" />
        </div>
        <div className="text-xs sm:text-sm text-slate-700 dark:text-slate-300 leading-relaxed">
          {actionRequiredCount > 0 ? (
            <>
              <span className="font-bold text-[#D80B0B]">Action required on {actionRequiredCount} payment record(s).</span>
              <span> Review disputed transactions or update payment information.</span>
            </>
          ) : awaitingConfirmationCount > 0 ? (
            <>
              <span className="font-bold text-[#0C2B4E] dark:text-white">
                {awaitingConfirmationCount} payment awaiting confirmation.
              </span>
              <span> You marked this payment as paid. Waiting for the host to confirm receipt.</span>
            </>
          ) : (
            <>
              <span className="font-bold text-[#0C2B4E] dark:text-white">No payment action required.</span>
              <span>
                {nextPayment && nextPropInfo
                  ? ` Your next payment is ${formatCurrency(nextPayment.amount, nextPayment.currency)} due ${formatDate(
                      nextPayment.dueDate
                    )} (${nextPayment.displayLabel}, ${nextPropInfo.propertyName}). Payment instructions are available from the verified recipient.`
                  : " All property rent and deposit obligations are up to date."}
              </span>
            </>
          )}
        </div>
      </div>

      {/* 4. Segmented Container: Tabs + Search & Filters + Table */}
      <div className="rounded-3xl bg-white dark:bg-slate-900 border border-slate-200/80 dark:border-slate-800 shadow-sm overflow-hidden">
        {/* Tabs Bar Header */}
        <div className="px-5 py-3.5 border-b border-slate-100 dark:border-slate-800 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div className="inline-flex p-1 bg-slate-100 dark:bg-slate-800 rounded-xl overflow-x-auto">
            <button
              onClick={() => setActiveTab("RENTAL_PAYMENTS")}
              className={`px-3.5 py-2 rounded-lg text-xs font-bold transition whitespace-nowrap cursor-pointer ${
                activeTab === "RENTAL_PAYMENTS"
                  ? "bg-white dark:bg-slate-700 text-[#0C2B4E] dark:text-white shadow-sm"
                  : "text-slate-600 dark:text-slate-400 hover:text-slate-900"
              }`}
            >
              Rental Payments ({rentCount})
            </button>
            <button
              onClick={() => setActiveTab("DEPOSITS")}
              className={`px-3.5 py-2 rounded-lg text-xs font-bold transition whitespace-nowrap cursor-pointer ${
                activeTab === "DEPOSITS"
                  ? "bg-white dark:bg-slate-700 text-[#0C2B4E] dark:text-white shadow-sm"
                  : "text-slate-600 dark:text-slate-400 hover:text-slate-900"
              }`}
            >
              Deposits ({depositCount})
            </button>
            <button
              onClick={() => setActiveTab("HISTORY")}
              className={`px-3.5 py-2 rounded-lg text-xs font-bold transition whitespace-nowrap cursor-pointer ${
                activeTab === "HISTORY"
                  ? "bg-white dark:bg-slate-700 text-[#0C2B4E] dark:text-white shadow-sm"
                  : "text-slate-600 dark:text-slate-400 hover:text-slate-900"
              }`}
            >
              Payment History ({historyCount})
            </button>
          </div>

          <div className="text-xs font-semibold text-slate-400 dark:text-slate-500">
            {filteredObligations.length} records
          </div>
        </div>

        {/* Search & Filter Row */}
        <div className="px-5 py-3 border-b border-slate-100 dark:border-slate-800 flex flex-col md:flex-row items-stretch md:items-center gap-3">
          {/* Search Box */}
          <div className="relative flex-1">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search payments"
              className="w-full pl-10 pr-4 py-2 text-xs sm:text-sm bg-slate-50 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700 rounded-full outline-none focus:border-indigo-400 text-slate-900 dark:text-slate-100 placeholder:text-slate-400 transition shadow-2xs"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery("")}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          {/* Rental Select */}
          <RoundedFilterDropdown
            label="Rental"
            value={rentalFilter}
            onChange={setRentalFilter}
            options={rentalOptions}
            size="sm"
            className="flex-initial min-w-[150px]"
          />

          {/* Sort Select */}
          <RoundedFilterDropdown
            label="Sort"
            value={sortOption}
            onChange={(v) => setSortOption(v as any)}
            options={sortOptions}
            size="sm"
            align="right"
            className="flex-initial min-w-[160px]"
          />
        </div>

        {/* 5. Payments Table (Desktop `lg+`) & Cards (Mobile `< lg`) */}
        {filteredObligations.length === 0 ? (
          <div className="p-8 sm:p-12 text-center flex flex-col items-center justify-center">
            <div className="w-12 h-12 rounded-2xl bg-indigo-50 dark:bg-indigo-900/20 text-[#0C2B4E] dark:text-indigo-400 flex items-center justify-center mb-3">
              <CreditCard className="h-6 w-6" />
            </div>
            <h3 className="text-base font-bold text-[#0C2B4E] dark:text-white">
              No payment records
            </h3>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 max-w-sm">
              Rental payments and deposit obligations for your active rooms will appear here.
            </p>
            <div className="mt-4">
              <Link href="/find-a-room">
                <button className="px-4 py-2 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-xs font-bold text-[#0C2B4E] dark:text-white hover:bg-slate-50 transition shadow-sm cursor-pointer">
                  Find a Room
                </button>
              </Link>
            </div>
          </div>
        ) : (
          <div>
            {/* Desktop Table View (lg+) */}
            <div className="hidden lg:block overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-slate-100 dark:border-slate-800 bg-slate-50/70 dark:bg-slate-800/40 text-[10px] font-bold text-slate-400 uppercase tracking-wider">
                    <th className="py-3 px-5">RENTAL / PROPERTY</th>
                    <th className="py-3 px-4">PAYMENT</th>
                    <th className="py-3 px-4">DUE</th>
                    <th className="py-3 px-4">AMOUNT</th>
                    <th className="py-3 px-4">STATUS</th>
                    <th className="py-3 px-4">VERIFIED RECIPIENT</th>
                    <th className="py-3 px-5 text-right">NEXT ACTION</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800/60 text-xs">
                  {filteredObligations.map((o) => {
                    const info = getObligationPropertyInfo(o);
                    const dueSoon = isDueSoon(o.dueDate);
                    const isAwaitingConfirmation =
                      o.status === "RECIPIENT_CONFIRMATION_PENDING" ||
                      o.status === "PAYER_RECORDED" ||
                      (o.records && o.records.some((r) => r.status === "RECIPIENT_CONFIRMATION_PENDING"));
                    const isUpcoming = o.status === "UPCOMING";
                    const isPaid = o.status === "CONFIRMED";
                    const isOverdue = o.status === "OVERDUE";
                    const lastRecord = o.records && o.records[o.records.length - 1];

                    return (
                      <tr
                        key={o.id}
                        className="hover:bg-slate-50/50 dark:hover:bg-slate-800/30 transition-colors"
                      >
                        {/* Column 1: Rental / Property */}
                        <td className="py-4 px-5 align-top">
                          <div className="font-bold text-slate-900 dark:text-slate-100 text-sm">
                            {info.propertyName}
                          </div>
                          <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                            {info.rentalCode}
                          </div>
                        </td>

                        {/* Column 2: Payment */}
                        <td className="py-4 px-4 align-top">
                          <div className="font-bold text-slate-900 dark:text-slate-100">
                            {o.displayLabel}
                          </div>
                        </td>

                        {/* Column 3: Due */}
                        <td className="py-4 px-4 align-top">
                          <div className="font-bold text-slate-900 dark:text-slate-100">
                            {formatShortDueDate(o.dueDate)}
                          </div>
                        </td>

                        {/* Column 4: Amount */}
                        <td className="py-4 px-4 align-top">
                          <div className="font-bold text-slate-900 dark:text-slate-100 text-base">
                            {formatCurrency(o.amount, o.currency)}
                          </div>
                        </td>

                        {/* Column 5: Status */}
                        <td className="py-4 px-4 align-top">
                          {isAwaitingConfirmation && (
                            <div>
                              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-extrabold uppercase tracking-wide bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300">
                                <Clock className="h-3 w-3" />
                                MARKED PAID — AWAITING CONFIRMATION
                              </span>
                              <div className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">
                                {lastRecord?.declaredDate
                                  ? `Marked paid ${formatShortDueDate(lastRecord.declaredDate)}`
                                  : "Marked paid by you"}
                              </div>
                            </div>
                          )}

                          {!isAwaitingConfirmation && dueSoon && (
                            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-extrabold uppercase tracking-wide bg-amber-50 text-amber-800 border border-amber-200 dark:bg-amber-950/30 dark:text-amber-300 dark:border-amber-800">
                              <Clock className="h-3 w-3" />
                              DUE SOON
                            </span>
                          )}

                          {!isAwaitingConfirmation && !dueSoon && isUpcoming && (
                            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-extrabold uppercase tracking-wide bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-300 border border-slate-200 dark:border-slate-700">
                              <Calendar className="h-3 w-3 text-slate-400" />
                              UPCOMING
                            </span>
                          )}

                          {!isAwaitingConfirmation && isPaid && (
                            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-extrabold uppercase tracking-wide bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300">
                              <Check className="h-3 w-3 stroke-[2.5]" />
                              PAID
                            </span>
                          )}

                          {!isAwaitingConfirmation && isOverdue && (
                            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-extrabold uppercase tracking-wide bg-red-50 text-[#D80B0B] dark:bg-red-950/30 dark:text-red-300">
                              <AlertTriangle className="h-3 w-3" />
                              OVERDUE
                            </span>
                          )}
                        </td>

                        {/* Column 6: Verified Recipient */}
                        <td className="py-4 px-4 align-top">
                          <div className="font-bold text-slate-900 dark:text-slate-100">
                            {info.recipientName}
                          </div>
                          <div className="flex items-center gap-1 text-[11px] font-bold text-emerald-600 dark:text-emerald-400 mt-0.5">
                            <ShieldCheck className="h-3.5 w-3.5 stroke-[2.5]" />
                            <span>Recipient Verified</span>
                          </div>
                        </td>

                        {/* Column 7: Next Action (Tenant Actions) */}
                        <td className="py-4 px-5 align-top text-right">
                          <div className="flex items-center justify-end gap-2">
                            {isAwaitingConfirmation && lastRecord ? (
                              <button
                                onClick={() => setViewingRecord({ obligation: o, record: lastRecord })}
                                className="inline-flex items-center justify-center px-4 py-2.5 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-[#0C2B4E] dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700/60 text-xs font-bold shadow-sm transition cursor-pointer"
                              >
                                View Payment
                              </button>
                            ) : isUpcoming || dueSoon || isOverdue ? (
                              <>
                                <button
                                  onClick={() => setPayingObligation(o)}
                                  className="inline-flex items-center justify-center px-4 py-2.5 rounded-xl bg-[#0C2B4E] hover:bg-[#071c33] text-white text-xs font-bold shadow-sm transition cursor-pointer"
                                >
                                  Record Payment
                                </button>
                                <button
                                  onClick={() => setViewingInstructions(o)}
                                  className="inline-flex items-center justify-center px-3 py-2.5 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-[#0C2B4E] dark:text-slate-200 hover:bg-slate-50 text-xs font-bold shadow-sm transition cursor-pointer"
                                  title="View payment instructions"
                                >
                                  Instructions
                                </button>
                              </>
                            ) : (
                              <button
                                onClick={() => {
                                  if (lastRecord) setViewingRecord({ obligation: o, record: lastRecord });
                                  else setViewingInstructions(o);
                                }}
                                className="inline-flex items-center justify-center px-4 py-2.5 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-[#0C2B4E] dark:text-slate-200 hover:bg-slate-50 text-xs font-bold shadow-sm transition cursor-pointer"
                              >
                                View Payment
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Mobile Card View (lg:hidden) */}
            <div className="lg:hidden divide-y divide-slate-100 dark:divide-slate-800">
              {filteredObligations.map((o) => {
                const info = getObligationPropertyInfo(o);
                const dueSoon = isDueSoon(o.dueDate);
                const isAwaitingConfirmation =
                  o.status === "RECIPIENT_CONFIRMATION_PENDING" ||
                  o.status === "PAYER_RECORDED" ||
                  (o.records && o.records.some((r) => r.status === "RECIPIENT_CONFIRMATION_PENDING"));
                const isUpcoming = o.status === "UPCOMING";
                const isPaid = o.status === "CONFIRMED";
                const isOverdue = o.status === "OVERDUE";
                const lastRecord = o.records && o.records[o.records.length - 1];

                return (
                  <div key={o.id} className="p-4 space-y-3">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="font-bold text-slate-900 dark:text-slate-100 text-sm">
                          {info.propertyName}
                        </div>
                        <div className="text-[11px] text-slate-400 font-mono">
                          {info.rentalCode}
                        </div>
                      </div>
                      <div className="font-extrabold text-slate-900 dark:text-white text-base">
                        {formatCurrency(o.amount, o.currency)}
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-2 text-xs bg-slate-50 dark:bg-slate-800/50 p-2.5 rounded-xl">
                      <div>
                        <span className="text-[10px] text-slate-400 uppercase font-bold block">
                          Payment
                        </span>
                        <span className="font-bold text-slate-800 dark:text-slate-200">
                          {o.displayLabel}
                        </span>
                      </div>
                      <div>
                        <span className="text-[10px] text-slate-400 uppercase font-bold block">
                          Due Date
                        </span>
                        <span className="font-bold text-slate-800 dark:text-slate-200">
                          {formatShortDueDate(o.dueDate)}
                        </span>
                      </div>
                    </div>

                    <div className="flex items-center justify-between text-xs pt-1">
                      <div>
                        <div className="font-bold text-slate-800 dark:text-slate-200">
                          {info.recipientName}
                        </div>
                        <div className="flex items-center gap-1 text-[11px] text-emerald-600 font-bold">
                          <ShieldCheck className="h-3 w-3" />
                          <span>Recipient Verified</span>
                        </div>
                      </div>

                      <div>
                        {isAwaitingConfirmation && (
                          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-bold uppercase bg-blue-50 text-blue-700">
                            <Clock className="h-3 w-3" /> Awaiting Confirmation
                          </span>
                        )}
                        {!isAwaitingConfirmation && dueSoon && (
                          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-bold uppercase bg-amber-50 text-amber-800 border border-amber-200">
                            Due soon
                          </span>
                        )}
                        {!isAwaitingConfirmation && !dueSoon && isUpcoming && (
                          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-bold uppercase bg-slate-100 text-slate-700">
                            Upcoming
                          </span>
                        )}
                        {!isAwaitingConfirmation && isPaid && (
                          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-bold uppercase bg-emerald-50 text-emerald-700">
                            Paid
                          </span>
                        )}
                      </div>
                    </div>

                    <div className="flex gap-2 pt-1">
                      {isAwaitingConfirmation && lastRecord ? (
                        <button
                          onClick={() => setViewingRecord({ obligation: o, record: lastRecord })}
                          className="w-full py-2.5 rounded-xl bg-white border border-slate-200 text-[#0C2B4E] text-xs font-bold shadow-sm"
                        >
                          View Payment
                        </button>
                      ) : isUpcoming || dueSoon || isOverdue ? (
                        <>
                          <button
                            onClick={() => setPayingObligation(o)}
                            className="flex-1 py-2.5 rounded-xl bg-[#0C2B4E] text-white text-xs font-bold shadow-sm"
                          >
                            Record Payment
                          </button>
                          <button
                            onClick={() => setViewingInstructions(o)}
                            className="px-4 py-2.5 rounded-xl bg-white border border-slate-200 text-[#0C2B4E] text-xs font-bold shadow-sm"
                          >
                            Instructions
                          </button>
                        </>
                      ) : (
                        <button
                          onClick={() => {
                            if (lastRecord) setViewingRecord({ obligation: o, record: lastRecord });
                            else setViewingInstructions(o);
                          }}
                          className="w-full py-2.5 rounded-xl bg-white border border-slate-200 text-[#0C2B4E] text-xs font-bold shadow-sm"
                        >
                          View Payment
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* 6. Footer Security Explainer Note */}
      <div className="flex items-start gap-2 pt-2 pb-6 text-[11px] sm:text-xs text-slate-500 dark:text-slate-400 leading-relaxed">
        <Lock className="h-3.5 w-3.5 shrink-0 mt-0.5 text-slate-400" />
        <p>
          Under the current model, rent and deposits go directly to the verified recipient. Zoiko Rooms records obligations, your declarations and recipient confirmations — it does not receive or hold this money.
        </p>
      </div>

      {/* ======================================================== */}
      {/* MODAL 1: Payment Instructions Modal                      */}
      {/* ======================================================== */}
      {viewingInstructions && (
        <InstructionsModalView
          obligation={viewingInstructions}
          onClose={() => setViewingInstructions(null)}
          onRecordPayment={() => {
            const target = viewingInstructions;
            setViewingInstructions(null);
            setPayingObligation(target);
          }}
        />
      )}

      {/* ======================================================== */}
      {/* MODAL 2: Record Payment Modal                            */}
      {/* ======================================================== */}
      {payingObligation && (
        <PayObligationModal
          obligation={payingObligation}
          onClose={() => setPayingObligation(null)}
          onRecorded={() => {
            setPayingObligation(null);
            showToast("Payment recorded. Waiting for host to confirm receipt.", "success");
            loadData();
          }}
        />
      )}

      {/* ======================================================== */}
      {/* MODAL 3: View Payment Record Details Modal               */}
      {/* ======================================================== */}
      {viewingRecord && (
        <Modal
          open={Boolean(viewingRecord)}
          onClose={() => setViewingRecord(null)}
          title={`Payment Record · ${viewingRecord.obligation.displayLabel}`}
        >
          <div className="space-y-4 text-xs">
            <div className="p-4 bg-slate-50 dark:bg-slate-800 rounded-2xl space-y-2.5">
              <div className="flex justify-between">
                <span className="text-slate-400 font-semibold">Status:</span>
                <span className="font-extrabold text-[#0C2B4E] dark:text-white uppercase tracking-wider">
                  {viewingRecord.record.status === "RECIPIENT_CONFIRMATION_PENDING"
                    ? "Marked Paid — Awaiting Confirmation"
                    : viewingRecord.record.status}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400 font-semibold">Amount Declared:</span>
                <span className="font-extrabold text-base text-[#0C2B4E] dark:text-white">
                  {formatCurrency(viewingRecord.record.declaredAmount, viewingRecord.record.declaredCurrency)}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400 font-semibold">Payment Date:</span>
                <span className="font-bold text-slate-800 dark:text-slate-200">
                  {formatDate(viewingRecord.record.declaredDate)}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400 font-semibold">Payment Method:</span>
                <span className="font-bold text-slate-800 dark:text-slate-200">
                  {METHOD_LABELS[viewingRecord.record.paymentMethodCategory] || viewingRecord.record.paymentMethodCategory}
                </span>
              </div>
              {viewingRecord.record.externalReference && (
                <div className="flex justify-between items-center pt-1 border-t border-slate-200 dark:border-slate-700">
                  <span className="text-slate-400 font-semibold">Transaction Reference:</span>
                  <span className="font-mono font-bold text-slate-800 dark:text-slate-200">
                    {viewingRecord.record.externalReference}
                  </span>
                </div>
              )}
            </div>

            <div className="p-3 bg-blue-50/70 dark:bg-blue-950/20 border border-blue-200 dark:border-blue-800 rounded-xl text-blue-900 dark:text-blue-300">
              <p>
                Your host has been notified of this payment declaration. Once verified against their bank statement, they will mark it as confirmed.
              </p>
            </div>

            <div className="flex items-center justify-between pt-2">
              <button
                type="button"
                onClick={() => {
                  setDisputeModalRecord(viewingRecord.record);
                }}
                className="text-xs font-semibold text-red-600 hover:underline"
              >
                Report an issue with this payment
              </button>
              <button
                type="button"
                onClick={() => setViewingRecord(null)}
                className="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-800 rounded-xl font-bold"
              >
                Close
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* ======================================================== */}
      {/* MODAL 4: Report Discrepancy Modal                        */}
      {/* ======================================================== */}
      {disputeModalRecord && (
        <Modal
          open={Boolean(disputeModalRecord)}
          onClose={() => setDisputeModalRecord(null)}
          title="Report Payment Issue"
        >
          <div className="space-y-4 text-xs">
            <p className="text-slate-500">
              If there was an issue with your transfer or amount, report it here so your host can investigate.
            </p>
            <div className="space-y-1">
              <label className="font-bold text-slate-700 dark:text-slate-300">Reason</label>
              <select
                value={disputeReason}
                onChange={(e) => setDisputeReason(e.target.value)}
                className="w-full p-2.5 bg-slate-50 border rounded-xl"
              >
                <option value="">Select a reason...</option>
                {DISCREPANCY_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <label className="font-bold text-slate-700 dark:text-slate-300">Details</label>
              <textarea
                rows={3}
                value={disputeNotes}
                onChange={(e) => setDisputeNotes(e.target.value)}
                placeholder="Describe what happened..."
                className="w-full p-2.5 bg-slate-50 border rounded-xl"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setDisputeModalRecord(null)}
                className="px-4 py-2 border rounded-xl font-bold"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleReportDiscrepancy}
                disabled={!disputeReason}
                className="px-4 py-2 bg-[#D80B0B] text-white rounded-xl font-bold disabled:opacity-50"
              >
                Submit Report
              </button>
            </div>
          </div>
        </Modal>
      )}

      <Toast toast={toast} />
    </div>
  );
}

/** Modernized Instructions Modal View */
function InstructionsModalView({
  obligation,
  onClose,
  onRecordPayment,
}: {
  obligation: RentalPaymentObligation;
  onClose: () => void;
  onRecordPayment: () => void;
}) {
  const { toast, showToast } = useToast();
  const [view, setView] = useState<HowToPay | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setLoading(true);
    setError("");
    getHowToPay(obligation.id)
      .then(setView)
      .catch((err) => setError(errorMessage(err, "Payment details aren't available yet.")))
      .finally(() => setLoading(false));
  }, [obligation]);

  async function copy(text: string, what: string) {
    try {
      await navigator.clipboard.writeText(text);
      showToast(`${what} copied.`, "success");
    } catch {
      showToast("Could not copy to clipboard.", "error");
    }
  }

  const schema = view?.details ? resolveBankFieldSchema(view.countryCode, view.method as RentalPaymentMethodCategory) : null;
  const detailRows = view?.details
    ? Object.entries(view.details).map(([key, value]) => ({ key, value, label: schema?.fields.find((f) => f.key === key)?.label ?? key }))
    : [];

  return (
    <Modal
      open={Boolean(obligation)}
      onClose={onClose}
      title={`Payment Instructions · ${obligation.displayLabel}`}
    >
      {loading ? (
        <div className="py-8 text-center text-xs text-slate-500">Loading instructions...</div>
      ) : error ? (
        <div className="p-4 bg-amber-50 text-amber-800 rounded-xl text-xs">{error}</div>
      ) : view ? (
        <div className="space-y-4 text-xs">
          {/* Summary Box */}
          <div className="p-3.5 bg-slate-50 dark:bg-slate-800 rounded-xl space-y-2">
            <div className="flex justify-between">
              <span className="text-slate-400 font-semibold">Amount Due:</span>
              <span className="font-extrabold text-base text-[#0C2B4E] dark:text-white">
                {formatCurrency(obligation.amount, obligation.currency)}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400 font-semibold">Due Date:</span>
              <span className="font-bold text-slate-800 dark:text-slate-200">
                {formatDate(obligation.dueDate)}
              </span>
            </div>
            <div className="flex justify-between items-center pt-1 border-t border-slate-200 dark:border-slate-700">
              <span className="text-slate-400 font-semibold">Payment Reference:</span>
              <div className="flex items-center gap-1.5 font-mono font-bold text-indigo-600 dark:text-indigo-400">
                <span>{view.paymentReference || `ZR-R-${obligation.id}`}</span>
                <button
                  type="button"
                  onClick={() => copy(view.paymentReference || `ZR-R-${obligation.id}`, "Payment reference")}
                  className="p-1 hover:bg-slate-200 dark:hover:bg-slate-700 rounded cursor-pointer"
                >
                  <Copy className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          </div>

          {/* Payee Details */}
          <div className="p-3.5 bg-emerald-50/70 dark:bg-emerald-950/20 border border-emerald-200 dark:border-emerald-800 rounded-xl space-y-2">
            <div className="flex items-center gap-1.5 font-bold text-emerald-800 dark:text-emerald-300">
              <ShieldCheck className="h-4 w-4" />
              <span>Verified Payee Information</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Pay to:</span>
              <span className="font-bold text-slate-800 dark:text-slate-100">{view.payee.name}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Payee Type:</span>
              <span className="font-semibold text-slate-700 dark:text-slate-200">{payeeTypeLabel[view.payee.type] || view.payee.type}</span>
            </div>
          </div>

          {/* Bank / UPI / Cash Details */}
          {detailRows.length > 0 && (
            <div className="p-3.5 bg-slate-50 dark:bg-slate-800 rounded-xl space-y-2">
              <span className="font-bold text-slate-700 dark:text-slate-300 block">
                {view.method === "UPI" ? "UPI Transfer Details" : "Bank Transfer Details"}
              </span>
              {detailRows.map((row) => (
                <div key={row.key} className="flex justify-between items-center py-1 border-b border-slate-100 last:border-b-0">
                  <span className="text-slate-400">{row.label}:</span>
                  <div className="flex items-center gap-1.5 font-mono font-bold text-slate-800 dark:text-slate-200">
                    <span>{row.value}</span>
                    <button
                      type="button"
                      onClick={() => copy(row.value, row.label)}
                      className="p-1 hover:bg-slate-200 rounded cursor-pointer"
                    >
                      <Copy className="h-3 w-3" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {view.additionalInstructions && (
            <div className="p-3 bg-slate-50 rounded-xl">
              <span className="font-bold block text-slate-600 mb-1">Notes from Host:</span>
              <p className="text-slate-700 whitespace-pre-wrap">{view.additionalInstructions}</p>
            </div>
          )}

          {/* Action CTAs */}
          <div className="flex items-center justify-between pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-xl font-bold"
            >
              Close
            </button>
            <button
              type="button"
              onClick={onRecordPayment}
              className="px-5 py-2.5 bg-[#0C2B4E] hover:bg-[#071c33] text-white rounded-xl font-bold shadow-sm"
            >
              I&apos;ve Paid — Record Payment
            </button>
          </div>
        </div>
      ) : null}
      <Toast toast={toast} />
    </Modal>
  );
}

/** Record Payment Modal for Tenant */
function PayObligationModal({
  obligation,
  onClose,
  onRecorded,
}: {
  obligation: RentalPaymentObligation | null;
  onClose: () => void;
  onRecorded: () => void;
}) {
  const [amount, setAmount] = useState("");
  const [date, setDate] = useState("");
  const [method, setMethod] = useState<RentalPaymentMethodCategory>("BANK_TRANSFER");
  const [reference, setReference] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!obligation) return;
    setAmount(String(obligation.outstandingAmount > 0 ? obligation.outstandingAmount : obligation.amount));
    setDate(new Date().toISOString().slice(0, 10));
    setMethod("BANK_TRANSFER");
    setReference("");
    setFile(null);
    setError("");
  }, [obligation]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!obligation) return;
    setSubmitting(true);
    setError("");
    try {
      const updated = await markRentalPaymentPaid(obligation.id, {
        amount: Number(amount),
        currency: obligation.currency,
        declaredDate: date,
        paymentMethodCategory: method,
        externalReference: reference,
      });
      const newRecord = updated.records && updated.records[updated.records.length - 1];
      if (file && newRecord) {
        try {
          await uploadRentalPaymentEvidence(newRecord.id, file);
        } catch {
          // best-effort upload
        }
      }
      onRecorded();
    } catch (err) {
      setError(errorMessage(err, "Could not record this payment."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open={Boolean(obligation)} onClose={onClose} title={`Record Payment · ${obligation?.displayLabel || ""}`}>
      {obligation && (
        <form onSubmit={handleSubmit} className="space-y-4 text-xs">
          {error && <p className="p-3 rounded-xl bg-rose-50 text-rose-700 font-bold">{error}</p>}
          <div className="grid grid-cols-2 gap-3">
            <Field label="Amount paid *">
              <input
                type="number"
                step="0.01"
                min="0"
                required
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                className={inputClass}
              />
            </Field>
            <Field label="Currency">
              <input value={obligation.currency} disabled className={inputClass} />
            </Field>
            <Field label="Date paid *">
              <input
                type="date"
                required
                value={date}
                onChange={(e) => setDate(e.target.value)}
                className={inputClass}
              />
            </Field>
            <Field label="Payment method *">
              <select
                value={method}
                onChange={(e) => setMethod(e.target.value as RentalPaymentMethodCategory)}
                className={inputClass}
              >
                {METHOD_OPTIONS.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <Field label="Transaction reference / UTR" hint="e.g. Bank UTR, IMPS reference, or UPI transaction ID">
            <input
              value={reference}
              onChange={(e) => setReference(e.target.value)}
              placeholder="e.g. UTR1234567890"
              className={inputClass}
            />
          </Field>

          <Field label="Upload receipt / payment evidence (optional)">
            <input
              type="file"
              accept="image/*,.pdf"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="text-xs text-slate-500 file:mr-3 file:py-2 file:px-4 file:rounded-xl file:border-0 file:text-xs file:font-bold file:bg-slate-100 file:text-[#0C2B4E]"
            />
          </Field>

          <div className="flex justify-end gap-2 pt-2 border-t border-slate-100">
            <Button variant="ghost" type="button" onClick={onClose}>
              Cancel
            </Button>
            <Button loading={submitting} type="submit">
              Submit Payment Record
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

/** Backward compatibility export for ApplicationsManager */
export function SingleObligationPaymentCard({
  obligation,
  onChanged,
}: {
  obligation: RentalPaymentObligation;
  onChanged: () => void;
}) {
  const { toast, showToast } = useToast();
  const [paying, setPaying] = useState(false);
  const [showInstructions, setShowInstructions] = useState(false);

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3 p-4 bg-slate-50 dark:bg-slate-800 rounded-2xl border border-slate-200">
        <div>
          <span className="text-xs font-bold uppercase tracking-wider text-slate-400 block">
            {obligation.displayLabel}
          </span>
          <span className="text-lg font-extrabold text-[#0C2B4E] dark:text-white">
            {formatCurrency(obligation.amount, obligation.currency)}
          </span>
          <span className="text-xs text-slate-500 ml-2">Due {formatDate(obligation.dueDate)}</span>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setShowInstructions(true)}>
            Instructions
          </Button>
          <Button size="sm" onClick={() => setPaying(true)}>
            Record Payment
          </Button>
        </div>
      </div>
      <PayObligationModal
        obligation={paying ? obligation : null}
        onClose={() => setPaying(false)}
        onRecorded={() => {
          setPaying(false);
          showToast("Payment recorded successfully.", "success");
          onChanged();
        }}
      />
      {showInstructions && (
        <InstructionsModalView
          obligation={obligation}
          onClose={() => setShowInstructions(false)}
          onRecordPayment={() => {
            setShowInstructions(false);
            setPaying(true);
          }}
        />
      )}
      <Toast toast={toast} />
    </>
  );
}
