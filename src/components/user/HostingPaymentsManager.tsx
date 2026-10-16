"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
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
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { RoundedFilterDropdown } from "@/components/ui/RoundedFilterDropdown";
import { Toast, useToast } from "@/components/user/ui";
import {
  RentalPaymentObligation,
  RentalPaymentRecord,
  RentalPaymentInstruction,
  Property,
  RentalPaymentDiscrepancyReason,
} from "@/lib/types";
import { formatCurrency, formatDate, formatMoney } from "@/lib/utils";
import {
  confirmRentalPaymentReceipt,
  errorMessage,
  listHostedProperties,
  listMyRentalPaymentInstructions,
  listMyRentalPaymentObligations,
  listRecipientRentalPaymentObligations,
  recordRentalPaymentReceiptAsRecipient,
  reportRentalPaymentDiscrepancyAsRecipient,
} from "@/lib/user-api";
import { rentalPaymentStatusLabel, rentalPaymentStatusTone } from "@/lib/status";
import { useUserSession } from "@/components/user/UserSessionContext";
import { InstructionsManager } from "@/components/user/RecipientRentalPaymentsManager";
import { paymentMethodDisplay } from "@/components/user/payment-methods/PaymentMethodFields";
import { resolveBankFieldSchema } from "@/lib/bankFieldSchemas";
import { InstructionsModalView } from "@/components/user/RentalPaymentsManager";

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

export function HostingPaymentsManager() {
  const { user } = useUserSession();
  const { toast, showToast } = useToast();

  const [loading, setLoading] = useState(true);
  const [obligations, setObligations] = useState<RentalPaymentObligation[]>([]);
  const [instructions, setInstructions] = useState<RentalPaymentInstruction[]>([]);
  // Payments this user receives; anything else in the list is one they owe
  // (e.g. rent taken over in a sublet handover).
  const [receivableIds, setReceivableIds] = useState<Set<number>>(new Set());
  const [paymentDetailsOpen, setPaymentDetailsOpen] = useState(false);
  const [properties, setProperties] = useState<Property[]>([]);

  // Tab: "RENTAL_PAYMENTS" | "DEPOSITS" | "HISTORY"
  const [activeTab, setActiveTab] = useState<"RENTAL_PAYMENTS" | "DEPOSITS" | "HISTORY">("RENTAL_PAYMENTS");

  // Filter states
  const [searchQuery, setSearchQuery] = useState("");
  const [rentalFilter, setRentalFilter] = useState<string>("ALL");
  const [sortOption, setSortOption] = useState<"NEXT_DUE_FIRST" | "AMOUNT_HIGH" | "AMOUNT_LOW" | "NEWEST">("NEXT_DUE_FIRST");

  // Modal states
  const [viewingInstructions, setViewingInstructions] = useState<RentalPaymentObligation | null>(null);
  // The host's own active payment method -- what the tenant is shown to pay with.
  const activeInstruction = instructions.find((i) => i.status === "ACTIVE") ?? null;
  const [viewingRecord, setViewingRecord] = useState<{
    obligation: RentalPaymentObligation;
    record: RentalPaymentRecord;
  } | null>(null);
  const [busyConfirmId, setBusyConfirmId] = useState<number | null>(null);
  const [disputeModalRecord, setDisputeModalRecord] = useState<RentalPaymentRecord | null>(null);
  const [disputeReason, setDisputeReason] = useState("");
  const [disputeNotes, setDisputeNotes] = useState("");

  // Load data from live FastAPI + PostgreSQL backend
  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [recipientPage, myPage, instr, props] = await Promise.all([
        listRecipientRentalPaymentObligations(undefined, { limit: 100, offset: 0 }).catch(() => ({
          items: [] as RentalPaymentObligation[],
        })),
        listMyRentalPaymentObligations(undefined, { limit: 100, offset: 0 }).catch(() => ({
          items: [] as RentalPaymentObligation[],
        })),
        listMyRentalPaymentInstructions().catch(() => [] as RentalPaymentInstruction[]),
        listHostedProperties().catch(() => [] as Property[]),
      ]);

      // Combine obligations, deduplicating by ID
      const allItemsMap = new Map<number, RentalPaymentObligation>();
      (recipientPage.items || []).forEach((item) => allItemsMap.set(item.id, item));
      (myPage.items || []).forEach((item) => allItemsMap.set(item.id, item));
      const combined = Array.from(allItemsMap.values());

      setObligations(combined);
      setReceivableIds(new Set((recipientPage.items || []).map((item) => item.id)));
      setInstructions(instr);
      setProperties(props);
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


  const activeObligations = obligations;

  // Derive Property name / Rental details for an obligation
  const getObligationPropertyInfo = useCallback(
    (o: RentalPaymentObligation) => {
      // Live DB matching
      const prop = properties.find((p) => p.id === o.occupancyId || p.id === o.agreementId);
      const propName = prop
        ? `${prop.address.split(",")[0] || prop.city} · Room #${o.occupancyId || 1}`
        : o.payeeBasis
        ? `${o.payeeBasis} · Room #${o.occupancyId || 1}`
        : `Rental #${o.occupancyId || o.id}`;
      const rentalCode = `Rental #ZR-R-${o.occupancyId || o.agreementId || o.id}`;
      const tenantName = o.tenantGuestId ? `Tenant #${o.tenantGuestId.replace("guest_", "")}` : "Tenant";
      const recipientName = o.payeeBasis || (prop ? prop.city : "Verified Landlord");

      return {
        propertyName: propName,
        rentalCode,
        tenantName,
        tenantVerified: true,
        recipientName,
        recipientVerified: true,
      };
    },
    [properties]
  );

  // Filter tabs
  const tabFilteredObligations = useMemo(() => {
    return activeObligations.filter((o) => {
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
  }, [activeObligations, activeTab]);

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
          const matchesTenant = info.tenantName.toLowerCase().includes(q);
          const matchesRecipient = info.recipientName.toLowerCase().includes(q);
          const matchesRef = (o.paymentReference || "").toLowerCase().includes(q);
          if (!matchesProp && !matchesCode && !matchesLabel && !matchesTenant && !matchesRecipient && !matchesRef) {
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
    const open = activeObligations
      .filter((o) => o.status === "UPCOMING" || o.status === "DUE" || o.status === "OVERDUE")
      .sort((a, b) => new Date(a.dueDate).getTime() - new Date(b.dueDate).getTime());
    return open[0] || null;
  }, [activeObligations]);

  const awaitingConfirmationCount = useMemo(() => {
    return activeObligations.filter(
      (o) =>
        o.status === "RECIPIENT_CONFIRMATION_PENDING" ||
        o.status === "PAYER_RECORDED" ||
        (o.records && o.records.some((r) => r.status === "RECIPIENT_CONFIRMATION_PENDING"))
    ).length;
  }, [activeObligations]);

  const overdueCount = useMemo(() => {
    return activeObligations.filter((o) => o.status === "OVERDUE").length;
  }, [activeObligations]);

  const actionRequiredCount = useMemo(() => {
    return activeObligations.filter((o) => o.status === "DISPUTED" || o.status === "REVERSED").length;
  }, [activeObligations]);

  // Tab counts
  const rentCount = useMemo(() => activeObligations.filter((o) => o.obligationType === "RENT").length, [activeObligations]);
  const depositCount = useMemo(() => activeObligations.filter((o) => o.obligationType === "DEPOSIT").length, [activeObligations]);
  const historyCount = useMemo(
    () => activeObligations.filter((o) => o.status === "CONFIRMED" || o.records?.length > 0).length,
    [activeObligations]
  );

  // Distinct property rentals for filter dropdown
  const distinctRentals = useMemo(() => {
    const set = new Set<string>();
    activeObligations.forEach((o) => {
      const info = getObligationPropertyInfo(o);
      set.add(info.propertyName);
    });
    return Array.from(set);
  }, [activeObligations, getObligationPropertyInfo]);

  // const rentalOptions = useMemo(
  //   () => [
  //     { value: "ALL", label: "All rentals" },
  //     ...distinctRentals.map((r) => ({ value: r, label: r })),
  //   ],
  //   [distinctRentals]
  // );

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
      "Tenant / Payer",
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
        `"${info.tenantName.replace(/"/g, '""')}"`,
      ];
    });

    const csvContent =
      "data:text/csv;charset=utf-8," + [headers.join(","), ...rows.map((e) => e.join(","))].join("\n");
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `host_payment_records_${new Date().toISOString().slice(0, 10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    showToast("Payment records exported successfully.", "success");
  }

  // Confirm Receipt Handler (for host)
  async function handleConfirmReceipt(recordId: number) {
    setBusyConfirmId(recordId);
    try {
      await confirmRentalPaymentReceipt(recordId);
      showToast("Payment receipt confirmed. Record updated.", "success");
      setViewingRecord(null);
      loadData();
    } catch (err) {
      showToast(errorMessage(err, "Could not confirm payment receipt."), "error");
    } finally {
      setBusyConfirmId(null);
    }
  }

  // Report Discrepancy Handler
  async function handleReportDiscrepancy() {
    if (!disputeModalRecord || !disputeReason) return;
    try {
      await reportRentalPaymentDiscrepancyAsRecipient(disputeModalRecord.id, {
        reasonCode: disputeReason as RentalPaymentDiscrepancyReason,
        details: disputeNotes.trim(),
      });
      showToast("Payment discrepancy reported.", "success");
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
      {/* 1. Page Header with HOST WORKSPACE overline & Export Button */}
      <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4">
        <div>
          {/* Strict requirement: Named "HOST WORKSPACE" in red, not renter */}
          <div className="text-xs font-bold text-[#D80B0B] uppercase tracking-wider">
            HOST WORKSPACE
          </div>
          <h1 className="text-3xl sm:text-4xl font-extrabold text-[#0C2B4E] dark:text-white tracking-tight mt-0.5">
            Payments
          </h1>
          <p className="mt-1 text-xs sm:text-sm text-slate-500 dark:text-slate-400 max-w-3xl leading-relaxed">
            Track rent and deposits for your properties, manage tenant payment methods, review payment declarations, and confirm receipts.
          </p>
        </div>

        <div className="flex items-center gap-2 self-start sm:self-auto">
          <button
            onClick={() => setPaymentDetailsOpen(true)}
            className="inline-flex items-center gap-2 px-5 py-3 text-xs sm:text-sm font-bold text-white bg-[#0C2B4E] hover:bg-[#071c33] rounded-2xl shadow-sm transition cursor-pointer"
          >
            Payment details
          </button>
          <button
            onClick={handleExportCSV}
            className="inline-flex items-center gap-2 px-5 py-3 text-xs sm:text-sm font-bold text-[#0C2B4E] dark:text-slate-200 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl hover:bg-slate-50 dark:hover:bg-slate-800/80 shadow-sm transition cursor-pointer"
          >
            <Download className="h-4 w-4 text-[#0C2B4E] dark:text-slate-300" />
            <span>Export Payment Records</span>
          </button>
        </div>
      </div>

      {/* Tenants can't see how to pay until there's an ACTIVE payment instruction. */}
      {!instructions.some((i) => i.status === "ACTIVE") && (
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-4 rounded-2xl border border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/20 dark:text-amber-200">
          <div className="text-xs sm:text-sm">
            <strong className="block">Add your payment details</strong>
            Your tenants can see what they owe but not where to pay it until you add bank, UPI or cash details.
          </div>
          <button
            onClick={() => setPaymentDetailsOpen(true)}
            className="shrink-0 px-4 py-2 rounded-xl bg-[#0C2B4E] hover:bg-[#071c33] text-white text-xs font-bold cursor-pointer"
          >
            Add payment details
          </button>
        </div>
      )}

      <Modal open={paymentDetailsOpen} onClose={() => setPaymentDetailsOpen(false)} title="Payment details">
        <InstructionsManager instructions={instructions} onChanged={loadData} />
      </Modal>

      {/* 2. 4 KPI Metric Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
        {/* Card 1: NEXT INCOMING PAYMENT (Solid Dark Navy Card #0C2B4E) */}
        <div className="bg-[#0C2B4E] text-white rounded-[20px] p-3 shadow-sm flex flex-col justify-between gap-2 border border-[#0C2B4E]">
          <div>
            <div className="text-[11px] font-extrabold text-sky-300 uppercase tracking-wider">
              NEXT INCOMING PAYMENT
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
              : "All tenant payments up to date"}
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
              Marked paid by tenant
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
              Tenant past due, unpaid
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
              Discrepancy or mismatch
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
              <span> Review mismatched transactions and update receipt confirmation.</span>
            </>
          ) : awaitingConfirmationCount > 0 ? (
            <>
              <span className="font-bold text-[#0C2B4E] dark:text-white">
                {awaitingConfirmationCount} payment awaiting your confirmation.
              </span>
              <span> A tenant has declared payment. Verify your bank receipt and confirm.</span>
            </>
          ) : (
            <>
              <span className="font-bold text-[#0C2B4E] dark:text-white">No payment action required.</span>
              <span>
                {nextPayment && nextPropInfo
                  ? ` Next expected payment is ${formatCurrency(nextPayment.amount, nextPayment.currency)} due ${formatDate(
                      nextPayment.dueDate
                    )} (${nextPayment.displayLabel}, ${nextPropInfo.propertyName}) from ${nextPropInfo.tenantName}.`
                  : " All property rent and deposit obligations from tenants are up to date."}
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
              placeholder="Search by property, tenant, room or reference"
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
          {/* <RoundedFilterDropdown
            label="Rental"
            value={rentalFilter}
            onChange={setRentalFilter}
            options={rentalOptions}
            size="sm"
            className="flex-initial min-w-[150px]"
          /> */}

          {/* Sort Select */}
          <RoundedFilterDropdown
            label="Sort"
            value={sortOption}
            onChange={(v) => setSortOption(v as typeof sortOption)}
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
              Rental payments and deposit obligations for your properties will appear here.
            </p>
            <div className="mt-4">
              <Link href="/account/host/listings">
                <button className="px-4 py-2 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-xs font-bold text-[#0C2B4E] dark:text-white hover:bg-slate-50 transition shadow-sm cursor-pointer">
                  View Properties
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
                    <th className="py-3 px-4">TENANT / PAYER</th>
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
                                   : "Tenant marked paid"}
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

                        {/* Column 6: Tenant / Payer */}
                        <td className="py-4 px-4 align-top">
                          <div className="font-bold text-slate-900 dark:text-slate-100">
                            {info.tenantName}
                          </div>
                          <div className="flex items-center gap-1 text-[11px] font-bold text-emerald-600 dark:text-emerald-400 mt-0.5">
                            <ShieldCheck className="h-3.5 w-3.5 stroke-[2.5]" />
                            <span>Tenant Verified</span>
                          </div>
                        </td>

                        {/* Column 7: Next Action */}
                        <td className="py-4 px-5 align-top text-right">
                          {isAwaitingConfirmation && lastRecord ? (
                            <button
                              onClick={() => setViewingRecord({ obligation: o, record: lastRecord })}
                              className="inline-flex items-center justify-center px-4 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-bold shadow-sm transition cursor-pointer"
                            >
                              Confirm Receipt
                            </button>
                          ) : isUpcoming || dueSoon || isOverdue ? (
                            <button
                              onClick={() => setViewingInstructions(o)}
                              className="inline-flex items-center justify-center px-4 py-2.5 rounded-xl bg-[#0C2B4E] hover:bg-[#071c33] text-white text-xs font-bold shadow-sm transition cursor-pointer"
                            >
                              Payment Method
                            </button>
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
                          {info.tenantName}
                        </div>
                        <div className="flex items-center gap-1 text-[11px] text-emerald-600 font-bold">
                          <ShieldCheck className="h-3 w-3" />
                          <span>Tenant Verified</span>
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

                    <div className="pt-1">
                      {isAwaitingConfirmation && lastRecord ? (
                        <button
                          onClick={() => setViewingRecord({ obligation: o, record: lastRecord })}
                          className="w-full py-2.5 rounded-xl bg-emerald-600 text-white text-xs font-bold shadow-sm cursor-pointer"
                        >
                          Confirm Receipt
                        </button>
                      ) : (
                        <button
                          onClick={() => setViewingInstructions(o)}
                          className="w-full py-2.5 rounded-xl bg-[#0C2B4E] text-white text-xs font-bold shadow-sm cursor-pointer"
                        >
                          Payment Method
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
          Under the current model, rent and deposits are paid directly to your designated account by tenants. Zoiko Rooms records obligations, tenant declarations and your receipt confirmations — it does not receive or hold this money.
        </p>
      </div>

      {/* ======================================================== */}
      {/* MODAL 1: Payment Instructions Modal                      */}
      {/* ======================================================== */}
      {viewingInstructions && !receivableIds.has(viewingInstructions.id) && (
        <InstructionsModalView
          obligation={viewingInstructions}
          onClose={() => setViewingInstructions(null)}
          onRecordPayment={() => {
            setViewingInstructions(null);
            window.location.assign("/account/rent-payments");
          }}
        />
      )}
      {viewingInstructions && receivableIds.has(viewingInstructions.id) && (
        <Modal
          open={Boolean(viewingInstructions)}
          onClose={() => setViewingInstructions(null)}
          title={`Payment Method for Tenant · ${viewingInstructions.displayLabel}`}
        >
          <div className="space-y-4 text-xs">
            <div className="p-3.5 bg-slate-50 dark:bg-slate-800 rounded-xl space-y-2">
              <div className="flex justify-between">
                <span className="text-slate-400 font-semibold">Amount Due:</span>
                <span className="font-extrabold text-base text-[#0C2B4E] dark:text-white">
                  {formatCurrency(viewingInstructions.amount, viewingInstructions.currency)}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400 font-semibold">Due Date:</span>
                <span className="font-bold text-slate-800 dark:text-slate-200">
                  {formatDate(viewingInstructions.dueDate)}
                </span>
              </div>
              <div className="flex justify-between items-center pt-1 border-t border-slate-200 dark:border-slate-700">
                <span className="text-slate-400 font-semibold">Payment Reference:</span>
                <div className="flex items-center gap-1.5 font-mono font-bold text-indigo-600 dark:text-indigo-400">
                  <span>{viewingInstructions.paymentReference || `ZR-R-${viewingInstructions.id}`}</span>
                  <button
                    onClick={() => {
                      navigator.clipboard?.writeText(
                        viewingInstructions.paymentReference || `ZR-R-${viewingInstructions.id}`
                      );
                      showToast("Payment reference copied.", "success");
                    }}
                    className="p-1 hover:bg-slate-200 dark:hover:bg-slate-700 rounded cursor-pointer"
                  >
                    <Copy className="h-3 w-3" />
                  </button>
                </div>
              </div>
            </div>

            <div className="p-3.5 bg-emerald-50/70 dark:bg-emerald-950/20 border border-emerald-200 dark:border-emerald-800 rounded-xl space-y-1.5">
              <div className="flex items-center gap-1.5 font-bold text-emerald-800 dark:text-emerald-300">
                <ShieldCheck className="h-4 w-4" />
                <span>Designated Host Payee Information</span>
              </div>
              {activeInstruction ? (
                <>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Pay to:</span>
                    <span className="font-bold text-slate-800 dark:text-slate-100">{activeInstruction.recipientName}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Payment method:</span>
                    <span className="font-semibold text-slate-700 dark:text-slate-200">{activeInstruction.methodLabel || activeInstruction.method}</span>
                  </div>
                  {Object.entries(activeInstruction.bankDetails ?? {}).map(([key, value]) => (
                    <div key={key} className="flex justify-between">
                      <span className="text-slate-500">
                        {resolveBankFieldSchema(activeInstruction.countryCode, activeInstruction.method, activeInstruction.methodSubtype)
                          ?.fields.find((f) => f.key === key)?.label ?? key}:
                      </span>
                      <span className="font-mono font-bold text-slate-800 dark:text-slate-100">{value}</span>
                    </div>
                  ))}
                  {activeInstruction.additionalInstructions && (
                    <p className="text-slate-600 dark:text-slate-300 whitespace-pre-wrap pt-1">{activeInstruction.additionalInstructions}</p>
                  )}
                  <p className="text-[11px] text-slate-500 dark:text-slate-400 pt-1">
                    The tenant sees this payment method and reference, and pays you directly.
                  </p>
                </>
              ) : (
                <p className="text-amber-800 dark:text-amber-300">
                  You haven&apos;t got an active payment method, so the tenant has no details to pay you with. Add one under
                  Payment Methods.
                </p>
              )}
            </div>

            <div className="flex justify-end pt-2">
              <button
                onClick={() => setViewingInstructions(null)}
                className="px-4 py-2 rounded-xl bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 font-bold text-slate-800 dark:text-slate-200 cursor-pointer"
              >
                Close
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* ======================================================== */}
      {/* MODAL 2: View Payment Record & Confirm Receipt Modal     */}
      {/* ======================================================== */}
      {viewingRecord && (
        <Modal
          open={Boolean(viewingRecord)}
          onClose={() => setViewingRecord(null)}
          title={`Tenant Payment Declaration · ${viewingRecord.obligation.displayLabel}`}
        >
          <div className="space-y-4 text-xs">
            <div className="p-3.5 bg-slate-50 dark:bg-slate-800 rounded-xl space-y-2">
              <div className="flex justify-between">
                <span className="text-slate-400 font-semibold">Tenant / Payer:</span>
                <span className="font-bold text-slate-800 dark:text-slate-200">
                  {getObligationPropertyInfo(viewingRecord.obligation).tenantName}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400 font-semibold">Declared Amount:</span>
                <span className="font-extrabold text-base text-[#0C2B4E] dark:text-white">
                  {formatCurrency(
                    viewingRecord.record.declaredAmount,
                    viewingRecord.record.declaredCurrency
                  )}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400 font-semibold">Payment Date:</span>
                <span className="font-bold text-slate-800 dark:text-slate-200">
                  {formatDate(viewingRecord.record.declaredDate)}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400 font-semibold">Method:</span>
                <span className="font-bold text-slate-800 dark:text-slate-200">
                  {paymentMethodDisplay(viewingRecord.record)}
                </span>
              </div>
              {viewingRecord.record.externalReference && (
                <div className="flex justify-between">
                  <span className="text-slate-400 font-semibold">Bank / Transaction Reference:</span>
                  <span className="font-mono text-slate-800 dark:text-slate-200">
                    {viewingRecord.record.externalReference}
                  </span>
                </div>
              )}
            </div>

            <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => {
                  setDisputeModalRecord(viewingRecord.record);
                  setDisputeReason("NOT_ARRIVED");
                  setDisputeNotes("");
                }}
                className="px-4 py-2 rounded-xl border border-red-200 text-red-600 hover:bg-red-50 font-bold transition cursor-pointer"
              >
                Report Discrepancy
              </button>

              <button
                type="button"
                disabled={busyConfirmId === viewingRecord.record.id}
                onClick={() => handleConfirmReceipt(viewingRecord.record.id)}
                className="px-5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white font-bold transition shadow-sm cursor-pointer"
              >
                {busyConfirmId === viewingRecord.record.id ? "Confirming..." : "Confirm Receipt"}
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* ======================================================== */}
      {/* MODAL 3: Report Discrepancy Modal                        */}
      {/* ======================================================== */}
      {disputeModalRecord && (
        <Modal
          open={Boolean(disputeModalRecord)}
          onClose={() => setDisputeModalRecord(null)}
          title="Report Payment Discrepancy"
        >
          <div className="space-y-3.5 text-xs">
            <div>
              <label className="block text-xs font-bold uppercase tracking-wide text-slate-500 mb-1">
                Discrepancy Reason
              </label>
              <select
                value={disputeReason}
                onChange={(e) => setDisputeReason(e.target.value)}
                aria-label="Select discrepancy reason"
                className="w-full rounded-xl bg-slate-50 dark:bg-slate-800 p-2.5 outline-none border border-slate-200 dark:border-slate-700"
              >
                <option value="NOT_ARRIVED">Payment has not arrived</option>
                <option value="AMOUNT_DIFFERENT">Amount received is different</option>
                <option value="REFERENCE_MISMATCH">Reference cannot be matched</option>
                <option value="RETURNED_OR_REVERSED">Payment was returned or reversed</option>
                <option value="OTHER">Other</option>
              </select>
            </div>

            <div>
              <label className="block text-xs font-bold uppercase tracking-wide text-slate-500 mb-1">
                Details for Payer
              </label>
              <textarea
                value={disputeNotes}
                onChange={(e) => setDisputeNotes(e.target.value)}
                rows={3}
                placeholder="Explain the issue for the tenant..."
                className="w-full rounded-xl bg-slate-50 dark:bg-slate-800 p-2.5 outline-none border border-slate-200 dark:border-slate-700"
              />
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setDisputeModalRecord(null)}
                className="px-4 py-2 font-semibold text-slate-600 cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleReportDiscrepancy}
                className="px-5 py-2 rounded-xl bg-[#D80B0B] text-white font-bold shadow-sm cursor-pointer"
              >
                Submit Discrepancy
              </button>
            </div>
          </div>
        </Modal>
      )}

      <Toast toast={toast} />
    </div>
  );
}

// Re-export alias
export const RecipientPaymentsManager = HostingPaymentsManager;
