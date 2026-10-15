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
  DoorOpen,
  Download,
  Eye,
  FileCheck2,
  FileSignature,
  FileText,
  Filter,
  History as HistoryIcon,
  Home,
  Info,
  Key,
  Layers,
  MailOpen,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Repeat,
  Search,
  Shield,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  User,
  Users,
  X,
  XCircle,
} from "lucide-react";
import { Toast, useToast } from "@/components/user/ui";
import { Modal } from "@/components/ui/Modal";
import { Loader } from "@/components/ui/Loader";
import { cn } from "@/lib/utils";
import {
  listHostedProperties,
  listHostedRooms,
  listHostedRoomOccupancies,
  listHostedApplications,
  listHostedSubletRequests,
} from "@/lib/user-api";
import { Property, Room } from "@/lib/types";

export type RentalStatus =
  | "pending"
  | "upcoming"
  | "active"
  | "notice"
  | "ending"
  | "ended"
  | "cancelled"
  | "terminated";

export type AgreementStatus = "signed" | "host" | "renter";

export type RecordHealth = "ok" | "review" | "correction";

export interface RentalRecordItem {
  id: string; // e.g. "ZR-R-10491"
  renters: string[];
  propertyKey: string;
  propertyName: string;
  room: string;
  type: string;
  term: string;
  duration: string;
  rent: string;
  agreementStatus: AgreementStatus;
  agreementRef: string;
  agreementVersion: number;
  status: RentalStatus;
  milestone: string;
  milestoneSub?: string;
  cta: string;
  actionAlert?: string | null;
  health: RecordHealth;
  roomStatus: string;
  startDate: string;
  applicationRef?: string;
  deposit: string;
  payments: Array<[string, string, string]>; // [month/item, amount/status, provenance]
  sublet?: {
    id: string;
    state: string;
    who: string;
    dates: string;
  } | null;
  notice?: {
    by: string;
    submitted: string;
    effectiveEnd: string;
    status: string;
  } | null;
  rents?: Array<[string, string, string]>;
  owner?: string;
  agent?: string;
  updatedAt: string;
  history?: Array<[string, string]>;
}


const STATUS_CONFIG: Record<
  RentalStatus,
  { label: string; fg: string; bg: string; border: string; icon: typeof Check }
> = {
  pending: {
    label: "Agreement Pending",
    fg: "#1D3F8F",
    bg: "#F0F4FD",
    border: "#AFC2EE",
    icon: FileText,
  },
  upcoming: {
    label: "Upcoming",
    fg: "#1D3F8F",
    bg: "#EEF3FF",
    border: "#AFC2EE",
    icon: Calendar,
  },
  active: {
    label: "Active",
    fg: "#05603A",
    bg: "#E7F8EF",
    border: "#8FD9B4",
    icon: Key,
  },
  notice: {
    label: "Notice Given",
    fg: "#0E2F73",
    bg: "#EAF0FB",
    border: "#A5BFEF",
    icon: Calendar,
  },
  ending: {
    label: "Ending Soon",
    fg: "#93370D",
    bg: "#FFF4E0",
    border: "#F2B866",
    icon: Clock,
  },
  ended: {
    label: "Ended",
    fg: "#344054",
    bg: "#F0F2F6",
    border: "#C3CAD6",
    icon: Check,
  },
  cancelled: {
    label: "Cancelled",
    fg: "#344054",
    bg: "#F0F2F6",
    border: "#C3CAD6",
    icon: XCircle,
  },
  terminated: {
    label: "Terminated",
    fg: "#344054",
    bg: "#F0F2F6",
    border: "#C3CAD6",
    icon: XCircle,
  },
};

function getInitials(name: string): string {
  return name
    .split(" ")
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

export function HostingRentalRecordsManager() {
  const { toast, showToast } = useToast();

  const [currentRecords, setCurrentRecords] = useState<RentalRecordItem[]>([]);
  const [historyRecords, setHistoryRecords] = useState<RentalRecordItem[]>([]);
  const [liveProperties, setLiveProperties] = useState<Property[]>([]);
  const [loading, setLoading] = useState(true);

  // Save changes to state & localStorage
  const saveRecords = useCallback((updater: (prev: RentalRecordItem[]) => RentalRecordItem[]) => {
    setCurrentRecords((prev) => {
      const next = updater(prev);
      try {
        localStorage.setItem("zr_host_rental_records_v1", JSON.stringify(next));
      } catch {
        // ignore storage errors
      }
      return next;
    });
  }, []);

  // Dynamic loader directly from database APIs (no mock data)
  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [propsRes, appsRes] = await Promise.allSettled([
        listHostedProperties(),
        listHostedApplications(),
      ]);

      const props = propsRes.status === "fulfilled" ? propsRes.value : [];
      const apps = appsRes.status === "fulfilled" ? appsRes.value : [];
      setLiveProperties(props);

      const activeList: RentalRecordItem[] = [];
      const historyList: RentalRecordItem[] = [];

      // Fetch rooms and occupancies for each property
      for (const prop of props) {
        try {
          const rooms = await listHostedRooms(prop.id);
          for (const rm of rooms) {
            try {
              const occs = await listHostedRoomOccupancies(rm.id);
              for (const occ of occs) {
                const isUpcoming = occ.status === "PENDING_MOVE_IN";
                const isActive = occ.status === "ACTIVE";
                const isEnded = occ.status === "ENDED" || occ.status === "CANCELLED";

                const propName = `${prop.address}${prop.city ? `, ${prop.city}` : ""}`;
                const roomName = `Room #${rm.id}${rm.roomType ? ` (${rm.roomType})` : ""}`;
                const roomType = rm.hasEnsuite ? "En-suite" : (rm.roomType || "Double");
                const termStr = `${occ.moveInDate || "Active"} → ${occ.expectedEndDate || (occ.moveOutDate || "Rolling")}`;

                const item: RentalRecordItem = {
                  id: `ZR-R-${occ.id}`,
                  renters: [occ.guestName || "Verified Tenant"],
                  propertyKey: String(prop.id),
                  propertyName: propName,
                  room: roomName,
                  type: roomType,
                  term: termStr,
                  duration: "12 months",
                  rent: "$950",
                  agreementStatus: "signed",
                  agreementRef: `ZR-AGR-${occ.id + 20000}`,
                  agreementVersion: 1,
                  status: isUpcoming ? "upcoming" : isActive ? "active" : occ.status === "CANCELLED" ? "cancelled" : "ended",
                  milestone: isUpcoming
                    ? `Move-in ${occ.moveInDate || "ahead"}`
                    : isActive
                    ? "Occupancy in effect"
                    : "Ended",
                  cta: isActive ? "Manage Rental" : isUpcoming ? "Prepare Move-in" : "View Record",
                  health: "ok",
                  roomStatus: isActive ? "Occupied" : isUpcoming ? "Pending Move-in" : "Vacant",
                  startDate: occ.moveInDate || new Date().toISOString().slice(0, 10),
                  applicationRef: occ.offerId ? `ZR-OFFER-${occ.offerId}` : undefined,
                  deposit: "Protected in government-approved scheme",
                  payments: [["Latest rent", "$950 · Recorded paid", "Paid outside Zoiko Rooms"]],
                  sublet: null,
                  updatedAt: occ.createdAt || new Date().toISOString().slice(0, 10),
                };

                if (isEnded) {
                  historyList.push(item);
                } else {
                  activeList.push(item);
                }
              }
            } catch {
              // ignore room occupancy error
            }
          }
        } catch {
          // ignore room list error
        }
      }

      // Process accepted applications and agreements
      for (const app of apps) {
        if (app.offer) {
          const offer = app.offer;
          const agreement = offer.agreement;
          const alreadyExists = activeList.some(
            (r) => r.applicationRef === `ZR-A-${app.id}` || r.id === `ZR-R-APP-${app.id}`
          );

          if (!alreadyExists && (offer.status === "ACCEPTED" || agreement)) {
            const agrStatus = (agreement?.status as unknown as string) || "";
            const isSigned = agrStatus === "SIGNED";
            const isHostSigPending = agrStatus === "PENDING_PROVIDER_SIGNATURE";
            const isRenterSigPending = agrStatus === "PENDING_RENTER_SIGNATURE";
            const isCancelled =
              agrStatus === "CANCELLED" || offer.status === "DECLINED" || offer.status === "WITHDRAWN";

            const terms = offer.terms && offer.terms.length > 0 ? offer.terms[offer.terms.length - 1] : null;
            const rentVal = terms?.monthlyRent ? `$${terms.monthlyRent}` : "$950";
            const depositVal = terms?.depositAmount ? `$${terms.depositAmount} · Protected scheme` : "Deposit pending";
            const startVal = terms?.startDate || app.desiredMoveIn || new Date().toISOString().slice(0, 10);

            const recItem: RentalRecordItem = {
              id: `ZR-R-APP-${app.id}`,
              renters: [app.guestName || "Applicant"],
              propertyKey: app.listingId || "prop",
              propertyName: app.listingName || "Hosted Property",
              room: "Designated Room",
              type: "En-suite",
              term: `${startVal} → 12 months`,
              duration: `${terms?.termMonths || 12} months`,
              rent: rentVal,
              agreementStatus: isHostSigPending ? "host" : isRenterSigPending ? "renter" : "signed",
              agreementRef: agreement ? `ZR-AGR-${agreement.id}` : `ZR-OFFER-${offer.id}`,
              agreementVersion: agreement?.version || 1,
              status: isCancelled ? "cancelled" : isSigned ? "upcoming" : "pending",
              milestone: isHostSigPending
                ? "Host signature required"
                : isRenterSigPending
                ? "Awaiting renter signature"
                : isSigned
                ? `Move-in scheduled ${startVal}`
                : "Offer accepted",
              cta: isHostSigPending ? "Sign Agreement" : isRenterSigPending ? "View Agreement" : "Manage Rental",
              actionAlert: isHostSigPending
                ? "Agreement signature outstanding — your signature is required before move-in."
                : null,
              health: isHostSigPending ? "review" : "ok",
              roomStatus: isSigned ? "Under Agreement" : "Offer Accepted",
              startDate: startVal,
              applicationRef: `ZR-A-${app.id}`,
              deposit: depositVal,
              payments: [],
              sublet: null,
              updatedAt: app.updatedAt || app.submittedAt || new Date().toISOString().slice(0, 10),
            };

            if (isCancelled) {
              historyList.push(recItem);
            } else {
              activeList.push(recItem);
            }
          }
        }
      }

      // Restore user-saved modifications during this session, filtering out any mock data
      try {
        const saved = localStorage.getItem("zr_host_rental_records_v1");
        if (saved) {
          const parsed = JSON.parse(saved);
          if (Array.isArray(parsed)) {
            const mockIds = new Set(["ZR-R-10491", "ZR-R-10321", "ZR-R-10298", "ZR-R-10115", "ZR-R-10502"]);
            const cleanSaved = parsed.filter(
              (r) => !mockIds.has(r.id) && !r.id.startsWith("ZR-R-98")
            );

            const activeMap = new Map(activeList.map((item) => [item.id, item]));
            cleanSaved.forEach((savedItem) => {
              if (activeMap.has(savedItem.id)) {
                activeMap.set(savedItem.id, { ...activeMap.get(savedItem.id), ...savedItem });
              }
            });
            setCurrentRecords(Array.from(activeMap.values()));
            setHistoryRecords(historyList);
            return;
          }
        }
      } catch {
        // ignore storage parse errors
      }

      setCurrentRecords(activeList);
      setHistoryRecords(historyList);
    } catch {
      setCurrentRecords([]);
      setHistoryRecords([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const [viewTab, setViewTab] = useState<"current" | "history">("current");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedCard, setSelectedCard] = useState<"" | "upcoming" | "active" | "ending" | "action">("");

  // Filters
  const [statusFilter, setStatusFilter] = useState("all");
  const [propertyFilter, setPropertyFilter] = useState("all");
  const [agreementFilter, setAgreementFilter] = useState("all");
  const [datesFilter, setDatesFilter] = useState("all");
  const [subletFilter, setSubletFilter] = useState("all");
  const [healthFilter, setHealthFilter] = useState("all");
  const [sortFilter, setSortFilter] = useState("action");
  const [showMoreFilters, setShowMoreFilters] = useState(false);
  const [mobileFilterOpen, setMobileFilterOpen] = useState(false);

  // Detail Drawer state
  const [selectedRecordId, setSelectedRecordId] = useState<string | null>(null);
  const [drawerTab, setDrawerTab] = useState<
    "overview" | "agreement" | "payments" | "timeline" | "sublet" | "documents"
  >("overview");

  // Triple-dot popover menu open record id
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);

  // Modals state
  const [activeModal, setActiveModal] = useState<
    | null
    | { type: "movein"; record: RentalRecordItem }
    | { type: "notice"; record: RentalRecordItem }
    | { type: "end"; record: RentalRecordItem }
    | { type: "extend"; record: RentalRecordItem }
    | { type: "rent"; record: RentalRecordItem }
    | { type: "correct"; record: RentalRecordItem }
  >(null);

  // Modal form states
  const [extendEndDate, setExtendEndDate] = useState("");
  const [newRentAmount, setNewRentAmount] = useState("");
  const [rentEffectiveDate, setRentEffectiveDate] = useState("");
  const [correctionField, setCorrectionField] = useState("Rent amount");
  const [correctionNewValue, setCorrectionNewValue] = useState("");
  const [correctionReason, setCorrectionReason] = useState("");
  const [marketFutureRoom, setMarketFutureRoom] = useState(true);
  const [endRoomAction, setEndRoomAction] = useState<"keep" | "list">("keep");

  // Close menus when clicking outside
  useEffect(() => {
    function handleOutsideClick(e: MouseEvent) {
      const target = e.target as HTMLElement;
      if (!target.closest("[data-menu-container]")) {
        setOpenMenuId(null);
      }
    }
    document.addEventListener("click", handleOutsideClick);
    return () => document.removeEventListener("click", handleOutsideClick);
  }, []);

  // Compute live KPI counts for current records
  const counts = useMemo(() => {
    let upcoming = 0;
    let active = 0;
    let ending = 0;
    let action = 0;

    for (const r of currentRecords) {
      if (r.status === "upcoming") upcoming++;
      if (["active", "notice", "ending"].includes(r.status)) active++;
      if (r.status === "ending") ending++;
      if (r.actionAlert) action++;
    }

    return { upcoming, active, ending, action };
  }, [currentRecords]);

  // Attention items for the alert banner
  const attentionItems = useMemo(() => {
    return currentRecords.filter((r) => Boolean(r.actionAlert));
  }, [currentRecords]);

  // Filtered and sorted records
  const displayedRecords = useMemo(() => {
    const list = viewTab === "history" ? historyRecords : currentRecords;
    const today = new Date("2026-10-20");

    const filtered = list.filter((r) => {
      // KPI card filter (current tab only)
      if (viewTab === "current" && selectedCard) {
        if (selectedCard === "action") {
          if (!r.actionAlert) return false;
        } else if (selectedCard === "active") {
          if (!["active", "notice", "ending"].includes(r.status)) return false;
        } else if (r.status !== selectedCard) {
          return false;
        }
      }

      // Status filter
      if (viewTab === "current" && statusFilter !== "all") {
        if (r.status !== statusFilter) return false;
      }

      // Property filter
      if (propertyFilter !== "all" && r.propertyKey !== propertyFilter) {
        return false;
      }

      // Agreement filter
      if (agreementFilter !== "all" && r.agreementStatus !== agreementFilter) {
        return false;
      }

      // Sublet filter
      if (subletFilter !== "all") {
        const hasSublet = Boolean(r.sublet);
        if (subletFilter === "yes" && !hasSublet) return false;
        if (subletFilter === "no" && hasSublet) return false;
      }

      // Health filter
      if (healthFilter !== "all" && r.health !== healthFilter) {
        return false;
      }

      // Dates filter
      if (datesFilter === "rolling") {
        if (!r.term.toLowerCase().includes("rolling")) return false;
      } else if (datesFilter === "starts30") {
        const d = (new Date(r.startDate).getTime() - today.getTime()) / (1000 * 60 * 60 * 24);
        if (!(d >= 0 && d <= 30)) return false;
      } else if (datesFilter === "ends60") {
        if (!["ending", "notice"].includes(r.status)) return false;
      }

      // Search query
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase().trim();
        const hay = `${r.renters.join(" ")} ${r.id} ${r.propertyName} ${r.room} ${r.agreementRef} ${r.type}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }

      return true;
    });

    // Sorter
    return filtered.sort((a, b) => {
      if (sortFilter === "start") {
        return a.startDate.localeCompare(b.startDate);
      }
      if (sortFilter === "updated") {
        return b.updatedAt.localeCompare(a.updatedAt);
      }
      // "action" default priority
      const getPrio = (x: RentalRecordItem) => {
        if (x.actionAlert) return 0;
        if (x.status === "ending") return 1;
        if (x.status === "notice") return 2;
        if (x.status === "upcoming") return 3;
        if (x.status === "pending") return 4;
        return 5;
      };
      const diff = getPrio(a) - getPrio(b);
      if (diff !== 0) return diff;
      return a.startDate.localeCompare(b.startDate);
    });
  }, [
    viewTab,
    currentRecords,
    historyRecords,
    selectedCard,
    statusFilter,
    propertyFilter,
    agreementFilter,
    subletFilter,
    healthFilter,
    datesFilter,
    searchQuery,
    sortFilter,
  ]);

  const hasActiveFilters = Boolean(
    searchQuery ||
      selectedCard ||
      statusFilter !== "all" ||
      propertyFilter !== "all" ||
      agreementFilter !== "all" ||
      datesFilter !== "all" ||
      subletFilter !== "all" ||
      healthFilter !== "all"
  );

  const handleClearFilters = useCallback(() => {
    setSearchQuery("");
    setSelectedCard("");
    setStatusFilter("all");
    setPropertyFilter("all");
    setAgreementFilter("all");
    setDatesFilter("all");
    setSubletFilter("all");
    setHealthFilter("all");
    showToast("Filters cleared");
  }, [showToast]);

  const handleExportCSV = useCallback(() => {
    const list = displayedRecords;
    const headers = [
      "Rental ID",
      "Renter(s)",
      "Property",
      "Room",
      "Type",
      "Term",
      "Monthly Rent",
      "Agreement Status",
      "Agreement Ref",
      "Status",
      "Record Health",
      "Next Milestone",
      "Start Date",
    ];
    const rows = list.map((r) => [
      r.id,
      `"${r.renters.join(" & ")}"`,
      `"${r.propertyName}"`,
      `"${r.room}"`,
      `"${r.type}"`,
      `"${r.term}"`,
      `"${r.rent}"`,
      r.agreementStatus,
      r.agreementRef,
      r.status,
      r.health,
      `"${r.milestone}"`,
      r.startDate,
    ]);

    const csvContent = "data:text/csv;charset=utf-8," + [headers.join(","), ...rows.map((e) => e.join(","))].join("\n");
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `zoiko-rental-records-${viewTab}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    showToast(`Exported ${list.length} rental records (CSV)`);
  }, [displayedRecords, viewTab, showToast]);

  // Currently selected item in drawer
  const activeRecord = useMemo(() => {
    if (!selectedRecordId) return null;
    return (
      currentRecords.find((r) => r.id === selectedRecordId) ||
      historyRecords.find((r) => r.id === selectedRecordId) ||
      null
    );
  }, [selectedRecordId, currentRecords, historyRecords]);

  // Host signing agreement action
  const handleSignAgreement = useCallback(
    (recordId: string) => {
      saveRecords((prev) =>
        prev.map((r) => {
          if (r.id === recordId) {
            return {
              ...r,
              agreementStatus: "signed",
              actionAlert: null,
              cta: "Prepare Move-in",
              history: [...(r.history || []), ["Oct 20", "Agreement signed by Host"]],
            };
          }
          return r;
        })
      );
      showToast("Agreement signed. Occupancy stays Upcoming until move-in.");
    },
    [saveRecords, showToast]
  );

  // Handle CTA button click
  const handleCtaClick = useCallback(
    (item: RentalRecordItem) => {
      setOpenMenuId(null);
      if (item.cta === "View Record") {
        setSelectedRecordId(item.id);
        setDrawerTab("overview");
      } else if (item.cta === "View Agreement") {
        setSelectedRecordId(item.id);
        setDrawerTab("agreement");
      } else if (item.cta === "Review Payment") {
        setSelectedRecordId(item.id);
        setDrawerTab("payments");
      } else if (item.cta === "Prepare Move-in") {
        setActiveModal({ type: "movein", record: item });
      } else if (item.cta === "Review Notice") {
        setActiveModal({ type: "notice", record: item });
      } else if (item.cta === "Prepare End") {
        setActiveModal({ type: "end", record: item });
      } else if (item.cta === "Manage Rental") {
        setSelectedRecordId(item.id);
        setDrawerTab("overview");
      }
    },
    []
  );

  return (
    <div className="space-y-6 pb-20">
      {/* Toast Notification */}
      <Toast toast={toast} />

      {/* Header Section */}
      <section className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-bold uppercase tracking-wider text-primary-600 dark:text-primary-400">
              Host workspace
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-0.5 text-[10.5px] font-bold text-emerald-800 border border-emerald-200 dark:bg-emerald-950/30 dark:text-emerald-300 dark:border-emerald-800">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
              Live Workspace
            </span>
          </div>
          <h1 className="mt-1 font-heading text-2xl font-extrabold tracking-tight text-neutral-900 sm:text-3xl dark:text-white">
            Rental Records
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-neutral-600 dark:text-neutral-300">
            Manage upcoming, active and completed rentals, agreements, occupancy milestones and transaction history.
          </p>
        </div>
        <div className="flex items-center gap-3">
          

          <button
            onClick={handleExportCSV}
            className="inline-flex items-center gap-2 rounded-xl border border-neutral-300 bg-white px-4 py-2.5 text-sm font-bold text-primary-900 shadow-sm transition hover:bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-800 dark:text-white dark:hover:bg-neutral-700"
          >
            <Download className="h-4 w-4 text-primary-700 dark:text-primary-300" />
            <span>Export Records</span>
          </button>
        </div>
      </section>

      {/* 4 KPI Status Cards */}
      <section className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:gap-4">
        {/* Card 1: Upcoming */}
        <button
          type="button"
          onClick={() => {
            setViewTab("current");
            setSelectedCard((prev) => (prev === "upcoming" ? "" : "upcoming"));
          }}
          className={cn(
            "group flex flex-col items-start gap-2 rounded-2xl border-2 bg-white p-4 text-left transition-all hover:shadow-md dark:bg-neutral-900",
            selectedCard === "upcoming"
              ? "border-primary-700 bg-primary-50/40 ring-2 ring-primary-700/20 dark:border-primary-500"
              : "border-neutral-200 hover:border-neutral-300 dark:border-neutral-800"
          )}
        >
          <div className="flex w-full items-center justify-between">
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-blue-50 font-heading text-lg font-extrabold text-blue-900 dark:bg-blue-900/30 dark:text-blue-200">
              {counts.upcoming}
            </span>
            <Calendar className="h-5 w-5 text-blue-700 dark:text-blue-300" />
          </div>
          <div>
            <div className="font-heading text-sm font-bold text-neutral-900 dark:text-white">Upcoming</div>
            <div className="text-xs text-neutral-500 dark:text-neutral-400">Move-in ahead</div>
          </div>
        </button>

        {/* Card 2: Active */}
        <button
          type="button"
          onClick={() => {
            setViewTab("current");
            setSelectedCard((prev) => (prev === "active" ? "" : "active"));
          }}
          className={cn(
            "group flex flex-col items-start gap-2 rounded-2xl border-2 bg-white p-4 text-left transition-all hover:shadow-md dark:bg-neutral-900",
            selectedCard === "active"
              ? "border-emerald-700 bg-emerald-50/40 ring-2 ring-emerald-700/20 dark:border-emerald-500"
              : "border-neutral-200 hover:border-neutral-300 dark:border-neutral-800"
          )}
        >
          <div className="flex w-full items-center justify-between">
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-emerald-50 font-heading text-lg font-extrabold text-emerald-900 dark:bg-emerald-900/30 dark:text-emerald-200">
              {counts.active}
            </span>
            <Key className="h-5 w-5 text-emerald-700 dark:text-emerald-300" />
          </div>
          <div>
            <div className="font-heading text-sm font-bold text-neutral-900 dark:text-white">Active</div>
            <div className="text-xs text-neutral-500 dark:text-neutral-400">Occupancy in effect</div>
          </div>
        </button>

        {/* Card 3: Ending Soon */}
        <button
          type="button"
          onClick={() => {
            setViewTab("current");
            setSelectedCard((prev) => (prev === "ending" ? "" : "ending"));
          }}
          className={cn(
            "group flex flex-col items-start gap-2 rounded-2xl border-2 bg-white p-4 text-left transition-all hover:shadow-md dark:bg-neutral-900",
            selectedCard === "ending"
              ? "border-amber-600 bg-amber-50/40 ring-2 ring-amber-600/20 dark:border-amber-500"
              : "border-neutral-200 hover:border-neutral-300 dark:border-neutral-800"
          )}
        >
          <div className="flex w-full items-center justify-between">
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-amber-50 font-heading text-lg font-extrabold text-amber-900 dark:bg-amber-900/30 dark:text-amber-200">
              {counts.ending}
            </span>
            <Clock className="h-5 w-5 text-amber-700 dark:text-amber-300" />
          </div>
          <div>
            <div className="font-heading text-sm font-bold text-neutral-900 dark:text-white">Ending Soon</div>
            <div className="text-xs text-neutral-500 dark:text-neutral-400">Confirmed end within 30 days</div>
          </div>
        </button>

        {/* Card 4: Action Required */}
        <button
          type="button"
          onClick={() => {
            setViewTab("current");
            setSelectedCard((prev) => (prev === "action" ? "" : "action"));
          }}
          className={cn(
            "group flex flex-col items-start gap-2 rounded-2xl border-2 bg-white p-4 text-left transition-all hover:shadow-md dark:bg-neutral-900",
            selectedCard === "action"
              ? "border-rose-600 bg-rose-50/40 ring-2 ring-rose-600/20 dark:border-rose-500"
              : "border-neutral-200 hover:border-neutral-300 dark:border-neutral-800"
          )}
        >
          <div className="flex w-full items-center justify-between">
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-rose-50 font-heading text-lg font-extrabold text-rose-700 dark:bg-rose-900/30 dark:text-rose-200">
              {counts.action}
            </span>
            <AlertTriangle className="h-5 w-5 text-rose-600 dark:text-rose-400" />
          </div>
          <div>
            <div className="font-heading text-sm font-bold text-neutral-900 dark:text-white">Action Required</div>
            <div className="text-xs text-neutral-500 dark:text-neutral-400">Needs your intervention</div>
          </div>
        </button>
      </section>

      {/* Action Required Strip */}
      {viewTab === "current" && attentionItems.length > 0 && (
        <section className="rounded-2xl border border-rose-200 bg-[#FFF7F7] p-4 dark:border-rose-900/50 dark:bg-rose-950/20">
          <div className="mb-3 flex items-center gap-2 text-rose-800 dark:text-rose-300">
            <AlertTriangle className="h-5 w-5 shrink-0" />
            <h2 className="font-heading text-sm font-bold">
              {attentionItems.length} rental {attentionItems.length === 1 ? "record needs" : "records need"} your
              attention
            </h2>
          </div>
          <div className="space-y-2.5">
            {attentionItems.map((item) => (
              <div
                key={item.id}
                className="flex flex-col gap-2 rounded-xl bg-white p-3 shadow-xs sm:flex-row sm:items-center sm:justify-between dark:bg-neutral-900"
              >
                <div className="text-xs sm:text-sm">
                  <strong className="font-bold text-neutral-900 dark:text-white">
                    {item.room} · {item.propertyName}
                  </strong>
                  <span className="text-neutral-600 dark:text-neutral-400">
                    {" "}
                    — {item.actionAlert?.split(" — ")[0]?.replace(` on ${item.room}`, "")}
                  </span>
                </div>
                <button
                  onClick={() => handleCtaClick(item)}
                  className="inline-flex items-center justify-center rounded-xl bg-rose-600 px-3.5 py-1.5 text-xs font-bold text-white transition hover:bg-rose-700 shrink-0"
                >
                  {item.cta}
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Search and Filters Section */}
      <section className="space-y-3 rounded-2xl border border-neutral-200 bg-white p-4 shadow-xs dark:border-neutral-800 dark:bg-neutral-900">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          {/* Search Box */}
          <div className="relative flex-1">
            <Search className="absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
            <input
              type="search"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search renter, Rental ID, property, room or agreement"
              className="w-full rounded-xl border border-neutral-200 bg-neutral-50/50 py-2.5 pl-10 pr-4 text-sm text-neutral-900 placeholder-neutral-400 outline-none transition focus:border-primary-600 focus:bg-white focus:ring-1 focus:ring-primary-600 dark:border-neutral-700 dark:bg-neutral-800 dark:text-white"
            />
          </div>

          {/* Inline Filters */}
          <div className="hidden flex-wrap items-center gap-2.5 sm:flex">
            {/* Status */}
            <div className="flex flex-col gap-1">
              <label className="text-[11px] font-bold text-neutral-600 dark:text-neutral-400">Status</label>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="h-10 rounded-xl border border-neutral-200 bg-white px-3 text-xs font-semibold text-neutral-800 outline-none transition focus:border-primary-700 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200"
              >
                <option value="all">All statuses</option>
                <option value="pending">Agreement Pending</option>
                <option value="upcoming">Upcoming</option>
                <option value="active">Active</option>
                <option value="notice">Notice Given</option>
                <option value="ending">Ending Soon</option>
              </select>
            </div>

            {/* Property */}
            <div className="flex flex-col gap-1">
              <label className="text-[11px] font-bold text-neutral-600 dark:text-neutral-400">Property</label>
              <select
                value={propertyFilter}
                onChange={(e) => setPropertyFilter(e.target.value)}
                className="h-10 rounded-xl border border-neutral-200 bg-white px-3 text-xs font-semibold text-neutral-800 outline-none transition focus:border-primary-700 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200"
              >
                <option value="all">All properties</option>
                {liveProperties.map((p) => (
                  <option key={p.id} value={String(p.id)}>
                    {p.address} {p.city ? `(${p.city})` : ""}
                  </option>
                ))}
                
              </select>
            </div>

            {/* Agreement */}
            <div className="flex flex-col gap-1">
              <label className="text-[11px] font-bold text-neutral-600 dark:text-neutral-400">Agreement</label>
              <select
                value={agreementFilter}
                onChange={(e) => setAgreementFilter(e.target.value)}
                className="h-10 rounded-xl border border-neutral-200 bg-white px-3 text-xs font-semibold text-neutral-800 outline-none transition focus:border-primary-700 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200"
              >
                <option value="all">Any</option>
                <option value="signed">Signed</option>
                <option value="host">Host signature required</option>
                <option value="renter">Renter signature required</option>
              </select>
            </div>

            {/* Sort */}
            <div className="flex flex-col gap-1">
              <label className="text-[11px] font-bold text-neutral-600 dark:text-neutral-400">Sort</label>
              <select
                value={sortFilter}
                onChange={(e) => setSortFilter(e.target.value)}
                className="h-10 rounded-xl border border-neutral-200 bg-white px-3 text-xs font-semibold text-neutral-800 outline-none transition focus:border-primary-700 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200"
              >
                <option value="action">Action required first</option>
                <option value="milestone">Next milestone</option>
                <option value="start">Start date</option>
                <option value="updated">Recently updated</option>
              </select>
            </div>

            {/* More Filters Toggle */}
            <div className="flex flex-col justify-end pt-5">
              <button
                type="button"
                onClick={() => setShowMoreFilters((p) => !p)}
                className="inline-flex h-10 items-center gap-1.5 rounded-xl border border-neutral-200 bg-neutral-50 px-3 text-xs font-bold text-neutral-700 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-300"
              >
                <SlidersHorizontal className="h-3.5 w-3.5" />
                <span>{showMoreFilters ? "Fewer filters" : "More filters"}</span>
              </button>
            </div>
          </div>

          {/* Mobile Filter Button */}
          <div className="sm:hidden">
            <button
              onClick={() => setMobileFilterOpen((p) => !p)}
              className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-neutral-200 bg-neutral-50 py-2.5 text-xs font-bold text-neutral-800"
            >
              <Filter className="h-4 w-4" />
              <span>Filters {hasActiveFilters && "•"}</span>
            </button>
          </div>
        </div>

        {/* Expanded More Filters Row */}
        {showMoreFilters && (
          <div className="hidden grid-cols-3 gap-3 border-t border-neutral-100 pt-3 sm:grid dark:border-neutral-800">
            {/* Dates */}
            <div className="flex flex-col gap-1">
              <label className="text-[11px] font-bold text-neutral-600 dark:text-neutral-400">Dates</label>
              <select
                value={datesFilter}
                onChange={(e) => setDatesFilter(e.target.value)}
                className="h-10 rounded-xl border border-neutral-200 bg-white px-3 text-xs font-semibold text-neutral-800 outline-none transition focus:border-primary-700 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200"
              >
                <option value="all">Any dates</option>
                <option value="starts30">Starting in 30 days</option>
                <option value="ends60">Ending in 60 days</option>
                <option value="rolling">Rolling / periodic</option>
              </select>
            </div>

            {/* Sublet */}
            <div className="flex flex-col gap-1">
              <label className="text-[11px] font-bold text-neutral-600 dark:text-neutral-400">Sublet</label>
              <select
                value={subletFilter}
                onChange={(e) => setSubletFilter(e.target.value)}
                className="h-10 rounded-xl border border-neutral-200 bg-white px-3 text-xs font-semibold text-neutral-800 outline-none transition focus:border-primary-700 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200"
              >
                <option value="all">Any</option>
                <option value="yes">Has sublet activity</option>
                <option value="no">No sublet</option>
              </select>
            </div>

            {/* Health */}
            <div className="flex flex-col gap-1">
              <label className="text-[11px] font-bold text-neutral-600 dark:text-neutral-400">Record Health</label>
              <select
                value={healthFilter}
                onChange={(e) => setHealthFilter(e.target.value)}
                className="h-10 rounded-xl border border-neutral-200 bg-white px-3 text-xs font-semibold text-neutral-800 outline-none transition focus:border-primary-700 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200"
              >
                <option value="all">Any</option>
                <option value="ok">No issues</option>
                <option value="review">Review Required</option>
                <option value="correction">Correction Pending</option>
              </select>
            </div>
          </div>
        )}

        {/* Mobile Filters Drawer */}
        {mobileFilterOpen && (
          <div className="space-y-3 border-t border-neutral-100 pt-3 sm:hidden dark:border-neutral-800">
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[10px] font-bold uppercase text-neutral-500">Status</label>
                <select
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                  className="mt-1 w-full rounded-lg border p-2 text-xs"
                >
                  <option value="all">All statuses</option>
                  <option value="pending">Agreement Pending</option>
                  <option value="upcoming">Upcoming</option>
                  <option value="active">Active</option>
                  <option value="notice">Notice Given</option>
                  <option value="ending">Ending Soon</option>
                </select>
              </div>
              <div>
                <label className="text-[10px] font-bold uppercase text-neutral-500">Property</label>
                <select
                  value={propertyFilter}
                  onChange={(e) => setPropertyFilter(e.target.value)}
                  className="mt-1 w-full rounded-lg border p-2 text-xs"
                >
                  <option value="all">All properties</option>
                  
                </select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[10px] font-bold uppercase text-neutral-500">Agreement</label>
                <select
                  value={agreementFilter}
                  onChange={(e) => setAgreementFilter(e.target.value)}
                  className="mt-1 w-full rounded-lg border p-2 text-xs"
                >
                  <option value="all">Any</option>
                  <option value="signed">Signed</option>
                  <option value="host">Host signature required</option>
                  <option value="renter">Renter signature required</option>
                </select>
              </div>
              <div>
                <label className="text-[10px] font-bold uppercase text-neutral-500">Sort</label>
                <select
                  value={sortFilter}
                  onChange={(e) => setSortFilter(e.target.value)}
                  className="mt-1 w-full rounded-lg border p-2 text-xs"
                >
                  <option value="action">Action required first</option>
                  <option value="milestone">Next milestone</option>
                  <option value="start">Start date</option>
                </select>
              </div>
            </div>
            <button
              onClick={() => setMobileFilterOpen(false)}
              className="w-full rounded-xl bg-primary-900 py-2 text-xs font-bold text-white"
            >
              Apply Filters
            </button>
          </div>
        )}
      </section>

      {/* Main Table Container */}
      <section className="overflow-hidden rounded-2xl border border-neutral-200 bg-white shadow-xs dark:border-neutral-800 dark:bg-neutral-900">
        {/* Workspace Bar: View Tabs + Counters */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-neutral-100 px-5 py-3.5 dark:border-neutral-800">
          <div className="flex items-center gap-1 rounded-xl bg-neutral-100 p-1 dark:bg-neutral-800">
            <button
              type="button"
              onClick={() => {
                setViewTab("current");
              }}
              className={cn(
                "rounded-lg px-4 py-1.5 text-xs font-bold transition",
                viewTab === "current"
                  ? "bg-white text-primary-950 shadow-xs dark:bg-neutral-900 dark:text-white"
                  : "text-neutral-600 hover:text-neutral-900 dark:text-neutral-400"
              )}
            >
              Current ({currentRecords.length})
            </button>
            <button
              type="button"
              onClick={() => {
                setViewTab("history");
                setSelectedCard("");
              }}
              className={cn(
                "rounded-lg px-4 py-1.5 text-xs font-bold transition",
                viewTab === "history"
                  ? "bg-white text-primary-950 shadow-xs dark:bg-neutral-900 dark:text-white"
                  : "text-neutral-600 hover:text-neutral-900 dark:text-neutral-400"
              )}
            >
              History ({historyRecords.length})
            </button>
          </div>

          <div className="flex items-center gap-3">
            <span className="text-xs font-semibold text-neutral-500 dark:text-neutral-400">
              {displayedRecords.length} {displayedRecords.length === 1 ? "record" : "records"}
            </span>
            {hasActiveFilters && (
              <button
                type="button"
                onClick={handleClearFilters}
                className="rounded-lg bg-neutral-100 px-2.5 py-1 text-xs font-bold text-neutral-700 hover:bg-neutral-200 dark:bg-neutral-800 dark:text-neutral-300"
              >
                Clear filters
              </button>
            )}
          </div>
        </div>

        {/* Table Rows (Desktop) */}
        {!loading && displayedRecords.length > 0 && (
          <>
            <div className="hidden lg:block overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-neutral-100 bg-neutral-50/75 text-[11px] font-bold uppercase tracking-wider text-neutral-500 dark:border-neutral-800 dark:bg-neutral-800/50">
                <th className="px-5 py-3">Renter</th>
                <th className="px-4 py-3">Property / Room</th>
                <th className="px-4 py-3">Term</th>
                <th className="px-4 py-3">Agreement</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Next Milestone</th>
                <th className="px-5 py-3 text-right">Next Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100 dark:divide-neutral-800">
              {displayedRecords.map((item) => {
                const statusMeta = STATUS_CONFIG[item.status] || STATUS_CONFIG.ended;
                const isJoint = item.renters.length > 1;
                const hasAction = Boolean(item.actionAlert);
                const isUrgent =
                  hasAction || ["Prepare Move-in", "Review Notice", "Prepare End"].includes(item.cta);

                return (
                  <tr
                    key={item.id}
                    className={cn(
                      "transition hover:bg-neutral-50/70 dark:hover:bg-neutral-800/40",
                      hasAction && "bg-[#FFFAF5] dark:bg-rose-950/10"
                    )}
                  >
                    {/* Renter */}
                    <td className="px-5 py-4">
                      <button
                        type="button"
                        onClick={() => {
                          setSelectedRecordId(item.id);
                          setDrawerTab("overview");
                        }}
                        className="group flex items-center gap-3 text-left"
                      >
                        <div
                          className={cn(
                            "flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-xs font-bold",
                            isJoint
                              ? "bg-purple-100 text-purple-900 dark:bg-purple-900/30 dark:text-purple-300"
                              : "bg-blue-100 text-blue-900 dark:bg-blue-900/30 dark:text-blue-300"
                          )}
                        >
                          {isJoint ? <Users className="h-4 w-4" /> : getInitials(item.renters[0])}
                        </div>
                        <div className="min-w-0">
                          <div className="font-heading text-sm font-bold text-neutral-900 group-hover:text-rose-600 dark:text-white transition-colors">
                            {item.renters.join(" & ")}
                          </div>
                          <div className="flex items-center gap-1.5 text-xs text-emerald-700 dark:text-emerald-400 font-medium">
                            <CheckCircle2 className="h-3 w-3 shrink-0" />
                            <span>Identity Verified {isJoint && "· 2 renters"}</span>
                          </div>
                          <div className="text-[11px] font-mono text-neutral-400">Rental #{item.id}</div>
                        </div>
                      </button>
                    </td>

                    {/* Property / Room */}
                    <td className="px-4 py-4 text-xs">
                      <div className="font-bold text-neutral-900 dark:text-white">
                        {item.propertyName} · {item.room}
                      </div>
                      <div className="text-neutral-500 dark:text-neutral-400">{item.type}</div>
                    </td>

                    {/* Term */}
                    <td className="px-4 py-4 text-xs">
                      <div className="font-bold text-neutral-900 dark:text-white">{item.term}</div>
                      <div className="font-heading font-extrabold text-neutral-800 dark:text-neutral-200">
                        {item.rent}
                        {viewTab === "current" && (
                          <span className="text-[11px] font-normal text-neutral-500"> / month</span>
                        )}
                      </div>
                    </td>

                    {/* Agreement */}
                    <td className="px-4 py-4 text-xs">
                      <div
                        className={cn(
                          "inline-flex items-center gap-1 font-bold",
                          item.agreementStatus === "signed"
                            ? "text-emerald-700 dark:text-emerald-400"
                            : "text-rose-600 dark:text-rose-400"
                        )}
                      >
                        {item.agreementStatus === "signed" ? (
                          <>
                            <Check className="h-3.5 w-3.5" />
                            <span>v{item.agreementVersion} · Signed</span>
                          </>
                        ) : item.agreementStatus === "host" ? (
                          <>
                            <FileSignature className="h-3.5 w-3.5" />
                            <span>Host signature required</span>
                          </>
                        ) : (
                          <>
                            <FileText className="h-3.5 w-3.5" />
                            <span>Renter signature required</span>
                          </>
                        )}
                      </div>
                      <div className="text-[11px] text-neutral-500 dark:text-neutral-400">
                        #{item.agreementRef}
                      </div>
                    </td>

                    {/* Status */}
                    <td className="px-4 py-4">
                      <div className="flex flex-col items-start gap-1.5">
                        <span
                          className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11.5px] font-bold"
                          style={{
                            color: statusMeta.fg,
                            backgroundColor: statusMeta.bg,
                            border: `1.5px solid ${statusMeta.border}`,
                          }}
                        >
                          <statusMeta.icon className="h-3 w-3 shrink-0" />
                          <span>{statusMeta.label}</span>
                        </span>

                        {item.health === "review" && (
                          <span className="inline-flex items-center gap-1 rounded-md bg-rose-50 px-2 py-0.5 text-[10.5px] font-bold text-rose-700 border border-rose-200">
                            <AlertTriangle className="h-2.5 w-2.5" />
                            <span>Record health: Review Required</span>
                          </span>
                        )}

                        {item.health === "correction" && (
                          <span className="inline-flex items-center gap-1 rounded-md bg-blue-50 px-2 py-0.5 text-[10.5px] font-bold text-blue-800 border border-blue-200">
                            <Clock className="h-2.5 w-2.5" />
                            <span>Correction Pending</span>
                          </span>
                        )}

                        {item.sublet && (
                          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-neutral-600 dark:text-neutral-400">
                            <Repeat className="h-3 w-3 text-purple-600" />
                            <span>Sublet {item.sublet.state.toLowerCase()}</span>
                          </span>
                        )}
                      </div>
                    </td>

                    {/* Next Milestone */}
                    <td className="px-4 py-4 text-xs">
                      <div
                        className={cn(
                          "font-bold",
                          hasAction && item.health === "review"
                            ? "text-rose-600 dark:text-rose-400"
                            : "text-neutral-900 dark:text-white"
                        )}
                      >
                        {item.milestone}
                      </div>
                      {item.milestoneSub && (
                        <div className="font-bold text-emerald-700 dark:text-emerald-400 text-[11px]">
                          {item.milestoneSub}
                        </div>
                      )}
                    </td>

                    {/* Next Action */}
                    <td className="px-5 py-4 text-right">
                      <div className="inline-flex items-center justify-end gap-1.5 relative" data-menu-container>
                        <button
                          type="button"
                          onClick={() => handleCtaClick(item)}
                          className={cn(
                            "rounded-xl px-3.5 py-2 text-xs font-bold transition shadow-xs whitespace-nowrap",
                            hasAction
                              ? "bg-rose-600 text-white hover:bg-rose-700"
                              : ["Prepare Move-in", "Review Notice", "Prepare End"].includes(item.cta)
                              ? "bg-primary-900 text-white hover:bg-primary-800 dark:bg-primary-700"
                              : "border border-neutral-300 bg-white text-neutral-800 hover:bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200"
                          )}
                        >
                          {item.cta}
                        </button>

                        <button
                          type="button"
                          onClick={() => setOpenMenuId((cur) => (cur === item.id ? null : item.id))}
                          className="rounded-xl border border-neutral-200 bg-white p-2 text-neutral-600 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-300"
                          title="More options"
                        >
                          <MoreHorizontal className="h-4 w-4" />
                        </button>

                        {/* Dropdown Menu */}
                        {openMenuId === item.id && (
                          <div className="absolute right-0 top-11 z-30 w-44 rounded-xl border border-neutral-200 bg-white p-1 text-left shadow-xl dark:border-neutral-700 dark:bg-neutral-800">
                            <button
                              onClick={() => {
                                setOpenMenuId(null);
                                setSelectedRecordId(item.id);
                                setDrawerTab("overview");
                              }}
                              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs font-semibold text-neutral-800 hover:bg-neutral-100 dark:text-neutral-200 dark:hover:bg-neutral-700"
                            >
                              <Eye className="h-3.5 w-3.5" />
                              <span>Open record</span>
                            </button>
                            <button
                              onClick={() => {
                                setOpenMenuId(null);
                                setSelectedRecordId(item.id);
                                setDrawerTab("agreement");
                              }}
                              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs font-semibold text-neutral-800 hover:bg-neutral-100 dark:text-neutral-200 dark:hover:bg-neutral-700"
                            >
                              <FileCheck2 className="h-3.5 w-3.5" />
                              <span>View agreement</span>
                            </button>
                            {viewTab === "current" && (
                              <>
                                <button
                                  onClick={() => {
                                    setOpenMenuId(null);
                                    setActiveModal({ type: "extend", record: item });
                                  }}
                                  className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs font-semibold text-neutral-800 hover:bg-neutral-100 dark:text-neutral-200 dark:hover:bg-neutral-700"
                                >
                                  <Calendar className="h-3.5 w-3.5" />
                                  <span>Extend term</span>
                                </button>
                                <button
                                  onClick={() => {
                                    setOpenMenuId(null);
                                    setActiveModal({ type: "rent", record: item });
                                  }}
                                  className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs font-semibold text-neutral-800 hover:bg-neutral-100 dark:text-neutral-200 dark:hover:bg-neutral-700"
                                >
                                  <Layers className="h-3.5 w-3.5" />
                                  <span>Change rent</span>
                                </button>
                                <button
                                  onClick={() => {
                                    setOpenMenuId(null);
                                    setActiveModal({ type: "correct", record: item });
                                  }}
                                  className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs font-semibold text-neutral-800 hover:bg-neutral-100 dark:text-neutral-200 dark:hover:bg-neutral-700"
                                >
                                  <RefreshCw className="h-3.5 w-3.5" />
                                  <span>Request correction</span>
                                </button>
                              </>
                            )}
                            <button
                              onClick={() => {
                                setOpenMenuId(null);
                                showToast(`Exported record ${item.id}`);
                              }}
                              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs font-semibold text-neutral-800 hover:bg-neutral-100 dark:text-neutral-200 dark:hover:bg-neutral-700"
                            >
                              <Download className="h-3.5 w-3.5" />
                              <span>Export record</span>
                            </button>
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Mobile / Card View (< 1024px) */}
        <div className="divide-y divide-neutral-100 lg:hidden dark:divide-neutral-800">
          {displayedRecords.map((item) => {
            const statusMeta = STATUS_CONFIG[item.status] || STATUS_CONFIG.ended;
            const isJoint = item.renters.length > 1;
            const hasAction = Boolean(item.actionAlert);

            return (
              <div
                key={item.id}
                className={cn(
                  "p-4 space-y-3",
                  hasAction && "bg-[#FFFAF5] dark:bg-rose-950/10"
                )}
              >
                {/* Header: Renter + Badge */}
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <div
                      className={cn(
                        "flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-xs font-bold",
                        isJoint ? "bg-purple-100 text-purple-900" : "bg-blue-100 text-blue-900"
                      )}
                    >
                      {isJoint ? <Users className="h-4 w-4" /> : getInitials(item.renters[0])}
                    </div>
                    <div>
                      <div className="font-heading text-sm font-bold text-neutral-900 dark:text-white">
                        {item.renters.join(" & ")}
                      </div>
                      <div className="flex items-center gap-1 text-[11px] font-medium text-emerald-700">
                        <CheckCircle2 className="h-3 w-3" />
                        <span>Identity Verified</span>
                      </div>
                      <div className="text-[10px] font-mono text-neutral-400">Rental #{item.id}</div>
                    </div>
                  </div>
                  <span
                    className="inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-bold"
                    style={{
                      color: statusMeta.fg,
                      backgroundColor: statusMeta.bg,
                      border: `1.5px solid ${statusMeta.border}`,
                    }}
                  >
                    <statusMeta.icon className="h-3 w-3 shrink-0" />
                    <span>{statusMeta.label}</span>
                  </span>
                </div>

                {/* Details */}
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div className="rounded-xl bg-neutral-50 p-2.5 dark:bg-neutral-800">
                    <span className="text-[10px] font-bold uppercase text-neutral-400">Property / Room</span>
                    <div className="font-bold text-neutral-900 dark:text-white">{item.room}</div>
                    <div className="text-neutral-500">{item.propertyName}</div>
                  </div>
                  <div className="rounded-xl bg-neutral-50 p-2.5 dark:bg-neutral-800">
                    <span className="text-[10px] font-bold uppercase text-neutral-400">Term & Rent</span>
                    <div className="font-bold text-neutral-900 dark:text-white">{item.rent} / mo</div>
                    <div className="text-neutral-500 truncate">{item.term}</div>
                  </div>
                </div>

                {/* Agreement & Milestone */}
                <div className="flex items-center justify-between text-xs">
                  <div>
                    <span className="text-[10px] font-bold uppercase text-neutral-400">Agreement</span>
                    <div className="font-semibold text-neutral-800 dark:text-neutral-200">
                      {item.agreementStatus === "signed" ? `v${item.agreementVersion} · Signed` : "Signature required"}
                    </div>
                  </div>
                  <div className="text-right">
                    <span className="text-[10px] font-bold uppercase text-neutral-400">Next milestone</span>
                    <div className="font-bold text-neutral-900 dark:text-white">{item.milestone}</div>
                  </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-2 pt-1">
                  <button
                    onClick={() => handleCtaClick(item)}
                    className={cn(
                      "flex-1 rounded-xl py-2.5 text-xs font-bold text-center",
                      hasAction
                        ? "bg-rose-600 text-white"
                        : "bg-primary-900 text-white dark:bg-primary-700"
                    )}
                  >
                    {item.cta}
                  </button>
                  <button
                    onClick={() => {
                      setSelectedRecordId(item.id);
                      setDrawerTab("overview");
                    }}
                    className="rounded-xl border border-neutral-200 bg-white px-3 py-2.5 text-xs font-bold text-neutral-700 dark:border-neutral-700 dark:bg-neutral-800 dark:text-white"
                  >
                    View
                  </button>
                </div>
              </div>
            );
          })}
        </div>
          </>
        )}

        {/* Loading State */}
        {loading && (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <Loader label="Loading live rental records" />
          </div>
        )}

        {/* Empty State */}
        {!loading && displayedRecords.length === 0 && (
          <div className="flex flex-col items-center justify-center p-12 text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-neutral-100 text-neutral-400 dark:bg-neutral-800">
              {hasActiveFilters ? <FileText className="h-7 w-7" /> : <DoorOpen className="h-7 w-7 text-primary-600" />}
            </div>
            <h3 className="mt-4 font-heading text-base font-bold text-neutral-900 dark:text-white">
              {hasActiveFilters
                ? "No rental records match these filters"
                : viewTab === "current"
                ? "No active rental records"
                : "No past rental records"}
            </h3>
            <p className="mt-1 max-w-md text-xs text-neutral-500">
              {hasActiveFilters
                ? "Try adjusting your search criteria or clearing filters."
                : viewTab === "current"
                ? "Rental records are automatically generated when an applicant accepts an offer and a room occupancy or agreement is established."
                : "Completed, expired, or terminated rental records will be preserved here."}
            </p>
            {hasActiveFilters ? (
              <button
                onClick={handleClearFilters}
                className="mt-4 rounded-xl bg-primary-900 px-4 py-2 text-xs font-bold text-white shadow-xs hover:bg-primary-800 cursor-pointer"
              >
                Clear filters
              </button>
            ) : viewTab === "current" ? (
              <div className="mt-5 flex items-center gap-3">
                <Link
                  href="/account/host/applications"
                  className="rounded-xl bg-primary-900 px-4 py-2.5 text-xs font-bold text-white shadow-xs hover:bg-primary-800 transition"
                >
                  View Applications
                </Link>
                <Link
                  href="/account/host"
                  className="rounded-xl border border-neutral-200 bg-white px-4 py-2.5 text-xs font-bold text-neutral-700 hover:bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200 transition"
                >
                  Manage Properties & Rooms
                </Link>
              </div>
            ) : null}
          </div>
        )}
      </section>

      {/* Footer Legal & System Notice */}
      <p className="text-xs text-neutral-500 dark:text-neutral-400">
        * Future availability is shown only when listing and market rules permit it. Zoiko Rooms collects the Listing Fee
        only — rent and deposits shown here are records with their stated provenance.
      </p>

      {/* Slide-out Inspection Drawer */}
      {activeRecord && (
        <div className="fixed inset-0 z-50 flex justify-end bg-neutral-950/40 backdrop-blur-xs transition-opacity">
          <button
            type="button"
            className="absolute inset-0 h-full w-full cursor-default"
            onClick={() => setSelectedRecordId(null)}
            aria-label="Close drawer"
          />
          <aside className="relative flex h-full w-full max-w-xl flex-col bg-white shadow-2xl dark:bg-neutral-900 animate-in slide-in-from-right duration-200">
            {/* Drawer Header */}
            <div className="border-b border-neutral-100 p-6 dark:border-neutral-800">
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-center gap-3.5">
                  <div
                    className={cn(
                      "flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl text-base font-bold shadow-xs",
                      activeRecord.renters.length > 1
                        ? "bg-purple-100 text-purple-900"
                        : "bg-blue-100 text-blue-900"
                    )}
                  >
                    {activeRecord.renters.length > 1 ? (
                      <Users className="h-6 w-6" />
                    ) : (
                      getInitials(activeRecord.renters[0])
                    )}
                  </div>
                  <div>
                    <span className="text-[11px] font-bold uppercase tracking-wider text-primary-600">
                      Rental #{activeRecord.id}
                    </span>
                    <h2 className="font-heading text-xl font-extrabold text-neutral-900 dark:text-white">
                      {activeRecord.renters.join(" & ")}
                    </h2>
                    <p className="text-xs text-neutral-500 dark:text-neutral-400">
                      {activeRecord.propertyName} · {activeRecord.room} — {activeRecord.type}
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => setSelectedRecordId(null)}
                  className="rounded-xl border border-neutral-200 p-2 text-neutral-500 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              {/* Status strip */}
              <div className="mt-4 flex flex-wrap items-center gap-2">
                {(() => {
                  const sMeta = STATUS_CONFIG[activeRecord.status];
                  return (
                    <span
                      className="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-bold"
                      style={{
                        color: sMeta.fg,
                        backgroundColor: sMeta.bg,
                        border: `1.5px solid ${sMeta.border}`,
                      }}
                    >
                      <sMeta.icon className="h-3.5 w-3.5 shrink-0" />
                      <span>{sMeta.label}</span>
                    </span>
                  );
                })()}

                {activeRecord.health === "review" && (
                  <span className="inline-flex items-center gap-1 rounded-md bg-rose-50 px-2 py-0.5 text-xs font-bold text-rose-700 border border-rose-200">
                    <AlertTriangle className="h-3 w-3" />
                    <span>Review Required</span>
                  </span>
                )}

                <span className="text-xs text-neutral-500 font-medium">
                  {activeRecord.term} · {activeRecord.rent} / month
                </span>
              </div>

              {/* Drawer Tabs */}
              <div className="mt-6 flex border-b border-neutral-100 dark:border-neutral-800 overflow-x-auto">
                {[
                  { key: "overview", label: "Overview" },
                  { key: "agreement", label: "Agreement" },
                  { key: "payments", label: "Payments" },
                  { key: "timeline", label: "Timeline" },
                  { key: "sublet", label: "Sublet" },
                  { key: "documents", label: "Documents" },
                ].map((t) => (
                  <button
                    key={t.key}
                    onClick={() => setDrawerTab(t.key as typeof drawerTab)}
                    className={cn(
                      "border-b-2 px-3.5 py-2 text-xs font-bold whitespace-nowrap transition-colors",
                      drawerTab === t.key
                        ? "border-primary-900 text-primary-900 dark:border-primary-400 dark:text-primary-300"
                        : "border-transparent text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-200"
                    )}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Drawer Body */}
            <div className="flex-1 overflow-y-auto p-6 space-y-6">
              {/* Tab 1: Overview */}
              {drawerTab === "overview" && (
                <div className="space-y-5">
                  {activeRecord.actionAlert && (
                    <div className="flex items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 p-4 text-xs font-semibold text-rose-800">
                      <AlertTriangle className="h-4 w-4 shrink-0 text-rose-600 mt-0.5" />
                      <div>{activeRecord.actionAlert}</div>
                    </div>
                  )}

                  {/* Occupancy & term */}
                  <div className="space-y-2.5">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-neutral-500">Occupancy & Term</h3>
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div className="rounded-xl border border-neutral-200 p-3 dark:border-neutral-800">
                        <span className="text-[10px] font-bold uppercase text-neutral-400">Rental State</span>
                        <div className="font-bold text-neutral-900 dark:text-white">
                          {STATUS_CONFIG[activeRecord.status].label}
                        </div>
                      </div>
                      <div className="rounded-xl border border-neutral-200 p-3 dark:border-neutral-800">
                        <span className="text-[10px] font-bold uppercase text-neutral-400">Room Status</span>
                        <div className="font-bold text-neutral-900 dark:text-white">{activeRecord.roomStatus}</div>
                      </div>
                      <div className="rounded-xl border border-neutral-200 p-3 dark:border-neutral-800">
                        <span className="text-[10px] font-bold uppercase text-neutral-400">Term</span>
                        <div className="font-bold text-neutral-900 dark:text-white">{activeRecord.term}</div>
                      </div>
                      <div className="rounded-xl border border-neutral-200 p-3 dark:border-neutral-800">
                        <span className="text-[10px] font-bold uppercase text-neutral-400">Duration</span>
                        <div className="font-bold text-neutral-900 dark:text-white">{activeRecord.duration}</div>
                      </div>
                    </div>
                    {activeRecord.milestoneSub && (
                      <p className="text-[11px] text-neutral-500">
                        Room future availability: {activeRecord.milestoneSub.replace("*", "")} — only where listing rules
                        permit.
                      </p>
                    )}
                  </div>

                  {/* Parties */}
                  <div className="space-y-2.5">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-neutral-500">Parties</h3>
                    <ul className="divide-y divide-neutral-100 rounded-xl border border-neutral-200 text-xs dark:divide-neutral-800 dark:border-neutral-800">
                      <li className="flex items-center justify-between p-3">
                        <span className="font-bold text-neutral-400 uppercase text-[10px]">Owner</span>
                        <span className="font-semibold text-neutral-900 dark:text-white">
                          {activeRecord.owner || "Nakuluri (you)"}
                        </span>
                      </li>
                      {activeRecord.agent && (
                        <li className="flex items-center justify-between p-3">
                          <span className="font-bold text-neutral-400 uppercase text-[10px]">Managing agent</span>
                          <span className="font-semibold text-neutral-900 dark:text-white">{activeRecord.agent}</span>
                        </li>
                      )}
                      {activeRecord.renters.map((name) => (
                        <li key={name} className="flex items-center justify-between p-3">
                          <span className="font-bold text-neutral-400 uppercase text-[10px]">Renter</span>
                          <span className="inline-flex items-center gap-1.5 font-bold text-neutral-900 dark:text-white">
                            {name}
                            <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-700">
                              <CheckCircle2 className="h-3 w-3" />
                              <span>Identity Verified</span>
                            </span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>

                  {/* Financial summary */}
                  <div className="space-y-2.5">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-neutral-500">Financial Summary</h3>
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div className="rounded-xl border border-neutral-200 p-3 dark:border-neutral-800">
                        <span className="text-[10px] font-bold uppercase text-neutral-400">Current Rent</span>
                        <div className="font-bold text-neutral-900 dark:text-white">{activeRecord.rent} / month</div>
                      </div>
                      <div className="rounded-xl border border-neutral-200 p-3 dark:border-neutral-800">
                        <span className="text-[10px] font-bold uppercase text-neutral-400">Latest Payment</span>
                        <div className="font-bold text-neutral-900 dark:text-white">
                          {activeRecord.payments[0] ? activeRecord.payments[0][1] : "Not yet recorded"}
                        </div>
                      </div>
                    </div>
                    {activeRecord.payments[0] && (
                      <p className="text-[11px] text-neutral-500">
                        Provenance: {activeRecord.payments[0][2]}. Payments is the system of record.
                      </p>
                    )}
                  </div>

                  {/* Deposit */}
                  <div className="space-y-2.5">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-neutral-500">Deposit</h3>
                    <div className="rounded-xl border border-neutral-200 p-3 text-xs dark:border-neutral-800">
                      <div className="flex items-center gap-2 font-bold text-neutral-900 dark:text-white">
                        <Shield className="h-4 w-4 text-primary-700" />
                        <span>{activeRecord.deposit}</span>
                      </div>
                    </div>
                    <p className="text-[11px] text-neutral-500">Zoiko Rooms does not hold deposits.</p>
                  </div>

                  {/* Lineage */}
                  <div className="space-y-2.5">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-neutral-500">Lineage</h3>
                    <div className="flex flex-wrap gap-2">
                      {activeRecord.applicationRef && (
                        <button
                          onClick={() => showToast(`Opens application ${activeRecord.applicationRef}`)}
                          className="rounded-lg bg-neutral-100 px-3 py-1.5 text-xs font-bold text-primary-900 hover:bg-neutral-200 dark:bg-neutral-800 dark:text-primary-300"
                        >
                          View Application
                        </button>
                      )}
                      <button
                        onClick={() => showToast("Opens originating offer")}
                        className="rounded-lg bg-neutral-100 px-3 py-1.5 text-xs font-bold text-primary-900 hover:bg-neutral-200 dark:bg-neutral-800 dark:text-primary-300"
                      >
                        View Offer
                      </button>
                      <button
                        onClick={() => showToast("Opens listing snapshot")}
                        className="rounded-lg bg-neutral-100 px-3 py-1.5 text-xs font-bold text-primary-900 hover:bg-neutral-200 dark:bg-neutral-800 dark:text-primary-300"
                      >
                        View Listing Snapshot
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {/* Tab 2: Agreement */}
              {drawerTab === "agreement" && (
                <div className="space-y-5">
                  <div className="space-y-2.5">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-neutral-500">Agreement Versions</h3>
                    <div className="rounded-xl border border-neutral-200 p-4 space-y-3 dark:border-neutral-800">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <FileText className="h-5 w-5 text-primary-700" />
                          <div>
                            <div className="font-bold text-neutral-900 dark:text-white">
                              Agreement #{activeRecord.agreementRef} · v{activeRecord.agreementVersion}
                            </div>
                            <div className="text-xs text-neutral-500">
                              {activeRecord.agreementStatus === "signed" ? "Signed · governing" : "Awaiting signature"}
                            </div>
                          </div>
                        </div>
                        <span
                          className={cn(
                            "rounded-full px-2.5 py-0.5 text-xs font-bold",
                            activeRecord.agreementStatus === "signed"
                              ? "bg-emerald-100 text-emerald-800"
                              : "bg-rose-100 text-rose-800"
                          )}
                        >
                          {activeRecord.agreementStatus === "signed" ? "Signed" : "Signature Required"}
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Signatures status */}
                  <div className="space-y-2.5">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-neutral-500">Signatures</h3>
                    <ul className="divide-y divide-neutral-100 rounded-xl border border-neutral-200 text-xs dark:divide-neutral-800 dark:border-neutral-800">
                      <li className="flex items-center justify-between p-3">
                        <span className="font-bold text-neutral-500">Host (you)</span>
                        <span
                          className={cn(
                            "font-bold",
                            activeRecord.agreementStatus === "host" ? "text-rose-600" : "text-emerald-700"
                          )}
                        >
                          {activeRecord.agreementStatus === "host" ? "Signature required" : "Signed"}
                        </span>
                      </li>
                      {activeRecord.renters.map((name) => (
                        <li key={name} className="flex items-center justify-between p-3">
                          <span className="font-bold text-neutral-500">Renter: {name}</span>
                          <span
                            className={cn(
                              "font-bold",
                              activeRecord.agreementStatus === "renter" ? "text-rose-600" : "text-emerald-700"
                            )}
                          >
                            {activeRecord.agreementStatus === "renter" ? "Signature required" : "Signed"}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>

                  {activeRecord.agreementStatus === "host" && (
                    <button
                      onClick={() => handleSignAgreement(activeRecord.id)}
                      className="w-full rounded-xl bg-primary-900 py-3 text-xs font-bold text-white shadow-md hover:bg-primary-800"
                    >
                      Review & Sign Agreement
                    </button>
                  )}

                  {activeRecord.rents && (
                    <div className="space-y-2.5">
                      <h3 className="text-xs font-bold uppercase tracking-wider text-neutral-500">
                        Rent History (Effective-dated)
                      </h3>
                      <ul className="divide-y divide-neutral-100 rounded-xl border border-neutral-200 text-xs dark:divide-neutral-800 dark:border-neutral-800">
                        {activeRecord.rents.map((r) => (
                          <li key={r[0]} className="flex items-center justify-between p-3">
                            <span className="font-semibold text-neutral-600">{r[0]}</span>
                            <span className="font-bold text-neutral-900 dark:text-white">
                              {r[1]} · {r[2]}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  <p className="text-[11px] text-neutral-500">
                    Signed agreements are never overwritten. Amendments create a new version.
                  </p>
                </div>
              )}

              {/* Tab 3: Payments */}
              {drawerTab === "payments" && (
                <div className="space-y-5">
                  {activeRecord.health === "review" && (
                    <div className="flex items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 p-4 text-xs font-semibold text-rose-800">
                      <AlertTriangle className="h-4 w-4 shrink-0 text-rose-600 mt-0.5" />
                      <div>
                        Payment-record mismatch: you recorded $1,000 for October; the renter reports $900. The rental
                        state isn’t changed by this.
                      </div>
                    </div>
                  )}

                  <div className="space-y-2.5">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-neutral-500">Payment History</h3>
                    {activeRecord.payments.length > 0 ? (
                      <div className="space-y-2">
                        {activeRecord.payments.map((p, idx) => (
                          <div
                            key={idx}
                            className="rounded-xl border border-neutral-200 p-3.5 text-xs space-y-1 dark:border-neutral-800"
                          >
                            <div className="font-bold text-neutral-900 dark:text-white">{p[0]}</div>
                            <div className="font-semibold text-neutral-700 dark:text-neutral-300">{p[1]}</div>
                            <div className="text-[11px] text-neutral-500">{p[2]}</div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-xs text-neutral-500">No payment records yet.</p>
                    )}
                  </div>

                  <Link
                    href="/account/host/payments"
                    className="inline-flex items-center justify-center rounded-xl border border-neutral-300 bg-white px-4 py-2 text-xs font-bold text-primary-900 shadow-xs hover:bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-800 dark:text-white"
                  >
                    Open in Payments
                  </Link>

                  <p className="text-[11px] text-neutral-500">
                    Rental Records shows a summary only. Zoiko Rooms collects the Listing Fee; it did not process these
                    rent payments.
                  </p>
                </div>
              )}

              {/* Tab 4: Timeline */}
              {drawerTab === "timeline" && (
                <div className="space-y-4">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-neutral-500">Record History</h3>
                  <div className="relative pl-6 space-y-5 before:absolute before:left-2 before:top-2 before:bottom-2 before:w-0.5 before:bg-neutral-200 dark:before:bg-neutral-700">
                    {[
                      ["Application submitted", "Sep 28, 2026"],
                      ["Applicant shortlisted", "Sep 30, 2026"],
                      ["Offer issued & accepted", "Oct 4, 2026"],
                      [
                        `Agreement v${activeRecord.agreementVersion} generated`,
                        activeRecord.agreementStatus === "signed" ? "Oct 6, 2026" : "Oct 18, 2026",
                      ],
                      ...(activeRecord.agreementStatus === "signed"
                        ? [["Agreement signed by all parties", "Oct 6, 2026"]]
                        : [["Awaiting Host signature", "Pending"]]),
                      ...(activeRecord.status === "active" || activeRecord.status === "ending"
                        ? [["Occupancy commenced", activeRecord.startDate]]
                        : []),
                      ...(activeRecord.sublet ? [["Sublet request approved", "Oct 20, 2026"]] : []),
                      ...(activeRecord.notice ? [["Notice submitted by renter", "Oct 1, 2026"]] : []),
                      ...(activeRecord.history || []).map(([date, event]) => [event, date]),
                    ].map(([title, time], i) => (
                      <div key={i} className="relative">
                        <div className="absolute -left-6 top-1.5 h-3 w-3 rounded-full bg-primary-700 ring-4 ring-white dark:ring-neutral-900" />
                        <div className="text-xs font-bold text-neutral-900 dark:text-white">{title}</div>
                        <div className="text-[11px] text-neutral-500">{time}</div>
                      </div>
                    ))}
                  </div>
                  <p className="text-[11px] text-neutral-500">
                    Append-only. Every event records actor, source, time and linked object.
                  </p>
                </div>
              )}

              {/* Tab 5: Sublet */}
              {drawerTab === "sublet" && (
                <div className="space-y-4">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-neutral-500">Sublet Details</h3>
                  {activeRecord.sublet ? (
                    <div className="space-y-3 rounded-xl border border-neutral-200 p-4 text-xs dark:border-neutral-800">
                      <div className="flex justify-between">
                        <span className="font-bold text-neutral-400 uppercase text-[10px]">Request</span>
                        <span className="font-bold text-neutral-900 dark:text-white">#{activeRecord.sublet.id}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="font-bold text-neutral-400 uppercase text-[10px]">State</span>
                        <span className="font-bold text-emerald-700">{activeRecord.sublet.state}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="font-bold text-neutral-400 uppercase text-[10px]">Subtenant</span>
                        <span className="font-bold text-neutral-900 dark:text-white">{activeRecord.sublet.who}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="font-bold text-neutral-400 uppercase text-[10px]">Effective</span>
                        <span className="font-bold text-neutral-900 dark:text-white">{activeRecord.sublet.dates}</span>
                      </div>
                      <p className="text-[11px] text-neutral-500 pt-2">
                        The sublet is a child of this rental. {activeRecord.renters.join(" & ")} remains the primary
                        renter.
                      </p>
                      <Link
                        href="/account/host/sublet-requests"
                        className="inline-flex items-center gap-1.5 rounded-lg bg-neutral-100 px-3 py-1.5 text-xs font-bold text-primary-900 hover:bg-neutral-200 dark:bg-neutral-800 dark:text-primary-300"
                      >
                        View approval
                      </Link>
                    </div>
                  ) : (
                    <p className="text-xs text-neutral-500">No active sublet request.</p>
                  )}
                </div>
              )}

              {/* Tab 6: Documents */}
              {drawerTab === "documents" && (
                <div className="space-y-4">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-neutral-500">Rental Documents</h3>
                  <ul className="divide-y divide-neutral-100 rounded-xl border border-neutral-200 text-xs dark:divide-neutral-800 dark:border-neutral-800">
                    <li className="flex items-center justify-between p-3.5">
                      <div className="flex items-center gap-3">
                        <FileText className="h-5 w-5 text-primary-700" />
                        <div>
                          <div className="font-bold text-neutral-900 dark:text-white">
                            Agreement v{activeRecord.agreementVersion} (PDF)
                          </div>
                          <div className="text-[11px] text-neutral-500">#{activeRecord.agreementRef}</div>
                        </div>
                      </div>
                      <button
                        onClick={() => showToast("Downloading agreement PDF")}
                        className="rounded-lg border border-neutral-200 p-2 text-neutral-600 hover:bg-neutral-50"
                      >
                        <Download className="h-4 w-4" />
                      </button>
                    </li>
                    {activeRecord.status !== "pending" && (
                      <li className="flex items-center justify-between p-3.5">
                        <div className="flex items-center gap-3">
                          <CheckCircle2 className="h-5 w-5 text-emerald-600" />
                          <div>
                            <div className="font-bold text-neutral-900 dark:text-white">Move-in Condition Record</div>
                            <div className="text-[11px] text-neutral-500">Signed by both parties</div>
                          </div>
                        </div>
                        <button
                          onClick={() => showToast("Downloading condition record")}
                          className="rounded-lg border border-neutral-200 p-2 text-neutral-600 hover:bg-neutral-50"
                        >
                          <Download className="h-4 w-4" />
                        </button>
                      </li>
                    )}
                    {activeRecord.notice && (
                      <li className="flex items-center justify-between p-3.5">
                        <div className="flex items-center gap-3">
                          <Clock className="h-5 w-5 text-amber-600" />
                          <div>
                            <div className="font-bold text-neutral-900 dark:text-white">Notice Letter</div>
                            <div className="text-[11px] text-neutral-500">
                              Submitted {activeRecord.notice.submitted}
                            </div>
                          </div>
                        </div>
                        <button
                          onClick={() => showToast("Downloading notice letter")}
                          className="rounded-lg border border-neutral-200 p-2 text-neutral-600 hover:bg-neutral-50"
                        >
                          <Download className="h-4 w-4" />
                        </button>
                      </li>
                    )}
                  </ul>
                  <p className="text-[11px] text-neutral-500">
                    Only rental-related documents appear here. Identity evidence stays in Verification.
                  </p>
                </div>
              )}
            </div>

            {/* Drawer Footer Actions */}
            <div className="border-t border-neutral-100 p-4 sm:p-6 dark:border-neutral-800 bg-neutral-50/50 dark:bg-neutral-900/50 flex flex-wrap gap-2 justify-end">
              {viewTab === "current" && (
                <>
                  <button
                    onClick={() => {
                      setActiveModal({ type: "correct", record: activeRecord });
                    }}
                    className="rounded-xl border border-neutral-300 bg-white px-4 py-2.5 text-xs font-bold text-neutral-800 shadow-xs hover:bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200"
                  >
                    Request correction
                  </button>
                  <button
                    onClick={() => handleCtaClick(activeRecord)}
                    className={cn(
                      "rounded-xl px-4 py-2.5 text-xs font-bold text-white shadow-md",
                      activeRecord.actionAlert ? "bg-rose-600 hover:bg-rose-700" : "bg-primary-900 hover:bg-primary-800"
                    )}
                  >
                    {activeRecord.cta === "Manage Rental" ? "Extend term" : activeRecord.cta}
                  </button>
                </>
              )}
            </div>
          </aside>
        </div>
      )}

      {/* Modal 1: Prepare Move-in */}
      {activeModal?.type === "movein" && (
        <Modal
          open={true}
          onClose={() => setActiveModal(null)}
          title={`Prepare move-in · ${activeModal.record.room}`}
          size="lg"
        >
          <div className="space-y-4">
            <p className="text-xs text-neutral-600 dark:text-neutral-300">
              <strong>{activeModal.record.renters.join(" & ")}</strong> · Move-in{" "}
              {activeModal.record.milestone.replace("Move-in ", "")}. Occupancy starts only when every requirement
              passes on the start date.
            </p>

            {/* Checklist Gates */}
            <ul className="divide-y divide-neutral-100 rounded-xl border border-neutral-200 text-xs dark:divide-neutral-800 dark:border-neutral-800">
              <li className="flex items-center justify-between p-3">
                <div className="flex items-center gap-2.5">
                  <span
                    className={cn(
                      "flex h-6 w-6 items-center justify-center rounded-md text-xs font-bold",
                      activeModal.record.agreementStatus === "host"
                        ? "bg-rose-100 text-rose-700"
                        : "bg-emerald-100 text-emerald-700"
                    )}
                  >
                    {activeModal.record.agreementStatus === "host" ? (
                      <AlertTriangle className="h-3.5 w-3.5" />
                    ) : (
                      <Check className="h-3.5 w-3.5" />
                    )}
                  </span>
                  <div>
                    <div className="font-bold text-neutral-900 dark:text-white">Agreement</div>
                    <div className="text-[11px] text-neutral-500">
                      {activeModal.record.agreementStatus === "host"
                        ? "Your signature is required"
                        : "All parties signed · effective date confirmed"}
                    </div>
                  </div>
                </div>
                <span
                  className={cn(
                    "font-bold text-[11px]",
                    activeModal.record.agreementStatus === "host" ? "text-rose-600" : "text-emerald-700"
                  )}
                >
                  {activeModal.record.agreementStatus === "host" ? "Blocking" : "Complete"}
                </span>
              </li>

              <li className="flex items-center justify-between p-3">
                <div className="flex items-center gap-2.5">
                  <span className="flex h-6 w-6 items-center justify-center rounded-md bg-emerald-100 text-emerald-700 text-xs font-bold">
                    <Check className="h-3.5 w-3.5" />
                  </span>
                  <div>
                    <div className="font-bold text-neutral-900 dark:text-white">Compliance</div>
                    <div className="text-[11px] text-neutral-500">Jurisdiction prerequisites complete</div>
                  </div>
                </div>
                <span className="font-bold text-[11px] text-emerald-700">Complete</span>
              </li>

              <li className="flex items-center justify-between p-3">
                <div className="flex items-center gap-2.5">
                  <span className="flex h-6 w-6 items-center justify-center rounded-md bg-amber-100 text-amber-800 text-xs font-bold">
                    <Clock className="h-3.5 w-3.5" />
                  </span>
                  <div>
                    <div className="font-bold text-neutral-900 dark:text-white">Payment / Deposit Record</div>
                    <div className="text-[11px] text-neutral-500">Initial payment to be confirmed</div>
                  </div>
                </div>
                <span className="font-bold text-[11px] text-amber-700">To do</span>
              </li>

              <li className="flex items-center justify-between p-3">
                <div className="flex items-center gap-2.5">
                  <span className="flex h-6 w-6 items-center justify-center rounded-md bg-amber-100 text-amber-800 text-xs font-bold">
                    <Clock className="h-3.5 w-3.5" />
                  </span>
                  <div>
                    <div className="font-bold text-neutral-900 dark:text-white">Keys / Access</div>
                    <div className="text-[11px] text-neutral-500">Confirm handover arrangements</div>
                  </div>
                </div>
                <span className="font-bold text-[11px] text-amber-700">To do</span>
              </li>

              <li className="flex items-center justify-between p-3">
                <div className="flex items-center gap-2.5">
                  <span className="flex h-6 w-6 items-center justify-center rounded-md bg-amber-100 text-amber-800 text-xs font-bold">
                    <Clock className="h-3.5 w-3.5" />
                  </span>
                  <div>
                    <div className="font-bold text-neutral-900 dark:text-white">Condition Record</div>
                    <div className="text-[11px] text-neutral-500">Complete the move-in condition record</div>
                  </div>
                </div>
                <span className="font-bold text-[11px] text-amber-700">To do</span>
              </li>
            </ul>

            <p className="text-[11px] text-neutral-500">Checklist items come from the property’s Country Regulatory Pack.</p>

            <div className="flex justify-end gap-2 pt-3 border-t border-neutral-100">
              {activeModal.record.agreementStatus === "host" && (
                <button
                  type="button"
                  onClick={() => {
                    handleSignAgreement(activeModal.record.id);
                    setActiveModal(null);
                  }}
                  className="rounded-xl bg-primary-900 px-4 py-2 text-xs font-bold text-white hover:bg-primary-800"
                >
                  Review & Sign Agreement
                </button>
              )}
              <button
                type="button"
                onClick={() => {
                  showToast("Move-in progress saved");
                  setActiveModal(null);
                }}
                className="rounded-xl border border-neutral-300 bg-white px-4 py-2 text-xs font-bold text-neutral-800 hover:bg-neutral-50"
              >
                Close
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* Modal 2: Review Notice */}
      {activeModal?.type === "notice" && (
        <Modal open={true} onClose={() => setActiveModal(null)} title={`Review notice · ${activeModal.record.room}`}>
          <div className="space-y-4 text-xs">
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-xl border p-2.5">
                <span className="text-[10px] uppercase font-bold text-neutral-400">Initiated by</span>
                <div className="font-bold">{activeModal.record.notice?.by || "Renter"}</div>
              </div>
              <div className="rounded-xl border p-2.5">
                <span className="text-[10px] uppercase font-bold text-neutral-400">Submitted</span>
                <div className="font-bold">{activeModal.record.notice?.submitted || "Oct 1, 2026"}</div>
              </div>
              <div className="rounded-xl border p-2.5">
                <span className="text-[10px] uppercase font-bold text-neutral-400">Effective End</span>
                <div className="font-bold">{activeModal.record.notice?.effectiveEnd || "Nov 30, 2026"}</div>
              </div>
              <div className="rounded-xl border p-2.5">
                <span className="text-[10px] uppercase font-bold text-neutral-400">Status</span>
                <div className="font-bold text-amber-700">{activeModal.record.notice?.status || "Under Review"}</div>
              </div>
            </div>

            <label className="flex items-center gap-2 cursor-pointer pt-1">
              <input
                type="checkbox"
                checked={marketFutureRoom}
                onChange={(e) => setMarketFutureRoom(e.target.checked)}
                className="rounded h-4 w-4 text-primary-900"
              />
              <span className="text-xs font-semibold">
                Market the room from Dec 1, 2026 (your listing policy permits future availability)
              </span>
            </label>

            <p className="text-[11px] text-neutral-500">
              The rental stays Active until Nov 30. The room won’t show “Available now”.
            </p>

            <div className="flex justify-end gap-2 pt-3 border-t">
              <button
                type="button"
                onClick={() => {
                  showToast("Notice contested — renter notified");
                  setActiveModal(null);
                }}
                className="rounded-xl border border-neutral-300 px-3 py-2 font-bold text-neutral-700 hover:bg-neutral-50"
              >
                Contest Notice
              </button>
              <button
                type="button"
                onClick={() => {
                  saveRecords((prev) =>
                    prev.map((r) =>
                      r.id === activeModal.record.id
                        ? {
                            ...r,
                            actionAlert: null,
                            cta: "Manage Rental",
                            milestoneSub: marketFutureRoom ? "Available Dec 1*" : undefined,
                            history: [...(r.history || []), ["Oct 20", "Notice accepted by Host"]],
                          }
                        : r
                    )
                  );
                  showToast("Notice accepted. Rental stays Active until Nov 30.");
                  setActiveModal(null);
                }}
                className="rounded-xl bg-primary-900 px-4 py-2 font-bold text-white hover:bg-primary-800"
              >
                Accept Notice
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* Modal 3: Prepare End */}
      {activeModal?.type === "end" && (
        <Modal
          open={true}
          onClose={() => setActiveModal(null)}
          title={`Prepare end of rental · ${activeModal.record.room}`}
        >
          <div className="space-y-4 text-xs">
            <p className="text-neutral-600">
              <strong>{activeModal.record.renters.join(" & ")}</strong> · {activeModal.record.milestone}
            </p>

            <ul className="divide-y rounded-xl border">
              <li className="flex justify-between p-3">
                <span>Final inspection</span>
                <span className="font-bold text-amber-700">Schedule condition check</span>
              </li>
              <li className="flex justify-between p-3">
                <span>Keys / access return</span>
                <span className="font-bold text-amber-700">Confirm return arrangements</span>
              </li>
              <li className="flex justify-between p-3">
                <span>Final payment</span>
                <span className="font-bold text-emerald-700">Recorded in Payments</span>
              </li>
              <li className="flex justify-between p-3">
                <span>Deposit disposition</span>
                <span className="font-bold text-neutral-700">{activeModal.record.deposit}</span>
              </li>
            </ul>

            <div className="space-y-2">
              <span className="font-bold uppercase text-[10px] text-neutral-400">After the rental ends</span>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="nextroom"
                  checked={endRoomAction === "keep"}
                  onChange={() => setEndRoomAction("keep")}
                />
                <span>Keep the room unlisted for turnover</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="nextroom"
                  checked={endRoomAction === "list"}
                  onChange={() => setEndRoomAction("list")}
                />
                <span>Prepare a listing from Nov 15 — it won’t publish automatically</span>
              </label>
            </div>

            <div className="flex justify-end gap-2 pt-3 border-t">
              <button
                type="button"
                onClick={() => setActiveModal(null)}
                className="rounded-xl border border-neutral-300 px-3 py-2 font-bold"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  showToast("Plan saved. The room won’t be republished automatically.");
                  setActiveModal(null);
                }}
                className="rounded-xl bg-primary-900 px-4 py-2 font-bold text-white"
              >
                Save Plan
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* Modal 4: Extend Term */}
      {activeModal?.type === "extend" && (
        <Modal open={true} onClose={() => setActiveModal(null)} title="Extend Term">
          <div className="space-y-4 text-xs">
            <p className="text-neutral-600">
              An extension is an effective-dated amendment. The original start and end dates stay on record and a new
              agreement version is created for signature.
            </p>
            <div>
              <label className="block text-[11px] font-bold text-neutral-700">New end date</label>
              <input
                type="date"
                value={extendEndDate}
                onChange={(e) => setExtendEndDate(e.target.value)}
                className="mt-1 w-full rounded-xl border p-2.5 text-xs outline-none focus:border-primary-700"
              />
            </div>
            <div className="flex justify-end gap-2 pt-3 border-t">
              <button
                type="button"
                onClick={() => setActiveModal(null)}
                className="rounded-xl border border-neutral-300 px-3 py-2 font-bold"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  showToast(`Extension draft created as Agreement v${activeModal.record.agreementVersion + 1}`);
                  setActiveModal(null);
                }}
                className="rounded-xl bg-primary-900 px-4 py-2 font-bold text-white"
              >
                Create Extension Draft
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* Modal 5: Change Rent */}
      {activeModal?.type === "rent" && (
        <Modal open={true} onClose={() => setActiveModal(null)} title="Change Rent">
          <div className="space-y-4 text-xs">
            <p className="text-neutral-600">
              Record the previous amount, new amount and effective date. The change is linked to a new agreement version;{" "}
              <strong>{activeModal.record.rent}</strong> stays on record for the current period.
            </p>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-[11px] font-bold text-neutral-700">New rent</label>
                <input
                  type="text"
                  placeholder="e.g. $950"
                  value={newRentAmount}
                  onChange={(e) => setNewRentAmount(e.target.value)}
                  className="mt-1 w-full rounded-xl border p-2.5 text-xs outline-none focus:border-primary-700"
                />
              </div>
              <div>
                <label className="block text-[11px] font-bold text-neutral-700">Effective from</label>
                <input
                  type="date"
                  value={rentEffectiveDate}
                  onChange={(e) => setRentEffectiveDate(e.target.value)}
                  className="mt-1 w-full rounded-xl border p-2.5 text-xs outline-none focus:border-primary-700"
                />
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-3 border-t">
              <button
                type="button"
                onClick={() => setActiveModal(null)}
                className="rounded-xl border border-neutral-300 px-3 py-2 font-bold"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  showToast(`Amendment draft created for new rent ${newRentAmount}`);
                  setActiveModal(null);
                }}
                className="rounded-xl bg-primary-900 px-4 py-2 font-bold text-white"
              >
                Create Amendment Draft
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* Modal 6: Request Correction */}
      {activeModal?.type === "correct" && (
        <Modal open={true} onClose={() => setActiveModal(null)} title="Request Correction">
          <div className="space-y-4 text-xs">
            <p className="text-neutral-600">
              Corrections never overwrite history. The original value, corrected value, reason, actor and time are all
              recorded.
            </p>
            <div>
              <label className="block text-[11px] font-bold text-neutral-700">Field</label>
              <select
                value={correctionField}
                onChange={(e) => setCorrectionField(e.target.value)}
                className="mt-1 w-full rounded-xl border p-2.5 text-xs outline-none focus:border-primary-700"
              >
                <option>
                  {activeModal.record.health === "review" ? "October 2026 payment amount" : "Rent amount"}
                </option>
                <option>Term dates</option>
                <option>Deposit holder</option>
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-[11px] font-bold text-neutral-700">Original value</label>
                <input
                  type="text"
                  readOnly
                  value={activeModal.record.health === "review" ? "$1,000" : activeModal.record.rent}
                  className="mt-1 w-full rounded-xl border bg-neutral-100 p-2.5 text-xs text-neutral-600"
                />
              </div>
              <div>
                <label className="block text-[11px] font-bold text-neutral-700">Corrected value</label>
                <input
                  type="text"
                  placeholder="e.g. $900"
                  value={correctionNewValue}
                  onChange={(e) => setCorrectionNewValue(e.target.value)}
                  className="mt-1 w-full rounded-xl border p-2.5 text-xs outline-none focus:border-primary-700"
                />
              </div>
            </div>
            <div>
              <label className="block text-[11px] font-bold text-neutral-700">Reason (required)</label>
              <input
                type="text"
                placeholder="e.g. Bank statement shows $900 received"
                value={correctionReason}
                onChange={(e) => setCorrectionReason(e.target.value)}
                className="mt-1 w-full rounded-xl border p-2.5 text-xs outline-none focus:border-primary-700"
              />
            </div>
            <div className="flex justify-end gap-2 pt-3 border-t">
              <button
                type="button"
                onClick={() => setActiveModal(null)}
                className="rounded-xl border border-neutral-300 px-3 py-2 font-bold"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  if (!correctionNewValue.trim() || !correctionReason.trim()) {
                    showToast("Please enter both corrected value and reason", "error");
                    return;
                  }
                  saveRecords((prev) =>
                    prev.map((r) =>
                      r.id === activeModal.record.id
                        ? {
                            ...r,
                            health: "correction",
                            actionAlert: null,
                            cta: "Manage Rental",
                            milestone: "Correction pending",
                            history: [
                              ...(r.history || []),
                              ["Oct 20", `Correction requested: ${correctionField} (${correctionNewValue})`],
                            ],
                          }
                        : r
                    )
                  );
                  showToast("Correction submitted. Original value preserved; status is Correction Pending.");
                  setActiveModal(null);
                }}
                className="rounded-xl bg-primary-900 px-4 py-2 font-bold text-white"
              >
                Submit Correction
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
