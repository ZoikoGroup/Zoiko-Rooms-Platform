"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Building2,
  Calendar,
  Check,
  CheckCircle2,
  Clock,
  Eye,
  FileSignature,
  FileText,
  Inbox,
  Info,
  Lock,
  MessageSquare,
  MoreHorizontal,
  Pause,
  RefreshCw,
  RotateCcw,
  Search,
  Send,
  ShieldCheck,
  Star,
  User,
  Users,
  X,
  XCircle,
} from "lucide-react";
import { ApiError } from "@/lib/api-client";
import { Application, HostedListing, Property, Offer } from "@/lib/types";
import { formatDate } from "@/lib/utils";
import {
  decideHostedApplication,
  errorMessage,
  listHostedApplications,
  listHostedListings,
  listHostedProperties,
} from "@/lib/user-api";
import { Toast, useToast } from "@/components/user/ui";
import { HostOfferAgreementPanel } from "@/components/user/HostOfferAgreementPanel";
import { Modal } from "@/components/ui/Modal";
import { RoundedFilterDropdown } from "@/components/ui/RoundedFilterDropdown";
import { Loader } from "@/components/ui/Loader";

export type ApplicationStatusKey =
  | "new"
  | "review"
  | "short"
  | "selected"
  | "declined"
  | "withdrawn"
  | "expired"
  | "superseded";

export interface ApplicationRecord {
  id: number;
  idCode: string;
  guestName: string;
  guestEmail: string;
  listingId: string;
  listingName: string;
  roomType: string;
  rentText: string;
  termText: string;
  roomStatus: "available" | "offer" | "occupied";
  propertyName: string;
  propertyPlace: string;
  status: ApplicationStatusKey;
  desiredMoveIn: string | null;
  durationText: string;
  occupantsText: string;
  timeAgoText: string;
  submittedDateText: string;
  unreadCount: number;
  messages: string[];
  history: Array<[string, string]>;
  idVerified: boolean;
  attention?: string | null;
  cta?: string | null;
  event?: string | null;
  offer?: Offer | null;
  rawApp: Application;
}

const CLOSED_STATUSES: ApplicationStatusKey[] = [
  "declined",
  "withdrawn",
  "expired",
  "superseded",
];

function buildApplicationRecord(
  app: Application,
  listingMap: Map<string, HostedListing>,
  propertyMap: Map<number, Property>
): ApplicationRecord {
  const listing = listingMap.get(app.listingId);
  const property =
    listing && listing.ownerId != null
      ? propertyMap.get(listing.ownerId)
      : undefined;
  const latestDecision = app.decisions[app.decisions.length - 1];

  let status: ApplicationStatusKey = "new";
  if (app.status === "WITHDRAWN") {
    status = "withdrawn";
  } else if (latestDecision?.decision === "REJECTED") {
    status = "declined";
  } else if (app.offer) {
    status = "selected";
  } else if (latestDecision?.decision === "APPROVED") {
    status = "short";
  } else if (app.decisions.length > 0) {
    status = "review";
  }

  const roomName = listing?.name || app.listingName || `Listing #${app.listingId}`;
  const roomType = listing?.roomType || "Private Room";
  const propertyName = property?.address.split(",")[0] || listing?.city || "Property";
  const propertyPlace = property
    ? `${property.address}, ${property.city}`
    : listing?.location || "India";

  const rentText = listing?.defaultMonthlyRent
    ? `₹${listing.defaultMonthlyRent.toLocaleString()}`
    : listing?.pricePerNight
    ? `₹${(listing.pricePerNight * 30).toLocaleString()}`
    : "₹24,000";

  const termText = listing?.defaultTermMonths
    ? `Minimum ${listing.defaultTermMonths} months`
    : "Minimum 6 months";

  const roomStatus = status === "selected" ? "offer" : "available";

  let attention: string | null = null;
  let cta: string | null = null;

  if (app.offer && app.offer.status === "SENT") {
    attention = `Offer for ${roomName} is pending renter acceptance.`;
    cta = "Review offer";
  } else if (status === "new") {
    attention = `New application from ${app.guestName} needs review.`;
    cta = "Review";
  }

  const history: Array<[string, string]> = [
    ["Application submitted", formatDate(app.submittedAt)],
  ];
  app.decisions.forEach((d) => {
    history.push([
      `Decision: ${d.decision}${d.note ? ` (${d.note})` : ""}`,
      formatDate(d.decidedAt),
    ]);
  });
  if (app.offer) {
    history.push([
      `Offer created (${app.offer.status})`,
      formatDate(app.offer.createdAt),
    ]);
  }

  return {
    id: app.id,
    idCode: `ZR-A-${app.id.toString().padStart(5, "0")}`,
    guestName: app.guestName,
    guestEmail: app.guestEmail,
    listingId: app.listingId,
    listingName: roomName,
    roomType,
    rentText,
    termText,
    roomStatus,
    propertyName,
    propertyPlace,
    status,
    desiredMoveIn: app.desiredMoveIn,
    durationText: "6 months",
    occupantsText: "1 adult",
    timeAgoText: formatDate(app.submittedAt),
    submittedDateText: formatDate(app.submittedAt),
    unreadCount: 0,
    messages: app.message ? [app.message] : [],
    history,
    idVerified: true,
    attention,
    cta,
    offer: app.offer,
    rawApp: app,
  };
}

export function HostApplicationsManager() {
  const [loading, setLoading] = useState(true);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  // Live database data from FastAPI & PostgreSQL
  const [rawApplications, setRawApplications] = useState<Application[]>([]);
  const [rawProperties, setRawProperties] = useState<Property[]>([]);
  const [rawListings, setRawListings] = useState<HostedListing[]>([]);

  // Search & Filter state
  const [searchQuery, setSearchQuery] = useState("");
  const [cardFilter, setCardFilter] = useState<"" | "new" | "review" | "short" | "action">("");
  const [statusFilter, setStatusFilter] = useState("active");
  const [propertyFilter, setPropertyFilter] = useState("all");
  const [roomFilter, setRoomFilter] = useState("all");
  const [moveInFilter, setMoveInFilter] = useState("all");
  const [roomStatusFilter, setRoomStatusFilter] = useState("all");
  const [sortOption, setSortOption] = useState("action");
  const [viewTab, setViewTab] = useState<"all" | "room">("all");

  // Interactive UI state
  const [activeMenuId, setActiveMenuId] = useState<number | null>(null);
  const [drawerTarget, setDrawerTarget] = useState<ApplicationRecord | null>(null);
  const [drawerTab, setDrawerTab] = useState<"overview" | "details" | "messages" | "history">(
    "overview"
  );
  const [chatInput, setChatInput] = useState("");

  // Modals state
  const [pendingModal, setPendingModal] = useState<{
    kind: "select" | "decline" | "reqinfo";
    app: ApplicationRecord;
    replacingApp?: ApplicationRecord | null;
  } | null>(null);

  const [replaceReason, setReplaceReason] = useState("");
  const [replaceReasonError, setReplaceReasonError] = useState(false);
  const [declineReason, setDeclineReason] = useState("");
  const [reqInfoItem, setReqInfoItem] = useState("Rental reference");

  // Offer Agreement Panel Modal
  const [offerAgreementTarget, setOfferAgreementTarget] = useState<ApplicationRecord | null>(null);

  const { toast, showToast } = useToast();

  // Load real backend data
  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setConnectionError(null);
      const [apps, props, listings] = await Promise.all([
        listHostedApplications().catch((err) => {
          if (err instanceof ApiError && err.status === 401) {
            setConnectionError("AUTH_REQUIRED");
          }
          return [] as Application[];
        }),
        listHostedProperties().catch(() => [] as Property[]),
        listHostedListings().catch(() => [] as HostedListing[]),
      ]);

      setRawApplications(apps);
      setRawProperties(props);
      setRawListings(listings);
    } catch (err) {
      setConnectionError(errorMessage(err, "Failed to load applications from database."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Lookup maps
  const listingMap = useMemo(() => {
    const map = new Map<string, HostedListing>();
    rawListings.forEach((l) => map.set(l.id, l));
    return map;
  }, [rawListings]);

  const propertyMap = useMemo(() => {
    const map = new Map<number, Property>();
    rawProperties.forEach((p) => map.set(p.id, p));
    return map;
  }, [rawProperties]);

  // Dynamic Application Records
  const appRecords: ApplicationRecord[] = useMemo(() => {
    return rawApplications.map((app) =>
      buildApplicationRecord(app, listingMap, propertyMap)
    );
  }, [rawApplications, listingMap, propertyMap]);

  // Close menus on outside click or escape
  useEffect(() => {
    const handleGlobalClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement | null;
      if (!target?.closest("[data-menu-container]")) {
        setActiveMenuId(null);
      }
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setActiveMenuId(null);
        if (pendingModal) setPendingModal(null);
        else if (drawerTarget) setDrawerTarget(null);
      }
    };
    window.addEventListener("click", handleGlobalClick);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("click", handleGlobalClick);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [pendingModal, drawerTarget]);

  // Counts & KPI metrics derived dynamically from live database records
  const counts = useMemo(() => {
    let newCount = 0;
    let reviewCount = 0;
    let shortCount = 0;
    let actionCount = 0;

    appRecords.forEach((x) => {
      if (x.status === "new") newCount++;
      if (x.status === "review") reviewCount++;
      if (x.status === "short") shortCount++;
      if (x.attention) actionCount++;
    });

    const activeCount = appRecords.filter((x) => !CLOSED_STATUSES.includes(x.status)).length;
    return { newCount, reviewCount, shortCount, actionCount, activeCount };
  }, [appRecords]);

  // Urgent attention items list
  const attentionItems = useMemo(() => {
    return appRecords.filter((x) => x.attention);
  }, [appRecords]);

  // Dynamic filter options based on real database records
  const propertyOptions = useMemo(() => {
    const seen = new Set<string>();
    const opts = [{ value: "all", label: "All properties" }];
    rawProperties.forEach((p) => {
      const name = p.address.split(",")[0] || p.city || `Property #${p.id}`;
      if (!seen.has(name)) {
        seen.add(name);
        opts.push({ value: name.toLowerCase(), label: name });
      }
    });
    return opts;
  }, [rawProperties]);

  const roomOptions = useMemo(() => {
    return [
      { value: "all", label: "All rooms / listings" },
      ...rawListings.map((l) => ({
        value: l.id,
        label: `${l.name} (${l.roomType || "Room"})`,
      })),
    ];
  }, [rawListings]);

  const applicationStatusOptions = useMemo(
    () => [
      { value: "active", label: "All active" },
      { value: "new", label: "New" },
      { value: "review", label: "In Review" },
      { value: "short", label: "Shortlisted" },
      { value: "selected", label: "Selected / Offer" },
      { value: "closed", label: "History (closed)" },
    ],
    []
  );

  const moveInOptions = useMemo(
    () => [
      { value: "all", label: "Any time" },
      { value: "2w", label: "Within 2 weeks" },
      { value: "oct", label: "Next 30 days" },
      { value: "nov", label: "Next 60 days" },
      { value: "later", label: "Later" },
    ],
    []
  );

  const roomStatusOptions = useMemo(
    () => [
      { value: "all", label: "Any room status" },
      { value: "available", label: "Available" },
      { value: "offer", label: "Under Offer" },
      { value: "occupied", label: "Occupied" },
    ],
    []
  );

  const sortOptions = useMemo(
    () => [
      { value: "action", label: "Action required first" },
      { value: "newest", label: "Newest" },
      { value: "oldest", label: "Oldest" },
      { value: "movein", label: "Earliest move-in" },
    ],
    []
  );

  // Filtered applications
  const filteredApps = useMemo(() => {
    let result = [...appRecords];

    // Card filter
    if (cardFilter) {
      if (cardFilter === "action") {
        result = result.filter((x) => x.attention);
      } else {
        result = result.filter((x) => x.status === cardFilter);
      }
    }

    // Status filter
    if (statusFilter === "closed") {
      result = result.filter((x) => CLOSED_STATUSES.includes(x.status));
    } else if (statusFilter === "active") {
      result = result.filter((x) => !CLOSED_STATUSES.includes(x.status));
    } else {
      result = result.filter((x) => x.status === statusFilter);
    }

    // Property filter
    if (propertyFilter !== "all") {
      result = result.filter((x) =>
        x.propertyName.toLowerCase().includes(propertyFilter.toLowerCase())
      );
    }

    // Room / listing filter
    if (roomFilter !== "all") {
      result = result.filter((x) => x.listingId === roomFilter);
    }

    // Room status filter
    if (roomStatusFilter !== "all") {
      result = result.filter((x) => x.roomStatus === roomStatusFilter);
    }

    // Move in filter
    if (moveInFilter !== "all") {
      const now = new Date();
      result = result.filter((x) => {
        if (!x.desiredMoveIn) return false;
        const d = new Date(x.desiredMoveIn);
        const days = (d.getTime() - now.getTime()) / (1000 * 3600 * 24);
        if (moveInFilter === "2w") return days >= 0 && days <= 14;
        if (moveInFilter === "oct") return days >= 0 && days <= 30;
        if (moveInFilter === "nov") return days >= 0 && days <= 60;
        if (moveInFilter === "later") return days > 60;
        return true;
      });
    }

    // Search query
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase().trim();
      result = result.filter((x) => {
        const hay = `${x.guestName} ${x.idCode} ${x.listingName} ${x.propertyName} ${x.listingId}`.toLowerCase();
        return hay.includes(q);
      });
    }

    // Sorter
    result.sort((a, b) => {
      if (sortOption === "newest")
        return new Date(b.rawApp.submittedAt).getTime() - new Date(a.rawApp.submittedAt).getTime();
      if (sortOption === "oldest")
        return new Date(a.rawApp.submittedAt).getTime() - new Date(b.rawApp.submittedAt).getTime();
      if (sortOption === "movein") {
        const aDate = a.desiredMoveIn ? new Date(a.desiredMoveIn).getTime() : Infinity;
        const bDate = b.desiredMoveIn ? new Date(b.desiredMoveIn).getTime() : Infinity;
        return aDate - bDate;
      }

      // Action priority first
      const prio = (x: ApplicationRecord) =>
        x.attention ? 0 : x.status === "new" ? 1 : x.status === "short" ? 2 : x.status === "review" ? 3 : x.status === "selected" ? 4 : 5;
      const diff = prio(a) - prio(b);
      if (diff !== 0) return diff;
      return new Date(b.rawApp.submittedAt).getTime() - new Date(a.rawApp.submittedAt).getTime();
    });

    return result;
  }, [
    appRecords,
    cardFilter,
    statusFilter,
    propertyFilter,
    roomFilter,
    roomStatusFilter,
    moveInFilter,
    searchQuery,
    sortOption,
  ]);

  // Grouped by Listing records for "By Room" view
  const groupedByListing = useMemo(() => {
    const map: Record<string, ApplicationRecord[]> = {};
    filteredApps.forEach((x) => {
      if (!map[x.listingId]) map[x.listingId] = [];
      map[x.listingId].push(x);
    });
    return map;
  }, [filteredApps]);

  // Drawer Opener helper
  const openDrawer = (
    app: ApplicationRecord,
    tab: "overview" | "details" | "messages" | "history" = "overview"
  ) => {
    setDrawerTab(tab);
    setDrawerTarget(app);

    // If new, update locally to review
    if (app.status === "new") {
      setRawApplications((prev) =>
        prev.map((x) =>
          x.id === app.id
            ? {
                ...x,
                decisions: [
                  ...x.decisions,
                  {
                    id: Date.now(),
                    decision: "APPROVED" as any,
                    reasonCode: "REVIEW_STARTED",
                    note: "Host opened review",
                    decidedByAdminId: null,
                    decidedByUserId: null,
                    decidedAt: new Date().toISOString(),
                  },
                ],
              }
            : x
        )
      );
    }
  };

  // Action Handlers bound to real backend API
  const handleShortlist = async (app: ApplicationRecord) => {
    try {
      await decideHostedApplication(app.id, { decision: "APPROVED", reasonCode: "SHORTLISTED" });
      await loadData();
      showToast(`${app.guestName} shortlisted.`, "success");
      if (drawerTarget?.id === app.id) {
        setDrawerTarget((curr) => (curr ? { ...curr, status: "short" } : null));
      }
    } catch (err) {
      showToast(errorMessage(err, "Failed to shortlist application."), "error");
    }
  };

  const handleOpenSelectModal = (app: ApplicationRecord) => {
    const currentHolder = appRecords.find(
      (y) => y.listingId === app.listingId && y.status === "selected" && y.id !== app.id
    );
    setReplaceReason("");
    setReplaceReasonError(false);
    setPendingModal({
      kind: "select",
      app,
      replacingApp: currentHolder || null,
    });
  };

  const handleConfirmSelect = async () => {
    if (!pendingModal) return;
    const { app, replacingApp } = pendingModal;

    if (replacingApp && !replaceReason.trim()) {
      setReplaceReasonError(true);
      return;
    }

    try {
      if (replacingApp) {
        await decideHostedApplication(replacingApp.id, {
          decision: "REJECTED",
          reasonCode: "REPLACED",
          note: replaceReason,
        });
      }
      await decideHostedApplication(app.id, { decision: "APPROVED" });
      await loadData();

      showToast(`${app.guestName} selected. Under Offer set for this room.`, "success");
      setPendingModal(null);
      setOfferAgreementTarget(app);
    } catch (err) {
      showToast(errorMessage(err, "Failed to progress selected applicant."), "error");
    }
  };

  const handleOpenDeclineModal = (app: ApplicationRecord) => {
    setDeclineReason("Another applicant progressed");
    setPendingModal({
      kind: "decline",
      app,
    });
  };

  const handleConfirmDecline = async () => {
    if (!pendingModal) return;
    const { app } = pendingModal;

    try {
      await decideHostedApplication(app.id, {
        decision: "REJECTED",
        note: declineReason,
      });
      await loadData();
      showToast("Application declined. Stored in history.");
      setPendingModal(null);
      if (drawerTarget?.id === app.id) {
        setDrawerTarget(null);
      }
    } catch (err) {
      showToast(errorMessage(err, "Failed to decline application."), "error");
    }
  };

  const handleOpenReqInfoModal = (app: ApplicationRecord) => {
    setReqInfoItem("Rental reference");
    setPendingModal({
      kind: "reqinfo",
      app,
    });
  };

  const handleConfirmReqInfo = () => {
    if (!pendingModal) return;
    showToast(
      `Request for ${reqInfoItem} recorded. Renter has been notified.`,
      "success"
    );
    setPendingModal(null);
  };

  const handleSendMessage = () => {
    if (!drawerTarget || !chatInput.trim()) return;
    const msg = chatInput.trim();
    setDrawerTarget((curr) =>
      curr ? { ...curr, messages: [...curr.messages, `Host: ${msg}`] } : null
    );
    setChatInput("");
    showToast("Message sent to applicant.", "success");
  };

  const resetAllFilters = () => {
    setSearchQuery("");
    setCardFilter("");
    setStatusFilter("active");
    setPropertyFilter("all");
    setRoomFilter("all");
    setMoveInFilter("all");
    setRoomStatusFilter("all");
    setSortOption("action");
  };

  if (loading) {
    return <Loader label="Loading host applications from database..." />;
  }

  return (
    <div className="w-full space-y-5 font-sans">
      {/* 1. Page Header (Without Application settings button) */}
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 pt-1">
        <div className="space-y-1.5 max-w-3xl">
          <p className="text-[11px] font-bold text-[#D80B0B] dark:text-red-400 uppercase tracking-widest font-heading">
            Host workspace
          </p>
          <h1 className="text-3xl sm:text-4xl font-extrabold text-[#0C2B4E] dark:text-white font-heading tracking-tight leading-tight">
            Applications
          </h1>
          <p className="text-xs sm:text-sm md:text-base text-slate-500 dark:text-slate-400 font-normal leading-relaxed">
            Review applicants, compare relevant rental details, communicate with renters, and progress selected
            applicants to the next stage.
          </p>
        </div>
      </div>

      {/* Backend Auth / Connection Status */}
      {connectionError === "AUTH_REQUIRED" ? (
        <div className="p-4 bg-sky-50 dark:bg-sky-950/30 border border-sky-200 dark:border-sky-900 rounded-2xl flex items-start gap-3 text-xs text-sky-900 dark:text-sky-200">
          <CheckCircle2 className="size-5 text-emerald-600 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <p className="font-bold text-sm text-sky-950 dark:text-sky-100">
              Host Authentication Required
            </p>
            <p className="text-slate-600 dark:text-slate-300">
              To view and manage your live applications from PostgreSQL, please{" "}
              <Link
                href="/account/login"
                className="font-bold underline text-[#0C2B4E] dark:text-sky-300 hover:text-blue-600"
              >
                Log In
              </Link>{" "}
              or{" "}
              <Link
                href="/account/register"
                className="font-bold underline text-[#0C2B4E] dark:text-sky-300 hover:text-blue-600"
              >
                Register as Host
              </Link>
              .
            </p>
          </div>
        </div>
      ) : connectionError ? (
        <div className="p-4 bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-900 rounded-2xl flex items-start gap-3 text-xs text-amber-800 dark:text-amber-200">
          <AlertTriangle className="size-5 text-amber-600 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <p className="font-bold text-sm">Database Sync Notice</p>
            <p>{connectionError}</p>
          </div>
        </div>
      ) : null}

      {/* 2. Four Interactive Status Metric Cards (Dynamically calculated) */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3.5 pt-1">
        {/* New Card */}
        <button
          type="button"
          onClick={() => setCardFilter((prev) => (prev === "new" ? "" : "new"))}
          aria-pressed={cardFilter === "new"}
          className={`p-4 rounded-2xl border-2 transition text-left flex items-center justify-between gap-3 shadow-2xs cursor-pointer ${
            cardFilter === "new"
              ? "border-[#0C2B4E] bg-[#F5F8FE] dark:bg-blue-950/20"
              : "bg-white dark:bg-slate-900 border-slate-200/90 dark:border-slate-800 hover:border-slate-300"
          }`}
        >
          <div className="flex items-center gap-3 min-w-0">
            <span className="size-12 rounded-xl bg-emerald-50 dark:bg-emerald-950/40 text-[#05603A] dark:text-emerald-400 font-extrabold text-2xl font-heading flex items-center justify-center shrink-0">
              {counts.newCount}
            </span>
            <div className="min-w-0">
              <h3 className="text-base font-extrabold text-[#0C2B4E] dark:text-white font-heading">New</h3>
              <p className="text-xs text-slate-500 dark:text-slate-400 truncate">Not yet reviewed</p>
            </div>
          </div>
          <div className="size-2.5 rounded-full bg-[#05603A] self-start mt-1 shrink-0" />
        </button>

        {/* In Review Card */}
        <button
          type="button"
          onClick={() => setCardFilter((prev) => (prev === "review" ? "" : "review"))}
          aria-pressed={cardFilter === "review"}
          className={`p-4 rounded-2xl border-2 transition text-left flex items-center justify-between gap-3 shadow-2xs cursor-pointer ${
            cardFilter === "review"
              ? "border-[#0C2B4E] bg-[#F5F8FE] dark:bg-blue-950/20"
              : "bg-white dark:bg-slate-900 border-slate-200/90 dark:border-slate-800 hover:border-slate-300"
          }`}
        >
          <div className="flex items-center gap-3 min-w-0">
            <span className="size-12 rounded-xl bg-blue-50 dark:bg-blue-950/40 text-[#1D3F8F] dark:text-blue-300 font-extrabold text-2xl font-heading flex items-center justify-center shrink-0">
              {counts.reviewCount}
            </span>
            <div className="min-w-0">
              <h3 className="text-base font-extrabold text-[#0C2B4E] dark:text-white font-heading">In Review</h3>
              <p className="text-xs text-slate-500 dark:text-slate-400 truncate">You’ve started review</p>
            </div>
          </div>
          <Eye className="size-4 text-[#1D3F8F] self-start mt-1 shrink-0" />
        </button>

        {/* Shortlisted Card */}
        <button
          type="button"
          onClick={() => setCardFilter((prev) => (prev === "short" ? "" : "short"))}
          aria-pressed={cardFilter === "short"}
          className={`p-4 rounded-2xl border-2 transition text-left flex items-center justify-between gap-3 shadow-2xs cursor-pointer ${
            cardFilter === "short"
              ? "border-[#5B2AB8] bg-[#F9F5FF] dark:bg-purple-950/20"
              : "bg-white dark:bg-slate-900 border-slate-200/90 dark:border-slate-800 hover:border-slate-300"
          }`}
        >
          <div className="flex items-center gap-3 min-w-0">
            <span className="size-12 rounded-xl bg-purple-50 dark:bg-purple-950/40 text-[#5B2AB8] dark:text-purple-300 font-extrabold text-2xl font-heading flex items-center justify-center shrink-0">
              {counts.shortCount}
            </span>
            <div className="min-w-0">
              <h3 className="text-base font-extrabold text-[#0C2B4E] dark:text-white font-heading">Shortlisted</h3>
              <p className="text-xs text-slate-500 dark:text-slate-400 truncate">Kept for consideration</p>
            </div>
          </div>
          <Star className="size-4 text-[#5B2AB8] self-start mt-1 shrink-0" />
        </button>

        {/* Action Required Card */}
        <button
          type="button"
          onClick={() => setCardFilter((prev) => (prev === "action" ? "" : "action"))}
          aria-pressed={cardFilter === "action"}
          className={`p-4 rounded-2xl border-2 transition text-left flex items-center justify-between gap-3 shadow-2xs cursor-pointer ${
            cardFilter === "action"
              ? "border-[#B42318] bg-[#FFF7F7] dark:bg-red-950/20"
              : "bg-white dark:bg-slate-900 border-slate-200/90 dark:border-slate-800 hover:border-slate-300"
          }`}
        >
          <div className="flex items-center gap-3 min-w-0">
            <span className="size-12 rounded-xl bg-red-50 dark:bg-red-950/40 text-[#B42318] dark:text-red-400 font-extrabold text-2xl font-heading flex items-center justify-center shrink-0">
              {counts.actionCount}
            </span>
            <div className="min-w-0">
              <h3 className="text-base font-extrabold text-[#0C2B4E] dark:text-white font-heading">Action Required</h3>
              <p className="text-xs text-slate-500 dark:text-slate-400 truncate">Deadline or review</p>
            </div>
          </div>
          <AlertTriangle className="size-4 text-[#B42318] self-start mt-1 shrink-0" />
        </button>
      </div>

      {/* 3. Action Strip (Dynamic attention items) */}
      {attentionItems.length > 0 && (
        <div className="rounded-2xl border border-red-200 dark:border-red-900/40 bg-[#FFF5F5] dark:bg-red-950/20 p-4 sm:p-5 flex flex-col gap-3 shadow-2xs">
          <div className="flex items-center gap-2">
            <AlertTriangle className="size-4 text-[#B42318] shrink-0" />
            <strong className="text-sm font-bold text-[#B42318] dark:text-red-400 font-heading">
              {attentionItems.length}{" "}
              {attentionItems.length === 1 ? "application needs" : "applications need"} your attention
            </strong>
          </div>
          <div className="space-y-2">
            {attentionItems.slice(0, 2).map((item) => (
              <div
                key={item.id}
                className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pt-1"
              >
                <span className="text-xs sm:text-sm text-[#3A1B1B] dark:text-red-200 leading-relaxed">
                  {item.attention}
                </span>
                <button
                  type="button"
                  onClick={() => {
                    if (item.cta === "Review offer") {
                      setOfferAgreementTarget(item);
                    } else {
                      openDrawer(item, "overview");
                    }
                  }}
                  className="h-9 px-5 rounded-full bg-[#D80B0B] hover:bg-red-700 text-white text-xs font-bold font-heading transition cursor-pointer self-start sm:self-auto shrink-0 shadow-sm"
                >
                  {item.cta || "Review"}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 4. Search and Rounded Filters Bar */}
      <div className="w-full bg-white dark:bg-slate-900 rounded-[24px] border border-slate-200 dark:border-slate-800 p-4 shadow-xs">
        <div className="flex flex-col lg:flex-row items-stretch lg:items-end gap-2.5">
          {/* Search */}
          <div className="flex-1 lg:flex-[1.4] min-w-[200px]">
            <span className="text-xs font-bold text-[#0C2B4E] dark:text-slate-300 font-heading block mb-1">
              Search
            </span>
            <div className="relative">
              <Search className="size-4 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search applicant, application ID, property or room…"
                className="w-full h-11 pl-10 pr-8 bg-[#F1F5F9] dark:bg-slate-800 border border-slate-200/90 dark:border-slate-700 rounded-full text-xs text-[#0C2B4E] dark:text-slate-100 placeholder:text-slate-400 outline-none focus:ring-2 focus:ring-primary-500 font-normal transition shadow-2xs"
              />
              {searchQuery && (
                <button
                  onClick={() => setSearchQuery("")}
                  className="absolute right-3.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 cursor-pointer"
                >
                  <X className="size-3.5" />
                </button>
              )}
            </div>
          </div>

          {/* 1. Application Status */}
          <RoundedFilterDropdown
            label="Application Status"
            value={statusFilter}
            onChange={setStatusFilter}
            options={applicationStatusOptions}
            size="md"
          />

          {/* 2. Property */}
          <RoundedFilterDropdown
            label="Property"
            value={propertyFilter}
            onChange={setPropertyFilter}
            options={propertyOptions}
            size="md"
          />

          {/* 3. Room */}
          <RoundedFilterDropdown
            label="Room / Listing"
            value={roomFilter}
            onChange={setRoomFilter}
            options={roomOptions}
            size="md"
          />

          {/* 4. Move-in */}
          <RoundedFilterDropdown
            label="Move-in"
            value={moveInFilter}
            onChange={setMoveInFilter}
            options={moveInOptions}
            size="md"
          />

          {/* 5. Room Status */}
          <RoundedFilterDropdown
            label="Room Status"
            value={roomStatusFilter}
            onChange={setRoomStatusFilter}
            options={roomStatusOptions}
            size="md"
          />

          {/* 6. Sort */}
          <RoundedFilterDropdown
            label="Sort"
            value={sortOption}
            onChange={setSortOption}
            options={sortOptions}
            align="right"
            size="md"
          />
        </div>
      </div>

      {/* 5. Workspace Container */}
      <div className="w-full bg-white dark:bg-slate-900 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden">
        {/* Workspace Bar: Tabs & Counters */}
        <div className="px-5 py-4 border-b border-slate-100 dark:border-slate-800 flex flex-wrap items-center justify-between gap-3">
          <div className="inline-flex p-1 bg-slate-100 dark:bg-slate-800 rounded-full gap-1">
            <button
              type="button"
              onClick={() => setViewTab("all")}
              className={`h-9 px-4 rounded-full text-xs font-bold font-heading transition cursor-pointer ${
                viewTab === "all"
                  ? "bg-white dark:bg-slate-900 text-[#0C2B4E] dark:text-white shadow-xs"
                  : "text-slate-500 dark:text-slate-400 hover:text-slate-800"
              }`}
            >
              All Applications ({counts.activeCount})
            </button>
            <button
              type="button"
              onClick={() => setViewTab("room")}
              className={`h-9 px-4 rounded-full text-xs font-bold font-heading transition cursor-pointer ${
                viewTab === "room"
                  ? "bg-white dark:bg-slate-900 text-[#0C2B4E] dark:text-white shadow-xs"
                  : "text-slate-500 dark:text-slate-400 hover:text-slate-800"
              }`}
            >
              By Room
            </button>
          </div>

          <div className="flex items-center gap-3">
            <span className="text-xs font-semibold text-slate-500 dark:text-slate-400">
              {filteredApps.length} {filteredApps.length === 1 ? "application" : "applications"}
            </span>
            {(searchQuery ||
              cardFilter ||
              statusFilter !== "active" ||
              propertyFilter !== "all" ||
              roomFilter !== "all" ||
              moveInFilter !== "all" ||
              roomStatusFilter !== "all") && (
              <button
                type="button"
                onClick={resetAllFilters}
                className="h-8 px-3 rounded-full bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 text-xs font-bold text-[#0C2B4E] dark:text-slate-200 transition cursor-pointer"
              >
                Clear filters
              </button>
            )}
          </div>
        </div>

        {/* VIEW 1: BY ROOM VIEW */}
        {viewTab === "room" ? (
          filteredApps.length === 0 ? (
            <div className="p-12 text-center flex flex-col items-center justify-center gap-4">
              <div className="size-16 rounded-3xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-slate-400">
                <Inbox className="size-8" />
              </div>
              <div className="space-y-1">
                <h3 className="text-[#0C2B4E] dark:text-white font-extrabold text-base font-heading">
                  {rawApplications.length === 0 ? "No applications yet" : "No applications match your filter"}
                </h3>
                <p className="text-slate-500 text-xs">
                  {rawApplications.length === 0
                    ? "Applications will group automatically by room when submitted by prospective renters."
                    : "Try adjusting filters to see other applications."}
                </p>
              </div>
            </div>
          ) : (
            <div className="p-5 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {Object.keys(groupedByListing).map((listingId) => {
                const roomApps = groupedByListing[listingId] || [];
                const firstApp = roomApps[0];
                const newInRoom = roomApps.filter((x) => x.status === "new").length;

                return (
                  <div
                    key={listingId}
                    className="rounded-2xl border border-slate-200 dark:border-slate-800 p-4 space-y-3.5 bg-white dark:bg-slate-900 flex flex-col justify-between"
                  >
                    <div className="space-y-2">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <h3 className="text-base font-extrabold text-[#0C2B4E] dark:text-white font-heading">
                            {firstApp.propertyName} · {firstApp.listingName}
                          </h3>
                          <div className="flex items-center gap-2 mt-1">
                            <span
                              className={`inline-flex items-center gap-1 text-xs font-bold ${
                                firstApp.roomStatus === "available"
                                  ? "text-emerald-600"
                                  : firstApp.roomStatus === "offer"
                                  ? "text-amber-600"
                                  : "text-[#0C2B4E]"
                              }`}
                            >
                              <span className="size-1.5 rounded-full bg-current" />
                              Room:{" "}
                              {firstApp.roomStatus === "available"
                                ? "Available"
                                : firstApp.roomStatus === "offer"
                                ? "Under Offer"
                                : "Occupied"}
                            </span>
                          </div>
                        </div>
                      </div>

                      <p className="text-xs text-slate-500">
                        {roomApps.length} {roomApps.length === 1 ? "application" : "applications"}
                        {newInRoom > 0 && <b className="text-emerald-600 font-bold"> · {newInRoom} new</b>}
                      </p>

                      <ul className="space-y-1.5 pt-1">
                        {roomApps.slice(0, 3).map((app) => (
                          <li
                            key={app.id}
                            className="flex items-center justify-between gap-2 p-2 rounded-xl bg-slate-50 dark:bg-slate-800/60"
                          >
                            <button
                              type="button"
                              onClick={() => openDrawer(app)}
                              className="flex items-center gap-2 text-xs font-bold text-[#0C2B4E] dark:text-white hover:text-[#D80B0B] transition cursor-pointer truncate text-left"
                            >
                              <span className="size-7 rounded-full bg-slate-200 dark:bg-slate-700 flex items-center justify-center font-extrabold text-[10px] shrink-0">
                                {app.guestName
                                  .split(" ")
                                  .map((w) => w[0])
                                  .join("")
                                  .slice(0, 2)}
                              </span>
                              <span className="truncate">{app.guestName}</span>
                            </button>
                            <div className="flex items-center gap-1.5 shrink-0">
                              <span
                                className={`text-[10px] font-extrabold uppercase px-2 py-0.5 rounded-full ${
                                  app.status === "new"
                                    ? "bg-blue-50 text-blue-700"
                                    : app.status === "short"
                                    ? "bg-purple-50 text-purple-700"
                                    : app.status === "selected"
                                    ? "bg-emerald-50 text-emerald-700"
                                    : "bg-slate-100 text-slate-600"
                                }`}
                              >
                                {app.status}
                              </span>
                            </div>
                          </li>
                        ))}
                      </ul>
                    </div>

                    <button
                      type="button"
                      onClick={() => {
                        setViewTab("all");
                        setRoomFilter(listingId);
                      }}
                      className="w-full h-9 rounded-full border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-[#0C2B4E] dark:text-white text-xs font-bold font-heading transition cursor-pointer"
                    >
                      View room applications
                    </button>
                  </div>
                );
              })}
            </div>
          )
        ) : (
          /* VIEW 2: ALL APPLICATIONS TABLE VIEW */
          <div>
            {/* Table Header Columns */}
            <div className="hidden lg:grid grid-cols-[minmax(210px,1.3fr)_minmax(180px,1.1fr)_minmax(160px,1fr)_minmax(170px,1fr)_120px_190px] gap-4 px-6 py-3 bg-[#F7F9FD] dark:bg-slate-800/50 border-b border-slate-100 dark:border-slate-800 text-[11px] font-bold text-slate-500 uppercase tracking-wider font-heading">
              <div>Applicant</div>
              <div>Room / Property</div>
              <div>Requested Tenancy</div>
              <div>Application State</div>
              <div>Messages</div>
              <div className="text-right">Next Action</div>
            </div>

            {/* Empty Result State */}
            {filteredApps.length === 0 ? (
              <div className="p-12 text-center flex flex-col items-center justify-center gap-4">
                <div className="size-16 rounded-3xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-slate-400">
                  <Inbox className="size-8" />
                </div>
                <div className="space-y-1">
                  <h3 className="text-[#0C2B4E] dark:text-white font-extrabold text-base font-heading">
                    {rawApplications.length === 0 ? "No applications yet" : "No applications match your filter"}
                  </h3>
                  <p className="text-slate-500 text-xs max-w-sm mx-auto">
                    {rawApplications.length === 0
                      ? "Applications from renters will appear here when people apply for your rooms."
                      : "Try clearing search or adjusting your status filters."}
                  </p>
                </div>
                {rawApplications.length === 0 ? (
                  <Link
                    href="/account/host/listings"
                    className="mt-2 px-6 py-2.5 rounded-full bg-[#0C2B4E] hover:bg-[#09223e] text-white text-xs font-bold font-heading shadow-sm transition"
                  >
                    View Listings
                  </Link>
                ) : (
                  <button
                    type="button"
                    onClick={resetAllFilters}
                    className="px-5 py-2.5 rounded-full bg-[#0C2B4E] hover:bg-[#09223e] text-white text-xs font-bold font-heading transition cursor-pointer"
                  >
                    Clear filters
                  </button>
                )}
              </div>
            ) : (
              <div className="divide-y divide-slate-100 dark:divide-slate-800">
                {filteredApps.map((app) => {
                  const initials = app.guestName
                    .split(" ")
                    .map((w) => w[0])
                    .join("")
                    .slice(0, 2);
                  const isClosed = CLOSED_STATUSES.includes(app.status);

                  return (
                    <article
                      key={app.id}
                      className={`grid grid-cols-1 lg:grid-cols-[minmax(210px,1.3fr)_minmax(180px,1.1fr)_minmax(160px,1fr)_minmax(170px,1fr)_120px_190px] gap-4 items-center px-6 py-4 transition ${
                        app.attention
                          ? "bg-[#FFFBF5] dark:bg-amber-950/10"
                          : isClosed
                          ? "opacity-90 hover:bg-slate-50/50"
                          : "hover:bg-slate-50/70 dark:hover:bg-slate-800/20"
                      }`}
                    >
                      {/* 1. Applicant */}
                      <button
                        type="button"
                        onClick={() => openDrawer(app)}
                        className="flex items-center gap-3 text-left min-w-0 cursor-pointer group"
                      >
                        <span className="size-11 rounded-full bg-[#E6ECF7] dark:bg-slate-800 text-[#0E2F73] dark:text-sky-300 font-extrabold text-sm flex items-center justify-center shrink-0">
                          {initials}
                        </span>
                        <div className="space-y-0.5 min-w-0">
                          <h4 className="text-sm font-extrabold text-[#0C2B4E] dark:text-white font-heading truncate group-hover:text-[#D80B0B] transition">
                            {app.guestName}
                          </h4>
                          <div className="flex items-center gap-1.5">
                            {app.idVerified ? (
                              <span className="inline-flex items-center gap-1 text-[11px] font-bold text-[#05603A]">
                                <Check className="size-3 stroke-[2.5]" />
                                Identity Verified
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1 text-[11px] font-bold text-amber-700">
                                <Clock className="size-3 stroke-[2.5]" />
                                Identity check pending
                              </span>
                            )}
                          </div>
                          <span className="text-[11px] text-slate-400 block font-mono">
                            #{app.idCode} · {app.timeAgoText}
                          </span>
                        </div>
                      </button>

                      {/* 2. Room / Property */}
                      <div className="space-y-0.5 min-w-0">
                        <span className="text-xs font-bold text-[#0C2B4E] dark:text-white block truncate">
                          {app.listingName}
                        </span>
                        <span className="text-xs font-bold text-slate-700 dark:text-slate-300 block">
                          {app.rentText} <small className="text-slate-400 font-normal">/ month</small>
                        </span>
                        <span
                          className={`inline-flex items-center gap-1 text-[11px] font-bold ${
                            app.roomStatus === "available"
                              ? "text-[#05603A]"
                              : app.roomStatus === "offer"
                              ? "text-[#93370D]"
                              : "text-[#0E2F73]"
                          }`}
                        >
                          <span className="size-1 rounded-full bg-current" />
                          Room:{" "}
                          {app.roomStatus === "available"
                            ? "Available"
                            : app.roomStatus === "offer"
                            ? "Under Offer"
                            : "Occupied"}
                        </span>
                      </div>

                      {/* 3. Requested Tenancy */}
                      <div className="space-y-0.5 min-w-0">
                        <span className="text-xs font-bold text-[#0C2B4E] dark:text-white block truncate">
                          {app.desiredMoveIn ? formatDate(app.desiredMoveIn) : "Flexible"} · {app.durationText}
                        </span>
                        <span className="text-xs text-slate-500 dark:text-slate-400 block">
                          {app.occupantsText}
                        </span>
                      </div>

                      {/* 4. Application State */}
                      <div className="space-y-1 min-w-0 flex flex-col items-start">
                        {app.status === "new" && (
                          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-[#EEF3FF] border border-[#AFC2EE] text-[#1D3F8F] text-xs font-extrabold uppercase tracking-wide">
                            <span className="size-1.5 rounded-full bg-[#1D3F8F]" />
                            New
                          </span>
                        )}
                        {app.status === "review" && (
                          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-white border border-[#AFC2EE] text-[#1D3F8F] text-xs font-extrabold uppercase tracking-wide">
                            <Eye className="size-3 stroke-[2.5]" />
                            In Review
                          </span>
                        )}
                        {app.status === "short" && (
                          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-[#F3EEFF] border border-[#C9B5F0] text-[#5B2AB8] text-xs font-extrabold uppercase tracking-wide">
                            <Star className="size-3 stroke-[2.5]" />
                            Shortlisted
                          </span>
                        )}
                        {app.status === "selected" && (
                          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-[#0E2F73] border border-[#0E2F73] text-white text-xs font-extrabold uppercase tracking-wide">
                            <Check className="size-3 stroke-[2.5]" />
                            Selected · Offer pending
                          </span>
                        )}
                        {app.status === "declined" && (
                          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-[#F0F2F6] border border-[#C3CAD6] text-[#344054] text-xs font-extrabold uppercase tracking-wide">
                            <X className="size-3 stroke-[2.5]" />
                            Declined
                          </span>
                        )}
                        {app.status === "withdrawn" && (
                          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-[#F0F2F6] border border-[#C3CAD6] text-[#344054] text-xs font-extrabold uppercase tracking-wide">
                            <RotateCcw className="size-3 stroke-[2.5]" />
                            Withdrawn
                          </span>
                        )}
                        {app.status === "expired" && (
                          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-[#F0F2F6] border border-[#C3CAD6] text-[#344054] text-xs font-extrabold uppercase tracking-wide">
                            <Clock className="size-3 stroke-[2.5]" />
                            Expired
                          </span>
                        )}
                        {app.status === "superseded" && (
                          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-[#F0F2F6] border border-[#C3CAD6] text-[#344054] text-xs font-extrabold uppercase tracking-wide">
                            <Pause className="size-3 stroke-[2.5]" />
                            Closed
                          </span>
                        )}

                        {app.attention && (
                          <span className="inline-flex items-center gap-1 text-[11px] font-bold text-[#B42318]">
                            <AlertTriangle className="size-3 stroke-[2.5]" />
                            Action needed
                          </span>
                        )}
                      </div>

                      {/* 5. Messages */}
                      <div>
                        {app.messages.length > 0 ? (
                          <button
                            type="button"
                            onClick={() => openDrawer(app, "messages")}
                            className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-[#EEF3FF] hover:bg-[#DFE8FB] text-[#1D3F8F] text-xs font-extrabold transition cursor-pointer"
                          >
                            <MessageSquare className="size-3.5" />
                            <span>{app.messages.length} msg</span>
                          </button>
                        ) : (
                          <span className="text-slate-400 text-sm font-semibold pl-2">—</span>
                        )}
                      </div>

                      {/* 6. Next Action */}
                      <div
                        className="flex items-center justify-end gap-1.5 relative"
                        data-menu-container="true"
                      >
                        {app.cta ? (
                          <button
                            type="button"
                            onClick={() => {
                              if (app.cta === "Review offer") {
                                setOfferAgreementTarget(app);
                              } else {
                                openDrawer(app, "overview");
                              }
                            }}
                            className="h-10 px-4 rounded-full bg-[#D80B0B] hover:bg-red-700 text-white text-xs font-bold font-heading shadow-sm transition cursor-pointer whitespace-nowrap"
                          >
                            {app.cta}
                          </button>
                        ) : app.status === "new" ? (
                          <button
                            type="button"
                            onClick={() => openDrawer(app, "overview")}
                            className="h-10 px-4 rounded-full bg-[#0C2B4E] hover:bg-[#09223e] text-white text-xs font-bold font-heading transition cursor-pointer whitespace-nowrap shadow-2xs"
                          >
                            Review Application
                          </button>
                        ) : app.status === "review" ? (
                          <button
                            type="button"
                            onClick={() => openDrawer(app, "overview")}
                            className="h-10 px-4 rounded-full border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-[#0C2B4E] dark:text-white text-xs font-bold font-heading transition cursor-pointer whitespace-nowrap shadow-2xs"
                          >
                            Continue Review
                          </button>
                        ) : app.status === "short" ? (
                          <button
                            type="button"
                            onClick={() => handleOpenSelectModal(app)}
                            className="h-10 px-4 rounded-full bg-[#0C2B4E] hover:bg-[#09223e] text-white text-xs font-bold font-heading transition cursor-pointer whitespace-nowrap shadow-2xs"
                          >
                            Review &amp; Select
                          </button>
                        ) : app.status === "selected" ? (
                          <button
                            type="button"
                            onClick={() => setOfferAgreementTarget(app)}
                            className="h-10 px-4 rounded-full border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-[#0C2B4E] dark:text-white text-xs font-bold font-heading transition cursor-pointer whitespace-nowrap shadow-2xs flex items-center gap-1.5"
                          >
                            <FileSignature className="size-3.5 text-[#0C2B4E]" />
                            <span>Manage offer</span>
                          </button>
                        ) : (
                          <button
                            type="button"
                            onClick={() => openDrawer(app, "overview")}
                            className="h-10 px-4 rounded-full text-slate-500 hover:bg-slate-100 text-xs font-bold font-heading transition cursor-pointer whitespace-nowrap"
                          >
                            View record
                          </button>
                        )}

                        {/* 3-Dots More Menu */}
                        <div className="relative">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              setActiveMenuId((curr) => (curr === app.id ? null : app.id));
                            }}
                            aria-expanded={activeMenuId === app.id}
                            className="size-9 rounded-full border border-slate-200 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800 flex items-center justify-center text-slate-600 dark:text-slate-300 transition cursor-pointer"
                          >
                            <MoreHorizontal className="size-4" />
                          </button>

                          {activeMenuId === app.id && (
                            <div className="absolute right-0 top-11 z-30 min-w-[200px] bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-xl p-1.5 space-y-0.5 animate-in fade-in zoom-in-95 duration-150">
                              <button
                                type="button"
                                onClick={() => {
                                  setActiveMenuId(null);
                                  openDrawer(app, "overview");
                                }}
                                className="w-full h-10 px-3 rounded-xl hover:bg-slate-100 dark:hover:bg-slate-800 flex items-center gap-2.5 text-xs font-bold text-[#0C2B4E] dark:text-white transition cursor-pointer text-left"
                              >
                                <Eye className="size-4 text-slate-500" />
                                <span>Open application</span>
                              </button>
                              <button
                                type="button"
                                onClick={() => {
                                  setActiveMenuId(null);
                                  openDrawer(app, "messages");
                                }}
                                className="w-full h-10 px-3 rounded-xl hover:bg-slate-100 dark:hover:bg-slate-800 flex items-center gap-2.5 text-xs font-bold text-[#0C2B4E] dark:text-white transition cursor-pointer text-left"
                              >
                                <Send className="size-4 text-slate-500" />
                                <span>Message applicant</span>
                              </button>
                              {!isClosed && (
                                <>
                                  <button
                                    type="button"
                                    onClick={() => {
                                      setActiveMenuId(null);
                                      handleOpenReqInfoModal(app);
                                    }}
                                    className="w-full h-10 px-3 rounded-xl hover:bg-slate-100 dark:hover:bg-slate-800 flex items-center gap-2.5 text-xs font-bold text-[#0C2B4E] dark:text-white transition cursor-pointer text-left"
                                  >
                                    <Info className="size-4 text-slate-500" />
                                    <span>Request information</span>
                                  </button>
                                  {app.status !== "short" && app.status !== "selected" && (
                                    <button
                                      type="button"
                                      onClick={() => {
                                        setActiveMenuId(null);
                                        handleShortlist(app);
                                      }}
                                      className="w-full h-10 px-3 rounded-xl hover:bg-slate-100 dark:hover:bg-slate-800 flex items-center gap-2.5 text-xs font-bold text-[#0C2B4E] dark:text-white transition cursor-pointer text-left"
                                    >
                                      <Star className="size-4 text-purple-600" />
                                      <span>Shortlist</span>
                                    </button>
                                  )}
                                  <button
                                    type="button"
                                    onClick={() => {
                                      setActiveMenuId(null);
                                      handleOpenDeclineModal(app);
                                    }}
                                    className="w-full h-10 px-3 rounded-xl hover:bg-red-50 dark:hover:bg-red-950/40 flex items-center gap-2.5 text-xs font-bold text-[#B42318] transition cursor-pointer text-left border-t border-slate-100 dark:border-slate-800 mt-1 pt-1"
                                  >
                                    <XCircle className="size-4 text-[#B42318]" />
                                    <span>Decline</span>
                                  </button>
                                </>
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>

      <p className="text-xs text-slate-400 pl-1 leading-relaxed">
        Declined, withdrawn, expired and closed applications stay in History (Application Status filter) so they don’t
        congest this workspace.
      </p>

      {/* ========================================================================= */}
      {/* 6. SLIDE-OUT DRAWER PANEL                                                 */}
      {/* ========================================================================= */}
      {drawerTarget && (
        <div className="fixed inset-0 z-50 flex justify-end">
          <div
            className="fixed inset-0 bg-slate-900/50 backdrop-blur-[2px] transition-opacity animate-in fade-in duration-200"
            onClick={() => setDrawerTarget(null)}
            aria-hidden="true"
          />

          <aside
            className="relative z-10 w-full max-w-[520px] bg-white dark:bg-slate-900 h-full shadow-2xl flex flex-col justify-between overflow-y-auto p-6 sm:p-7 border-l border-slate-200 dark:border-slate-800 sm:rounded-l-[28px] animate-in slide-in-from-right duration-200 text-slate-800 dark:text-slate-100"
            role="dialog"
            aria-modal="true"
          >
            <div className="space-y-5">
              {/* Drawer Top Header */}
              <div className="flex items-start justify-between gap-4 pb-3 border-b border-slate-100 dark:border-slate-800">
                <div className="flex items-center gap-3.5 min-w-0">
                  <span className="size-14 rounded-full bg-[#E6ECF7] dark:bg-slate-800 text-[#0E2F73] dark:text-sky-300 font-extrabold text-lg flex items-center justify-center shrink-0">
                    {drawerTarget.guestName
                      .split(" ")
                      .map((w) => w[0])
                      .join("")
                      .slice(0, 2)}
                  </span>
                  <div className="space-y-0.5 min-w-0">
                    <p className="text-[11px] font-bold text-[#D80B0B] dark:text-red-400 uppercase tracking-widest font-heading">
                      Application #{drawerTarget.idCode}
                    </p>
                    <h2 className="text-xl sm:text-2xl font-extrabold text-[#0C2B4E] dark:text-white font-heading truncate">
                      {drawerTarget.guestName}
                    </h2>
                    <span className="inline-flex items-center gap-1 text-xs font-bold text-[#05603A]">
                      <Check className="size-3.5 stroke-[2.5]" />
                      Identity Verified
                    </span>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setDrawerTarget(null)}
                  className="size-9 rounded-full border border-slate-200 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800 flex items-center justify-center text-slate-500 hover:text-slate-900 dark:hover:text-white transition cursor-pointer shrink-0"
                  aria-label="Close application drawer"
                >
                  <X className="size-4" />
                </button>
              </div>

              {/* Status & Timestamp Sub-bar */}
              <div className="flex items-center gap-3 flex-wrap text-xs">
                <span
                  className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-extrabold uppercase tracking-wide ${
                    drawerTarget.status === "new"
                      ? "bg-[#EEF3FF] border border-[#AFC2EE] text-[#1D3F8F]"
                      : drawerTarget.status === "review"
                      ? "bg-white border border-[#AFC2EE] text-[#1D3F8F]"
                      : drawerTarget.status === "short"
                      ? "bg-[#F3EEFF] border border-[#C9B5F0] text-[#5B2AB8]"
                      : drawerTarget.status === "selected"
                      ? "bg-[#0E2F73] border border-[#0E2F73] text-white"
                      : "bg-slate-100 text-slate-600"
                  }`}
                >
                  {drawerTarget.status}
                </span>
                <span className="text-slate-500">
                  Submitted {drawerTarget.timeAgoText} · {drawerTarget.submittedDateText}
                </span>
              </div>

              {/* Drawer Tabs */}
              <div className="flex border-b border-slate-100 dark:border-slate-800 gap-1 overflow-x-auto">
                {(
                  [
                    { key: "overview", label: "Overview" },
                    { key: "details", label: "Application Details" },
                    { key: "messages", label: "Messages" },
                    { key: "history", label: "History" },
                  ] as const
                ).map((t) => (
                  <button
                    key={t.key}
                    type="button"
                    onClick={() => setDrawerTab(t.key)}
                    className={`h-10 px-3.5 border-b-2 font-bold text-xs transition cursor-pointer whitespace-nowrap ${
                      drawerTab === t.key
                        ? "border-[#0E2F73] text-[#0E2F73] dark:text-sky-300 dark:border-sky-300"
                        : "border-transparent text-slate-400 hover:text-slate-700"
                    }`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>

              {/* TAB 1: OVERVIEW */}
              {drawerTab === "overview" && (
                <div className="space-y-5 text-xs sm:text-sm">
                  {drawerTarget.attention && (
                    <div className="p-3.5 rounded-2xl bg-[#FDECEC] border border-red-200 text-[#B42318] flex items-start gap-2.5 font-bold">
                      <AlertTriangle className="size-4 shrink-0 mt-0.5" />
                      <span>{drawerTarget.attention}</span>
                    </div>
                  )}

                  {/* Applied For */}
                  <div className="space-y-2">
                    <h3 className="text-xs font-extrabold uppercase tracking-wider text-slate-500 font-heading">
                      Applied for
                    </h3>
                    <div className="grid grid-cols-2 gap-2 border border-slate-200 dark:border-slate-800 rounded-2xl p-3">
                      <div>
                        <span className="text-[11px] font-bold text-slate-400 uppercase block">Room</span>
                        <strong className="text-xs font-bold text-[#0C2B4E] dark:text-white block mt-0.5">
                          {drawerTarget.listingName}
                        </strong>
                      </div>
                      <div>
                        <span className="text-[11px] font-bold text-slate-400 uppercase block">Room status</span>
                        <strong className="text-xs font-bold text-[#0C2B4E] dark:text-white block mt-0.5">
                          {drawerTarget.roomStatus === "available"
                            ? "Available"
                            : drawerTarget.roomStatus === "offer"
                            ? "Under Offer"
                            : "Occupied"}
                        </strong>
                      </div>
                      <div>
                        <span className="text-[11px] font-bold text-slate-400 uppercase block">Rent</span>
                        <strong className="text-xs font-bold text-[#0C2B4E] dark:text-white block mt-0.5">
                          {drawerTarget.rentText} / month
                        </strong>
                      </div>
                      <div>
                        <span className="text-[11px] font-bold text-slate-400 uppercase block">Listing ID</span>
                        <strong className="text-xs font-bold text-[#0C2B4E] dark:text-white block mt-0.5">
                          #{drawerTarget.listingId}
                        </strong>
                      </div>
                    </div>
                  </div>

                  {/* Requested Tenancy */}
                  <div className="space-y-2">
                    <h3 className="text-xs font-extrabold uppercase tracking-wider text-slate-500 font-heading">
                      Requested tenancy
                    </h3>
                    <div className="grid grid-cols-2 gap-2 border border-slate-200 dark:border-slate-800 rounded-2xl p-3">
                      <div>
                        <span className="text-[11px] font-bold text-slate-400 uppercase block">Move-in</span>
                        <strong className="text-xs font-bold text-[#0C2B4E] dark:text-white block mt-0.5">
                          {drawerTarget.desiredMoveIn ? formatDate(drawerTarget.desiredMoveIn) : "Flexible"}
                        </strong>
                      </div>
                      <div>
                        <span className="text-[11px] font-bold text-slate-400 uppercase block">Duration</span>
                        <strong className="text-xs font-bold text-[#0C2B4E] dark:text-white block mt-0.5">
                          {drawerTarget.durationText}
                        </strong>
                      </div>
                    </div>
                  </div>

                  {/* Verification */}
                  <div className="space-y-2">
                    <h3 className="text-xs font-extrabold uppercase tracking-wider text-slate-500 font-heading">
                      Verification
                    </h3>
                    <div className="p-3.5 border border-slate-200 dark:border-slate-800 rounded-2xl space-y-1">
                      <span className="inline-flex items-center gap-1.5 text-xs font-bold text-[#05603A]">
                        <Check className="size-4 stroke-[2.5]" />
                        Identity Verified
                      </span>
                      <p className="text-xs text-slate-400">
                        Assertion only — identity documents stay with Zoiko Rooms Trust &amp; Safety.
                      </p>
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 2: APPLICATION DETAILS */}
              {drawerTab === "details" && (
                <div className="space-y-4 text-xs sm:text-sm">
                  <h3 className="text-xs font-extrabold uppercase tracking-wider text-slate-500 font-heading">
                    Application statement &amp; responses
                  </h3>
                  <div className="border border-slate-200 dark:border-slate-800 rounded-2xl overflow-hidden divide-y divide-slate-100 dark:divide-slate-800 text-xs sm:text-sm">
                    <div className="p-3.5 space-y-1">
                      <span className="text-slate-400 font-bold block uppercase text-[11px]">Renter Statement</span>
                      <p className="text-slate-800 dark:text-slate-200 font-medium">
                        {drawerTarget.rawApp.message ? `"${drawerTarget.rawApp.message}"` : "No message provided."}
                      </p>
                    </div>
                    <div className="flex justify-between items-center p-3.5">
                      <span className="text-slate-500">Applicant Email</span>
                      <strong className="text-[#0C2B4E] dark:text-white">{drawerTarget.guestEmail}</strong>
                    </div>
                    <div className="flex justify-between items-center p-3.5">
                      <span className="text-slate-500">Submitted Date</span>
                      <strong className="text-[#0C2B4E] dark:text-white">{drawerTarget.submittedDateText}</strong>
                    </div>
                  </div>
                  <div className="p-3.5 rounded-2xl bg-[#EEF3FF] dark:bg-slate-800 border border-[#AFC2EE]/40 text-[#1D3F8F] dark:text-blue-300 flex items-start gap-2.5 text-xs leading-relaxed">
                    <Lock className="size-4 shrink-0 mt-0.5" />
                    <span>
                      Protected characteristics are never collected or displayed. All applicant data complies with
                      jurisdiction Fair Housing standards.
                    </span>
                  </div>
                </div>
              )}

              {/* TAB 3: MESSAGES */}
              {drawerTab === "messages" && (
                <div className="space-y-4">
                  <div className="space-y-2.5 max-h-[300px] overflow-y-auto pr-1">
                    {drawerTarget.messages.length === 0 ? (
                      <p className="text-xs text-slate-400 text-center py-6">No messages yet.</p>
                    ) : (
                      drawerTarget.messages.map((m, idx) => {
                        const isHost = m.startsWith("Host: ");
                        const text = isHost ? m.replace("Host: ", "") : m;
                        return (
                          <div
                            key={idx}
                            className={`p-3 rounded-2xl text-xs max-w-[85%] leading-relaxed ${
                              isHost
                                ? "bg-[#0E2F73] text-white ml-auto rounded-br-none"
                                : "bg-slate-100 dark:bg-slate-800 text-slate-800 dark:text-slate-100 mr-auto rounded-bl-none"
                            }`}
                          >
                            <p>{text}</p>
                            <span className="text-[10px] opacity-75 block text-right mt-1">
                              {drawerTarget.submittedDateText}
                            </span>
                          </div>
                        );
                      })
                    )}
                  </div>

                  {!CLOSED_STATUSES.includes(drawerTarget.status) && (
                    <div className="space-y-2 pt-2 border-t border-slate-100 dark:border-slate-800">
                      <div className="flex gap-2">
                        <input
                          type="text"
                          value={chatInput}
                          onChange={(e) => setChatInput(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") handleSendMessage();
                          }}
                          placeholder={`Write a message to ${drawerTarget.guestName.split(" ")[0]}…`}
                          className="flex-1 h-11 px-4 rounded-full border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-xs outline-none focus:ring-2 focus:ring-primary-500"
                        />
                        <button
                          type="button"
                          onClick={handleSendMessage}
                          className="size-11 rounded-full bg-[#0E2F73] hover:bg-[#09223e] text-white flex items-center justify-center transition cursor-pointer shrink-0"
                          aria-label="Send message"
                        >
                          <Send className="size-4" />
                        </button>
                      </div>
                      <button
                        type="button"
                        onClick={() => handleOpenReqInfoModal(drawerTarget)}
                        className="text-xs font-bold text-[#0E2F73] hover:underline flex items-center gap-1.5 cursor-pointer pt-1"
                      >
                        <Info className="size-3.5" />
                        <span>Request information</span>
                      </button>
                    </div>
                  )}
                </div>
              )}

              {/* TAB 4: HISTORY */}
              {drawerTab === "history" && (
                <div className="space-y-3">
                  <ol className="relative border-l border-slate-200 dark:border-slate-800 ml-2 space-y-4 py-2">
                    {drawerTarget.history.map(([title, time], idx) => (
                      <li key={idx} className="ml-4">
                        <span className="absolute -left-1.5 mt-1 size-3 rounded-full bg-[#0E2F73] ring-4 ring-white dark:ring-slate-900" />
                        <h4 className="text-xs font-bold text-[#0C2B4E] dark:text-white">{title}</h4>
                        <time className="text-[11px] text-slate-400">{time}</time>
                      </li>
                    ))}
                  </ol>
                  <p className="text-xs text-slate-400 pt-2 border-t border-slate-100 dark:border-slate-800">
                    History is immutable and stays linked to any offer or rental record.
                  </p>
                </div>
              )}
            </div>

            {/* Drawer Bottom Actions */}
            <div className="pt-5 border-t border-slate-100 dark:border-slate-800 flex items-center justify-between gap-2 flex-wrap shrink-0">
              {CLOSED_STATUSES.includes(drawerTarget.status) ? (
                <p className="text-xs text-slate-400">This record is read-only.</p>
              ) : drawerTarget.status === "selected" ? (
                <div className="flex items-center gap-2 w-full justify-between">
                  <button
                    type="button"
                    onClick={() => {
                      setDrawerTarget(null);
                      setOfferAgreementTarget(drawerTarget);
                    }}
                    className="h-10 px-6 rounded-full bg-[#0C2B4E] hover:bg-[#09223e] text-white text-xs font-bold font-heading transition cursor-pointer"
                  >
                    Manage offer
                  </button>
                  <button
                    type="button"
                    onClick={() => setDrawerTab("messages")}
                    className="h-10 px-5 rounded-full border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-xs font-bold text-[#0C2B4E] dark:text-white transition cursor-pointer"
                  >
                    Message
                  </button>
                </div>
              ) : (
                <>
                  <div className="flex items-center gap-2">
                    {drawerTarget.status !== "short" && (
                      <button
                        type="button"
                        onClick={() => handleShortlist(drawerTarget)}
                        className="h-10 px-4 rounded-full border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-xs font-bold text-[#0C2B4E] dark:text-white transition cursor-pointer flex items-center gap-1.5"
                      >
                        <Star className="size-3.5 text-purple-600" />
                        <span>Shortlist</span>
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => handleOpenSelectModal(drawerTarget)}
                      className="h-10 px-5 rounded-full bg-[#0C2B4E] hover:bg-[#09223e] text-white text-xs font-bold font-heading transition cursor-pointer flex items-center gap-1.5"
                    >
                      <Check className="size-3.5" />
                      <span>Select Applicant</span>
                    </button>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setDrawerTab("messages")}
                      className="h-10 px-4 rounded-full text-xs font-bold text-[#0C2B4E] dark:text-white hover:bg-slate-100 transition cursor-pointer"
                    >
                      Message
                    </button>
                    <button
                      type="button"
                      onClick={() => handleOpenDeclineModal(drawerTarget)}
                      className="h-10 px-4 rounded-full text-xs font-bold text-[#B42318] hover:bg-red-50 transition cursor-pointer"
                    >
                      Decline
                    </button>
                  </div>
                </>
              )}
            </div>
          </aside>
        </div>
      )}

      {/* ========================================================================= */}
      {/* 7. ACTION MODALS                                                          */}
      {/* ========================================================================= */}

      {/* MODAL 1: SELECT APPLICANT */}
      {pendingModal?.kind === "select" && (
        <Modal
          open={Boolean(pendingModal)}
          onClose={() => setPendingModal(null)}
          title={
            pendingModal.replacingApp
              ? `${pendingModal.app.listingName} is already Under Offer`
              : "Select this applicant?"
          }
          size="md"
        >
          <div className="space-y-4 text-xs sm:text-sm">
            {pendingModal.replacingApp ? (
              <>
                <p className="text-slate-600 dark:text-slate-300 leading-relaxed">
                  <strong>{pendingModal.replacingApp.guestName}</strong> is currently the selected applicant.
                  To progress <strong>{pendingModal.app.guestName}</strong> instead, provide a reason.
                </p>
                <div className="space-y-1">
                  <label className="text-xs font-bold text-slate-700 dark:text-slate-300 block">
                    Reason for replacement (required)
                  </label>
                  <select
                    value={replaceReason}
                    onChange={(e) => {
                      setReplaceReason(e.target.value);
                      setReplaceReasonError(false);
                    }}
                    className="w-full h-11 px-3 rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-xs"
                  >
                    <option value="">Choose a reason</option>
                    <option value="Previous applicant didn’t respond">Previous applicant didn’t respond</option>
                    <option value="Previous applicant asked to step back">Previous applicant asked to step back</option>
                    <option value="Other">Other</option>
                  </select>
                  {replaceReasonError && (
                    <p className="text-xs text-red-600 font-bold">
                      Choose a reason to replace the selected applicant.
                    </p>
                  )}
                </div>
                <div className="flex justify-end gap-2 pt-2 border-t border-slate-100 dark:border-slate-800">
                  <button
                    type="button"
                    onClick={() => setPendingModal(null)}
                    className="h-10 px-5 rounded-full border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-xs font-bold cursor-pointer"
                  >
                    Keep current offer
                  </button>
                  <button
                    type="button"
                    onClick={handleConfirmSelect}
                    className="h-10 px-5 rounded-full bg-[#0C2B4E] hover:bg-[#09223e] text-white text-xs font-bold cursor-pointer"
                  >
                    Replace and select
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="text-slate-600 dark:text-slate-300 leading-relaxed">
                  You are choosing <strong>{pendingModal.app.guestName}</strong> to progress for{" "}
                  <strong>{pendingModal.app.listingName}</strong>.<br />
                  Move-in:{" "}
                  {pendingModal.app.desiredMoveIn
                    ? formatDate(pendingModal.app.desiredMoveIn)
                    : "Flexible"}
                  . Rent: {pendingModal.app.rentText}/month.
                </p>
                <p className="text-slate-500 text-xs">
                  Selecting an applicant does not create a rental agreement or start occupancy. It moves the
                  application into the Offer stage and sets the room to Under Offer.
                </p>
                <div className="flex justify-end gap-2 pt-2 border-t border-slate-100 dark:border-slate-800">
                  <button
                    type="button"
                    onClick={() => setPendingModal(null)}
                    className="h-10 px-5 rounded-full border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-xs font-bold cursor-pointer"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={handleConfirmSelect}
                    className="h-10 px-5 rounded-full bg-[#0C2B4E] hover:bg-[#09223e] text-white text-xs font-bold cursor-pointer"
                  >
                    Select applicant
                  </button>
                </div>
              </>
            )}
          </div>
        </Modal>
      )}

      {/* MODAL 2: DECLINE APPLICATION */}
      {pendingModal?.kind === "decline" && (
        <Modal
          open={Boolean(pendingModal)}
          onClose={() => setPendingModal(null)}
          title="Decline this application?"
          size="md"
        >
          <div className="space-y-4 text-xs sm:text-sm">
            <p className="text-slate-600 dark:text-slate-300 leading-relaxed">
              <strong>{pendingModal.app.guestName}</strong> will get a neutral message that the application wasn’t
              successful. Your internal reason isn’t shared.
            </p>
            <div className="space-y-1">
              <label className="text-xs font-bold text-slate-700 dark:text-slate-300 block">
                Internal reason (optional)
              </label>
              <select
                value={declineReason}
                onChange={(e) => setDeclineReason(e.target.value)}
                className="w-full h-11 px-3 rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-xs"
              >
                <option value="Room no longer available">Room no longer available</option>
                <option value="Another applicant progressed">Another applicant progressed</option>
                <option value="Application incomplete">Application incomplete</option>
                <option value="Configured rental requirement not met">
                  Configured rental requirement not met
                </option>
                <option value="Other">Other</option>
              </select>
            </div>
            <p className="text-xs text-slate-400">Never decline on the basis of protected characteristics.</p>
            <div className="flex justify-end gap-2 pt-2 border-t border-slate-100 dark:border-slate-800">
              <button
                type="button"
                onClick={() => setPendingModal(null)}
                className="h-10 px-5 rounded-full border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-xs font-bold cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmDecline}
                className="h-10 px-5 rounded-full bg-[#B42318] hover:bg-red-700 text-white text-xs font-bold cursor-pointer"
              >
                Decline application
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* MODAL 3: REQUEST INFORMATION */}
      {pendingModal?.kind === "reqinfo" && (
        <Modal
          open={Boolean(pendingModal)}
          onClose={() => setPendingModal(null)}
          title="Request information"
          size="md"
        >
          <div className="space-y-4 text-xs sm:text-sm">
            <p className="text-slate-600 dark:text-slate-300 leading-relaxed">
              Ask <strong>{pendingModal.app.guestName}</strong> for a configured item. Don’t request identity documents in
              chat — identity is verified by Zoiko Rooms.
            </p>
            <div className="space-y-1">
              <label className="text-xs font-bold text-slate-700 dark:text-slate-300 block">Item</label>
              <select
                value={reqInfoItem}
                onChange={(e) => setReqInfoItem(e.target.value)}
                className="w-full h-11 px-3 rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-xs"
              >
                <option value="Rental reference">Rental reference</option>
                <option value="Employer reference">Employer reference</option>
                <option value="Proof of affordability (where configured)">
                  Proof of affordability (where configured)
                </option>
                <option value="Guarantor details (where configured)">
                  Guarantor details (where configured)
                </option>
              </select>
            </div>
            <div className="flex justify-end gap-2 pt-2 border-t border-slate-100 dark:border-slate-800">
              <button
                type="button"
                onClick={() => setPendingModal(null)}
                className="h-10 px-5 rounded-full border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-xs font-bold cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmReqInfo}
                className="h-10 px-5 rounded-full bg-[#0C2B4E] hover:bg-[#09223e] text-white text-xs font-bold cursor-pointer"
              >
                Send request
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* MODAL 4: OFFER AGREEMENT PANEL */}
      {offerAgreementTarget && (
        <HostOfferAgreementPanel
          open={Boolean(offerAgreementTarget)}
          onClose={() => setOfferAgreementTarget(null)}
          applicationId={offerAgreementTarget.id}
          offerId={offerAgreementTarget.offer?.id ?? null}
          renterName={offerAgreementTarget.guestName}
          onChanged={loadData}
        />
      )}

      <Toast toast={toast} />
    </div>
  );
}
