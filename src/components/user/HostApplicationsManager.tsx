"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Building2,
  Calendar,
  Check,
  CheckCircle2,
  ChevronDown,
  Clock,
  Eye,
  FileSignature,
  Filter,
  Inbox,
  Mail,
  RefreshCw,
  Search,
  Star,
  ThumbsDown,
  ThumbsUp,
  User,
  X,
  XCircle,
} from "lucide-react";
import { ApiError } from "@/lib/api-client";
import { Application } from "@/lib/types";
import { formatDate } from "@/lib/utils";
import { decideHostedApplication, errorMessage, listHostedApplications } from "@/lib/user-api";
import { Toast, useToast } from "@/components/user/ui";
import { HostOfferAgreementPanel } from "@/components/user/HostOfferAgreementPanel";
import { Modal } from "@/components/ui/Modal";

function getApplicationDisplay(app: Application): {
  statusText: string;
  badgeTone: "new" | "in_review" | "approved" | "rejected" | "withdrawn";
  isUrgent: boolean;
} {
  const latestDecision = app.decisions[app.decisions.length - 1];

  if (app.status === "WITHDRAWN") {
    return { statusText: "Withdrawn", badgeTone: "withdrawn", isUrgent: false };
  }
  if (!latestDecision) {
    return { statusText: "New", badgeTone: "new", isUrgent: true };
  }
  if (latestDecision.decision === "APPROVED") {
    return { statusText: "Approved", badgeTone: "approved", isUrgent: false };
  }
  return { statusText: "Rejected", badgeTone: "rejected", isUrgent: false };
}

export function HostApplicationsManager() {
  const [applications, setApplications] = useState<Application[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  // Filters & Search matching Figma exact design
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("ALL_ACTIVE");
  const [propertyFilter, setPropertyFilter] = useState<string>("ALL");
  const [roomFilter, setRoomFilter] = useState<string>("ALL");
  const [moveInFilter, setMoveInFilter] = useState<string>("ANY");
  const [roomStatusFilter, setRoomStatusFilter] = useState<string>("ANY");
  const [sortOption, setSortOption] = useState<string>("ACTION_REQUIRED");
  const [viewTab, setViewTab] = useState<"ALL" | "BY_ROOM">("ALL");

  // Detail & Modals
  const [inspectTarget, setInspectTarget] = useState<Application | null>(null);
  const [rejectTarget, setRejectTarget] = useState<Application | null>(null);
  const [rejectNote, setRejectNote] = useState("");
  const [offerTarget, setOfferTarget] = useState<Application | null>(null);

  const { toast, showToast } = useToast();

  // Load real data from FastAPI backend
  const loadApplications = useCallback(async () => {
    setLoading(true);
    setConnectionError(null);
    try {
      const data = await listHostedApplications();
      setApplications(data || []);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setConnectionError("AUTH_REQUIRED");
      } else {
        const msg = errorMessage(err, "Could not load applications from backend.");
        setConnectionError(msg);
        showToast(msg, "error");
      }
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    loadApplications();
  }, [loadApplications]);

  // Real KPI Metrics derived strictly from backend applications matching Figma
  const metrics = useMemo(() => {
    let newCount = 0;
    let inReviewCount = 0;
    let shortlistedCount = 0;
    let actionRequiredCount = 0;

    applications.forEach((app) => {
      const { badgeTone, isUrgent } = getApplicationDisplay(app);
      if (badgeTone === "new") newCount++;
      if (app.status === "SUBMITTED" || badgeTone === "in_review") inReviewCount++;
      if (badgeTone === "approved") shortlistedCount++;
      if (isUrgent || badgeTone === "new") actionRequiredCount++;
    });

    return {
      newCount,
      inReviewCount,
      shortlistedCount,
      actionRequiredCount,
      total: applications.length,
    };
  }, [applications]);

  // Filtered applications list matching Figma filters
  const filteredApplications = useMemo(() => {
    let result = [...applications];

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase().trim();
      result = result.filter(
        (a) =>
          a.guestName.toLowerCase().includes(q) ||
          a.guestEmail.toLowerCase().includes(q) ||
          (a.listingName && a.listingName.toLowerCase().includes(q)) ||
          a.id.toString().includes(q) ||
          (a.message && a.message.toLowerCase().includes(q))
      );
    }

    if (statusFilter !== "ALL") {
      result = result.filter((a) => {
        const { badgeTone, isUrgent } = getApplicationDisplay(a);
        if (statusFilter === "ALL_ACTIVE") return badgeTone !== "rejected" && badgeTone !== "withdrawn";
        if (statusFilter === "NEW") return badgeTone === "new";
        if (statusFilter === "IN_REVIEW") return badgeTone === "in_review" || badgeTone === "new";
        if (statusFilter === "SHORTLISTED") return badgeTone === "approved";
        if (statusFilter === "SELECTED") return Boolean(a.offer);
        if (statusFilter === "ACTION_REQUIRED") return isUrgent;
        if (statusFilter === "REJECTED") return badgeTone === "rejected";
        if (statusFilter === "WITHDRAWN") return badgeTone === "withdrawn";
        return true;
      });
    }

    if (propertyFilter !== "ALL") {
      result = result.filter((a) => (a.listingName || a.listingId) === propertyFilter);
    }

    if (roomFilter !== "ALL") {
      result = result.filter((a) => (a.listingName || a.listingId) === roomFilter);
    }

    if (moveInFilter !== "ANY") {
      const now = new Date();
      result = result.filter((a) => {
        if (!a.desiredMoveIn) return false;
        const d = new Date(a.desiredMoveIn);
        const diffDays = (d.getTime() - now.getTime()) / (1000 * 3600 * 24);
        if (moveInFilter === "IMMEDIATE") return diffDays <= 7;
        if (moveInFilter === "NEXT_30") return diffDays <= 30;
        if (moveInFilter === "NEXT_60") return diffDays <= 60;
        return true;
      });
    }

    result.sort((a, b) => {
      if (sortOption === "ACTION_REQUIRED") {
        const aUrgent = getApplicationDisplay(a).isUrgent ? 1 : 0;
        const bUrgent = getApplicationDisplay(b).isUrgent ? 1 : 0;
        if (bUrgent !== aUrgent) return bUrgent - aUrgent;
        return new Date(b.submittedAt).getTime() - new Date(a.submittedAt).getTime();
      }
      if (sortOption === "NEWEST") {
        return new Date(b.submittedAt).getTime() - new Date(a.submittedAt).getTime();
      }
      if (sortOption === "OLDEST") {
        return new Date(a.submittedAt).getTime() - new Date(b.submittedAt).getTime();
      }
      if (sortOption === "MOVE_IN") {
        const aDate = a.desiredMoveIn ? new Date(a.desiredMoveIn).getTime() : Infinity;
        const bDate = b.desiredMoveIn ? new Date(b.desiredMoveIn).getTime() : Infinity;
        return aDate - bDate;
      }
      return 0;
    });

    return result;
  }, [applications, searchQuery, statusFilter, propertyFilter, roomFilter, moveInFilter, sortOption]);

  // Unique properties and rooms extracted dynamically from real application records
  const uniqueProperties = useMemo(() => {
    const set = new Set<string>();
    applications.forEach((a) => {
      if (a.listingName) set.add(a.listingName);
      else if (a.listingId) set.add(a.listingId);
    });
    return Array.from(set);
  }, [applications]);

  const uniqueRooms = useMemo(() => {
    const set = new Set<string>();
    applications.forEach((a) => {
      if (a.listingName) set.add(a.listingName);
      else if (a.listingId) set.add(a.listingId);
    });
    return Array.from(set);
  }, [applications]);

  // Real Approve action -> updates backend DB
  async function handleApprove(appId: number) {
    setBusyId(appId);
    try {
      const updated = await decideHostedApplication(appId, { decision: "APPROVED" });
      setApplications((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
      showToast("Application approved! You can now create an offer agreement.", "success");
      setInspectTarget(null);
    } catch (err) {
      showToast(errorMessage(err, "Could not approve this application."), "error");
    } finally {
      setBusyId(null);
    }
  }

  // Real Reject action -> updates backend DB
  async function handleConfirmReject() {
    if (!rejectTarget) return;
    setBusyId(rejectTarget.id);
    try {
      const updated = await decideHostedApplication(rejectTarget.id, {
        decision: "REJECTED",
        note: rejectNote.trim(),
      });
      setApplications((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
      showToast("Application rejected.");
      setRejectTarget(null);
      setInspectTarget(null);
    } catch (err) {
      showToast(errorMessage(err, "Could not reject this application."), "error");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="w-full space-y-6 font-sans">
      {/* 1. Header Section */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pt-1">
        <div className="space-y-1">
          <span className="text-[#D80B0B] dark:text-rose-400 text-xs font-bold font-heading uppercase tracking-wider block">
            Host workspace
          </span>
          <h1 className="text-3xl sm:text-4xl font-extrabold text-[#0C2B4E] dark:text-white font-heading tracking-tight leading-10">
            Applications
          </h1>
          <p className="text-slate-500 dark:text-slate-400 text-sm sm:text-base font-normal max-w-[760px] leading-relaxed">
           Review applicants, compare relevant rental details, communicate with renters, and progress selected
applicants to the next stage.
          </p>
        </div>

       
      </div>

      {/* Backend Status / Notice */}
      {connectionError === "AUTH_REQUIRED" ? (
        <div className="p-4 bg-sky-50 dark:bg-sky-950/30 border border-sky-200 dark:border-sky-900 rounded-2xl flex items-start gap-3 text-xs text-sky-900 dark:text-sky-200">
          <CheckCircle2 className="size-5 text-emerald-600 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <p className="font-bold text-sm text-sky-950 dark:text-sky-100">
              FastAPI Backend Connected & Running
            </p>
            <p className="text-slate-600 dark:text-slate-300">
              To fetch and manage your live applications from the database, please{" "}
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
                Register a Host Account
              </Link>
              .
            </p>
          </div>
        </div>
      ) : connectionError ? (
        <div className="p-4 bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-900 rounded-2xl flex items-start gap-3 text-xs text-amber-800 dark:text-amber-200">
          <AlertTriangle className="size-5 text-amber-600 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <p className="font-bold text-sm">Backend API not connected</p>
            <p>
              Make sure your FastAPI server is running on{" "}
              <code className="bg-amber-100 px-1 rounded">http://localhost:8000</code>.
            </p>
          </div>
        </div>
      ) : null}

      {/* 2. Real KPI Metric Cards matching Figma */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
        {/* New Card */}
        <button
          onClick={() => setStatusFilter((curr) => (curr === "NEW" ? "ALL_ACTIVE" : "NEW"))}
          className={`text-left p-4 bg-white dark:bg-slate-900 rounded-[20px] border-2 transition-all shadow-sm flex items-center justify-between cursor-pointer ${
            statusFilter === "NEW"
              ? "border-emerald-500 ring-4 ring-emerald-500/15"
              : "border-slate-200 dark:border-slate-800 hover:border-slate-300"
          }`}
        >
          <div className="space-y-1">
            <span className="text-3xl font-extrabold font-heading text-emerald-600 dark:text-emerald-400">
              {metrics.newCount}
            </span>
            <h3 className="text-[#0C2B4E] dark:text-white text-base font-extrabold font-heading">New</h3>
            <p className="text-slate-500 dark:text-slate-400 text-xs">Not yet reviewed</p>
          </div>
          <div className="size-3 rounded-full bg-emerald-500 self-start mt-1 mr-1" />
        </button>

        {/* In Review Card */}
        <button
          onClick={() => setStatusFilter((curr) => (curr === "IN_REVIEW" ? "ALL_ACTIVE" : "IN_REVIEW"))}
          className={`text-left p-4 bg-white dark:bg-slate-900 rounded-[20px] border-2 transition-all shadow-sm flex items-center justify-between cursor-pointer ${
            statusFilter === "IN_REVIEW"
              ? "border-blue-500 ring-4 ring-blue-500/15"
              : "border-slate-200 dark:border-slate-800 hover:border-slate-300"
          }`}
        >
          <div className="space-y-1">
            <span className="text-3xl font-extrabold font-heading text-[#1D4ED8] dark:text-blue-400">
              {metrics.inReviewCount}
            </span>
            <h3 className="text-[#0C2B4E] dark:text-white text-base font-extrabold font-heading">In Review</h3>
            <p className="text-slate-500 dark:text-slate-400 text-xs">You&apos;ve started review</p>
          </div>
          <Eye className="size-5 text-blue-500 self-start mt-1" />
        </button>

        {/* Shortlisted Card */}
        <button
          onClick={() => setStatusFilter((curr) => (curr === "SHORTLISTED" ? "ALL_ACTIVE" : "SHORTLISTED"))}
          className={`text-left p-4 bg-white dark:bg-slate-900 rounded-[20px] border-2 transition-all shadow-sm flex items-center justify-between cursor-pointer ${
            statusFilter === "SHORTLISTED"
              ? "border-purple-500 ring-4 ring-purple-500/15"
              : "border-slate-200 dark:border-slate-800 hover:border-slate-300"
          }`}
        >
          <div className="space-y-1">
            <span className="text-3xl font-extrabold font-heading text-purple-600 dark:text-purple-400">
              {metrics.shortlistedCount}
            </span>
            <h3 className="text-[#0C2B4E] dark:text-white text-base font-extrabold font-heading">Shortlisted</h3>
            <p className="text-slate-500 dark:text-slate-400 text-xs">Kept for consideration</p>
          </div>
          <Star className="size-5 text-purple-500 self-start mt-1" />
        </button>

        {/* Action Required Card */}
        <button
          onClick={() => setStatusFilter((curr) => (curr === "ACTION_REQUIRED" ? "ALL_ACTIVE" : "ACTION_REQUIRED"))}
          className={`text-left p-4 bg-white dark:bg-slate-900 rounded-[20px] border-2 transition-all shadow-sm flex items-center justify-between cursor-pointer ${
            statusFilter === "ACTION_REQUIRED"
              ? "border-[#D80B0B] ring-4 ring-rose-500/15"
              : "border-slate-200 dark:border-slate-800 hover:border-slate-300"
          }`}
        >
          <div className="space-y-1">
            <span className="text-3xl font-extrabold font-heading text-[#D80B0B] dark:text-rose-400">
              {metrics.actionRequiredCount}
            </span>
            <h3 className="text-[#0C2B4E] dark:text-white text-base font-extrabold font-heading">Action Required</h3>
            <p className="text-slate-500 dark:text-slate-400 text-xs">Deadline or conflict</p>
          </div>
          <AlertTriangle className="size-5 text-[#D80B0B] self-start mt-1" />
        </button>
      </div>

      {/* Figma Urgent Attention Banner (When items require action) */}
      {metrics.actionRequiredCount > 0 && (
        <div className="p-4 bg-rose-50/70 dark:bg-rose-950/30 border border-rose-200 dark:border-rose-900 rounded-[20px] flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 shadow-2xs">
          <div className="flex items-start gap-3">
            <AlertTriangle className="size-5 text-[#D80B0B] shrink-0 mt-0.5" />
            <div className="space-y-0.5">
              <p className="font-bold text-sm text-[#0C2B4E] dark:text-white">
                {metrics.actionRequiredCount} {metrics.actionRequiredCount === 1 ? "application needs" : "applications need"} your attention
              </p>
              <p className="text-xs text-slate-600 dark:text-slate-300">
                Review pending applicant responses and active deadlines.
              </p>
            </div>
          </div>
          <button
            onClick={() => setStatusFilter("ACTION_REQUIRED")}
            className="px-4 py-2 bg-[#D80B0B] hover:bg-rose-700 text-white rounded-xl text-xs font-bold transition cursor-pointer shrink-0 shadow-sm"
          >
            Review actions
          </button>
        </div>
      )}

      {/* 3. Search and Filter Bar matching Figma exact layout (Search + 6 Filter Options) */}
      <div className="w-full bg-white dark:bg-slate-900 rounded-[24px] border border-slate-200/90 dark:border-slate-800 p-4 shadow-xs">
        <div className="flex flex-col lg:flex-row items-stretch lg:items-end gap-2.5">
          {/* 1. Search */}
          <div className="flex-1 lg:flex-[1.5] min-w-[200px]">
            <div className="relative">
              <Search className="size-4 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search applicant, application ID"
                className="w-full h-11 pl-10 pr-8 bg-[#F1F5F9] dark:bg-slate-800 border border-slate-200/80 dark:border-slate-700 rounded-[14px] text-xs text-slate-900 dark:text-slate-100 placeholder:text-slate-400 outline-none focus:ring-2 focus:ring-primary-500 font-normal transition"
              />
              {searchQuery && (
                <button
                  onClick={() => setSearchQuery("")}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 cursor-pointer"
                >
                  <X className="size-3.5" />
                </button>
              )}
            </div>
          </div>

          {/* 2. Application Status */}
          <div className="flex-1 min-w-[125px] flex flex-col gap-1">
            <span className="text-[11px] font-semibold text-[#334155] dark:text-slate-300 font-heading whitespace-nowrap">
              Application Status
            </span>
            <div className="relative">
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="w-full h-11 px-3.5 pr-8 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-[14px] text-xs text-[#1E293B] dark:text-slate-200 outline-none appearance-none cursor-pointer focus:ring-2 focus:ring-primary-500 font-normal"
              >
                <option value="ALL_ACTIVE">All active</option>
                <option value="ALL">All statuses</option>
                <option value="NEW">New</option>
                <option value="IN_REVIEW">In review</option>
                <option value="SHORTLISTED">Shortlisted</option>
                <option value="SELECTED">Selected / Offer</option>
                <option value="REJECTED">Declined</option>
                <option value="WITHDRAWN">Withdrawn</option>
              </select>
              <ChevronDown className="size-3.5 text-slate-400 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            </div>
          </div>

          {/* 3. Property */}
          <div className="flex-1 min-w-[115px] flex flex-col gap-1">
            <span className="text-[11px] font-semibold text-[#334155] dark:text-slate-300 font-heading whitespace-nowrap">
              Property
            </span>
            <div className="relative">
              <select
                value={propertyFilter}
                onChange={(e) => setPropertyFilter(e.target.value)}
                className="w-full h-11 px-3.5 pr-8 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-[14px] text-xs text-[#1E293B] dark:text-slate-200 outline-none appearance-none cursor-pointer focus:ring-2 focus:ring-primary-500 font-normal"
              >
                <option value="ALL">All properties</option>
                {uniqueProperties.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
              <ChevronDown className="size-3.5 text-slate-400 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            </div>
          </div>

          {/* 4. Room */}
          <div className="flex-1 min-w-[105px] flex flex-col gap-1">
            <span className="text-[11px] font-semibold text-[#334155] dark:text-slate-300 font-heading whitespace-nowrap">
              Room
            </span>
            <div className="relative">
              <select
                value={roomFilter}
                onChange={(e) => setRoomFilter(e.target.value)}
                className="w-full h-11 px-3.5 pr-8 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-[14px] text-xs text-[#1E293B] dark:text-slate-200 outline-none appearance-none cursor-pointer focus:ring-2 focus:ring-primary-500 font-normal"
              >
                <option value="ALL">All rooms</option>
                {uniqueRooms.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
              <ChevronDown className="size-3.5 text-slate-400 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            </div>
          </div>

          {/* 5. Move-in */}
          <div className="flex-1 min-w-[100px] flex flex-col gap-1">
            <span className="text-[11px] font-semibold text-[#334155] dark:text-slate-300 font-heading whitespace-nowrap">
              Move-in
            </span>
            <div className="relative">
              <select
                value={moveInFilter}
                onChange={(e) => setMoveInFilter(e.target.value)}
                className="w-full h-11 px-3.5 pr-8 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-[14px] text-xs text-[#1E293B] dark:text-slate-200 outline-none appearance-none cursor-pointer focus:ring-2 focus:ring-primary-500 font-normal"
              >
                <option value="ANY">Any time</option>
                <option value="IMMEDIATE">Immediate (&lt; 7 days)</option>
                <option value="NEXT_30">Next 30 days</option>
                <option value="NEXT_60">Next 60 days</option>
              </select>
              <ChevronDown className="size-3.5 text-slate-400 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            </div>
          </div>

          {/* 6. Room Status */}
          <div className="flex-1 min-w-[125px] flex flex-col gap-1">
            <span className="text-[11px] font-semibold text-[#334155] dark:text-slate-300 font-heading whitespace-nowrap">
              Room Status
            </span>
            <div className="relative">
              <select
                value={roomStatusFilter}
                onChange={(e) => setRoomStatusFilter(e.target.value)}
                className="w-full h-11 px-3.5 pr-8 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-[14px] text-xs text-[#1E293B] dark:text-slate-200 outline-none appearance-none cursor-pointer focus:ring-2 focus:ring-primary-500 font-normal"
              >
                <option value="ANY">Any room status</option>
                <option value="AVAILABLE">Available</option>
                <option value="UNDER_OFFER">Under Offer</option>
                <option value="OCCUPIED">Occupied</option>
              </select>
              <ChevronDown className="size-3.5 text-slate-400 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            </div>
          </div>

          {/* 7. Sort */}
          <div className="flex-1 min-w-[130px] flex flex-col gap-1">
            <span className="text-[11px] font-semibold text-[#334155] dark:text-slate-300 font-heading whitespace-nowrap">
              Sort
            </span>
            <div className="relative">
              <select
                value={sortOption}
                onChange={(e) => setSortOption(e.target.value)}
                className="w-full h-11 px-3.5 pr-8 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-[14px] text-xs text-[#1E293B] dark:text-slate-200 outline-none appearance-none cursor-pointer focus:ring-2 focus:ring-primary-500 font-normal"
              >
                <option value="ACTION_REQUIRED">Action required first</option>
                <option value="NEWEST">Newest submission</option>
                <option value="OLDEST">Oldest submission</option>
                <option value="MOVE_IN">Move-in date</option>
              </select>
              <ChevronDown className="size-3.5 text-slate-400 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            </div>
          </div>
        </div>
      </div>

      {/* Segmented View Tabs: [All Applications (N)] [By Room] and Total Counter */}
      <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
        <div className="inline-flex items-center gap-1.5 p-1 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-2xs">
          <button
            onClick={() => setViewTab("ALL")}
            className={`px-4 py-2 rounded-xl text-xs font-bold transition cursor-pointer ${
              viewTab === "ALL"
                ? "bg-[#0C2B4E] text-white shadow-sm"
                : "text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white"
            }`}
          >
            All Applications ({applications.length})
          </button>
          <button
            onClick={() => setViewTab("BY_ROOM")}
            className={`px-4 py-2 rounded-xl text-xs font-bold transition cursor-pointer ${
              viewTab === "BY_ROOM"
                ? "bg-[#0C2B4E] text-white shadow-sm"
                : "text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white"
            }`}
          >
            By Room
          </button>
        </div>

        <span className="text-xs font-semibold text-slate-500 dark:text-slate-400">
          {filteredApplications.length} {filteredApplications.length === 1 ? "application" : "applications"}
        </span>
      </div>

      {/* 4. Applications List / Table Container */}
      <div className="w-full bg-white dark:bg-slate-900 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden">
        {/* Table Subheader */}
        <div className="px-5 py-3.5 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between">
          <span className="text-xs font-bold text-slate-700 dark:text-slate-300 font-heading">
            Live Applications ({filteredApplications.length})
          </span>
          <span className="text-xs text-slate-400">
            {applications.length} total in database
          </span>
        </div>

        {/* Empty State from Figma Frame (Shown when 0 applications in DB or 0 match filter) */}
        {filteredApplications.length === 0 ? (
          <div className="p-12 text-center flex flex-col items-center justify-center gap-4">
            <div className="size-16 rounded-3xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-slate-400 shadow-inner">
              <Inbox className="size-8" />
            </div>
            <div className="space-y-1">
              <h3 className="text-slate-900 dark:text-white font-extrabold text-lg font-heading">
                {applications.length === 0 ? "No applications yet" : "No applications match your filter"}
              </h3>
              <p className="text-slate-500 dark:text-slate-400 text-xs max-w-md mx-auto">
                {applications.length === 0
                  ? "Applications from renters will appear here when people apply for your rooms."
                  : "Try clearing your search query or status filter to see all active applications."}
              </p>
            </div>
            {applications.length === 0 ? (
              <Link
                href="/account/host/listings"
                className="mt-2 px-4 py-2.5 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-[#0C2B4E] dark:text-white text-xs font-bold font-heading rounded-xl shadow-2xs transition"
              >
                View Listings
              </Link>
            ) : (
              <button
                onClick={() => {
                  setSearchQuery("");
                  setStatusFilter("ALL_ACTIVE");
                  setPropertyFilter("ALL");
                  setRoomFilter("ALL");
                  setMoveInFilter("ANY");
                  setRoomStatusFilter("ANY");
                  setSortOption("ACTION_REQUIRED");
                }}
                className="mt-2 text-xs font-bold text-primary-600 hover:underline cursor-pointer"
              >
                Reset filters
              </button>
            )}
          </div>
        ) : (
          /* Table of Real Backend Applications */
          <div className="divide-y divide-slate-100 dark:divide-slate-800">
            {/* Header labels */}
            <div className="hidden lg:flex px-5 py-3 bg-slate-50 dark:bg-slate-800/40 text-xs font-bold font-heading uppercase tracking-wide text-slate-500">
              <div className="w-56 shrink-0">Applicant</div>
              <div className="w-52 shrink-0">Listing / Room</div>
              <div className="w-44 shrink-0">Requested move-in</div>
              <div className="w-36 shrink-0">Status</div>
              <div className="flex-1 text-right">Actions</div>
            </div>

            {filteredApplications.map((app) => {
              const { statusText, badgeTone } = getApplicationDisplay(app);
              const initials =
                app.guestName
                  .split(" ")
                  .map((n) => n[0])
                  .join("")
                  .slice(0, 2)
                  .toUpperCase() || "AP";

              const isPending = app.status === "SUBMITTED" && app.decisions.length === 0;
              const isApproved = app.decisions[app.decisions.length - 1]?.decision === "APPROVED";

              return (
                <div
                  key={app.id}
                  onClick={() => setInspectTarget(app)}
                  className="px-5 py-4 hover:bg-slate-50/80 dark:hover:bg-slate-800/50 transition flex flex-col lg:flex-row items-start lg:items-center justify-between gap-4 cursor-pointer group"
                >
                  {/* Applicant column */}
                  <div className="w-full lg:w-56 shrink-0 flex items-start gap-3">
                    <div className="size-11 rounded-3xl bg-slate-200 dark:bg-slate-700 flex items-center justify-center font-extrabold font-heading text-base text-[#0C2B4E] dark:text-white shrink-0 group-hover:scale-105 transition">
                      {initials}
                    </div>
                    <div className="min-w-0 flex-1">
                      <h4 className="text-base font-extrabold font-heading text-slate-900 dark:text-white leading-tight group-hover:text-primary-600 transition">
                        {app.guestName}
                      </h4>
                      <p className="text-xs text-slate-500 mt-0.5 truncate">{app.guestEmail}</p>
                      <p className="text-[11px] text-slate-400 mt-0.5">
                        Applied {formatDate(app.submittedAt)}
                      </p>
                    </div>
                  </div>

                  {/* Listing / Room column */}
                  <div className="w-full lg:w-52 shrink-0 flex flex-col gap-0.5">
                    <p className="text-sm font-bold text-slate-900 dark:text-white">
                      {app.listingName || app.listingId}
                    </p>
                    <span className="text-xs text-slate-500">Listing ID: #{app.listingId}</span>
                  </div>

                  {/* Requested move-in column */}
                  <div className="w-full lg:w-44 shrink-0 flex flex-col gap-0.5">
                    <p className="text-sm font-bold text-slate-900 dark:text-white">
                      {app.desiredMoveIn ? formatDate(app.desiredMoveIn) : "Flexible move-in"}
                    </p>
                    {app.message && (
                      <p className="text-xs text-slate-500 truncate max-w-[160px]">
                        &ldquo;{app.message}&rdquo;
                      </p>
                    )}
                  </div>

                  {/* Status column */}
                  <div className="w-full lg:w-36 shrink-0">
                    {badgeTone === "new" && (
                      <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-blue-50 dark:bg-blue-950/50 text-[#1D4ED8] dark:text-blue-300 text-xs font-extrabold font-heading uppercase tracking-wide">
                        <span className="size-1.5 rounded-full bg-blue-500" />
                        New
                      </span>
                    )}
                    {badgeTone === "approved" && (
                      <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-[#0C2B4E] text-white text-xs font-extrabold font-heading uppercase tracking-wide">
                        <Check className="size-3" />
                        Approved
                      </span>
                    )}
                    {badgeTone === "rejected" && (
                      <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-red-50 text-red-600 text-xs font-extrabold font-heading uppercase tracking-wide">
                        <X className="size-3" />
                        Rejected
                      </span>
                    )}
                    {badgeTone === "withdrawn" && (
                      <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-slate-100 text-slate-500 text-xs font-extrabold font-heading uppercase tracking-wide">
                        Withdrawn
                      </span>
                    )}
                  </div>

                  {/* Action Buttons */}
                  <div
                    className="w-full lg:w-auto flex-1 flex items-center justify-start lg:justify-end gap-2"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {isPending ? (
                      <>
                        <button
                          disabled={busyId === app.id}
                          onClick={() => {
                            setRejectTarget(app);
                            setRejectNote("");
                          }}
                          className="px-3 py-2 text-xs font-bold text-red-600 hover:bg-red-50 rounded-xl transition cursor-pointer"
                        >
                          Decline
                        </button>
                        <button
                          disabled={busyId === app.id}
                          onClick={() => handleApprove(app.id)}
                          className="px-4 py-2 bg-[#0C2B4E] hover:bg-[#123e6f] text-white font-bold text-xs font-heading rounded-xl shadow-sm transition cursor-pointer"
                        >
                          Approve
                        </button>
                      </>
                    ) : isApproved ? (
                      <button
                        onClick={() => setOfferTarget(app)}
                        className="px-4 py-2 bg-[#D80B0B] hover:bg-red-700 text-white font-bold text-xs font-heading rounded-xl shadow-sm transition flex items-center gap-1.5 cursor-pointer"
                      >
                        <FileSignature className="size-3.5" />
                        Manage Offer
                      </button>
                    ) : (
                      <button
                        onClick={() => setInspectTarget(app)}
                        className="px-3.5 py-2 text-xs font-semibold text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white"
                      >
                        View Details
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 5. Detail / Review Modal */}
      {inspectTarget && (
        <Modal
          open={Boolean(inspectTarget)}
          onClose={() => setInspectTarget(null)}
          title={`Application Details · ${inspectTarget.guestName}`}
        >
          <div className="space-y-4">
            <div className="p-4 bg-slate-50 dark:bg-slate-800 rounded-2xl flex items-center justify-between">
              <div>
                <h4 className="font-extrabold text-base text-slate-900 dark:text-white font-heading">
                  {inspectTarget.guestName}
                </h4>
                <p className="text-xs text-slate-500">{inspectTarget.guestEmail}</p>
              </div>
              <span className="text-xs text-slate-400">Application #{inspectTarget.id}</span>
            </div>

            <div className="grid grid-cols-2 gap-3 text-xs">
              <div className="p-3 bg-slate-50 dark:bg-slate-800 rounded-xl">
                <span className="text-slate-400 font-semibold block uppercase">Listing</span>
                <span className="text-slate-900 dark:text-white font-bold text-sm block mt-0.5">
                  {inspectTarget.listingName || inspectTarget.listingId}
                </span>
              </div>
              <div className="p-3 bg-slate-50 dark:bg-slate-800 rounded-xl">
                <span className="text-slate-400 font-semibold block uppercase">Desired Move-in</span>
                <span className="text-slate-900 dark:text-white font-bold text-sm block mt-0.5">
                  {inspectTarget.desiredMoveIn ? formatDate(inspectTarget.desiredMoveIn) : "Flexible"}
                </span>
              </div>
            </div>

            {inspectTarget.message && (
              <div className="p-3.5 bg-slate-50 dark:bg-slate-800 rounded-xl text-xs">
                <span className="text-slate-400 font-semibold block uppercase mb-1">Renter Statement</span>
                <p className="text-slate-700 dark:text-slate-300 italic">
                  &ldquo;{inspectTarget.message}&rdquo;
                </p>
              </div>
            )}

            {/* Decisions History */}
            {inspectTarget.decisions.length > 0 && (
              <div className="p-3 bg-slate-50 dark:bg-slate-800 rounded-xl text-xs space-y-1">
                <span className="text-slate-400 font-semibold block uppercase">Decision History</span>
                {inspectTarget.decisions.map((d) => (
                  <div key={d.id} className="flex justify-between py-1 border-t border-slate-200/60 dark:border-slate-700">
                    <span className="font-bold">{d.decision}</span>
                    <span className="text-slate-400">{formatDate(d.decidedAt)}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Footer action buttons */}
            <div className="pt-2 flex justify-between gap-2 border-t border-slate-100 dark:border-slate-800">
              <button
                onClick={() => setInspectTarget(null)}
                className="px-3.5 py-2 text-xs font-semibold text-slate-600 hover:text-slate-800 cursor-pointer"
              >
                Close
              </button>

              {inspectTarget.status === "SUBMITTED" && inspectTarget.decisions.length === 0 && (
                <div className="flex gap-2">
                  <button
                    onClick={() => {
                      setRejectTarget(inspectTarget);
                      setRejectNote("");
                    }}
                    className="px-3.5 py-2 text-xs font-bold text-red-600 hover:bg-red-50 rounded-xl cursor-pointer"
                  >
                    Decline
                  </button>
                  <button
                    disabled={busyId === inspectTarget.id}
                    onClick={() => handleApprove(inspectTarget.id)}
                    className="px-4 py-2 bg-[#0C2B4E] hover:bg-[#123e6f] text-white font-bold text-xs rounded-xl shadow-sm cursor-pointer"
                  >
                    Approve
                  </button>
                </div>
              )}
            </div>
          </div>
        </Modal>
      )}

      {/* 6. Reject Modal */}
      <Modal open={Boolean(rejectTarget)} onClose={() => setRejectTarget(null)} title="Decline Application">
        <div className="space-y-3.5">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Optionally provide a reason for the applicant. This will be visible to them.
          </p>
          <textarea
            value={rejectNote}
            onChange={(e) => setRejectNote(e.target.value)}
            rows={3}
            placeholder="e.g. We have progressed with another applicant for this specific period."
            className="w-full rounded-xl bg-slate-50 dark:bg-slate-800 p-3 text-sm text-slate-900 dark:text-slate-100 border border-slate-200 dark:border-slate-700 outline-none focus:ring-2 focus:ring-primary-500"
          />
          <div className="flex justify-end gap-2 pt-2">
            <button
              onClick={() => setRejectTarget(null)}
              className="px-4 py-2 text-xs font-semibold text-slate-600 hover:text-slate-800 cursor-pointer"
            >
              Cancel
            </button>
            <button
              disabled={busyId === rejectTarget?.id}
              onClick={handleConfirmReject}
              className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white font-bold text-xs rounded-xl flex items-center gap-1.5 cursor-pointer shadow-sm"
            >
              <XCircle className="size-3.5" /> Confirm Decline
            </button>
          </div>
        </div>
      </Modal>

      {/* 7. Real Host Offer Agreement Panel */}
      {offerTarget && (
        <HostOfferAgreementPanel
          open={Boolean(offerTarget)}
          onClose={() => setOfferTarget(null)}
          applicationId={offerTarget.id}
          offerId={offerTarget.offer?.id ?? null}
          renterName={offerTarget.guestName}
          onChanged={loadApplications}
        />
      )}

      <Toast toast={toast} />
    </div>
  );
}
