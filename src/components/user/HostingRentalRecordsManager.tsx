"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Calendar,
  CheckCircle2,
  Clock,
  CreditCard,
  Download,
  FileText,
  Key,
  MessagesSquare,
  RefreshCw,
  Repeat,
  Search,
  X,
} from "lucide-react";
import { Toast, useToast } from "@/components/user/ui";
import { DepositClaimsSection } from "@/components/user/DepositClaimsSection";
import { Modal } from "@/components/ui/Modal";
import { Loader } from "@/components/ui/Loader";
import { RoundedFilterDropdown } from "@/components/ui/RoundedFilterDropdown";
import { HostMoveInSteps } from "@/components/user/HostMoveInSteps";
import { cn, formatCurrency, formatDate, formatDateTime } from "@/lib/utils";
import {
  acceptHostSurrender,
  approveHostChangeRequest,
  confirmHostMoveOut,
  declineHostChangeRequest,
  declineHostSurrender,
  errorMessage,
  getHostedRentalTransactionRecord,
  hostAgreementPdfUrl,
  hostTermination,
  listHostChangeRequests,
  listHostOwedRefunds,
  listHostHabitabilityIssues,
  acknowledgeHabitabilityIssue,
  reportHabitabilityFixed,
  listHostRentalRecords,
  markHostRefundPaid,
  proposeHostAmendment,
  signHostedAgreement,
  startHostTermination,
} from "@/lib/user-api";
import type {
  BookingChangeRequest,
  HostRefund,
  HabitabilityIncident,
  HostRentalNextAction,
  HostRentalRecord,
  HostRentalStatus,
  RentalTransactionRecord,
} from "@/lib/types";

const STATUS_CONFIG: Record<HostRentalStatus, { label: string; cls: string }> = {
  pending: { label: "Agreement pending", cls: "bg-violet-50 text-violet-800 border-violet-200 dark:bg-violet-950/30 dark:text-violet-300 dark:border-violet-900" },
  upcoming: { label: "Upcoming", cls: "bg-blue-50 text-blue-800 border-blue-200 dark:bg-blue-950/30 dark:text-blue-300 dark:border-blue-900" },
  active: { label: "Active", cls: "bg-emerald-50 text-emerald-800 border-emerald-200 dark:bg-emerald-950/30 dark:text-emerald-300 dark:border-emerald-900" },
  notice: { label: "Notice given", cls: "bg-amber-50 text-amber-800 border-amber-200 dark:bg-amber-950/30 dark:text-amber-300 dark:border-amber-900" },
  ending: { label: "Ending soon", cls: "bg-orange-50 text-orange-800 border-orange-200 dark:bg-orange-950/30 dark:text-orange-300 dark:border-orange-900" },
  ended: { label: "Ended", cls: "bg-neutral-100 text-neutral-700 border-neutral-200 dark:bg-neutral-800 dark:text-neutral-300 dark:border-neutral-700" },
  cancelled: { label: "Cancelled", cls: "bg-neutral-100 text-neutral-700 border-neutral-200 dark:bg-neutral-800 dark:text-neutral-300 dark:border-neutral-700" },
  terminated: { label: "Terminated", cls: "bg-rose-50 text-rose-800 border-rose-200 dark:bg-rose-950/30 dark:text-rose-300 dark:border-rose-900" },
};

const ACTION_LABEL: Record<HostRentalNextAction, string> = {
  SIGN_AGREEMENT: "Sign agreement",
  AWAITING_RENTER_SIGNATURE: "Awaiting renter signature",
  PREPARE_MOVE_IN: "Prepare move-in",
  REVIEW_NOTICE: "Review notice",
  RESPOND_SURRENDER: "Respond to end request",
  RESPOND_END_DATE: "Respond to end date",
  CONFIRM_MOVE_OUT: "Confirm move-out",
  REVIEW_PAYMENTS: "Review payments",
  NONE: "View record",
};

// Actions that need the host to do something (not just wait).
const HOST_ACTIONS: HostRentalNextAction[] = [
  "SIGN_AGREEMENT", "PREPARE_MOVE_IN", "REVIEW_NOTICE", "RESPOND_SURRENDER", "RESPOND_END_DATE", "CONFIRM_MOVE_OUT", "REVIEW_PAYMENTS",
];
const HISTORY_STATUSES: HostRentalStatus[] = ["ended", "cancelled", "terminated"];

// "ground" = the renter answers your proposed date (silence = accepted);
// "rules" = the market's rules set the date; "mutual" = the renter must agree.
const HOST_END_CAUSES: Array<{ code: string; label: string; kind: "mutual" | "ground" | "rules" }> = [
  { code: "MUTUAL_SURRENDER", label: "Agree an end date with the renter", kind: "mutual" },
  { code: "RENTER_BREACH", label: "Renter breached the agreement", kind: "ground" },
  { code: "HOST_LAWFUL_POSSESSION_ACTION", label: "Lawful possession ground (e.g. owner move-in, sale)", kind: "ground" },
  { code: "ABANDONMENT_REPORTED", label: "Renter has abandoned the room", kind: "ground" },
  { code: "CASUALTY_OR_FORCE_EVENT", label: "Room destroyed or unusable (fire, flood)", kind: "rules" },
  { code: "HOST_FAULT_OR_NONPERFORMANCE", label: "I can't provide the room as agreed", kind: "rules" },
];
const END_KIND_HELP = {
  mutual: "The renter must agree. If they don't by the deadline, nothing changes.",
  ground: "The renter can accept, propose another date or contest. No reply by the deadline counts as acceptance. The date must be at least 28 days away.",
  rules: "Ends on the date the market's rules set — no date needed.",
} as const;
const MIN_GROUND_NOTICE_DAYS = 28;
const SEVERITY_LABEL = { H0: "minor", H1: "affects use", H2: "serious", H3: "unsafe" } as const;

const CHANGE_LABEL: Record<string, string> = {
  DATE_SHIFT: "Move-in date change",
  TERM_SHIFT: "Term change",
  EXTENSION: "Extension",
  SHORTENING: "Shorter term",
  PREMISES_CHANGE: "Room change",
  FINANCIAL_CHANGE: "Rent change",
  DEPOSIT_CHANGE: "Deposit change",
};

const selectCls =
  "h-10 rounded-full border border-neutral-200 bg-white px-4 text-xs font-semibold text-neutral-800 outline-none transition focus:border-primary-700 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200 cursor-pointer";
const inputCls =
  "mt-1 w-full rounded-xl border border-neutral-300 bg-white p-2.5 text-xs outline-none focus:border-primary-700 dark:border-neutral-700 dark:bg-neutral-900 dark:text-white";

function money(r: HostRentalRecord, amount: number | null | undefined) {
  return amount == null ? "—" : formatCurrency(amount, r.currency);
}

function roomLabel(r: HostRentalRecord) {
  const type = r.roomType.replace(/_/g, " ");
  return `Room #${r.roomId ?? "—"}${type ? ` · ${type}` : ""}${r.hasEnsuite ? " · en-suite" : ""}`;
}

function termLabel(r: HostRentalRecord) {
  const start = r.startDate ? formatDate(r.startDate) : "—";
  const end = r.endDate ? formatDate(r.endDate) : "rolling";
  return `${start} → ${end}`;
}

function agreementLabel(r: HostRentalRecord) {
  if (!r.agreementStatus) return "Not generated yet";
  if (r.agreementStatus === "SIGNED") return `Signed · v${r.agreementVersion ?? 1}`;
  if (r.agreementStatus === "AMENDMENT_PENDING") return `Amendment awaiting signatures · v${r.agreementVersion ?? 1}`;
  if (!r.hostSignedAt && !r.renterSignedAt) return "Awaiting signatures";
  if (!r.hostSignedAt) return "Your signature required";
  if (!r.renterSignedAt) return "Renter signature required";
  return r.agreementStatus.replace(/_/g, " ").toLowerCase();
}

type ModalState =
  | null
  | { type: "sign"; record: HostRentalRecord }
  | { type: "movein"; record: HostRentalRecord }
  | { type: "notice"; record: HostRentalRecord }
  | { type: "end"; record: HostRentalRecord }
  | { type: "amend"; record: HostRentalRecord };

export function HostingRentalRecordsManager() {
  const { toast, showToast } = useToast();

  const [records, setRecords] = useState<HostRentalRecord[]>([]);
  const [changeRequests, setChangeRequests] = useState<BookingChangeRequest[]>([]);
  const [refunds, setRefunds] = useState<HostRefund[]>([]);
  const [issues, setIssues] = useState<HabitabilityIncident[]>([]);
  const [refundRef, setRefundRef] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  const [viewTab, setViewTab] = useState<"current" | "history">("current");
  const [selectedCard, setSelectedCard] = useState<"" | "upcoming" | "active" | "ending" | "action">("");
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | HostRentalStatus>("all");
  const [propertyFilter, setPropertyFilter] = useState("all");
  const [agreementFilter, setAgreementFilter] = useState<"all" | "signed" | "host" | "renter">("all");
  const [sortFilter, setSortFilter] = useState<"action" | "start" | "updated">("action");

  const [selectedRef, setSelectedRef] = useState<string | null>(null);
  const [drawerTab, setDrawerTab] = useState<"overview" | "agreement" | "payments" | "timeline" | "requests">("overview");
  const [txRecord, setTxRecord] = useState<RentalTransactionRecord | null>(null);
  const [txLoading, setTxLoading] = useState(false);
  const [txError, setTxError] = useState("");

  const [modal, setModal] = useState<ModalState>(null);
  const [busy, setBusy] = useState(false);
  // Modal form state
  const [endCause, setEndCause] = useState("MUTUAL_SURRENDER");
  const [endDate, setEndDate] = useState("");
  const [endNotes, setEndNotes] = useState("");
  const [amendMonths, setAmendMonths] = useState("");
  const [amendRent, setAmendRent] = useState("");
  const [amendReason, setAmendReason] = useState("");
  const [moveOutNotes, setMoveOutNotes] = useState("");
  const [counterDate, setCounterDate] = useState("");
  const [orderOutcome, setOrderOutcome] = useState<"ENDS_ON_DATE" | "TENANCY_CONTINUES">("ENDS_ON_DATE");
  const [orderRef, setOrderRef] = useState("");
  const [orderDate, setOrderDate] = useState("");
  const [orderLiability, setOrderLiability] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [recs, bcrs, owed, reported] = await Promise.all([
        listHostRentalRecords(),
        listHostChangeRequests(),
        listHostOwedRefunds(),
        listHostHabitabilityIssues(),
      ]);
      setRecords(recs);
      setChangeRequests(bcrs);
      setRefunds(owed);
      setIssues(reported);
    } catch (err) {
      setLoadError(errorMessage(err, "Could not load your rental records"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const selected = records.find((r) => r.ref === selectedRef) ?? null;

  // Detail for the drawer: the occupancy's transaction record.
  useEffect(() => {
    setTxRecord(null);
    setTxError("");
    if (!selected?.occupancyId) return;
    let active = true;
    setTxLoading(true);
    getHostedRentalTransactionRecord(selected.occupancyId)
      .then((tx) => active && setTxRecord(tx))
      .catch((err) => active && setTxError(errorMessage(err, "Could not load this rental's history")))
      .finally(() => active && setTxLoading(false));
    return () => {
      active = false;
    };
  }, [selected?.occupancyId]);

  const pendingRequestsFor = useCallback(
    (r: HostRentalRecord) =>
      changeRequests.filter((b) => b.agreementId === r.agreementId && b.status === "AWAITING_HOST"),
    [changeRequests],
  );

  const currentRecords = useMemo(() => records.filter((r) => !HISTORY_STATUSES.includes(r.status)), [records]);
  const historyRecords = useMemo(() => records.filter((r) => HISTORY_STATUSES.includes(r.status)), [records]);

  const openIssuesFor = useCallback(
    (r: HostRentalRecord) => issues.filter((i) => i.occupancyId === r.occupancyId && (i.status === "OPEN" || i.status === "ACKNOWLEDGED")),
    [issues],
  );

  const needsHost = useCallback(
    (r: HostRentalRecord) => HOST_ACTIONS.includes(r.nextAction) || pendingRequestsFor(r).length > 0 || openIssuesFor(r).length > 0,
    [pendingRequestsFor, openIssuesFor],
  );

  const counts = useMemo(
    () => ({
      upcoming: currentRecords.filter((r) => r.status === "upcoming" || r.status === "pending").length,
      active: currentRecords.filter((r) => r.status === "active").length,
      ending: currentRecords.filter((r) => r.status === "ending" || r.status === "notice").length,
      action: currentRecords.filter(needsHost).length,
    }),
    [currentRecords, needsHost],
  );

  const properties = useMemo(() => {
    const map = new Map<string, string>();
    records.forEach((r) => r.propertyId != null && map.set(String(r.propertyId), r.propertyAddress));
    return Array.from(map.entries());
  }, [records]);

  const displayed = useMemo(() => {
    let list = viewTab === "current" ? currentRecords : historyRecords;
    if (selectedCard === "upcoming") list = list.filter((r) => r.status === "upcoming" || r.status === "pending");
    if (selectedCard === "active") list = list.filter((r) => r.status === "active");
    if (selectedCard === "ending") list = list.filter((r) => r.status === "ending" || r.status === "notice");
    if (selectedCard === "action") list = list.filter(needsHost);
    if (statusFilter !== "all") list = list.filter((r) => r.status === statusFilter);
    if (propertyFilter !== "all") list = list.filter((r) => String(r.propertyId) === propertyFilter);
    if (agreementFilter === "signed") list = list.filter((r) => r.agreementStatus === "SIGNED");
    if (agreementFilter === "host") list = list.filter((r) => r.nextAction === "SIGN_AGREEMENT");
    if (agreementFilter === "renter") list = list.filter((r) => r.nextAction === "AWAITING_RENTER_SIGNATURE");
    const q = searchQuery.trim().toLowerCase();
    if (q) {
      list = list.filter((r) =>
        [r.ref, r.listingName, r.propertyAddress, ...r.renters, r.agreementId ? `ZR-AGR-${r.agreementId}` : ""]
          .join(" ")
          .toLowerCase()
          .includes(q),
      );
    }
    const sorted = [...list];
    if (sortFilter === "action") sorted.sort((a, b) => Number(needsHost(b)) - Number(needsHost(a)));
    if (sortFilter === "start") sorted.sort((a, b) => (a.startDate ?? "").localeCompare(b.startDate ?? ""));
    if (sortFilter === "updated") sorted.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    return sorted;
  }, [viewTab, currentRecords, historyRecords, selectedCard, statusFilter, propertyFilter, agreementFilter, searchQuery, sortFilter, needsHost]);

  const attention = useMemo(() => currentRecords.filter(needsHost), [currentRecords, needsHost]);
  const hasActiveFilters =
    statusFilter !== "all" || propertyFilter !== "all" || agreementFilter !== "all" || searchQuery.trim() !== "" || selectedCard !== "";

  const endKind = HOST_END_CAUSES.find((c) => c.code === endCause)?.kind ?? "mutual";
  const endMinDate = (() => {
    const d = new Date();
    if (endKind === "ground") d.setDate(d.getDate() + MIN_GROUND_NOTICE_DAYS);
    return d.toISOString().slice(0, 10);
  })();

  function clearFilters() {
    setStatusFilter("all");
    setPropertyFilter("all");
    setAgreementFilter("all");
    setSearchQuery("");
    setSelectedCard("");
  }

  function openRecord(r: HostRentalRecord, tab: typeof drawerTab = "overview") {
    setSelectedRef(r.ref);
    setDrawerTab(tab);
  }

  function hostTaskLabel(r: HostRentalRecord): string {
    if (HOST_ACTIONS.includes(r.nextAction)) return ACTION_LABEL[r.nextAction];
    if (openIssuesFor(r).length > 0) return "Fix reported issue";
    const requests = pendingRequestsFor(r).length;
    return `Review ${requests} change request${requests === 1 ? "" : "s"}`;
  }

  function handleCta(r: HostRentalRecord) {
    if (!HOST_ACTIONS.includes(r.nextAction) && openIssuesFor(r).length > 0) return openRecord(r, "overview");
    if (pendingRequestsFor(r).length > 0 && !HOST_ACTIONS.includes(r.nextAction)) return openRecord(r, "requests");
    switch (r.nextAction) {
      case "SIGN_AGREEMENT":
        return setModal({ type: "sign", record: r });
      case "PREPARE_MOVE_IN":
        return setModal({ type: "movein", record: r });
      case "REVIEW_NOTICE":
      case "RESPOND_SURRENDER":
      case "RESPOND_END_DATE":
      case "CONFIRM_MOVE_OUT":
        return openModal({ type: "notice", record: r });
      case "REVIEW_PAYMENTS":
        return openRecord(r, "payments");
      case "AWAITING_RENTER_SIGNATURE":
        return openRecord(r, "agreement");
      default:
        return openRecord(r);
    }
  }

  async function run(action: () => Promise<unknown>, success: string) {
    setBusy(true);
    try {
      await action();
      showToast(success);
      setModal(null);
      await load();
    } catch (err) {
      showToast(errorMessage(err, "That didn't work. Please try again."), "error");
    } finally {
      setBusy(false);
    }
  }

  function handleExportCSV() {
    const header = ["Rental ID", "Renters", "Property", "Room", "Status", "Start", "End", "Monthly rent", "Deposit", "Currency", "Agreement"];
    const rows = displayed.map((r) => [
      r.ref, r.renters.join(" & "), r.propertyAddress, roomLabel(r), STATUS_CONFIG[r.status].label,
      r.startDate ?? "", r.endDate ?? "", r.monthlyRent ?? "", r.depositAmount ?? "", r.currency, agreementLabel(r),
    ]);
    const csv = [header, ...rows].map((row) => row.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `rental-records-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    showToast(`Exported ${rows.length} rental records`);
  }

  function openModal(m: ModalState) {
    setEndCause("MUTUAL_SURRENDER");
    setEndDate("");
    setEndNotes("");
    setAmendMonths("");
    setAmendRent("");
    setAmendReason("");
    setMoveOutNotes("");
    setCounterDate("");
    setOrderOutcome("ENDS_ON_DATE");
    setOrderRef("");
    setOrderDate("");
    setOrderLiability("");
    setModal(m);
  }

  const card = (key: "upcoming" | "active" | "ending" | "action", count: number, title: string, sub: string, Icon: typeof Calendar, tone: string) => (
    <button
      type="button"
      onClick={() => {
        setViewTab("current");
        setSelectedCard((prev) => (prev === key ? "" : key));
      }}
      className={cn(
        "group flex flex-col items-start gap-2 rounded-2xl border-2 bg-white p-4 text-left transition-all hover:shadow-md dark:bg-neutral-900",
        selectedCard === key ? `${tone} ring-2` : "border-neutral-200 hover:border-neutral-300 dark:border-neutral-800",
      )}
    >
      <div className="flex w-full items-center justify-between">
        <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-neutral-50 font-heading text-lg font-extrabold text-neutral-900 dark:bg-neutral-800 dark:text-white">
          {count}
        </span>
        <Icon className="h-5 w-5 text-neutral-500" />
      </div>
      <div>
        <div className="font-heading text-sm font-bold text-neutral-900 dark:text-white">{title}</div>
        <div className="text-xs text-neutral-500 dark:text-neutral-400">{sub}</div>
      </div>
    </button>
  );

  return (
    <div className="space-y-6 pb-20">
      <Toast toast={toast} />

      <section className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <span className="text-[11px] font-bold uppercase tracking-wider text-primary-600 dark:text-primary-400">Host workspace</span>
          <h1 className="mt-1 font-heading text-2xl font-extrabold tracking-tight text-neutral-900 sm:text-3xl dark:text-white">
            Rental Records
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-neutral-600 dark:text-neutral-300">
            Every rental on your properties, from signed agreement to move-out — agreements, notices, payments and history.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={load}
            className="inline-flex items-center gap-2 rounded-xl border border-neutral-300 bg-white px-3 py-2.5 text-sm font-bold text-primary-900 hover:bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-800 dark:text-white"
          >
            <RefreshCw className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={handleExportCSV}
            disabled={displayed.length === 0}
            className="inline-flex items-center gap-2 rounded-xl border border-neutral-300 bg-white px-4 py-2.5 text-sm font-bold text-primary-900 shadow-sm transition hover:bg-neutral-50 disabled:opacity-50 dark:border-neutral-700 dark:bg-neutral-800 dark:text-white"
          >
            <Download className="h-4 w-4" />
            <span>Export</span>
          </button>
        </div>
      </section>

      {loading ? (
        <Loader label="Loading rental records..." />
      ) : loadError ? (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm font-semibold text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-300">
          {loadError}
        </div>
      ) : (
        <>
          <section className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:gap-4">
            {card("upcoming", counts.upcoming, "Upcoming", "Agreement or move-in ahead", Calendar, "border-primary-700 bg-primary-50/40 ring-primary-700/20")}
            {card("active", counts.active, "Active", "Occupancy in effect", Key, "border-emerald-700 bg-emerald-50/40 ring-emerald-700/20")}
            {card("ending", counts.ending, "Ending", "Notice given or ends within 30 days", Clock, "border-amber-600 bg-amber-50/40 ring-amber-600/20")}
            {card("action", counts.action, "Action required", "Waiting on you", AlertTriangle, "border-rose-600 bg-rose-50/40 ring-rose-600/20")}
          </section>

          {viewTab === "current" && attention.length > 0 && (
            <section className="rounded-2xl border border-rose-200 bg-[#FFF7F7] p-4 dark:border-rose-900/50 dark:bg-rose-950/20">
              <div className="mb-3 flex items-center gap-2 text-rose-800 dark:text-rose-300">
                <AlertTriangle className="h-5 w-5 shrink-0" />
                <h2 className="font-heading text-sm font-bold">
                  {attention.length} rental {attention.length === 1 ? "needs" : "need"} your attention
                </h2>
              </div>
              <div className="space-y-2.5">
                {attention.map((r) => {
                  const label = hostTaskLabel(r);
                  return (
                    <div key={r.ref} className="flex flex-col gap-2 rounded-xl bg-white p-3 shadow-xs sm:flex-row sm:items-center sm:justify-between dark:bg-neutral-900">
                      <div className="text-xs sm:text-sm">
                        <strong className="font-bold text-neutral-900 dark:text-white">
                          {r.renters.join(" & ")} · {r.listingName}
                        </strong>
                        <span className="text-neutral-600 dark:text-neutral-400"> — {label}</span>
                      </div>
                      <button
                        type="button"
                        onClick={() => handleCta(r)}
                        className="inline-flex shrink-0 items-center justify-center rounded-xl bg-rose-600 px-3.5 py-1.5 text-xs font-bold text-white hover:bg-rose-700"
                      >
                        {label}
                      </button>
                    </div>
                  );
                })}
              </div>
            </section>
          )}

          <section className="space-y-3 rounded-2xl border border-neutral-200 bg-white p-4 shadow-xs dark:border-neutral-800 dark:bg-neutral-900">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
              <div className="relative flex-1">
                <Search className="absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
                <input
                  type="search"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Search renter, rental ID, listing, property or agreement"
                  className="w-full h-10 rounded-full border border-neutral-200 bg-neutral-50/50 py-2 pl-10 pr-4 text-xs outline-none focus:border-primary-600 focus:bg-white dark:border-neutral-700 dark:bg-neutral-800 dark:text-white transition shadow-2xs"
                />
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:flex lg:flex-wrap gap-2.5 items-end">
                <RoundedFilterDropdown
                  label="Status"
                  value={statusFilter}
                  onChange={(val) => setStatusFilter(val as typeof statusFilter)}
                  options={[
                    { value: "all", label: "All statuses" },
                    ...(Object.keys(STATUS_CONFIG) as HostRentalStatus[]).map((s) => ({
                      value: s,
                      label: STATUS_CONFIG[s].label,
                    })),
                  ]}
                  size="sm"
                />
                <RoundedFilterDropdown
                  label="Property"
                  value={propertyFilter}
                  onChange={setPropertyFilter}
                  options={[
                    { value: "all", label: "All properties" },
                    ...properties.map(([id, address]) => ({ value: id, label: address })),
                  ]}
                  size="sm"
                />
                <RoundedFilterDropdown
                  label="Agreement"
                  value={agreementFilter}
                  onChange={(val) => setAgreementFilter(val as typeof agreementFilter)}
                  options={[
                    { value: "all", label: "Any agreement" },
                    { value: "signed", label: "Signed" },
                    { value: "host", label: "Your signature required" },
                    { value: "renter", label: "Renter signature required" },
                  ]}
                  size="sm"
                />
                <RoundedFilterDropdown
                  label="Sort"
                  value={sortFilter}
                  onChange={(val) => setSortFilter(val as typeof sortFilter)}
                  options={[
                    { value: "action", label: "Action required first" },
                    { value: "start", label: "Start date" },
                    { value: "updated", label: "Recently updated" },
                  ]}
                  align="right"
                  size="sm"
                />
              </div>
            </div>
          </section>

          <section className="overflow-hidden rounded-2xl border border-neutral-200 bg-white shadow-xs dark:border-neutral-800 dark:bg-neutral-900">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-neutral-100 px-5 py-3.5 dark:border-neutral-800">
              <div className="flex items-center gap-1 rounded-xl bg-neutral-100 p-1 dark:bg-neutral-800">
                {(["current", "history"] as const).map((tab) => (
                  <button
                    key={tab}
                    type="button"
                    onClick={() => {
                      setViewTab(tab);
                      if (tab === "history") setSelectedCard("");
                    }}
                    className={cn(
                      "rounded-lg px-4 py-1.5 text-xs font-bold transition",
                      viewTab === tab ? "bg-white text-primary-950 shadow-xs dark:bg-neutral-900 dark:text-white" : "text-neutral-600 dark:text-neutral-400",
                    )}
                  >
                    {tab === "current" ? `Current (${currentRecords.length})` : `History (${historyRecords.length})`}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-3 text-xs font-semibold text-neutral-500">
                <span>{displayed.length} {displayed.length === 1 ? "record" : "records"}</span>
                {hasActiveFilters && (
                  <button type="button" onClick={clearFilters} className="font-bold text-primary-700 hover:underline">
                    Clear filters
                  </button>
                )}
              </div>
            </div>

            {displayed.length === 0 ? (
              <div className="px-5 py-12 text-center">
                <FileText className="mx-auto mb-2 h-8 w-8 text-neutral-300" />
                <p className="text-sm font-bold text-neutral-800 dark:text-neutral-200">
                  {records.length === 0 ? "No rentals yet" : "No records match"}
                </p>
                <p className="mt-1 text-xs text-neutral-500">
                  {records.length === 0
                    ? "A rental appears here once a renter accepts your offer."
                    : "Try clearing the filters."}
                </p>
              </div>
            ) : (
              <ul className="divide-y divide-neutral-100 dark:divide-neutral-800">
                {displayed.map((r) => {
                  const requests = pendingRequestsFor(r).length;
                  return (
                    <li key={r.ref} className="flex flex-col gap-3 px-5 py-4 lg:flex-row lg:items-center">
                      <button type="button" onClick={() => openRecord(r)} className="min-w-0 flex-1 text-left">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-xs font-bold text-neutral-500">{r.ref}</span>
                          <span className={cn("rounded-full border px-2 py-0.5 text-[10px] font-bold", STATUS_CONFIG[r.status].cls)}>
                            {STATUS_CONFIG[r.status].label}
                          </span>
                          {requests > 0 && (
                            <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-[10px] font-bold text-violet-800">
                              {requests} change request{requests === 1 ? "" : "s"}
                            </span>
                          )}
                          {r.payments && r.payments.overdueCount > 0 && (
                            <span className="rounded-full border border-rose-200 bg-rose-50 px-2 py-0.5 text-[10px] font-bold text-rose-800">
                              {r.payments.overdueCount} overdue
                            </span>
                          )}
                        </div>
                        <p className="mt-1 truncate text-sm font-bold text-neutral-900 dark:text-white">
                          {r.renters.join(" & ")} · {r.listingName}
                        </p>
                        <p className="truncate text-xs text-neutral-500">
                          {r.propertyAddress} · {roomLabel(r)}
                        </p>
                      </button>
                      <div className="grid grid-cols-3 gap-4 text-xs lg:w-90">
                        <div>
                          <div className="text-[10px] font-bold uppercase text-neutral-400">Term</div>
                          <div className="font-semibold text-neutral-800 dark:text-neutral-200">{termLabel(r)}</div>
                        </div>
                        <div>
                          <div className="text-[10px] font-bold uppercase text-neutral-400">Rent</div>
                          <div className="font-semibold text-neutral-800 dark:text-neutral-200">{money(r, r.monthlyRent)}/mo</div>
                        </div>
                        <div>
                          <div className="text-[10px] font-bold uppercase text-neutral-400">Agreement</div>
                          <div className="font-semibold text-neutral-800 dark:text-neutral-200">{agreementLabel(r)}</div>
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => handleCta(r)}
                        className={cn(
                          "shrink-0 rounded-xl px-4 py-2 text-xs font-bold transition",
                          needsHost(r)
                            ? "bg-primary-900 text-white hover:bg-primary-800"
                            : "border border-neutral-300 bg-white text-neutral-800 hover:bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200",
                        )}
                      >
                        {needsHost(r) ? hostTaskLabel(r) : ACTION_LABEL[r.nextAction]}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </>
      )}

      {/* ===== Detail drawer ===== */}
      {selected && (
        <div className="fixed inset-0 z-40 flex justify-end bg-black/30" onClick={() => setSelectedRef(null)}>
          <aside
            className="flex h-full w-full max-w-xl flex-col overflow-hidden bg-white shadow-xl dark:bg-neutral-900"
            onClick={(e) => e.stopPropagation()}
          >
            <header className="flex items-start justify-between gap-3 border-b border-neutral-100 p-5 dark:border-neutral-800">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-bold text-neutral-500">{selected.ref}</span>
                  <span className={cn("rounded-full border px-2 py-0.5 text-[10px] font-bold", STATUS_CONFIG[selected.status].cls)}>
                    {STATUS_CONFIG[selected.status].label}
                  </span>
                </div>
                <h2 className="mt-1 truncate font-heading text-lg font-bold text-neutral-900 dark:text-white">
                  {selected.renters.join(" & ")}
                </h2>
                <p className="truncate text-xs text-neutral-500">{selected.listingName} · {selected.propertyAddress}</p>
              </div>
              <button type="button" onClick={() => setSelectedRef(null)} aria-label="Close" className="rounded-lg p-1.5 hover:bg-neutral-100 dark:hover:bg-neutral-800">
                <X className="h-5 w-5" />
              </button>
            </header>

            <nav className="flex gap-1 overflow-x-auto border-b border-neutral-100 px-5 dark:border-neutral-800">
              {(
                [
                  ["overview", "Overview"],
                  ["agreement", "Agreement"],
                  ["payments", "Payments"],
                  ["timeline", "Timeline"],
                  ["requests", `Requests${pendingRequestsFor(selected).length ? ` (${pendingRequestsFor(selected).length})` : ""}`],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setDrawerTab(key)}
                  className={cn(
                    "whitespace-nowrap border-b-2 px-3 py-3 text-xs font-bold",
                    drawerTab === key ? "border-primary-700 text-primary-900 dark:text-white" : "border-transparent text-neutral-500",
                  )}
                >
                  {label}
                </button>
              ))}
            </nav>

            <div className="flex-1 space-y-4 overflow-y-auto p-5 text-sm">
              {drawerTab === "overview" && (
                <>
                  <dl className="grid grid-cols-2 gap-3 text-xs">
                    {[
                      ["Room", roomLabel(selected)],
                      ["Term", termLabel(selected)],
                      ["Monthly rent", money(selected, selected.monthlyRent)],
                      ["Deposit", money(selected, selected.depositAmount)],
                      ["Length", selected.termMonths ? `${selected.termMonths} months` : "—"],
                      ["Rent cadence", selected.cadence ? selected.cadence.toLowerCase() : "—"],
                      ["Application", `ZR-A-${selected.applicationId}`],
                      ["Agreement", selected.agreementId ? `ZR-AGR-${selected.agreementId}` : "—"],
                    ].map(([k, v]) => (
                      <div key={k} className="rounded-xl border border-neutral-200 p-2.5 dark:border-neutral-800">
                        <dt className="text-[10px] font-bold uppercase text-neutral-400">{k}</dt>
                        <dd className="font-semibold text-neutral-900 dark:text-white">{v}</dd>
                      </div>
                    ))}
                  </dl>

                  {issues
                    .filter((i) => i.occupancyId === selected.occupancyId && i.status !== "RESOLVED")
                    .map((i) => (
                      <div key={i.id} className="space-y-2 rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs text-rose-900 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200">
                        <p>
                          <strong>Reported issue ({SEVERITY_LABEL[i.severity]})</strong> · fix due{" "}
                          {i.fixDueAt ? formatDate(i.fixDueAt) : "—"}
                          {i.fixRejectedCount > 0 && ` · renter said it wasn't fixed (${i.fixRejectedCount}×)`}
                        </p>
                        {i.description && <p className="italic">“{i.description}”</p>}
                        {i.status === "FIX_REPORTED" ? (
                          <p>You reported it fixed — waiting for the renter to confirm.</p>
                        ) : (
                          <div className="flex flex-wrap gap-2">
                            {i.status === "OPEN" && (
                              <button type="button" disabled={busy} onClick={() => run(() => acknowledgeHabitabilityIssue(i.id), "Acknowledged — the renter has been told.")}
                                className="rounded-lg border border-rose-300 px-3 py-1.5 font-bold disabled:opacity-50">Acknowledge</button>
                            )}
                            <button type="button" disabled={busy} onClick={() => run(() => reportHabitabilityFixed(i.id, ""), "Reported fixed — the renter will confirm.")}
                              className="rounded-lg bg-primary-900 px-3 py-1.5 font-bold text-white disabled:opacity-50">Mark fixed</button>
                          </div>
                        )}
                      </div>
                    ))}

                  {selected.notice && (
                    <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
                      <strong>{selected.notice.initiatedBy === "RENTER" ? "Renter" : "You"} gave notice</strong> on{" "}
                      {formatDate(selected.notice.givenAt)}
                      {selected.notice.effectiveDate && <> · ends {formatDate(selected.notice.effectiveDate)}</>} ·{" "}
                      {selected.notice.status.replace(/_/g, " ").toLowerCase()}
                    </div>
                  )}

                  {selected.sublet && (
                    <Link
                      href="/account/host/sublet-requests"
                      className="flex items-center gap-2 rounded-xl border border-neutral-200 p-3 text-xs font-semibold hover:bg-neutral-50 dark:border-neutral-800 dark:hover:bg-neutral-800"
                    >
                      <Repeat className="h-4 w-4" /> Sublet request #{selected.sublet.id} · {selected.sublet.status.replace(/_/g, " ")}
                    </Link>
                  )}

                  <div className="flex flex-wrap gap-2 border-t border-neutral-100 pt-4 dark:border-neutral-800">
                    <Link
                      href={`/account/host/applications?applicationId=${selected.applicationId}`}
                      className="inline-flex items-center gap-1.5 rounded-xl border border-neutral-300 px-3 py-2 text-xs font-bold dark:border-neutral-700"
                    >
                      <MessagesSquare className="h-3.5 w-3.5" /> Message renter
                    </Link>
                    {selected.agreementStatus === "SIGNED" && selected.occupancyId && ["active", "ending", "upcoming"].includes(selected.status) && (
                      <button
                        type="button"
                        onClick={() => openModal({ type: "amend", record: selected })}
                        className="rounded-xl border border-neutral-300 px-3 py-2 text-xs font-bold dark:border-neutral-700"
                      >
                        Extend or change rent
                      </button>
                    )}
                    {selected.occupancyId && ["active", "ending"].includes(selected.status) && (
                      <button
                        type="button"
                        onClick={() => openModal({ type: "end", record: selected })}
                        className="rounded-xl border border-rose-300 px-3 py-2 text-xs font-bold text-rose-700 dark:border-rose-900"
                      >
                        End tenancy
                      </button>
                    )}
                  </div>
                </>
              )}

              {drawerTab === "agreement" && (
                <div className="space-y-3 text-xs">
                  <p>
                    <strong>Status:</strong> {agreementLabel(selected)}
                  </p>
                  <p>
                    Your signature: {selected.hostSignedAt ? formatDateTime(selected.hostSignedAt) : "not yet"} · Renter:{" "}
                    {selected.renterSignedAt ? formatDateTime(selected.renterSignedAt) : "not yet"}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {selected.agreementId && (
                      <a
                        href={hostAgreementPdfUrl(selected.agreementId)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1.5 rounded-xl border border-neutral-300 px-3 py-2 font-bold dark:border-neutral-700"
                      >
                        <Download className="h-3.5 w-3.5" /> Agreement PDF
                      </a>
                    )}
                    {selected.nextAction === "SIGN_AGREEMENT" && (
                      <button
                        type="button"
                        onClick={() => setModal({ type: "sign", record: selected })}
                        className="rounded-xl bg-primary-900 px-3 py-2 font-bold text-white"
                      >
                        Review & sign
                      </button>
                    )}
                  </div>
                  {txRecord && txRecord.amendments.length > 0 && (
                    <div>
                      <h3 className="mb-1 text-[10px] font-bold uppercase text-neutral-400">Amendments</h3>
                      <ul className="space-y-1">
                        {txRecord.amendments.map((a) => (
                          <li key={a.id} className="rounded-lg border border-neutral-200 p-2 dark:border-neutral-800">
                            #{a.id} · {a.status.replace(/_/g, " ").toLowerCase()} · {a.reason || a.amendmentType}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}

              {drawerTab === "payments" && (
                <div className="space-y-3 text-xs">
                  {refunds
                    .filter((rf) => rf.occupancyId === selected.occupancyId && rf.status !== "CONFIRMED")
                    .map((rf) => (
                      <div key={rf.id} className="space-y-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-amber-900">
                        <p>
                          <strong>Refund owed: {formatCurrency(rf.amount, rf.currency)}</strong> · due by {formatDate(rf.dueBy)}
                          {rf.status === "OVERDUE" && " · overdue"}
                          {rf.status === "MARKED_PAID" && " · marked paid, waiting for the renter to confirm"}
                          {rf.status === "NOT_RECEIVED" && ` · the renter says it hasn't arrived${rf.notReceivedNote ? `: “${rf.notReceivedNote}”` : ""}`}
                        </p>
                        <p className="text-amber-800">{rf.reason}. Pay it to the renter directly, then mark it paid.</p>
                        {rf.status !== "MARKED_PAID" && (
                          <div className="flex gap-2">
                            <input
                              value={refundRef}
                              onChange={(e) => setRefundRef(e.target.value)}
                              placeholder="Payment reference (optional)"
                              className="h-9 flex-1 rounded-lg border border-amber-300 bg-white px-2"
                            />
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => run(() => markHostRefundPaid(rf.id, refundRef.trim()), "Marked paid — the renter will confirm.")}
                              className="rounded-lg bg-primary-900 px-3 font-bold text-white disabled:opacity-50"
                            >
                              Mark paid
                            </button>
                          </div>
                        )}
                      </div>
                    ))}
                  {selected.payments ? (
                    <div className="grid grid-cols-3 gap-2">
                      <div className="rounded-xl border p-2.5 dark:border-neutral-800">
                        <div className="text-[10px] font-bold uppercase text-neutral-400">Confirmed</div>
                        <div className="font-bold">{selected.payments.paidCount}</div>
                      </div>
                      <div className="rounded-xl border p-2.5 dark:border-neutral-800">
                        <div className="text-[10px] font-bold uppercase text-neutral-400">Overdue</div>
                        <div className="font-bold text-rose-700">
                          {selected.payments.overdueCount} · {formatCurrency(selected.payments.overdueAmount, selected.payments.currency)}
                        </div>
                      </div>
                      <div className="rounded-xl border p-2.5 dark:border-neutral-800">
                        <div className="text-[10px] font-bold uppercase text-neutral-400">Next due</div>
                        <div className="font-bold">
                          {selected.payments.nextDueDate
                            ? `${formatDate(selected.payments.nextDueDate)} · ${formatCurrency(selected.payments.nextDueAmount ?? 0, selected.payments.currency)}`
                            : "—"}
                        </div>
                      </div>
                    </div>
                  ) : (
                    <p className="text-neutral-500">No rent obligations recorded yet.</p>
                  )}
                  {txLoading && <p className="text-neutral-400">Loading payment records…</p>}
                  {txError && <p className="font-semibold text-rose-600">{txError}</p>}
                  {txRecord && txRecord.rentalPaymentObligations.length > 0 && (
                    <ul className="divide-y rounded-xl border dark:divide-neutral-800 dark:border-neutral-800">
                      {txRecord.rentalPaymentObligations.map((o) => (
                        <li key={o.id} className="flex items-center justify-between p-2.5">
                          <span>
                            {o.displayLabel} · due {formatDate(o.dueDate)}
                          </span>
                          <span className="font-semibold">
                            {formatCurrency(o.amount, o.currency)} · {o.status.replace(/_/g, " ").toLowerCase()}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                  <Link
                    href="/account/host/payments"
                    className="inline-flex items-center gap-1.5 rounded-xl border border-neutral-300 px-3 py-2 font-bold dark:border-neutral-700"
                  >
                    <CreditCard className="h-3.5 w-3.5" /> Confirm receipts or dispute a payment record
                  </Link>
                  <p className="text-neutral-500">
                    A payment correction is raised as a dispute on that payment record; the original value stays on record.
                  </p>
                </div>
              )}

              {drawerTab === "timeline" && (
                <div className="text-xs">
                  {!selected.occupancyId ? (
                    <p className="text-neutral-500">The timeline starts once the renter moves in.</p>
                  ) : txLoading ? (
                    <p className="text-neutral-400">Loading…</p>
                  ) : txError ? (
                    <p className="font-semibold text-rose-600">{txError}</p>
                  ) : txRecord && txRecord.timeline.length > 0 ? (
                    <ol className="space-y-2 border-l border-neutral-200 pl-4 dark:border-neutral-800">
                      {txRecord.timeline.map((t, i) => (
                        <li key={i}>
                          <div className="font-semibold text-neutral-900 dark:text-white">{t.eventType.replace(/[._]/g, " ")}</div>
                          <div className="text-neutral-500">{formatDateTime(t.timestamp)} · {t.source}</div>
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <p className="text-neutral-500">No events yet.</p>
                  )}
                </div>
              )}

              {drawerTab === "requests" && (
                <div className="space-y-3 text-xs">
                  {changeRequests.filter((b) => b.agreementId === selected.agreementId).length === 0 ? (
                    <p className="text-neutral-500">The renter hasn&apos;t asked for any changes.</p>
                  ) : (
                    changeRequests
                      .filter((b) => b.agreementId === selected.agreementId)
                      .map((b) => (
                        <div key={b.id} className="space-y-2 rounded-xl border border-neutral-200 p-3 dark:border-neutral-800">
                          <div className="flex items-center justify-between">
                            <strong>{CHANGE_LABEL[b.changeType] ?? b.changeType}</strong>
                            <span className="text-neutral-500">{b.status.replace(/_/g, " ").toLowerCase()}</span>
                          </div>
                          <p className="text-neutral-600 dark:text-neutral-300">
                            {b.additionalTermMonths ? `${b.additionalTermMonths > 0 ? "+" : ""}${b.additionalTermMonths} months` : ""}
                            {b.proposedEndDate ? ` · new end ${formatDate(b.proposedEndDate)}` : ""}
                            {b.proposedStartDate ? ` · new start ${formatDate(b.proposedStartDate)}` : ""}
                            {b.proposedMonthlyRent != null ? ` · rent ${formatCurrency(b.proposedMonthlyRent, selected.currency)}` : ""}
                          </p>
                          {b.reason && <p className="italic text-neutral-500">“{b.reason}”</p>}
                          {b.status === "AWAITING_HOST" && (
                            <div className="flex gap-2">
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() => run(() => approveHostChangeRequest(b.id), "Approved — both of you will sign the updated agreement.")}
                                className="rounded-lg bg-primary-900 px-3 py-1.5 font-bold text-white disabled:opacity-50"
                              >
                                Approve
                              </button>
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() => run(() => declineHostChangeRequest(b.id), "Declined — the renter has been told.")}
                                className="rounded-lg border border-neutral-300 px-3 py-1.5 font-bold disabled:opacity-50 dark:border-neutral-700"
                              >
                                Decline
                              </button>
                            </div>
                          )}
                        </div>
                      ))
                  )}
                </div>
              )}
            </div>
          </aside>
        </div>
      )}

      {/* ===== Sign agreement ===== */}
      {modal?.type === "sign" && modal.record.agreementId && (
        <Modal open onClose={() => setModal(null)} title="Sign the agreement">
          <div className="space-y-4 text-xs">
            <p>
              Read the agreement before signing. Your signature is recorded with the time and the exact document version.
            </p>
            <a
              href={hostAgreementPdfUrl(modal.record.agreementId)}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 rounded-xl border border-neutral-300 px-3 py-2 font-bold dark:border-neutral-700"
            >
              <FileText className="h-3.5 w-3.5" /> Open agreement PDF
            </a>
            <div className="flex justify-end gap-2 border-t pt-3 dark:border-neutral-800">
              <button type="button" onClick={() => setModal(null)} className="rounded-xl border border-neutral-300 px-3 py-2 font-bold dark:border-neutral-700">
                Cancel
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => run(() => signHostedAgreement(modal.record.agreementId!), "Agreement signed.")}
                className="rounded-xl bg-primary-900 px-4 py-2 font-bold text-white disabled:opacity-50"
              >
                Sign agreement
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* ===== Move-in ===== */}
      {modal?.type === "movein" && modal.record.occupancyId && (
        <Modal open onClose={() => setModal(null)} title={`Prepare move-in · ${modal.record.renters.join(" & ")}`}>
          <HostMoveInSteps
            occupancyId={modal.record.occupancyId}
            showToast={showToast}
            onMovedIn={() => {
              setModal(null);
              load();
            }}
          />
        </Modal>
      )}

      {/* ===== Notice ===== */}
      {modal?.type === "notice" && modal.record.notice && (
        <Modal open onClose={() => setModal(null)} title="Notice">
          <div className="space-y-4 text-xs">
            <div className="grid grid-cols-2 gap-2">
              {[
                ["Given by", modal.record.notice.initiatedBy === "RENTER" ? modal.record.renters.join(" & ") : "You"],
                ["Given on", formatDate(modal.record.notice.givenAt)],
                ["Ends", modal.record.notice.effectiveDate ? formatDate(modal.record.notice.effectiveDate) : "Not set yet"],
                ["Status", modal.record.notice.status.replace(/_/g, " ").toLowerCase()],
              ].map(([k, v]) => (
                <div key={k} className="rounded-xl border p-2.5 dark:border-neutral-800">
                  <span className="text-[10px] font-bold uppercase text-neutral-400">{k}</span>
                  <div className="font-bold">{v}</div>
                </div>
              ))}
            </div>

            {modal.record.nextAction === "RESPOND_SURRENDER" && modal.record.notice.terminationCaseId && (
              <>
                <p>The renter has asked to end the tenancy on this date. It only becomes binding if you accept.</p>
                <div className="flex justify-end gap-2 border-t pt-3 dark:border-neutral-800">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => run(() => declineHostSurrender(modal.record.notice!.terminationCaseId!), "Declined — the tenancy continues as agreed.")}
                    className="rounded-xl border border-neutral-300 px-3 py-2 font-bold disabled:opacity-50 dark:border-neutral-700"
                  >
                    Decline
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => run(() => acceptHostSurrender(modal.record.notice!.terminationCaseId!), "Accepted — the end date is now set.")}
                    className="rounded-xl bg-primary-900 px-4 py-2 font-bold text-white disabled:opacity-50"
                  >
                    Accept end date
                  </button>
                </div>
              </>
            )}

            {modal.record.notice.kind === "MOVE_OUT_NOTICE" && (
              <>
                {modal.record.notice.status === "GIVEN" && (
                  <p className="text-neutral-600 dark:text-neutral-300">
                    The renter will confirm here once they&apos;ve moved out. You&apos;ll then confirm the room was handed back.
                  </p>
                )}
                {modal.record.notice.status === "RENTER_VACATED" && modal.record.occupancyId && (
                  <>
                    <p>The renter says they&apos;ve moved out. Confirm once you have the room and keys back.</p>
                    <label className="block font-bold">
                      Notes (optional)
                      <textarea rows={2} value={moveOutNotes} onChange={(e) => setMoveOutNotes(e.target.value)} className={inputCls} />
                    </label>
                    <div className="flex justify-end border-t pt-3 dark:border-neutral-800">
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => run(() => confirmHostMoveOut(modal.record.occupancyId!, moveOutNotes.trim()), "Move-out confirmed.")}
                        className="rounded-xl bg-primary-900 px-4 py-2 font-bold text-white disabled:opacity-50"
                      >
                        Confirm move-out
                      </button>
                    </div>
                  </>
                )}
                {modal.record.notice.status === "HOST_CONFIRMED" && (
                  <p className="flex items-center gap-1.5 text-emerald-700">
                    <CheckCircle2 className="h-4 w-4" /> You confirmed the move-out.
                  </p>
                )}
              </>
            )}

            {modal.record.notice.kind === "TERMINATION_CASE" && modal.record.notice.terminationCaseId && (() => {
              const notice = modal.record.notice!;
              const caseId = notice.terminationCaseId!;
              const due = notice.responseDueAt ? formatDate(notice.responseDueAt) : null;
              if (modal.record.nextAction === "RESPOND_END_DATE") {
                return (
                  <div className="space-y-3 border-t pt-3 dark:border-neutral-800">
                    <p>
                      Answer by {due}. If you don&apos;t, the proposed date is accepted
                      {modal.record.renters.length > 1 ? " (every tenant also answers)" : ""}.
                    </p>
                    <label className="block font-bold">
                      Propose a different date instead
                      <input type="date" value={counterDate} min={new Date().toISOString().slice(0, 10)} onChange={(e) => setCounterDate(e.target.value)} className={inputCls} />
                    </label>
                    <div className="flex flex-wrap justify-end gap-2">
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => run(() => hostTermination.respond(caseId, { response: "CONTEST" }), "Contested — the tenancy continues unless a court or tribunal orders otherwise.")}
                        className="rounded-xl border border-rose-300 px-3 py-2 font-bold text-rose-700 disabled:opacity-50"
                      >
                        Contest
                      </button>
                      <button
                        type="button"
                        disabled={busy || !counterDate}
                        onClick={() => run(() => hostTermination.respond(caseId, { response: "COUNTER", proposedDate: counterDate }), "Your date was sent for the renter to answer.")}
                        className="rounded-xl border border-neutral-300 px-3 py-2 font-bold disabled:opacity-50 dark:border-neutral-700"
                      >
                        Propose this date
                      </button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => run(() => hostTermination.respond(caseId, { response: "ACCEPT" }), "Accepted.")}
                        className="rounded-xl bg-primary-900 px-4 py-2 font-bold text-white disabled:opacity-50"
                      >
                        Accept date
                      </button>
                    </div>
                  </div>
                );
              }
              if (notice.status === "CONTESTED") {
                return (
                  <div className="space-y-3 border-t pt-3 dark:border-neutral-800">
                    <p>This was contested, so the tenancy continues. If a court or tribunal decides it, record the order here and it&apos;s applied automatically.</p>
                    <select value={orderOutcome} onChange={(e) => setOrderOutcome(e.target.value as typeof orderOutcome)} className={selectCls}>
                      <option value="ENDS_ON_DATE">The order ends the tenancy</option>
                      <option value="TENANCY_CONTINUES">The order says the tenancy continues</option>
                    </select>
                    <label className="block font-bold">
                      Order reference
                      <input value={orderRef} onChange={(e) => setOrderRef(e.target.value)} className={inputCls} />
                    </label>
                    {orderOutcome === "ENDS_ON_DATE" && (
                      <div className="grid grid-cols-2 gap-3">
                        <label className="block font-bold">
                          End date in the order
                          <input type="date" value={orderDate} onChange={(e) => setOrderDate(e.target.value)} className={inputCls} />
                        </label>
                        <label className="block font-bold">
                          Amount the order awards (optional)
                          <input type="number" min={0} step="0.01" value={orderLiability} onChange={(e) => setOrderLiability(e.target.value)} className={inputCls} />
                        </label>
                      </div>
                    )}
                    <div className="flex justify-end">
                      <button
                        type="button"
                        disabled={busy || !orderRef.trim() || (orderOutcome === "ENDS_ON_DATE" && !orderDate)}
                        onClick={() =>
                          run(
                            () =>
                              hostTermination.recordExternalDecision(caseId, {
                                outcome: orderOutcome,
                                orderRef: orderRef.trim(),
                                effectiveDate: orderOutcome === "ENDS_ON_DATE" ? orderDate : undefined,
                                liabilityAmount: orderLiability ? Number(orderLiability) : undefined,
                              }),
                            "Order recorded.",
                          )
                        }
                        className="rounded-xl bg-primary-900 px-4 py-2 font-bold text-white disabled:opacity-50"
                      >
                        Record order
                      </button>
                    </div>
                  </div>
                );
              }
              if (notice.status === "AWAITING_RESPONSE" || notice.status === "SURRENDER_PROPOSED") {
                return (
                  <div className="space-y-3 border-t pt-3 dark:border-neutral-800">
                    <p className="text-neutral-600 dark:text-neutral-300">
                      Waiting for the renter{due ? ` until ${due}` : ""}.
                      {notice.status === "AWAITING_RESPONSE" && notice.causeCode !== "MUTUAL_SURRENDER"
                        ? " No reply by then counts as acceptance."
                        : ""}
                    </p>
                    {notice.initiatedBy === "HOST" && (
                      <div className="flex justify-end">
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => run(() => hostTermination.withdraw(caseId), "Withdrawn.")}
                          className="rounded-xl border border-neutral-300 px-3 py-2 font-bold disabled:opacity-50 dark:border-neutral-700"
                        >
                          Withdraw
                        </button>
                      </div>
                    )}
                  </div>
                );
              }
              return modal.record.nextAction !== "RESPOND_SURRENDER" ? (
                <p className="text-neutral-600 dark:text-neutral-300">No action is needed from you right now.</p>
              ) : null;
            })()}
          </div>
        </Modal>
      )}

      {/* ===== End tenancy ===== */}
      {modal?.type === "end" && modal.record.occupancyId && (
        <Modal open onClose={() => setModal(null)} title="End tenancy">
          <div className="space-y-4 text-xs">
            <fieldset className="space-y-2">
              <legend className="font-bold">Why is the tenancy ending?</legend>
              {HOST_END_CAUSES.map((c) => (
                <label key={c.code} className="flex cursor-pointer items-start gap-2 rounded-xl border p-2.5 dark:border-neutral-800">
                  <input type="radio" name="end-cause" checked={endCause === c.code} onChange={() => setEndCause(c.code)} className="mt-0.5" />
                  <span>
                    <span className="block font-semibold">{c.label}</span>
                    <span className="text-neutral-500">{END_KIND_HELP[c.kind]}</span>
                  </span>
                </label>
              ))}
            </fieldset>
            {endKind !== "rules" && (
              <label className="block font-bold">
                Proposed end date
                <input type="date" value={endDate} min={endMinDate} onChange={(e) => setEndDate(e.target.value)} className={inputCls} />
              </label>
            )}
            <label className="block font-bold">
              Details {endKind === "mutual" ? "(optional)" : "(required)"}
              <textarea rows={3} value={endNotes} onChange={(e) => setEndNotes(e.target.value)} className={inputCls} />
            </label>
            <div className="flex justify-end gap-2 border-t pt-3 dark:border-neutral-800">
              <button type="button" onClick={() => setModal(null)} className="rounded-xl border border-neutral-300 px-3 py-2 font-bold dark:border-neutral-700">
                Cancel
              </button>
              <button
                type="button"
                disabled={busy || (endKind !== "rules" && (!endDate || endDate < endMinDate)) || (endKind !== "mutual" && !endNotes.trim())}
                onClick={() =>
                  run(
                    () =>
                      startHostTermination(modal.record.occupancyId!, {
                        causeCode: endCause,
                        notes: endNotes.trim(),
                        proposedEffectiveDate: endKind !== "rules" ? endDate : undefined,
                      }),
                    endKind === "rules" ? "Submitted — the renter has been notified." : "End date proposed — the renter has been asked to answer.",
                  )
                }
                className="rounded-xl bg-rose-700 px-4 py-2 font-bold text-white disabled:opacity-50"
              >
                Submit
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* ===== Extend / change rent ===== */}
      {modal?.type === "amend" && modal.record.agreementId && (
        <Modal open onClose={() => setModal(null)} title="Extend or change rent">
          <div className="space-y-4 text-xs">
            <p className="text-neutral-600 dark:text-neutral-300">
              This creates a new version of the agreement. It takes effect only once you and the renter both sign it; until then the
              current agreement ({money(modal.record, modal.record.monthlyRent)}/mo, {modal.record.termMonths ?? "—"} months) stays in force.
            </p>
            <div className="grid grid-cols-2 gap-3">
              <label className="block font-bold">
                Extend by (months)
                <input type="number" min={1} max={60} value={amendMonths} onChange={(e) => setAmendMonths(e.target.value)} className={inputCls} />
              </label>
              <label className="block font-bold">
                New monthly rent ({modal.record.currency})
                <input type="number" min={1} step="0.01" value={amendRent} onChange={(e) => setAmendRent(e.target.value)} className={inputCls} />
              </label>
            </div>
            <label className="block font-bold">
              Reason (shown to the renter)
              <input type="text" value={amendReason} onChange={(e) => setAmendReason(e.target.value)} className={inputCls} />
            </label>
            <div className="flex justify-end gap-2 border-t pt-3 dark:border-neutral-800">
              <button type="button" onClick={() => setModal(null)} className="rounded-xl border border-neutral-300 px-3 py-2 font-bold dark:border-neutral-700">
                Cancel
              </button>
              <button
                type="button"
                disabled={busy || (!amendMonths && !amendRent)}
                onClick={() =>
                  run(
                    () =>
                      proposeHostAmendment(modal.record.agreementId!, {
                        additionalTermMonths: amendMonths ? Number(amendMonths) : undefined,
                        monthlyRent: amendRent ? Number(amendRent) : undefined,
                        reason: amendReason.trim(),
                      }),
                    "Updated agreement created — sign it, then the renter will be asked to sign.",
                  )
                }
                className="rounded-xl bg-primary-900 px-4 py-2 font-bold text-white disabled:opacity-50"
              >
                Create updated agreement
              </button>
            </div>
          </div>
        </Modal>
      )}
      <DepositClaimsSection side="host" showToast={showToast} />
    </div>
  );
}
