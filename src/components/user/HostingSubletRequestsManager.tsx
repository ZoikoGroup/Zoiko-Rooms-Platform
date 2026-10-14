"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Building2,
  Calendar,
  Check,
  ChevronDown,
  CircleDot,
  Clock,
  Download,
  Eye,
  FileText,
  Filter,
  HelpCircle,
  Info,
  KeyRound,
  Plus,
  RefreshCw,
  Repeat,
  Search,
  Settings,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  ThumbsDown,
  ThumbsUp,
  User,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { RoundedFilterDropdown } from "@/components/ui/RoundedFilterDropdown";
import { Toast, useToast } from "@/components/user/ui";
import { AuthorityVerificationWizard } from "@/components/user/AuthorityVerificationWizard";
import { SubletPaymentsPanel } from "@/components/user/SubletPaymentsPanel";
import { SubletRequest, Property } from "@/lib/types";
import { formatDate } from "@/lib/utils";
import {
  approveHostedSubletRequest,
  declineHostedSubletRequest,
  errorMessage,
  hostSubletDecisionRecordUrl,
  listHostedProperties,
  listHostedSubletRequests,
  requestHostedSubletMoreInfo,
  signHostedAgreement,
} from "@/lib/user-api";
import {
  AuthoritySummary,
  listMyAuthority,
} from "@/lib/authority-verification";
import {
  REPLACING_ARRANGEMENT_TYPES,
  subletArrangementTypeAdminLabel,
  subletDeclineReasonCodeLabel,
  subletRequestStatusLabel,
  subletRequestStatusTone,
} from "@/lib/status";
import { useUserSession } from "@/components/user/UserSessionContext";

/** Formats dates as e.g. "Nov 1 – Jan 31" matching the Figma mock */
function formatDateRange(start?: string | null, end?: string | null) {
  if (!start && !end) return "Dates to be confirmed";
  if (start && !end) return `From ${formatDate(start)}`;
  if (!start && end) return `Until ${formatDate(end)}`;
  try {
    const s = new Date(start!);
    const e = new Date(end!);
    const sStr = s.toLocaleDateString("en-US", { month: "short", day: "numeric" });
    const eStr = e.toLocaleDateString("en-US", { month: "short", day: "numeric" });
    return `${sStr} – ${eStr}`;
  } catch {
    return `${start} – ${end}`;
  }
}

/** Format parent end date */
function formatParentEndDate(date?: string | null) {
  if (!date) return "Ongoing tenancy";
  try {
    return `Parent ends ${new Date(date).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}`;
  } catch {
    return `Parent ends ${date}`;
  }
}

/** Extract initials from full name */
function getInitials(name?: string | null): string {
  if (!name) return "TR";
  const parts = name.trim().split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/** Status filter types */
type StatusFilterType = "ALL" | "NEW" | "IN_REVIEW" | "APPROVED" | "ACTION_REQUIRED" | "DECLINED" | "EXPIRED";
type AuthorityFilterType = "ALL" | "AUTHORIZED_OWNER" | "RENEWAL_REQUIRED" | "OWNER_REQUIRED";
type SortOptionType = "ACTION_REQUIRED_FIRST" | "NEWEST" | "OLDEST" | "REQUESTER_AZ";

export interface HostingSubletRequestsManagerProps {
  /** When true (e.g. on standalone preview route when database is empty), shows sample preview rows */
  allowPreviewFallback?: boolean;
}

export function HostingSubletRequestsManager({
  allowPreviewFallback = false,
}: HostingSubletRequestsManagerProps) {
  const { user, identityVerified } = useUserSession();
  const { toast, showToast } = useToast();

  const [loading, setLoading] = useState(true);
  const [requests, setRequests] = useState<SubletRequest[]>([]);
  const [properties, setProperties] = useState<Property[]>([]);
  const [authorityList, setAuthorityList] = useState<AuthoritySummary[]>([]);

  // Navigation tab: Current vs History
  const [activeTab, setActiveTab] = useState<"CURRENT" | "HISTORY">("CURRENT");

  // Filter states
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilterType>("ALL");
  const [propertyFilter, setPropertyFilter] = useState<string>("ALL");
  const [authorityFilter, setAuthorityFilter] = useState<AuthorityFilterType>("ALL");
  const [sortOption, setSortOption] = useState<SortOptionType>("ACTION_REQUIRED_FIRST");
  const [moreFiltersOpen, setMoreFiltersOpen] = useState(false);
  const [arrangementFilter, setArrangementFilter] = useState<string>("ALL");
  const [subtenantVerificationFilter, setSubtenantVerificationFilter] = useState<string>("ALL");

  // Sample preview state (used only when allowPreviewFallback is true and DB is empty)
  const [showSampleData, setShowSampleData] = useState(false);

  // Modal targets & form states
  const [busyId, setBusyId] = useState<number | null>(null);
  const [policySettingsOpen, setPolicySettingsOpen] = useState(false);

  // Decision & Review modal
  const [reviewTarget, setReviewTarget] = useState<SubletRequest | null>(null);
  const [reviewMode, setReviewMode] = useState<"APPROVE" | "INFO" | "DECLINE">("APPROVE");

  // Approve state
  const [approveConditions, setApproveConditions] = useState("");
  const [approveConditionList, setApproveConditionList] = useState<string[]>([]);
  const [approveConditionDraft, setApproveConditionDraft] = useState("");
  const [approveExpiresAt, setApproveExpiresAt] = useState("");
  const [approveAuthorityConfirmed, setApproveAuthorityConfirmed] = useState(false);
  const [approveStepUpPassword, setApproveStepUpPassword] = useState("");
  const [approveOverrideReason, setApproveOverrideReason] = useState("");

  // Ask for info state
  const [infoNote, setInfoNote] = useState("");
  const [infoDocumentTypes, setInfoDocumentTypes] = useState("");
  const [infoDueAt, setInfoDueAt] = useState("");

  // Decline state
  const [declineNotes, setDeclineNotes] = useState("");
  const [declineReasonCode, setDeclineReasonCode] = useState("");

  // View details modal
  const [detailsTarget, setDetailsTarget] = useState<SubletRequest | null>(null);

  // Send to owner modal
  const [sendToOwnerTarget, setSendToOwnerTarget] = useState<SubletRequest | null>(null);
  const [sendToOwnerNote, setSendToOwnerNote] = useState("");

  // Authority verification wizard modal
  const [authorityWizardTarget, setAuthorityWizardTarget] = useState<{
    propertyId: number;
    propertyLabel: string;
  } | null>(null);

  // Load data from live FastAPI + PostgreSQL backend
  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [reqs, props, auths] = await Promise.all([
        listHostedSubletRequests().catch(() => [] as SubletRequest[]),
        listHostedProperties().catch(() => [] as Property[]),
        listMyAuthority().catch(() => [] as AuthoritySummary[]),
      ]);
      setRequests(reqs);
      setProperties(props);
      setAuthorityList(auths);
    } catch (err) {
      console.warn("Could not load sublet requests data:", err);
      showToast(errorMessage(err, "Could not load sublet requests."), "error");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Derived authority status check per request
  const getAuthorityStateForRequest = useCallback(
    (req: SubletRequest): {
      type: "RENEWAL_REQUIRED" | "OWNER_REQUIRED" | "AUTHORIZED_OWNER";
      label: string;
      subtext: string;
    } => {
      // The host's current authority record for this request's property.
      const propAuth = authorityList.find(
        (a) => req.propertyId !== null && a.propertyId === req.propertyId && a.state !== "SUPERSEDED"
      );

      // Only authority that has actually lapsed blocks a decision -- EXPIRING_SOON is still valid.
      if (
        propAuth?.state === "EXPIRED" ||
        propAuth?.state === "REVOKED" ||
        propAuth?.state === "ACTION_REQUIRED" ||
        req.authorityEvidenceRef === "EXPIRED"
      ) {
        return {
          type: "RENEWAL_REQUIRED",
          label: "Renewal Required",
          subtext: "Other checks pending",
        };
      }

      // Check if agent/representative requires owner sign-off
      if (
        propAuth?.relationshipType === "AGENT" ||
        propAuth?.relationshipType === "PROPERTY_MANAGER" ||
        req.authorityEvidenceRef === "OWNER_REQUIRED"
      ) {
        return {
          type: "OWNER_REQUIRED",
          label: "Owner Required",
          subtext: "Other checks pending",
        };
      }

      // Authorized Owner
      return {
        type: "AUTHORIZED_OWNER",
        label: "Authorized Owner (you)",
        subtext: req.status === "approved" ? "No blocking issues" : "Platform checks pass",
      };
    },
    [authorityList]
  );

  // Status classification helpers
  const isRequestNew = (req: SubletRequest) =>
    req.status === "pending_admin_review" ||
    req.status === "pending_verification" ||
    req.status === "draft";

  const isRequestInReview = (req: SubletRequest) =>
    req.status === "more_information_requested" ||
    req.status === "tenant_response_submitted";

  const isRequestApproved = (req: SubletRequest) =>
    req.status === "approved";

  const isRequestActionRequired = (req: SubletRequest) => {
    const authState = getAuthorityStateForRequest(req);
    return (
      authState.type === "RENEWAL_REQUIRED" ||
      req.occupantRiskTier === "BLOCK" ||
      (isRequestNew(req) && authState.type !== "AUTHORIZED_OWNER")
    );
  };

  const isRequestHistory = (req: SubletRequest) =>
    req.status === "rejected" ||
    req.status === "withdrawn" ||
    req.status === "expired" ||
    req.status === "superseded" ||
    req.status === "cancelled_by_authority";

  // Compute KPI counts dynamically from live requests
  const counts = useMemo(() => {
    const activeReqs = requests.filter((r) => !isRequestHistory(r));
    return {
      new: activeReqs.filter(isRequestNew).length,
      inReview: activeReqs.filter(isRequestInReview).length,
      approved: activeReqs.filter(isRequestApproved).length,
      actionRequired: activeReqs.filter(isRequestActionRequired).length,
      current: activeReqs.length,
      history: requests.filter(isRequestHistory).length,
    };
  }, [requests, getAuthorityStateForRequest]);

  // Urgent action required requests
  const actionRequiredRequests = useMemo(() => {
    return requests.filter(isRequestActionRequired);
  }, [requests, isRequestActionRequired]);


  // Rounded Filter Dropdown Options
  const statusFilterOptions = useMemo(
    () => [
      { value: "ALL", label: "All statuses" },
      { value: "NEW", label: "New" },
      { value: "IN_REVIEW", label: "In Review" },
      { value: "APPROVED", label: "Approved" },
      { value: "ACTION_REQUIRED", label: "Action Required" },
      { value: "DECLINED", label: "Declined" },
      { value: "EXPIRED", label: "Expired" },
    ],
    []
  );

  const propertyFilterOptions = useMemo(
    () => [
      { value: "ALL", label: "All properties" },
      ...properties.map((p) => ({
        value: String(p.id),
        label: p.address.split(",")[0] || p.city || `Property #${p.id}`,
      })),
    ],
    [properties]
  );

  const authorityFilterOptions = useMemo(
    () => [
      { value: "ALL", label: "Any" },
      { value: "AUTHORIZED_OWNER", label: "Authorized Owner" },
      { value: "RENEWAL_REQUIRED", label: "Renewal Required" },
      { value: "OWNER_REQUIRED", label: "Owner Required" },
    ],
    []
  );

  const sortFilterOptions = useMemo(
    () => [
      { value: "ACTION_REQUIRED_FIRST", label: "Action required first" },
      { value: "NEWEST", label: "Newest first" },
      { value: "OLDEST", label: "Oldest first" },
      { value: "REQUESTER_AZ", label: "Requester (A-Z)" },
    ],
    []
  );

  const arrangementFilterOptions = useMemo(
    () => [
      { value: "ALL", label: "All arrangements" },
      ...Object.entries(subletArrangementTypeAdminLabel).map(([key, label]) => ({
        value: key,
        label,
      })),
    ],
    []
  );

  const subtenantVerificationOptions = useMemo(
    () => [
      { value: "ALL", label: "All statuses" },
      { value: "VERIFIED", label: "Verified" },
      { value: "PENDING", label: "Pending / Incomplete" },
    ],
    []
  );

  // Filter and sort requests
  const filteredRequests = useMemo(() => {
    return requests.filter((req) => {
      // Current vs History tab
      const isHist = isRequestHistory(req);
      if (activeTab === "CURRENT" && isHist) return false;
      if (activeTab === "HISTORY" && !isHist) return false;

      // Search query
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase().trim();
        const matchesName = req.currentTenantName?.toLowerCase().includes(q);
        const matchesSubtenant = req.proposedRenterName?.toLowerCase().includes(q);
        const matchesListing = req.listingName?.toLowerCase().includes(q);
        const matchesCity = req.listingCity?.toLowerCase().includes(q);
        const matchesId = `zr-sr-${req.id}`.includes(q) || String(req.id).includes(q);
        const matchesRental = `zr-r-${req.currentOccupancyId}`.includes(q) || String(req.currentOccupancyId).includes(q);
        if (!matchesName && !matchesSubtenant && !matchesListing && !matchesCity && !matchesId && !matchesRental) {
          return false;
        }
      }

      // Status filter
      if (statusFilter !== "ALL") {
        if (statusFilter === "NEW" && !isRequestNew(req)) return false;
        if (statusFilter === "IN_REVIEW" && !isRequestInReview(req)) return false;
        if (statusFilter === "APPROVED" && !isRequestApproved(req)) return false;
        if (statusFilter === "ACTION_REQUIRED" && !isRequestActionRequired(req)) return false;
        if (statusFilter === "DECLINED" && req.status !== "rejected") return false;
        if (statusFilter === "EXPIRED" && req.status !== "expired" && req.status !== "withdrawn") return false;
      }

      // Property filter
      if (propertyFilter !== "ALL") {
        if (String(req.currentOccupancyId) !== propertyFilter && req.listingName !== propertyFilter) {
          return false;
        }
      }

      // Authority filter
      if (authorityFilter !== "ALL") {
        const auth = getAuthorityStateForRequest(req);
        if (authorityFilter !== auth.type) return false;
      }

      // Arrangement filter
      if (arrangementFilter !== "ALL") {
        if (req.arrangementType !== arrangementFilter) return false;
      }

      // Subtenant verification filter
      if (subtenantVerificationFilter !== "ALL") {
        if (subtenantVerificationFilter === "VERIFIED" && req.occupantRiskTier !== "NONE") return false;
        if (subtenantVerificationFilter === "PENDING" && req.occupantRiskTier === "NONE") return false;
      }

      return true;
    }).sort((a, b) => {
      if (sortOption === "ACTION_REQUIRED_FIRST") {
        const aAct = isRequestActionRequired(a) ? 1 : 0;
        const bAct = isRequestActionRequired(b) ? 1 : 0;
        if (aAct !== bAct) return bAct - aAct;
      } else if (sortOption === "NEWEST") {
        return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime();
      } else if (sortOption === "OLDEST") {
        return new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime();
      } else if (sortOption === "REQUESTER_AZ") {
        return (a.currentTenantName || "").localeCompare(b.currentTenantName || "");
      }
      return 0;
    });
  }, [
    requests,
    activeTab,
    searchQuery,
    statusFilter,
    propertyFilter,
    authorityFilter,
    sortOption,
    arrangementFilter,
    subtenantVerificationFilter,
    getAuthorityStateForRequest,
  ]);

  // Handlers for Review & Decision
  function openReviewModal(request: SubletRequest, mode: "APPROVE" | "INFO" | "DECLINE" = "APPROVE") {
    setReviewTarget(request);
    setReviewMode(mode);
    setApproveConditions("");
    setApproveConditionList([]);
    setApproveConditionDraft("");
    setApproveExpiresAt("");
    setApproveAuthorityConfirmed(false);
    setApproveStepUpPassword("");
    setApproveOverrideReason("");
    setInfoNote("");
    setInfoDocumentTypes("");
    setInfoDueAt("");
    setDeclineNotes("");
    setDeclineReasonCode("");
  }

  function addApproveCondition() {
    if (!approveConditionDraft.trim()) return;
    setApproveConditionList((prev) => [...prev, approveConditionDraft.trim()]);
    setApproveConditionDraft("");
  }

  const approveRequiresStepUp = Boolean(
    reviewTarget && (REPLACING_ARRANGEMENT_TYPES as readonly string[]).includes(reviewTarget.arrangementType)
  );

  const approveNeedsOverride = reviewTarget?.occupantRiskTier === "BLOCK";

  async function handleSubmitApprove() {
    if (!reviewTarget || !approveAuthorityConfirmed) return;
    if (approveRequiresStepUp && !approveStepUpPassword) return;
    if (approveNeedsOverride && !approveOverrideReason.trim()) return;

    setBusyId(reviewTarget.id);
    try {
      const updated = await approveHostedSubletRequest(reviewTarget.id, {
        conditions: approveConditions.trim(),
        conditionList: approveConditionList,
        expiresAt: approveExpiresAt ? new Date(approveExpiresAt).toISOString() : null,
        authorityConfirmed: approveAuthorityConfirmed,
        stepUpPassword: approveStepUpPassword,
        overrideReason: approveNeedsOverride ? approveOverrideReason.trim() : "",
      });
      setRequests((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      showToast("Sublet request approved — tenant and subtenant notified.", "success");
      setReviewTarget(null);
    } catch (err) {
      setApproveStepUpPassword("");
      showToast(errorMessage(err, "Could not approve this sublet request."), "error");
    } finally {
      setBusyId(null);
    }
  }

  async function handleSubmitRequestInfo() {
    if (!reviewTarget || !infoNote.trim()) return;
    setBusyId(reviewTarget.id);
    try {
      const updated = await requestHostedSubletMoreInfo(reviewTarget.id, infoNote.trim(), {
        requestedDocumentTypes: infoDocumentTypes
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
        dueAt: infoDueAt ? new Date(infoDueAt).toISOString() : null,
      });
      setRequests((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      showToast("Sent information request to the tenant.", "success");
      setReviewTarget(null);
    } catch (err) {
      showToast(errorMessage(err, "Could not send information request."), "error");
    } finally {
      setBusyId(null);
    }
  }

  async function handleSubmitDecline() {
    if (!reviewTarget) return;
    if (!declineReasonCode) {
      showToast("Please choose a reason for declining this request.", "error");
      return;
    }
    if (declineReasonCode === "OTHER" && !declineNotes.trim()) {
      showToast("Please add an explanation when the reason is 'Other'.", "error");
      return;
    }

    setBusyId(reviewTarget.id);
    try {
      const updated = await declineHostedSubletRequest(reviewTarget.id, declineNotes.trim(), declineReasonCode);
      setRequests((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      showToast("Sublet request declined.", "success");
      setReviewTarget(null);
    } catch (err) {
      showToast(errorMessage(err, "Could not decline this sublet request."), "error");
    } finally {
      setBusyId(null);
    }
  }

  async function handleSignAgreement(request: SubletRequest) {
    if (!request.newAgreementId) return;
    setBusyId(request.id);
    try {
      await signHostedAgreement(request.newAgreementId);
      showToast("Agreement signed. Waiting on the co-tenant's signature and payment.", "success");
    } catch (err) {
      showToast(errorMessage(err, "Could not sign this agreement."), "error");
    } finally {
      setBusyId(null);
    }
  }

  function handleSendToOwnerSubmit() {
    if (!sendToOwnerTarget) return;
    showToast(`Sublet request #ZR-SR-${sendToOwnerTarget.id} forwarded to property owner for consent.`, "success");
    setSendToOwnerTarget(null);
  }

  function openAuthorityWizardForRequest(req?: SubletRequest) {
    const propId = req?.currentOccupancyId || properties[0]?.id || 1;
    const propLabel = req?.listingName || properties[0]?.address || `Property #ZR-${propId}`;
    setAuthorityWizardTarget({ propertyId: propId, propertyLabel: propLabel });
  }

  if (loading) {
    return (
      <div className="py-12">
        <Loader label="Loading sublet requests..." />
      </div>
    );
  }

  return (
    <div className="w-full space-y-5 font-sans">
      {/* 1. Header with red overline */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <div className="text-xs font-bold text-[#D80B0B] uppercase tracking-wider">
            HOST WORKSPACE
          </div>
          <h1 className="text-2xl sm:text-3xl font-extrabold text-[#0C2B4E] dark:text-white tracking-tight mt-0.5">
            Sublet Requests
          </h1>
          <p className="mt-1 text-xs sm:text-sm text-slate-500 dark:text-slate-400 max-w-3xl leading-relaxed">
            Review tenant requests to sublet, confirm decision authority and conditions, and track approved sublets.
          </p>
        </div>
        <div className="flex items-center gap-2 self-start sm:self-auto">
          <button
            onClick={() => setPolicySettingsOpen(true)}
            className="inline-flex items-center gap-2 px-3.5 py-2 text-xs font-bold text-[#0C2B4E] dark:text-slate-200 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl hover:bg-slate-50 dark:hover:bg-slate-800/80 shadow-sm transition cursor-pointer"
          >
            <Settings className="h-4 w-4 text-slate-500" />
            <span>Sublet policy settings</span>
          </button>
        </div>
      </div>

      {/* 2. 4 KPI Metric Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
        {/* Card 1: New */}
        <div className="bg-white dark:bg-slate-900 rounded-2xl p-4 sm:p-5 border border-slate-200/80 dark:border-slate-800 shadow-sm flex items-start justify-between">
          <div>
            <div className="text-2xl sm:text-3xl font-extrabold text-[#0C2B4E] dark:text-white">
              {counts.new}
            </div>
            <div className="text-sm font-bold text-slate-900 dark:text-slate-100 mt-1">New</div>
            <div className="text-xs text-slate-400 dark:text-slate-500 mt-0.5">Not yet reviewed</div>
          </div>
          <div className="w-8 h-8 rounded-full bg-blue-50 dark:bg-blue-900/30 flex items-center justify-center text-blue-600 dark:text-blue-400 shrink-0">
            <CircleDot className="h-4 w-4" />
          </div>
        </div>

        {/* Card 2: In Review */}
        <div className="bg-white dark:bg-slate-900 rounded-2xl p-4 sm:p-5 border border-slate-200/80 dark:border-slate-800 shadow-sm flex items-start justify-between">
          <div>
            <div className="text-2xl sm:text-3xl font-extrabold text-[#5B3FE6] dark:text-indigo-400">
              {counts.inReview}
            </div>
            <div className="text-sm font-bold text-slate-900 dark:text-slate-100 mt-1">In Review</div>
            <div className="text-xs text-slate-400 dark:text-slate-500 mt-0.5">Assessment started</div>
          </div>
          <div className="w-8 h-8 rounded-full bg-indigo-50 dark:bg-indigo-900/30 flex items-center justify-center text-[#5B3FE6] dark:text-indigo-400 shrink-0">
            <Eye className="h-4 w-4" />
          </div>
        </div>

        {/* Card 3: Approved */}
        <div className="bg-white dark:bg-slate-900 rounded-2xl p-4 sm:p-5 border border-slate-200/80 dark:border-slate-800 shadow-sm flex items-start justify-between">
          <div>
            <div className="text-2xl sm:text-3xl font-extrabold text-[#10B981] dark:text-emerald-400">
              {counts.approved}
            </div>
            <div className="text-sm font-bold text-slate-900 dark:text-slate-100 mt-1">Approved</div>
            <div className="text-xs text-slate-400 dark:text-slate-500 mt-0.5">Permission granted</div>
          </div>
          <div className="w-8 h-8 rounded-full bg-emerald-50 dark:bg-emerald-900/30 flex items-center justify-center text-[#10B981] dark:text-emerald-400 shrink-0">
            <Check className="h-4 w-4 stroke-[2.5]" />
          </div>
        </div>

        {/* Card 4: Action Required */}
        <div className="bg-white dark:bg-slate-900 rounded-2xl p-4 sm:p-5 border border-slate-200/80 dark:border-slate-800 shadow-sm flex items-start justify-between">
          <div>
            <div className="text-2xl sm:text-3xl font-extrabold text-[#D80B0B] dark:text-red-400">
              {counts.actionRequired}
            </div>
            <div className="text-sm font-bold text-slate-900 dark:text-slate-100 mt-1">Action Required</div>
            <div className="text-xs text-slate-400 dark:text-slate-500 mt-0.5">Needs your decision or fix</div>
          </div>
          <div className="w-8 h-8 rounded-full bg-red-50 dark:bg-red-900/30 flex items-center justify-center text-[#D80B0B] dark:text-red-400 shrink-0">
            <AlertTriangle className="h-4 w-4" />
          </div>
        </div>
      </div>

      {/* 3. Urgent Attention Alert Banner (When action required > 0) */}
      {actionRequiredRequests.length > 0 && (
        <div className="rounded-2xl border border-red-200 dark:border-red-900/50 bg-[#FEF2F2] dark:bg-red-950/20 p-4 sm:p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4 shadow-sm">
          <div className="space-y-1">
            <div className="flex items-center gap-2 text-sm font-bold text-[#D80B0B]">
              <AlertTriangle className="h-4 w-4 shrink-0 text-[#D80B0B]" />
              <span>
                {actionRequiredRequests.length} sublet request
                {actionRequiredRequests.length > 1 ? "s" : ""} needs your attention
              </span>
            </div>
            <p className="text-xs text-slate-600 dark:text-slate-400 pl-6 leading-relaxed">
              Request #ZR-SR-{actionRequiredRequests[0]?.id || "1054"} cannot be decided because your sublet-decision authority requires renewal.
            </p>
          </div>
          <button
            onClick={() => openAuthorityWizardForRequest(actionRequiredRequests[0])}
            className="shrink-0 inline-flex items-center justify-center px-5 py-2.5 rounded-xl bg-[#D80B0B] hover:bg-[#b50909] text-white text-xs font-bold shadow-sm transition cursor-pointer"
          >
            Complete Authority Verification
          </button>
        </div>
      )}

      {/* 4. Search and Filter Bar */}
      <div className="rounded-2xl bg-white dark:bg-slate-900 border border-slate-200/80 dark:border-slate-800 p-3 sm:p-4 shadow-sm space-y-3">
        <div className="flex flex-col lg:flex-row items-stretch lg:items-center gap-3">
          {/* Search Input */}
          <div className="relative flex-1">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search requester, request ID, rental, property or subtenant"
              className="w-full pl-10 pr-4 py-2 text-xs sm:text-sm bg-slate-50 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/80 rounded-full outline-none focus:border-indigo-400 focus:ring-1 focus:ring-indigo-400 text-slate-900 dark:text-slate-100 placeholder:text-slate-400 transition shadow-2xs"
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

          {/* Filter Dropdowns */}
          <div className="flex flex-wrap items-end gap-2.5">
            {/* Status Dropdown */}
            <RoundedFilterDropdown
              label="Status"
              value={statusFilter}
              onChange={(v) => setStatusFilter(v as any)}
              options={statusFilterOptions}
              size="sm"
              className="flex-1 sm:w-36"
              labelClassName="text-[10px] font-bold text-slate-400 uppercase tracking-wider"
            />

            {/* Property Dropdown */}
            <RoundedFilterDropdown
              label="Property"
              value={propertyFilter}
              onChange={setPropertyFilter}
              options={propertyFilterOptions}
              size="sm"
              className="flex-1 sm:w-40"
              labelClassName="text-[10px] font-bold text-slate-400 uppercase tracking-wider"
            />

            {/* Decision Authority Dropdown */}
            <RoundedFilterDropdown
              label="Decision Authority"
              value={authorityFilter}
              onChange={(v) => setAuthorityFilter(v as any)}
              options={authorityFilterOptions}
              size="sm"
              className="flex-1 sm:w-36"
              labelClassName="text-[10px] font-bold text-slate-400 uppercase tracking-wider"
            />

            {/* Sort Dropdown */}
            <RoundedFilterDropdown
              label="Sort"
              value={sortOption}
              onChange={(v) => setSortOption(v as any)}
              options={sortFilterOptions}
              align="right"
              size="sm"
              className="flex-1 sm:w-44"
              labelClassName="text-[10px] font-bold text-slate-400 uppercase tracking-wider"
            />

            {/* + More filters button */}
            <div className="shrink-0 self-end">
              <button
                type="button"
                onClick={() => setMoreFiltersOpen((o) => !o)}
                className={`inline-flex items-center gap-1.5 px-3.5 h-10 text-xs font-bold rounded-full border transition cursor-pointer shadow-2xs ${
                  moreFiltersOpen
                    ? "bg-[#0C2B4E] text-white border-[#0C2B4E]"
                    : "bg-white dark:bg-slate-800 text-[#0C2B4E] dark:text-slate-200 border-slate-200 dark:border-slate-700 hover:bg-slate-50"
                }`}
              >
                <Plus className="h-3.5 w-3.5" />
                <span>More filters</span>
              </button>
            </div>
          </div>
        </div>

        {/* Expandable More Filters Row */}
        {moreFiltersOpen && (
          <div className="pt-3 border-t border-slate-100 dark:border-slate-800 flex flex-wrap items-end gap-3 animate-fade-in">
            {/* Arrangement Type */}
            <RoundedFilterDropdown
              label="Arrangement Type"
              value={arrangementFilter}
              onChange={setArrangementFilter}
              options={arrangementFilterOptions}
              size="sm"
              className="flex-1 sm:w-56"
              labelClassName="text-[10px] font-bold text-slate-400 uppercase tracking-wider"
            />

            {/* Subtenant ID Status */}
            <RoundedFilterDropdown
              label="Subtenant ID Status"
              value={subtenantVerificationFilter}
              onChange={setSubtenantVerificationFilter}
              options={subtenantVerificationOptions}
              size="sm"
              className="flex-1 sm:w-52"
              labelClassName="text-[10px] font-bold text-slate-400 uppercase tracking-wider"
            />

            <div className="pb-1">
              <button
                type="button"
                onClick={() => {
                  setArrangementFilter("ALL");
                  setSubtenantVerificationFilter("ALL");
                  setSearchQuery("");
                  setStatusFilter("ALL");
                  setPropertyFilter("ALL");
                  setAuthorityFilter("ALL");
                }}
                className="inline-flex items-center text-xs text-indigo-600 dark:text-indigo-400 hover:underline font-semibold cursor-pointer h-10 px-2"
              >
                Reset all filters
              </button>
            </div>
          </div>
        )}
      </div>

      {/* 5. View Toggle Tabs & Total Count */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 pt-1">
        <div className="inline-flex p-1 bg-slate-200/80 dark:bg-slate-800/80 rounded-2xl">
          <button
            onClick={() => setActiveTab("CURRENT")}
            className={`px-4 py-1.5 rounded-xl text-xs font-bold transition cursor-pointer ${
              activeTab === "CURRENT"
                ? "bg-white dark:bg-slate-900 text-[#0C2B4E] dark:text-white shadow-sm"
                : "text-slate-600 dark:text-slate-400 hover:text-slate-900"
            }`}
          >
            Current ({counts.current})
          </button>
          <button
            onClick={() => setActiveTab("HISTORY")}
            className={`px-4 py-1.5 rounded-xl text-xs font-bold transition cursor-pointer ${
              activeTab === "HISTORY"
                ? "bg-white dark:bg-slate-900 text-[#0C2B4E] dark:text-white shadow-sm"
                : "text-slate-600 dark:text-slate-400 hover:text-slate-900"
            }`}
          >
            History ({counts.history})
          </button>
        </div>

        <div className="text-xs font-semibold text-slate-500 dark:text-slate-400">
          {filteredRequests.length} requests
        </div>
      </div>

      {/* 6. Requests Table (Desktop) / Cards (Mobile) OR Dynamic Empty State */}
      {filteredRequests.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-8 sm:p-12 text-center flex flex-col items-center justify-center shadow-sm">
          <div className="w-12 h-12 rounded-2xl bg-indigo-50 dark:bg-indigo-900/20 text-[#0C2B4E] dark:text-indigo-400 flex items-center justify-center mb-3">
            <Repeat className="h-6 w-6" />
          </div>
          <h3 className="text-base font-bold text-[#0C2B4E] dark:text-white">
            No sublet requests
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 max-w-sm">
            Tenant requests for permission to sublet will appear here.
          </p>
          <div className="mt-4">
            <Link href="/account/host/listings">
              <button className="px-4 py-2 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-xs font-bold text-[#0C2B4E] dark:text-white hover:bg-slate-50 dark:hover:bg-slate-700/60 transition shadow-sm cursor-pointer">
                View Rental Records
              </button>
            </Link>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          {/* Desktop Table View (lg+) */}
          <div className="hidden lg:block overflow-x-auto rounded-2xl bg-white dark:bg-slate-900 border border-slate-200/80 dark:border-slate-800 shadow-sm">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-slate-100 dark:border-slate-800/80 bg-slate-50/70 dark:bg-slate-800/30 text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                  <th className="py-3 px-4">REQUESTER</th>
                  <th className="py-3 px-4">PARENT RENTAL / ROOM</th>
                  <th className="py-3 px-4">REQUESTED PERIOD</th>
                  <th className="py-3 px-4">APPROVAL SCOPE / SUBTENANT</th>
                  <th className="py-3 px-4">STATUS</th>
                  <th className="py-3 px-4">AUTHORITY & CHECKS</th>
                  <th className="py-3 px-4 text-right">NEXT ACTION</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800/60 text-xs">
                {filteredRequests.map((req) => {
                  const authState = getAuthorityStateForRequest(req);
                  const requesterInitials = getInitials(req.currentTenantName);
                  const isPending = isRequestNew(req);
                  const isInReview = isRequestInReview(req);
                  const isApproved = isRequestApproved(req);
                  const hasConditions =
                    req.approvalConditions || req.approvalConditionList?.length > 0;

                  return (
                    <tr
                      key={req.id}
                      className="hover:bg-slate-50/50 dark:hover:bg-slate-800/30 transition-colors"
                    >
                      {/* Column 1: Requester */}
                      <td className="py-4 px-4 align-top">
                        <div className="flex items-start gap-2.5">
                          <div className="w-8 h-8 rounded-full bg-slate-100 dark:bg-slate-800 flex items-center justify-center font-bold text-xs text-[#0C2B4E] dark:text-slate-200 shrink-0">
                            {requesterInitials}
                          </div>
                          <div>
                            <div className="font-bold text-slate-900 dark:text-slate-100">
                              {req.currentTenantName || "Tenant"}
                            </div>
                            <div className="flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400 font-semibold mt-0.5">
                              <Check className="h-3 w-3 stroke-[2.5]" />
                              <span>Identity Verified</span>
                            </div>
                            <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                              #ZR-SR-{req.id}
                            </div>
                          </div>
                        </div>
                      </td>

                      {/* Column 2: Parent Rental / Room */}
                      <td className="py-4 px-4 align-top">
                        <div className="font-bold text-slate-900 dark:text-slate-100">
                          {req.listingName || "Rental Listing"} · Room #{req.bedrooms || "1"}
                        </div>
                        <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                          Rental #ZR-R-{req.currentOccupancyId}
                        </div>
                      </td>

                      {/* Column 3: Requested Period */}
                      <td className="py-4 px-4 align-top">
                        <div className="font-bold text-slate-900 dark:text-slate-100">
                          {formatDateRange(req.proposedStartDate, req.proposedEndDate)}
                        </div>
                        <div className="text-[11px] text-slate-400 mt-0.5">
                          {formatParentEndDate(req.proposedEndDate)}
                        </div>
                      </td>

                      {/* Column 4: Approval Scope / Subtenant */}
                      <td className="py-4 px-4 align-top">
                        {req.proposedRenterName ? (
                          <>
                            <div className="font-bold text-slate-900 dark:text-slate-100">
                              Named subtenant: {req.proposedRenterName}
                            </div>
                            {req.occupantRiskTier === "BLOCK" || authState.type === "RENEWAL_REQUIRED" ? (
                              <div className="flex items-center gap-1 text-[11px] text-amber-600 dark:text-amber-400 font-semibold mt-0.5">
                                <Clock className="h-3 w-3" />
                                <span>Identity verification pending</span>
                              </div>
                            ) : (
                              <div className="flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400 font-semibold mt-0.5">
                                <Check className="h-3 w-3 stroke-[2.5]" />
                                <span>Identity Verified</span>
                              </div>
                            )}
                          </>
                        ) : (
                          <>
                            <div className="font-bold text-slate-900 dark:text-slate-100">
                              Permission in principle
                            </div>
                            <div className="text-[11px] text-slate-400 mt-0.5">
                              Subtenant not yet named
                            </div>
                          </>
                        )}
                      </td>

                      {/* Column 5: Status */}
                      <td className="py-4 px-4 align-top">
                        {isPending && (
                          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-extrabold uppercase tracking-wide bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300">
                            <span className="h-1.5 w-1.5 rounded-full bg-blue-600" />
                            NEW
                          </span>
                        )}

                        {isInReview && (
                          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-extrabold uppercase tracking-wide bg-indigo-50 text-[#5B3FE6] dark:bg-indigo-900/30 dark:text-indigo-300">
                            <Eye className="h-3 w-3" />
                            IN REVIEW
                          </span>
                        )}

                        {isApproved && (
                          <div>
                            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-extrabold uppercase tracking-wide bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300">
                              <Check className="h-3 w-3 stroke-[2.5]" />
                              {hasConditions ? "APPROVED WITH CONDITIONS" : "APPROVED"}
                            </span>
                            <div className="flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400 mt-1">
                              <Repeat className="h-3 w-3 text-slate-400" />
                              <span>
                                {req.newAgreementId && !req.decidedAt
                                  ? "Sublet: Agreement Pending"
                                  : "Sublet: Active"}
                              </span>
                            </div>
                          </div>
                        )}

                        {!isPending && !isInReview && !isApproved && (
                          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-extrabold uppercase tracking-wide bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                            {subletRequestStatusLabel[req.status] || req.status}
                          </span>
                        )}
                      </td>

                      {/* Column 6: Authority & Checks */}
                      <td className="py-4 px-4 align-top">
                        {authState.type === "RENEWAL_REQUIRED" && (
                          <div>
                            <div className="flex items-center gap-1 text-[11px] font-bold text-[#D80B0B]">
                              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                              <span>Renewal Required</span>
                            </div>
                            <div className="text-[11px] text-slate-400 mt-0.5">
                              Other checks pending
                            </div>
                          </div>
                        )}

                        {authState.type === "OWNER_REQUIRED" && (
                          <div>
                            <div className="flex items-center gap-1 text-[11px] font-bold text-[#D80B0B]">
                              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                              <span>Owner Required</span>
                            </div>
                            <div className="text-[11px] text-slate-400 mt-0.5">
                              Other checks pending
                            </div>
                          </div>
                        )}

                        {authState.type === "AUTHORIZED_OWNER" && (
                          <div>
                            <div className="flex items-center gap-1 text-[11px] font-bold text-emerald-700 dark:text-emerald-400">
                              <ShieldCheck className="h-3.5 w-3.5 shrink-0" />
                              <span>Authorized Owner (you)</span>
                            </div>
                            <div className="text-[11px] text-slate-400 mt-0.5">
                              {authState.subtext}
                            </div>
                          </div>
                        )}
                      </td>

                      {/* Column 7: Next Action */}
                      <td className="py-4 px-4 align-top text-right">
                        {/* Red CTA if Renewal Required */}
                        {authState.type === "RENEWAL_REQUIRED" && (
                          <button
                            onClick={() => openAuthorityWizardForRequest(req)}
                            className="inline-flex items-center justify-center px-4 py-2 rounded-xl bg-[#D80B0B] hover:bg-[#b50909] text-white text-xs font-bold shadow-sm transition cursor-pointer"
                          >
                            Complete Authority
                          </button>
                        )}

                        {/* Navy CTA if Owner Required */}
                        {authState.type === "OWNER_REQUIRED" && (
                          <button
                            onClick={() => setSendToOwnerTarget(req)}
                            className="inline-flex items-center justify-center px-4 py-2 rounded-xl bg-[#0C2B4E] hover:bg-[#071c33] text-white text-xs font-bold shadow-sm transition cursor-pointer"
                          >
                            Send to Owner
                          </button>
                        )}

                        {/* Navy CTA if Pending / In Review */}
                        {authState.type === "AUTHORIZED_OWNER" && (isPending || isInReview) && (
                          <button
                            onClick={() => openReviewModal(req, "APPROVE")}
                            className="inline-flex items-center justify-center px-4 py-2 rounded-xl bg-[#0C2B4E] hover:bg-[#071c33] text-white text-xs font-bold shadow-sm transition cursor-pointer"
                          >
                            Continue Review
                          </button>
                        )}

                        {/* White border CTA if Approved */}
                        {authState.type === "AUTHORIZED_OWNER" && isApproved && (
                          <button
                            onClick={() => setDetailsTarget(req)}
                            className="inline-flex items-center justify-center px-4 py-2 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-[#0C2B4E] dark:text-slate-100 hover:bg-slate-50 dark:hover:bg-slate-700/60 text-xs font-bold shadow-sm transition cursor-pointer"
                          >
                            {hasConditions ? "View Approval" : "View Sublet"}
                          </button>
                        )}

                        {/* Other states */}
                        {!isPending && !isInReview && !isApproved && authState.type === "AUTHORIZED_OWNER" && (
                          <button
                            onClick={() => setDetailsTarget(req)}
                            className="inline-flex items-center justify-center px-4 py-2 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-[#0C2B4E] dark:text-slate-100 hover:bg-slate-50 text-xs font-bold shadow-sm transition cursor-pointer"
                          >
                            View Details
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
          <div className="lg:hidden space-y-3">
            {filteredRequests.map((req) => {
              const authState = getAuthorityStateForRequest(req);
              const requesterInitials = getInitials(req.currentTenantName);
              const isPending = isRequestNew(req);
              const isInReview = isRequestInReview(req);
              const isApproved = isRequestApproved(req);
              const hasConditions =
                req.approvalConditions || req.approvalConditionList?.length > 0;

              return (
                <div
                  key={req.id}
                  className="rounded-2xl bg-white dark:bg-slate-900 border border-slate-200/80 dark:border-slate-800 p-4 shadow-sm space-y-3"
                >
                  {/* Top Bar: Requester & Status */}
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2.5">
                      <div className="w-8 h-8 rounded-full bg-slate-100 dark:bg-slate-800 flex items-center justify-center font-bold text-xs text-[#0C2B4E] dark:text-slate-200 shrink-0">
                        {requesterInitials}
                      </div>
                      <div>
                        <div className="font-bold text-slate-900 dark:text-slate-100 text-sm">
                          {req.currentTenantName || "Tenant"}
                        </div>
                        <div className="flex items-center gap-1 text-[11px] text-emerald-600 font-semibold">
                          <Check className="h-3 w-3 stroke-[2.5]" />
                          <span>Identity Verified</span>
                          <span className="text-slate-400 font-mono ml-1">#ZR-SR-{req.id}</span>
                        </div>
                      </div>
                    </div>

                    <div>
                      {isPending && (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-extrabold uppercase bg-blue-50 text-blue-700">
                          <span className="h-1.5 w-1.5 rounded-full bg-blue-600" />
                          NEW
                        </span>
                      )}
                      {isInReview && (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-extrabold uppercase bg-indigo-50 text-[#5B3FE6]">
                          <Eye className="h-3 w-3" />
                          IN REVIEW
                        </span>
                      )}
                      {isApproved && (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-extrabold uppercase bg-emerald-50 text-emerald-700">
                          <Check className="h-3 w-3 stroke-[2.5]" />
                          APPROVED
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Middle Details Grid */}
                  <div className="grid grid-cols-2 gap-2 text-xs pt-1">
                    <div className="bg-slate-50 dark:bg-slate-800/50 p-2.5 rounded-xl">
                      <span className="text-[10px] font-bold text-slate-400 uppercase block mb-0.5">
                        Rental / Room
                      </span>
                      <div className="font-bold text-slate-800 dark:text-slate-200">
                        {req.listingName || "Rental"} · Rm #{req.bedrooms || "1"}
                      </div>
                      <div className="text-[10px] text-slate-400 font-mono">
                        Rental #ZR-R-{req.currentOccupancyId}
                      </div>
                    </div>

                    <div className="bg-slate-50 dark:bg-slate-800/50 p-2.5 rounded-xl">
                      <span className="text-[10px] font-bold text-slate-400 uppercase block mb-0.5">
                        Requested Period
                      </span>
                      <div className="font-bold text-slate-800 dark:text-slate-200">
                        {formatDateRange(req.proposedStartDate, req.proposedEndDate)}
                      </div>
                      <div className="text-[10px] text-slate-400">
                        {formatParentEndDate(req.proposedEndDate)}
                      </div>
                    </div>
                  </div>

                  {/* Scope & Subtenant */}
                  <div className="text-xs bg-slate-50 dark:bg-slate-800/50 p-2.5 rounded-xl">
                    <span className="text-[10px] font-bold text-slate-400 uppercase block mb-0.5">
                      Subtenant Scope
                    </span>
                    <div className="font-bold text-slate-800 dark:text-slate-200">
                      {req.proposedRenterName
                        ? `Named subtenant: ${req.proposedRenterName}`
                        : "Permission in principle"}
                    </div>
                  </div>

                  {/* Authority check note */}
                  <div className="flex items-center justify-between text-xs pt-1">
                    <div className="flex items-center gap-1">
                      {authState.type === "RENEWAL_REQUIRED" ? (
                        <span className="text-[#D80B0B] font-bold flex items-center gap-1">
                          <AlertTriangle className="h-3.5 w-3.5" /> Renewal Required
                        </span>
                      ) : authState.type === "OWNER_REQUIRED" ? (
                        <span className="text-[#D80B0B] font-bold flex items-center gap-1">
                          <AlertTriangle className="h-3.5 w-3.5" /> Owner Required
                        </span>
                      ) : (
                        <span className="text-emerald-700 dark:text-emerald-400 font-bold flex items-center gap-1">
                          <ShieldCheck className="h-3.5 w-3.5" /> Authorized Owner
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Action Button */}
                  <div className="pt-2">
                    {authState.type === "RENEWAL_REQUIRED" && (
                      <button
                        onClick={() => openAuthorityWizardForRequest(req)}
                        className="w-full py-2.5 rounded-xl bg-[#D80B0B] text-white text-xs font-bold shadow-sm cursor-pointer"
                      >
                        Complete Authority Verification
                      </button>
                    )}

                    {authState.type === "OWNER_REQUIRED" && (
                      <button
                        onClick={() => setSendToOwnerTarget(req)}
                        className="w-full py-2.5 rounded-xl bg-[#0C2B4E] text-white text-xs font-bold shadow-sm cursor-pointer"
                      >
                        Send to Owner
                      </button>
                    )}

                    {authState.type === "AUTHORIZED_OWNER" && (isPending || isInReview) && (
                      <button
                        onClick={() => openReviewModal(req, "APPROVE")}
                        className="w-full py-2.5 rounded-xl bg-[#0C2B4E] text-white text-xs font-bold shadow-sm cursor-pointer"
                      >
                        Continue Review
                      </button>
                    )}

                    {authState.type === "AUTHORIZED_OWNER" && isApproved && (
                      <button
                        onClick={() => setDetailsTarget(req)}
                        className="w-full py-2.5 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-[#0C2B4E] dark:text-white text-xs font-bold shadow-sm cursor-pointer"
                      >
                        {hasConditions ? "View Approval" : "View Sublet"}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 7. Footer Legal Explainer */}
      <div className="pt-3 pb-6">
        <p className="text-[11px] sm:text-xs text-slate-400 dark:text-slate-500 leading-relaxed max-w-4xl">
          The tenant requests permission; the verified, authorized Owner or Agent decides. Zoiko Rooms records the workflow and runs platform checks — it doesn’t grant legal permission. Platform fee on sublet rent: 0.
        </p>
      </div>

      {/* ======================================================== */}
      {/* MODAL 1: Authority Verification Wizard Modal             */}
      {/* ======================================================== */}
      {authorityWizardTarget && (
        <Modal
          open={Boolean(authorityWizardTarget)}
          onClose={() => setAuthorityWizardTarget(null)}
          title={`Authority Verification · ${authorityWizardTarget.propertyLabel}`}
        >
          <div className="p-2 sm:p-4 max-h-[85vh] overflow-y-auto">
            <AuthorityVerificationWizard
              propertyId={authorityWizardTarget.propertyId}
              propertyLabel={authorityWizardTarget.propertyLabel}
              onClose={() => {
                setAuthorityWizardTarget(null);
                loadData();
              }}
            />
          </div>
        </Modal>
      )}

      {/* ======================================================== */}
      {/* MODAL 2: Review and Decision Modal (Approve / Ask / Decline) */}
      {/* ======================================================== */}
      {reviewTarget && (
        <Modal
          open={Boolean(reviewTarget)}
          onClose={() => setReviewTarget(null)}
          title={`Review Sublet Request #ZR-SR-${reviewTarget.id}`}
        >
          <div className="space-y-4">
            {/* Target Summary Card */}
            <div className="p-3.5 bg-slate-50 dark:bg-slate-800/60 rounded-xl space-y-1.5 text-xs">
              <div className="flex justify-between">
                <span className="text-slate-400 font-semibold">Requester:</span>
                <span className="font-bold text-slate-800 dark:text-slate-200">
                  {reviewTarget.currentTenantName || "Tenant"}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400 font-semibold">Proposed Subtenant:</span>
                <span className="font-bold text-slate-800 dark:text-slate-200">
                  {reviewTarget.proposedRenterName || "Permission in principle"}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400 font-semibold">Requested Period:</span>
                <span className="font-bold text-slate-800 dark:text-slate-200">
                  {formatDateRange(reviewTarget.proposedStartDate, reviewTarget.proposedEndDate)}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400 font-semibold">Arrangement:</span>
                <span className="font-bold text-slate-800 dark:text-slate-200">
                  {subletArrangementTypeAdminLabel[reviewTarget.arrangementType] || reviewTarget.arrangementType}
                </span>
              </div>
              {reviewTarget.reason && (
                <div className="pt-1 text-slate-600 dark:text-slate-300 italic border-t border-slate-200 dark:border-slate-700">
                  &ldquo;{reviewTarget.reason}&rdquo;
                </div>
              )}
            </div>

            {/* Mode Selector Tabs */}
            <div className="flex rounded-xl bg-slate-100 dark:bg-slate-800 p-1 text-xs font-bold">
              <button
                type="button"
                onClick={() => setReviewMode("APPROVE")}
                className={`flex-1 py-1.5 rounded-lg transition cursor-pointer ${
                  reviewMode === "APPROVE"
                    ? "bg-white dark:bg-slate-700 text-[#0C2B4E] dark:text-white shadow-sm"
                    : "text-slate-500 hover:text-slate-800"
                }`}
              >
                Approve Request
              </button>
              <button
                type="button"
                onClick={() => setReviewMode("INFO")}
                className={`flex-1 py-1.5 rounded-lg transition cursor-pointer ${
                  reviewMode === "INFO"
                    ? "bg-white dark:bg-slate-700 text-[#0C2B4E] dark:text-white shadow-sm"
                    : "text-slate-500 hover:text-slate-800"
                }`}
              >
                Ask for Info
              </button>
              <button
                type="button"
                onClick={() => setReviewMode("DECLINE")}
                className={`flex-1 py-1.5 rounded-lg transition cursor-pointer ${
                  reviewMode === "DECLINE"
                    ? "bg-white dark:bg-slate-700 text-[#D80B0B] shadow-sm"
                    : "text-slate-500 hover:text-slate-800"
                }`}
              >
                Decline Request
              </button>
            </div>

            {/* Sub-form 1: Approve */}
            {reviewMode === "APPROVE" && (
              <div className="space-y-3.5">
                <div>
                  <label className="block text-xs font-bold uppercase tracking-wide text-slate-500 mb-1">
                    Conditions of Approval (optional)
                  </label>
                  {approveConditionList.length > 0 && (
                    <ul className="mb-2 space-y-1">
                      {approveConditionList.map((c, i) => (
                        <li
                          key={i}
                          className="flex items-center justify-between gap-2 rounded-lg bg-slate-50 dark:bg-slate-800 px-3 py-1.5 text-xs text-slate-700 dark:text-slate-300"
                        >
                          <span>{c}</span>
                          <button
                            type="button"
                            onClick={() =>
                              setApproveConditionList((prev) => prev.filter((_, idx) => idx !== i))
                            }
                            className="text-slate-400 hover:text-red-500"
                          >
                            <X className="h-3 w-3" />
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                  <div className="flex gap-2">
                    <input
                      value={approveConditionDraft}
                      onChange={(e) => setApproveConditionDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          addApproveCondition();
                        }
                      }}
                      placeholder="e.g. No additional occupants, quiet hours after 10pm"
                      className="flex-1 rounded-xl bg-slate-50 dark:bg-slate-800 px-3.5 py-2 text-xs outline-none ring-1 ring-slate-200 dark:ring-slate-700 focus:ring-2 focus:ring-indigo-400"
                    />
                    <button
                      type="button"
                      onClick={addApproveCondition}
                      className="px-3 py-2 text-xs font-bold rounded-xl border border-slate-200 dark:border-slate-700 hover:bg-slate-50"
                    >
                      + Add
                    </button>
                  </div>
                  <textarea
                    value={approveConditions}
                    onChange={(e) => setApproveConditions(e.target.value)}
                    rows={2}
                    placeholder="Additional notes on approval conditions..."
                    className="mt-2 w-full rounded-xl bg-slate-50 dark:bg-slate-800 px-3.5 py-2 text-xs outline-none ring-1 ring-slate-200 dark:ring-slate-700 focus:ring-2 focus:ring-indigo-400"
                  />
                </div>

                <div>
                  <label className="block text-xs font-bold uppercase tracking-wide text-slate-500 mb-1">
                    Approval Expires On (optional)
                  </label>
                  <input
                    type="date"
                    min={new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString().slice(0, 10)}
                    value={approveExpiresAt}
                    onChange={(e) => setApproveExpiresAt(e.target.value)}
                    className="w-full rounded-xl bg-slate-50 dark:bg-slate-800 px-3.5 py-2 text-xs outline-none ring-1 ring-slate-200 dark:ring-slate-700 focus:ring-2 focus:ring-indigo-400"
                  />
                </div>

                <label className="flex items-start gap-2 text-xs text-slate-700 dark:text-slate-300 pt-1">
                  <input
                    type="checkbox"
                    checked={approveAuthorityConfirmed}
                    onChange={(e) => setApproveAuthorityConfirmed(e.target.checked)}
                    className="mt-0.5 rounded border-slate-300 text-indigo-600"
                  />
                  <span>I confirm I am authorized to make this sublet decision for this rental property.</span>
                </label>

                {approveNeedsOverride && (
                  <div className="p-3 bg-red-50 dark:bg-red-950/30 rounded-xl space-y-1">
                    <label className="block text-xs font-bold text-red-600">
                      Overlapping Tenancy Override
                    </label>
                    <p className="text-[11px] text-slate-600 dark:text-slate-400">
                      The proposed renter holds another tenancy covering this period. State why this is acceptable:
                    </p>
                    <textarea
                      value={approveOverrideReason}
                      onChange={(e) => setApproveOverrideReason(e.target.value)}
                      rows={2}
                      placeholder="e.g. Moving out of other premises before sublet start"
                      className="w-full rounded-lg bg-white dark:bg-slate-800 p-2 text-xs outline-none ring-1 ring-red-200"
                    />
                  </div>
                )}

                {approveRequiresStepUp && (
                  <div className="space-y-1">
                    <label className="block text-xs font-bold uppercase tracking-wide text-slate-500">
                      Confirm Account Password
                    </label>
                    <input
                      type="password"
                      value={approveStepUpPassword}
                      onChange={(e) => setApproveStepUpPassword(e.target.value)}
                      placeholder="Account password required for permanent replacement"
                      className="w-full rounded-xl bg-slate-50 dark:bg-slate-800 px-3.5 py-2 text-xs outline-none ring-1 ring-slate-200 dark:ring-slate-700"
                    />
                  </div>
                )}

                <div className="flex justify-end gap-2 pt-2">
                  <button
                    type="button"
                    onClick={() => setReviewTarget(null)}
                    className="px-4 py-2 text-xs font-semibold text-slate-600 hover:text-slate-900"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    disabled={
                      !approveAuthorityConfirmed ||
                      (approveRequiresStepUp && !approveStepUpPassword) ||
                      (approveNeedsOverride && !approveOverrideReason.trim()) ||
                      busyId === reviewTarget.id
                    }
                    onClick={handleSubmitApprove}
                    className="px-5 py-2 rounded-xl bg-[#0C2B4E] hover:bg-[#071c33] disabled:opacity-50 text-white text-xs font-bold shadow-sm transition cursor-pointer"
                  >
                    {busyId === reviewTarget.id ? "Approving..." : "Approve Sublet Request"}
                  </button>
                </div>
              </div>
            )}

            {/* Sub-form 2: Ask Info */}
            {reviewMode === "INFO" && (
              <div className="space-y-3.5">
                <div>
                  <label className="block text-xs font-bold uppercase tracking-wide text-slate-500 mb-1">
                    Information Note to Tenant
                  </label>
                  <textarea
                    value={infoNote}
                    onChange={(e) => setInfoNote(e.target.value)}
                    rows={3}
                    placeholder="e.g. Please provide the proposed subtenant's employer reference and ID copy."
                    className="w-full rounded-xl bg-slate-50 dark:bg-slate-800 px-3.5 py-2 text-xs outline-none ring-1 ring-slate-200 dark:ring-slate-700 focus:ring-2 focus:ring-indigo-400"
                  />
                </div>

                <div>
                  <label className="block text-xs font-bold uppercase tracking-wide text-slate-500 mb-1">
                    Requested Documents (optional)
                  </label>
                  <input
                    value={infoDocumentTypes}
                    onChange={(e) => setInfoDocumentTypes(e.target.value)}
                    placeholder="e.g. Proof of income, Employer letter (comma-separated)"
                    className="w-full rounded-xl bg-slate-50 dark:bg-slate-800 px-3.5 py-2 text-xs outline-none ring-1 ring-slate-200 dark:ring-slate-700"
                  />
                </div>

                <div>
                  <label className="block text-xs font-bold uppercase tracking-wide text-slate-500 mb-1">
                    Response Due Date (optional)
                  </label>
                  <input
                    type="date"
                    value={infoDueAt}
                    onChange={(e) => setInfoDueAt(e.target.value)}
                    className="w-full rounded-xl bg-slate-50 dark:bg-slate-800 px-3.5 py-2 text-xs outline-none ring-1 ring-slate-200 dark:ring-slate-700"
                  />
                </div>

                <div className="flex justify-end gap-2 pt-2">
                  <button
                    type="button"
                    onClick={() => setReviewTarget(null)}
                    className="px-4 py-2 text-xs font-semibold text-slate-600 hover:text-slate-900"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    disabled={!infoNote.trim() || busyId === reviewTarget.id}
                    onClick={handleSubmitRequestInfo}
                    className="px-5 py-2 rounded-xl bg-[#0C2B4E] hover:bg-[#071c33] disabled:opacity-50 text-white text-xs font-bold shadow-sm transition cursor-pointer"
                  >
                    {busyId === reviewTarget.id ? "Sending..." : "Send Request for Info"}
                  </button>
                </div>
              </div>
            )}

            {/* Sub-form 3: Decline */}
            {reviewMode === "DECLINE" && (
              <div className="space-y-3.5">
                <div>
                  <label className="block text-xs font-bold uppercase tracking-wide text-slate-500 mb-1">
                    Decline Reason Code
                  </label>
                  <select
                    value={declineReasonCode}
                    onChange={(e) => setDeclineReasonCode(e.target.value)}
                    aria-label="Select decline reason"
                    className="w-full rounded-xl bg-slate-50 dark:bg-slate-800 px-3.5 py-2 text-xs outline-none ring-1 ring-slate-200 dark:ring-slate-700"
                  >
                    <option value="">Select a reason code...</option>
                    {Object.entries(subletDeclineReasonCodeLabel).map(([code, label]) => (
                      <option key={code} value={code}>
                        {label}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="block text-xs font-bold uppercase tracking-wide text-slate-500 mb-1">
                    Decline Notes (visible to tenant)
                  </label>
                  <textarea
                    value={declineNotes}
                    onChange={(e) => setDeclineNotes(e.target.value)}
                    rows={3}
                    placeholder="Provide explanatory context for the tenant..."
                    className="w-full rounded-xl bg-slate-50 dark:bg-slate-800 px-3.5 py-2 text-xs outline-none ring-1 ring-slate-200 dark:ring-slate-700 focus:ring-2 focus:ring-indigo-400"
                  />
                </div>

                <div className="flex justify-end gap-2 pt-2">
                  <button
                    type="button"
                    onClick={() => setReviewTarget(null)}
                    className="px-4 py-2 text-xs font-semibold text-slate-600 hover:text-slate-900"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    disabled={
                      !declineReasonCode ||
                      (declineReasonCode === "OTHER" && !declineNotes.trim()) ||
                      busyId === reviewTarget.id
                    }
                    onClick={handleSubmitDecline}
                    className="px-5 py-2 rounded-xl bg-[#D80B0B] hover:bg-[#b50909] disabled:opacity-50 text-white text-xs font-bold shadow-sm transition cursor-pointer"
                  >
                    {busyId === reviewTarget.id ? "Declining..." : "Decline Sublet Request"}
                  </button>
                </div>
              </div>
            )}
          </div>
        </Modal>
      )}

      {/* ======================================================== */}
      {/* MODAL 3: Sublet Details Modal (View Approval / Sublet)    */}
      {/* ======================================================== */}
      {detailsTarget && (
        <Modal
          open={Boolean(detailsTarget)}
          onClose={() => setDetailsTarget(null)}
          title={`Sublet Record · #ZR-SR-${detailsTarget.id}`}
        >
          <div className="space-y-4 text-xs">
            <div className="flex items-center justify-between p-3 bg-slate-50 dark:bg-slate-800 rounded-xl">
              <div>
                <span className="text-[10px] uppercase font-bold text-slate-400 block">Status</span>
                <span className="font-extrabold text-slate-800 dark:text-slate-100 uppercase">
                  {subletRequestStatusLabel[detailsTarget.status] || detailsTarget.status}
                </span>
              </div>
              <a
                href={hostSubletDecisionRecordUrl(detailsTarget.id)}
                download
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-[#0C2B4E] dark:text-slate-200 font-bold hover:bg-slate-50"
              >
                <Download className="h-3.5 w-3.5" />
                <span>Decision Record (PDF)</span>
              </a>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="p-3 bg-slate-50 dark:bg-slate-800 rounded-xl">
                <span className="text-[10px] uppercase font-bold text-slate-400 block">Original Tenant</span>
                <span className="font-bold text-slate-900 dark:text-slate-100">
                  {detailsTarget.currentTenantName || "Tenant"}
                </span>
              </div>
              <div className="p-3 bg-slate-50 dark:bg-slate-800 rounded-xl">
                <span className="text-[10px] uppercase font-bold text-slate-400 block">Proposed Subtenant</span>
                <span className="font-bold text-slate-900 dark:text-slate-100">
                  {detailsTarget.proposedRenterName || "Permission in principle"}
                </span>
              </div>
            </div>

            {/* Conditions list */}
            {detailsTarget.approvalConditionList && detailsTarget.approvalConditionList.length > 0 && (
              <div className="p-3 bg-emerald-50/80 dark:bg-emerald-950/20 border border-emerald-200 rounded-xl space-y-1">
                <span className="text-[10px] uppercase font-bold text-emerald-800 block">
                  Recorded Approval Conditions
                </span>
                <ul className="list-disc pl-4 space-y-0.5 text-emerald-900 dark:text-emerald-300">
                  {detailsTarget.approvalConditionList.map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ul>
                {detailsTarget.approvalConditions && (
                  <p className="pt-1 text-slate-600 dark:text-slate-400">
                    {detailsTarget.approvalConditions}
                  </p>
                )}
              </div>
            )}

            {/* Payments Panel if approved and agreement created */}
            {detailsTarget.status === "approved" && detailsTarget.newAgreementId && (
              <div className="pt-2">
                <SubletPaymentsPanel subletId={detailsTarget.id} />
              </div>
            )}

            <div className="flex justify-end pt-2">
              <button
                type="button"
                onClick={() => setDetailsTarget(null)}
                className="px-4 py-2 rounded-xl bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 font-bold text-slate-800 dark:text-slate-200"
              >
                Close
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* ======================================================== */}
      {/* MODAL 4: Send to Owner Modal                              */}
      {/* ======================================================== */}
      {sendToOwnerTarget && (
        <Modal
          open={Boolean(sendToOwnerTarget)}
          onClose={() => setSendToOwnerTarget(null)}
          title={`Send Request #ZR-SR-${sendToOwnerTarget.id} to Property Owner`}
        >
          <div className="space-y-3.5 text-xs">
            <p className="text-slate-600 dark:text-slate-400 leading-relaxed">
              As managing representative, this sublet request requires the registered property owner&apos;s direct decision. We will notify the owner with the request details and record their decision.
            </p>
            <div>
              <label className="block text-xs font-bold uppercase tracking-wide text-slate-500 mb-1">
                Note for Property Owner (optional)
              </label>
              <textarea
                value={sendToOwnerNote}
                onChange={(e) => setSendToOwnerNote(e.target.value)}
                rows={3}
                placeholder="e.g. Tenant has been reliable; tenant is traveling for 3 months."
                className="w-full rounded-xl bg-slate-50 dark:bg-slate-800 px-3.5 py-2 outline-none ring-1 ring-slate-200 dark:ring-slate-700"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setSendToOwnerTarget(null)}
                className="px-4 py-2 font-semibold text-slate-600"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleSendToOwnerSubmit}
                className="px-5 py-2 rounded-xl bg-[#0C2B4E] hover:bg-[#071c33] text-white font-bold shadow-sm"
              >
                Forward to Owner
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* ======================================================== */}
      {/* MODAL 5: Sublet Policy Settings Modal                    */}
      {/* ======================================================== */}
      {policySettingsOpen && (
        <Modal
          open={policySettingsOpen}
          onClose={() => setPolicySettingsOpen(false)}
          title="Host Sublet Policy Settings"
        >
          <div className="space-y-4 text-xs">
            <p className="text-slate-600 dark:text-slate-400">
              Configure baseline policy rules applied to incoming tenant sublet requests on your properties.
            </p>
            <div className="space-y-3">
              <label className="flex items-start gap-2.5 p-3 rounded-xl bg-slate-50 dark:bg-slate-800">
                <input
                  type="checkbox"
                  defaultChecked
                  className="mt-0.5 rounded text-indigo-600"
                />
                <div>
                  <div className="font-bold text-slate-900 dark:text-slate-100">
                    Mandatory Government ID verification
                  </div>
                  <div className="text-[11px] text-slate-500">
                    Require all proposed subtenants to complete Zoiko identity checks before approval.
                  </div>
                </div>
              </label>

              <label className="flex items-start gap-2.5 p-3 rounded-xl bg-slate-50 dark:bg-slate-800">
                <input
                  type="checkbox"
                  defaultChecked
                  className="mt-0.5 rounded text-indigo-600"
                />
                <div>
                  <div className="font-bold text-slate-900 dark:text-slate-100">
                    Minimum notice period (14 days)
                  </div>
                  <div className="text-[11px] text-slate-500">
                    Require requests to be submitted at least two weeks before proposed start date.
                  </div>
                </div>
              </label>

              <label className="flex items-start gap-2.5 p-3 rounded-xl bg-slate-50 dark:bg-slate-800">
                <input
                  type="checkbox"
                  defaultChecked
                  className="mt-0.5 rounded text-indigo-600"
                />
                <div>
                  <div className="font-bold text-slate-900 dark:text-slate-100">
                    Require Owner Confirmation for Agent accounts
                  </div>
                  <div className="text-[11px] text-slate-500">
                    Enforce written owner approval before representatives can grant sublet rights.
                  </div>
                </div>
              </label>
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setPolicySettingsOpen(false)}
                className="px-4 py-2 font-semibold text-slate-600"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  showToast("Sublet policy settings updated.", "success");
                  setPolicySettingsOpen(false);
                }}
                className="px-5 py-2 rounded-xl bg-[#0C2B4E] hover:bg-[#071c33] text-white font-bold shadow-sm"
              >
                Save Settings
              </button>
            </div>
          </div>
        </Modal>
      )}

      <Toast toast={toast} />
    </div>
  );
}

// Re-export alias for seamless compatibility
export const HostSubletRequestsManager = HostingSubletRequestsManager;
