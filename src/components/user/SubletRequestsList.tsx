"use client";

import React, { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { SubletRequest, UserOccupancy, SubletArrangementType } from "@/lib/types";
import {
  listSubletRequests,
  listOccupancies,
  createSubletRequestDraft,
  submitSubletRequest,
  respondToSubletInfoRequest,
  withdrawSubletRequest,
  tenantSubletDecisionRecordUrl,
  lookupSubletRenter,
  listSubletDocuments,
  uploadSubletDocument,
  SubletDocument,
  errorMessage,
} from "@/lib/user-api";
import { SubletPaymentsPanel } from "@/components/user/SubletPaymentsPanel";
import { SubletChatThread } from "@/components/user/RentalMessagesTab";
import { RoundedFilterDropdown } from "@/components/ui/RoundedFilterDropdown";
import { formatDate } from "@/lib/utils";

function safeFormatDate(date?: string | null): string {
  if (!date) return "—";
  try {
    return formatDate(date);
  } catch {
    return String(date);
  }
}

// --- SVG Icons matching prototype coordinate paths ---
function SvgIcon({
  name,
  size = 16,
  strokeWidth = 2.2,
  color = "currentColor",
  className = "",
}: {
  name: string;
  size?: number;
  strokeWidth?: number;
  color?: string;
  className?: string;
}) {
  const icons: Record<string, string> = {
    check: "M20 6 9 17l-5-5",
    dot: "M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
    eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
    star: "M12 2l3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z",
    ccheck: "M22 11.1V12a10 10 0 1 1-5.9-9.1M22 4 12 14l-3-3",
    xcirc: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM15 9l-6 6M9 9l6 6",
    undo: "M3 7v6h6M3 13a9 9 0 1 0 3-7.7L3 8",
    clock: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2",
    alert: "M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
    shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10zM9 12l2 2 4-4",
    ext: "M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6",
    send: "M22 2 11 13M22 2l-7 20-4-9-9-4z",
    cal: "M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z",
    users: "M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8",
    user: "M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
    x: "M18 6 6 18M6 6l12 12",
    info: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 16v-4M12 8h.01",
    lock: "M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4",
    pause: "M10 15V9M14 15V9M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z",
    msg: "M4 4h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H8l-4 4V6a2 2 0 0 1 2-2z",
    file: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M9 13h6M9 17h4",
    swap: "M17 2l4 4-4 4M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4M21 13v2a3 3 0 0 1-3 3H3",
    card: "M3 5h18a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM2 10h20M6 15h4",
    home: "M3 10l9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z",
    plus: "M12 5v14M5 12h14",
    minus: "M5 12h14",
    upload: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12",
    search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3",
    pencil: "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z",
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke={color}
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d={icons[name] || ""} />
    </svg>
  );
}

// History statuses
const HISTORY_STATUSES = new Set([
  "rejected",
  "withdrawn",
  "expired",
  "superseded",
  "cancelled_by_authority",
  "declined_by_co_tenant",
  "ended",
]);

interface StatusBadgeMeta {
  label: string;
  fg: string;
  bg: string;
  bd: string;
  icon: string;
}

const STATUS_META: Record<string, StatusBadgeMeta> = {
  draft: { label: "Draft", fg: "#344054", bg: "#FFFFFF", bd: "#C3CAD6", icon: "pencil" },
  pending_verification: { label: "Submitted", fg: "#1D3F8F", bg: "#FFFFFF", bd: "#AFC2EE", icon: "send" },
  pending_admin_review: { label: "Under Review", fg: "#1D3F8F", bg: "#EEF3FF", bd: "#AFC2EE", icon: "eye" },
  tenant_response_submitted: { label: "Under Review", fg: "#1D3F8F", bg: "#EEF3FF", bd: "#AFC2EE", icon: "eye" },
  more_information_requested: { label: "Information Requested", fg: "#93370D", bg: "#FFF4E0", bd: "#F2B866", icon: "file" },
  approved: { label: "Approved", fg: "#05603A", bg: "#E7F8EF", bd: "#8FD9B4", icon: "check" },
  rejected: { label: "Not Approved", fg: "#344054", bg: "#F0F2F6", bd: "#C3CAD6", icon: "xcirc" },
  withdrawn: { label: "Withdrawn", fg: "#344054", bg: "#F0F2F6", bd: "#C3CAD6", icon: "undo" },
  expired: { label: "Expired", fg: "#344054", bg: "#F0F2F6", bd: "#C3CAD6", icon: "clock" },
  superseded: { label: "Superseded", fg: "#344054", bg: "#F0F2F6", bd: "#C3CAD6", icon: "pause" },
  cancelled_by_authority: { label: "Cancelled", fg: "#344054", bg: "#F0F2F6", bd: "#C3CAD6", icon: "pause" },
  awaiting_co_tenant: { label: "Awaiting Co-tenant", fg: "#93370D", bg: "#FFF4E0", bd: "#F2B866", icon: "clock" },
  declined_by_co_tenant: { label: "Declined by Co-tenant", fg: "#344054", bg: "#F0F2F6", bd: "#C3CAD6", icon: "xcirc" },
  ended: { label: "Ended", fg: "#344054", bg: "#F0F2F6", bd: "#C3CAD6", icon: "pause" },
};

// A co-tenant shares the room under the renter's own tenancy -- there's no
// downstream agreement to wait for.
function downstreamLabel(request: SubletRequest): string {
  if (request.arrangementType === "ADD_CO_TENANT") return request.sharedRoomActive ? "Room Shared" : "Not Shared";
  return request.newAgreementId ? "Agreement Signed" : "Agreement Pending";
}

export function SubletRequestsList() {
  const [requests, setRequests] = useState<SubletRequest[]>([]);
  const [occupancies, setOccupancies] = useState<UserOccupancy[]>([]);
  // Captured once so render stays pure.
  const [todayIso] = useState(() => new Date().toISOString().slice(0, 10));
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  // Filters & Tabs
  const [viewTab, setViewTab] = useState<"current" | "history">("current");
  const [activeCard, setActiveCard] = useState<"" | "draft" | "review" | "approved" | "action">("");
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [rentalFilter, setRentalFilter] = useState("all");
  const [datesFilter, setDatesFilter] = useState("all");
  const [scopeFilter, setScopeFilter] = useState("all");
  const [sortFilter, setSortFilter] = useState<"action" | "start" | "updated">("action");
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);

  // Drawer
  const [selectedRequestId, setSelectedRequestId] = useState<number | null>(null);
  const [drawerTab, setDrawerTab] = useState<"overview" | "info" | "subtenant" | "approval" | "documents" | "timeline" | "messages">("overview");
  const [drawerDocs, setDrawerDocs] = useState<SubletDocument[]>([]);
  const [loadingDocs, setLoadingDocs] = useState(false);
  const [uploadingDoc, setUploadingDoc] = useState(false);

  // Information Response Form
  const [infoFormName, setInfoFormName] = useState("");
  const [infoFormFrom, setInfoFormFrom] = useState("");
  const [infoFormTo, setInfoFormTo] = useState("");
  const [infoFormOcc, setInfoFormOcc] = useState("1");
  const [infoFormNotes, setInfoFormNotes] = useState("");
  const [infoSubmitting, setInfoSubmitting] = useState(false);
  const [infoError, setInfoError] = useState("");

  // Withdrawal modal
  const [withdrawTarget, setWithdrawTarget] = useState<SubletRequest | null>(null);
  const [withdrawing, setWithdrawing] = useState(false);

  // Creation Wizard Modal
  const [isWizardOpen, setIsWizardOpen] = useState(false);
  const [wizardStep, setWizardStep] = useState(1);
  const [wizardRentalId, setWizardRentalId] = useState<number | null>(null);
  const [wizardScope, setWizardScope] = useState<"named" | "principle">("named");
  const [wizardArrangement, setWizardArrangement] = useState<SubletArrangementType>("ASSIGNMENT_FULL");
  const [wizardFrom, setWizardFrom] = useState("");
  const [wizardTo, setWizardTo] = useState("");
  const [wizardSubtenantEmail, setWizardSubtenantEmail] = useState("");
  const [wizardSubtenantName, setWizardSubtenantName] = useState("");
  const [wizardSubtenantPartyId, setWizardSubtenantPartyId] = useState<number | null>(null);
  const [wizardRent, setWizardRent] = useState("");
  const [wizardPayee, setWizardPayee] = useState("Tenant / Sublessor (you)");
  const [wizardDeposit, setWizardDeposit] = useState("");
  const [wizardOccupants, setWizardOccupants] = useState("1");
  const [wizardError, setWizardError] = useState("");
  const [wizardSubmitting, setWizardSubmitting] = useState(false);
  const [lookupLoading, setLookupLoading] = useState(false);
  const [lookupMessage, setLookupMessage] = useState("");

  // Toast
  const [toastMessage, setToastMessage] = useState("");
  const toastTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const showToast = useCallback((msg: string) => {
    setToastMessage(msg);
    if (toastTimeoutRef.current) clearTimeout(toastTimeoutRef.current);
    toastTimeoutRef.current = setTimeout(() => {
      setToastMessage("");
    }, 3400);
  }, []);

  const loadData = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [reqs, occs] = await Promise.all([
        listSubletRequests(),
        listOccupancies().catch(() => [] as UserOccupancy[]),
      ]);
      setRequests(reqs);
      setOccupancies(occs);
    } catch (err) {
      setLoadError(errorMessage(err, "Could not load your sublet requests."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Body scroll lock effect
  useEffect(() => {
    const isModalOpen = selectedRequestId !== null || isWizardOpen || withdrawTarget !== null;
    if (isModalOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [selectedRequestId, isWizardOpen, withdrawTarget]);

  // Load documents when drawer opens on Documents tab
  useEffect(() => {
    if (selectedRequestId && drawerTab === "documents") {
      setLoadingDocs(true);
      listSubletDocuments(selectedRequestId)
        .then(setDrawerDocs)
        .catch(() => setDrawerDocs([]))
        .finally(() => setLoadingDocs(false));
    }
  }, [selectedRequestId, drawerTab]);

  // Map occupancy ID to occupancy record
  const occupancyMap = useMemo(() => {
    const map = new Map<number, UserOccupancy>();
    occupancies.forEach((occ) => map.set(occ.id, occ));
    return map;
  }, [occupancies]);

  // Selected request record
  const activeRequest = useMemo(
    () => requests.find((r) => r.id === selectedRequestId) || null,
    [requests, selectedRequestId]
  );

  // Compute metric counts
  const counts = useMemo(() => {
    let draft = 0;
    let review = 0;
    let approved = 0;
    let action = 0;

    requests.forEach((r) => {
      if (r.status === "draft") draft++;
      if (
        r.status === "pending_verification" ||
        r.status === "awaiting_co_tenant" ||
        r.status === "pending_admin_review" ||
        r.status === "tenant_response_submitted" ||
        r.status === "more_information_requested"
      ) {
        review++;
      }
      if (r.status === "approved") approved++;
      if (r.status === "more_information_requested") action++;
    });

    const currentTotal = requests.filter((r) => !HISTORY_STATUSES.has(r.status)).length;
    const historyTotal = requests.filter((r) => HISTORY_STATUSES.has(r.status)).length;

    return { draft, review, approved, action, currentTotal, historyTotal };
  }, [requests]);

  // Action required items
  const actionItems = useMemo(
    () => requests.filter((r) => r.status === "more_information_requested"),
    [requests]
  );

  // Filter & sort requests
  const filteredRequests = useMemo(() => {
    const pool = requests.filter((r) =>
      viewTab === "history" ? HISTORY_STATUSES.has(r.status) : !HISTORY_STATUSES.has(r.status)
    );

    return pool
      .filter((x) => {
        // Active summary card filter
        if (viewTab === "current" && activeCard) {
          if (activeCard === "action") {
            if (x.status !== "more_information_requested") return false;
          } else if (activeCard === "approved") {
            if (x.status !== "approved") return false;
          } else if (activeCard === "review") {
            if (
              !["pending_verification", "awaiting_co_tenant", "pending_admin_review", "tenant_response_submitted", "more_information_requested"].includes(
                x.status
              )
            )
              return false;
          } else if (activeCard === "draft") {
            if (x.status !== "draft") return false;
          }
        }

        // Dropdown Status filter
        if (statusFilter !== "all") {
          if (statusFilter === "draft" && x.status !== "draft") return false;
          if (
            statusFilter === "review" &&
            !["pending_verification", "awaiting_co_tenant", "pending_admin_review", "tenant_response_submitted"].includes(x.status)
          )
            return false;
          if (statusFilter === "info" && x.status !== "more_information_requested") return false;
          if (statusFilter === "approved" && x.status !== "approved") return false;
        }

        // Parent rental filter
        if (rentalFilter !== "all" && String(x.currentOccupancyId) !== rentalFilter) {
          return false;
        }

        // Dates filter
        if (datesFilter !== "all") {
          if (!x.proposedStartDate || !x.proposedStartDate.startsWith(datesFilter)) return false;
        }

        // Scope filter
        if (scopeFilter !== "all") {
          const isNamed = Boolean(x.proposedRenterPartyId || x.proposedRenterName);
          if (scopeFilter === "named" && !isNamed) return false;
          if (scopeFilter === "principle" && isNamed) return false;
        }

        // Search text
        if (searchQuery.trim()) {
          const q = searchQuery.toLowerCase().trim();
          const occ = occupancyMap.get(x.currentOccupancyId);
          const haystack = [
            x.listingName,
            x.listingCity,
            occ?.listingName,
            occ?.propertyAddress,
            occ?.propertyCity,
            `zr-r-${x.currentOccupancyId}`,
            `zr-sr-${x.id}`,
            `#${x.id}`,
            x.proposedRenterName,
          ]
            .filter(Boolean)
            .join(" ")
            .toLowerCase();
          if (!haystack.includes(q)) return false;
        }

        return true;
      })
      .sort((a, b) => {
        if (sortFilter === "start") {
          return (a.proposedStartDate || "").localeCompare(b.proposedStartDate || "");
        }
        if (sortFilter === "updated") {
          return (b.decidedAt || b.createdAt || "").localeCompare(a.decidedAt || a.createdAt || "");
        }
        // "action" sort: action required first
        const aAction = a.status === "more_information_requested" ? 0 : 1;
        const bAction = b.status === "more_information_requested" ? 0 : 1;
        if (aAction !== bAction) return aAction - bAction;
        return (b.createdAt || "").localeCompare(a.createdAt || "");
      });
  }, [
    requests,
    viewTab,
    activeCard,
    statusFilter,
    rentalFilter,
    datesFilter,
    scopeFilter,
    searchQuery,
    sortFilter,
    occupancyMap,
  ]);

  // Open drawer helper
  function openDrawer(request: SubletRequest, tab: "overview" | "info" | "subtenant" | "approval" | "documents" | "timeline" | "messages" = "overview") {
    setSelectedRequestId(request.id);
    setDrawerTab(tab);
    setInfoError("");
    setInfoFormName(request.proposedRenterName || "");
    setInfoFormFrom(request.proposedStartDate || "");
    setInfoFormTo(request.proposedEndDate || "");
    setInfoFormNotes("");
  }

  function closeDrawer() {
    setSelectedRequestId(null);
  }

  // Handle responding to information request
  async function handleSubmitInfoResponse(subletRequestId: number) {
    if (!infoFormNotes.trim() && !infoFormName.trim()) {
      setInfoError("Please enter your response note or updated information.");
      return;
    }
    setInfoSubmitting(true);
    setInfoError("");
    try {
      const responseText = [
        infoFormNotes.trim(),
        infoFormName.trim() ? `Proposed Subtenant: ${infoFormName.trim()}` : "",
        infoFormFrom && infoFormTo ? `Dates: ${infoFormFrom} to ${infoFormTo}` : "",
        infoFormOcc ? `Occupants: ${infoFormOcc}` : "",
      ]
        .filter(Boolean)
        .join(" | ");

      const updated = await respondToSubletInfoRequest(subletRequestId, responseText);
      setRequests((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      showToast("Response submitted. Your request is back under review.");
      setDrawerTab("overview");
    } catch (err) {
      setInfoError(errorMessage(err, "Failed to submit information response."));
    } finally {
      setInfoSubmitting(false);
    }
  }

  // Handle withdrawing a request
  async function handleConfirmWithdraw() {
    if (!withdrawTarget) return;
    setWithdrawing(true);
    try {
      const updated = await withdrawSubletRequest(withdrawTarget.id);
      setRequests((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      showToast("Request withdrawn. It is now saved in History.");
      setWithdrawTarget(null);
      if (selectedRequestId === withdrawTarget.id) {
        closeDrawer();
      }
    } catch (err) {
      showToast(errorMessage(err, "Could not withdraw this sublet request."));
    } finally {
      setWithdrawing(false);
    }
  }

  // Handle file upload in drawer
  async function handleDocumentUpload(file: File) {
    if (!selectedRequestId) return;
    setUploadingDoc(true);
    try {
      const doc = await uploadSubletDocument(selectedRequestId, file);
      setDrawerDocs((prev) => [doc, ...prev]);
      showToast("Document uploaded successfully.");
    } catch (err) {
      showToast(errorMessage(err, "Could not upload document."));
    } finally {
      setUploadingDoc(false);
    }
  }

  // Open creation wizard
  /** Mirrors backend crud/sublet.py:_assert_sublet_permitted (plus "one open
   *  request at a time"), so a rental that can't be sublet is shown with the
   *  reason instead of failing only after all seven steps. */
  function subletBlockReason(occ: UserOccupancy): string | null {
    if (occ.reassignedViaSubletRequestId) {
      return "You received this room through a sublet handover -- it can't be sublet again.";
    }
    if (occ.status !== "ACTIVE") return "Only an active rental can be sublet.";
    const daysLeft = occ.expectedEndDate
      ? Math.round((Date.parse(occ.expectedEndDate) - Date.parse(todayIso)) / 86400000)
      : null;
    if (daysLeft === null || daysLeft < 30) return "A sublet needs at least 30 nights left on the agreement.";
    const OPEN = ["pending_verification", "awaiting_co_tenant", "pending_admin_review", "more_information_requested", "tenant_response_submitted"];
    if (requests.some((r) => r.currentOccupancyId === occ.id && OPEN.includes(r.status))) {
      return "There's already an open sublet request for this rental.";
    }
    return null;
  }

  function openCreateWizard(preselectedRentalId?: number) {
    setWizardStep(1);
    const preselected = occupancies.find((o) => o.id === preselectedRentalId);
    const defaultOcc =
      (preselected && !subletBlockReason(preselected) ? preselected : undefined) ??
      occupancies.find((o) => !subletBlockReason(o));
    setWizardRentalId(defaultOcc?.id ?? null);
    setWizardScope("named");
    setWizardArrangement("ASSIGNMENT_FULL");
    setWizardFrom(defaultOcc ? (defaultOcc.moveInDate && defaultOcc.moveInDate > todayIso ? defaultOcc.moveInDate : todayIso) : "");
    setWizardTo(defaultOcc?.expectedEndDate || "");
    setWizardSubtenantEmail("");
    setWizardSubtenantName("");
    setWizardSubtenantPartyId(null);
    setWizardRent("");
    setWizardPayee("Tenant / Sublessor (you)");
    setWizardDeposit("");
    setWizardOccupants("1");
    setWizardError("");
    setLookupMessage("");
    setIsWizardOpen(true);
  }

  // Lookup subtenant by email
  async function handleLookupEmail() {
    if (!wizardSubtenantEmail.trim()) return;
    setLookupLoading(true);
    setLookupMessage("");
    try {
      const res = await lookupSubletRenter(wizardSubtenantEmail.trim());
      if (res.found && res.partyId) {
        setWizardSubtenantPartyId(res.partyId);
        if (res.name) setWizardSubtenantName(res.name);
        setLookupMessage(
          `User found: ${res.name || "Registered Renter"} (${res.identityVerified ? "Identity Verified" : "Verification pending"})`
        );
      } else {
        setWizardSubtenantPartyId(null);
        setLookupMessage("No user found with this email. An invitation can be sent upon approval.");
      }
    } catch {
      setWizardSubtenantPartyId(null);
      setLookupMessage("Unable to verify user account. You can still propose their name.");
    } finally {
      setLookupLoading(false);
    }
  }

  // Next step in wizard
  async function handleWizardNext() {
    setWizardError("");
    if (wizardStep === 1) {
      if (!wizardRentalId) {
        setWizardError("Choose a rental.");
        return;
      }
      const chosen = occupancies.find((o) => o.id === wizardRentalId);
      const blocked = chosen ? subletBlockReason(chosen) : "Choose a rental.";
      if (blocked) {
        setWizardError(blocked);
        return;
      }
      setWizardStep(2);
      return;
    }

    if (wizardStep === 2) {
      setWizardStep(3);
      return;
    }

    if (wizardStep === 3) {
      if (!wizardFrom || !wizardTo) {
        setWizardError("Please enter both the start and end dates.");
        return;
      }
      const parentOcc = occupancies.find((o) => o.id === wizardRentalId);
      if (parentOcc?.moveInDate && wizardFrom < parentOcc.moveInDate) {
        setWizardError(`The start date cannot precede your rental start date (${formatDate(parentOcc.moveInDate)}).`);
        return;
      }
      if (parentOcc?.expectedEndDate && wizardTo > parentOcc.expectedEndDate) {
        setWizardError(`The end date cannot exceed your rental end date (${formatDate(parentOcc.expectedEndDate)}).`);
        return;
      }
      if (wizardTo <= wizardFrom) {
        setWizardError("The end date must be after the start date.");
        return;
      }
      setWizardStep(4);
      return;
    }

    if (wizardStep === 4) {
      if (wizardScope === "named" && !wizardSubtenantName.trim()) {
        setWizardError("Please enter the proposed subtenant's name.");
        return;
      }
      setWizardStep(5);
      return;
    }

    if (wizardStep === 5) {
      setWizardStep(6);
      return;
    }

    if (wizardStep === 6) {
      setWizardStep(7);
      return;
    }

    if (wizardStep === 7) {
      if (!wizardRentalId) return;
      setWizardSubmitting(true);
      try {
        const payload = {
          proposedRenterPartyId: wizardSubtenantPartyId || 0,
          arrangementType: wizardArrangement,
          proposedMonthlyRent: wizardRent ? Number(wizardRent.replace(/\D/g, "")) : undefined,
          proposedStartDate: wizardFrom,
          proposedEndDate: wizardTo,
          reason: `Subletting to ${wizardSubtenantName || "subtenant"}. Occupants: ${wizardOccupants}.`,
        };
        const created = await submitSubletRequest(wizardRentalId, payload);
        setRequests((prev) => [created, ...prev]);
        setIsWizardOpen(false);
        showToast("Request submitted to Property Owner / Landlord. This isn’t permission yet.");
      } catch (err) {
        setWizardError(errorMessage(err, "Failed to submit sublet request."));
      } finally {
        setWizardSubmitting(false);
      }
    }
  }

  // Save as draft in wizard
  async function handleWizardSaveDraft() {
    if (!wizardRentalId) return;
    try {
      const draft = await createSubletRequestDraft(wizardRentalId);
      setRequests((prev) => [draft, ...prev]);
      setIsWizardOpen(false);
      showToast("Draft saved. It stays private until you submit.");
    } catch {
      setIsWizardOpen(false);
      showToast("Draft saved locally.");
    }
  }

  // Clear all filters
  function clearAllFilters() {
    setSearchQuery("");
    setActiveCard("");
    setStatusFilter("all");
    setRentalFilter("all");
    setDatesFilter("all");
    setScopeFilter("all");
  }

  const hasActiveFilters = Boolean(
    searchQuery ||
      activeCard ||
      statusFilter !== "all" ||
      rentalFilter !== "all" ||
      datesFilter !== "all" ||
      scopeFilter !== "all"
  );

  // Term duration helper
  function computeTermDuration(from?: string | null, to?: string | null) {
    if (!from || !to) return "—";
    const d1 = new Date(from);
    const d2 = new Date(to);
    const diffDays = Math.round((d2.getTime() - d1.getTime()) / (1000 * 60 * 60 * 24));
    if (diffDays <= 0) return "—";
    const months = Math.round(diffDays / 30);
    if (months >= 1) return `${months} month${months > 1 ? "s" : ""}`;
    const weeks = Math.round(diffDays / 7);
    return `${weeks} week${weeks > 1 ? "s" : ""}`;
  }

  return (
    <>
      {/* Inline styles mapped pixel-for-pixel from prototype HTML */}
      <style jsx global>{`
        .scards {
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 14px;
        }
        @media (max-width: 767px) {
          .scards {
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
          }
        }
        .scard {
          display: flex;
          align-items: center;
          gap: 14px;
          text-align: left;
          background: #ffffff;
          border: 2px solid #e1e7f1;
          border-radius: 20px;
          padding: 16px 16px 16px 14px;
          cursor: pointer;
          transition: border-color 0.15s, background-color 0.15s;
        }
        .scard:hover {
          border-color: #b8c6e0;
        }
        .scard[aria-pressed="true"] {
          border-color: #0e2f73;
          background: #f5f8fe;
        }
        .scard--action[aria-pressed="true"] {
          border-color: #b42318;
          background: #fff7f7;
        }
        .snum {
          flex-shrink: 0;
          width: 52px;
          height: 52px;
          border-radius: 16px;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 24px;
          font-weight: 800;
          font-variant-numeric: tabular-nums;
        }
        .stext {
          display: flex;
          flex-direction: column;
          gap: 3px;
          flex-grow: 1;
          min-width: 0;
        }
        .stitle {
          font-size: 16px;
          font-weight: 800;
          color: #0b1b3f;
        }
        .shint {
          font-size: 12.5px;
          color: #4a5878;
        }
        .sico {
          align-self: flex-start;
          opacity: 0.85;
        }

        .astrip {
          display: flex;
          align-items: center;
          gap: 18px;
          flex-wrap: wrap;
          padding: 12px 14px 12px 18px;
          border-radius: 18px;
          background: #fff5f5;
          border: 1px solid #f3c2c2;
        }
        .astrip-title {
          display: flex;
          align-items: center;
          gap: 8px;
          color: #b42318;
          font-size: 14px;
          font-weight: 700;
        }
        .aitems {
          flex-grow: 1;
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .aitem {
          display: flex;
          align-items: center;
          gap: 10px;
          flex-wrap: wrap;
        }
        .atext {
          flex-grow: 1;
          font-size: 14px;
          color: #3a1b1b;
        }

        .filters-wrap {
          display: grid;
          grid-template-columns: minmax(260px, 1fr) auto;
          gap: 10px;
          align-items: end;
          background: #ffffff;
          border: 1px solid #e1e7f1;
          border-radius: 20px;
          padding: 14px;
        }
        @media (max-width: 1199px) {
          .filters-wrap {
            grid-template-columns: 1fr;
          }
        }
        .searchline {
          display: flex;
          gap: 10px;
        }
        .filter-toggle {
          display: none !important;
        }
        @media (max-width: 767px) {
          .filter-toggle {
            display: inline-flex !important;
          }
        }
        .search-box {
          flex-grow: 1;
          display: flex;
          align-items: center;
          gap: 8px;
          height: 46px;
          padding: 0 16px;
          border-radius: 9999px;
          background: #f4f6fb;
          border: 1px solid #d5ddea;
        }
        .search-box input {
          flex-grow: 1;
          min-width: 0;
          border: 0;
          background: transparent;
          font-size: 14px;
          outline: none;
        }
        .fpanel {
          display: flex;
          gap: 10px;
          align-items: end;
        }
        @media (max-width: 1199px) {
          .fpanel {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
          }
        }
        @media (max-width: 767px) {
          .fpanel {
            display: none;
          }
          .fpanel.open {
            display: flex;
            flex-direction: column;
            align-items: stretch;
            gap: 14px;
            position: fixed;
            left: 0;
            right: 0;
            bottom: 0;
            z-index: 60;
            background: #ffffff;
            border-radius: 24px 24px 0 0;
            padding: 18px 18px 24px;
            box-shadow: 0 -20px 50px rgba(8, 22, 56, 0.25);
          }
        }
        .field {
          display: flex;
          flex-direction: column;
          gap: 5px;
        }
        .field label {
          font-size: 11.5px;
          font-weight: 700;
          color: #3a4866;
        }
        .field input {
          height: 46px;
          min-width: 136px;
          padding: 0 12px;
          border-radius: 14px;
          border: 1px solid #d5ddea;
          background: #ffffff;
          font-size: 13.5px;
          outline: none;
        }
        .field select {
          height: 46px;
          min-width: 136px;
          padding: 0 16px;
          border-radius: 9999px;
          border: 1px solid #d5ddea;
          background: #ffffff;
          font-size: 13.5px;
          outline: none;
          cursor: pointer;
        }
        @media (max-width: 1199px) {
          .field select {
            min-width: 0;
            width: 100%;
          }
        }

        .workspace {
          background: #ffffff;
          border: 1px solid #e1e7f1;
          border-radius: 24px;
          overflow: visible;
        }
        .ws-bar {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 14px 22px;
          border-bottom: 1px solid #eef1f7;
          gap: 12px;
          flex-wrap: wrap;
        }
        .tabs {
          display: flex;
          gap: 4px;
          padding: 4px;
          border-radius: 12px;
          background: #eef2f9;
        }
        .tab {
          min-height: 38px;
          padding: 0 14px;
          border-radius: 9px;
          border: 0;
          background: none;
          font-size: 13px;
          font-weight: 700;
          color: #3a4866;
          cursor: pointer;
        }
        .tab[aria-selected="true"] {
          background: #ffffff;
          color: #0e2f73;
          box-shadow: 0 1px 3px rgba(8, 22, 56, 0.12);
        }

        .acols,
        .arow.srq {
          display: grid;
          grid-template-columns: minmax(200px, 1.3fr) minmax(130px, 0.9fr) minmax(130px, 0.9fr) minmax(170px, 1.1fr) minmax(170px, 1.1fr) 160px;
          gap: 14px;
        }
        @media (min-width: 1200px) and (max-width: 1399px) {
          .acols,
          .arow.srq {
            grid-template-columns: minmax(170px, 1.3fr) minmax(110px, 0.9fr) minmax(110px, 0.9fr) minmax(150px, 1.1fr) minmax(150px, 1.1fr) 150px;
            gap: 10px;
          }
        }
        @media (max-width: 1199px) {
          .acols {
            display: none;
          }
          .arow.srq {
            grid-template-columns: repeat(3, minmax(0, 1fr));
            row-gap: 12px;
          }
          .arow.srq .who {
            grid-column: 1 / 3;
          }
          .arow.srq .aact {
            grid-column: 3;
            grid-row: 1;
          }
        }
        @media (max-width: 767px) {
          .arow.srq {
            display: flex;
            flex-direction: column;
            align-items: stretch;
            gap: 10px;
            padding: 16px;
          }
          .arow.srq .astat {
            order: 1;
          }
          .arow.srq .areq {
            order: 2;
          }
          .arow.srq .aagr {
            order: 3;
          }
          .arow.srq .aroom {
            order: 4;
          }
          .arow.srq .aact {
            order: 5;
          }
          .arow.srq .aact .cta {
            width: 100%;
          }
        }

        .acols {
          padding: 12px 22px;
          background: #f7f9fd;
          font-size: 11px;
          font-weight: 700;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: #5b6886;
          border-bottom: 1px solid #eef1f7;
        }
        .acols .r {
          text-align: right;
        }
        .arow {
          align-items: center;
          padding: 16px 22px;
          border-bottom: 1px solid #eef1f7;
          transition: background-color 0.15s;
        }
        .arow:last-child {
          border-bottom: 0;
          border-radius: 0 0 24px 24px;
        }
        .arow--action {
          background: #fffaf5;
        }
        .arow--closed {
          opacity: 0.92;
        }

        .who {
          display: flex;
          gap: 12px;
          align-items: center;
          text-align: left;
          border: 0;
          background: none;
          padding: 4px;
          margin: -4px;
          border-radius: 14px;
          min-width: 0;
          cursor: pointer;
        }
        .who:hover .aname {
          color: #d80b0b;
        }
        .thumbsm {
          flex-shrink: 0;
          width: 46px;
          height: 46px;
          border-radius: 12px;
          background: #e3e9f6;
          color: #0e2f73;
          display: inline-flex;
          align-items: center;
          justify-content: center;
        }
        .thumbsm.lg {
          width: 56px;
          height: 56px;
        }
        .whot {
          display: flex;
          flex-direction: column;
          gap: 3px;
          min-width: 0;
        }
        .aname {
          font-size: 15px;
          font-weight: 800;
          color: #0b1b3f;
        }
        .muted {
          color: #4a5878;
          font-size: 13px;
        }
        .small {
          font-size: 12px;
        }
        .mono {
          font-variant-numeric: tabular-nums;
          font-size: 13px;
        }
        .zl {
          display: none;
          font-size: 11px;
          font-weight: 700;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: #5b6886;
        }
        @media (max-width: 1199px) {
          .zl {
            display: block;
          }
        }
        .aroom,
        .areq,
        .aagr,
        .astat {
          display: flex;
          flex-direction: column;
          gap: 3px;
          min-width: 0;
        }
        .abadge {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          min-height: 28px;
          padding: 2px 11px 2px 9px;
          border-radius: 999px;
          border: 1.5px solid;
          font-size: 11.5px;
          font-weight: 800;
          letter-spacing: 0.05em;
          text-transform: uppercase;
          white-space: nowrap;
          width: fit-content;
        }
        .vflag {
          display: inline-flex;
          align-items: center;
          gap: 5px;
          font-size: 12px;
          font-weight: 600;
          color: #3a4866;
        }
        .aact {
          display: flex;
          gap: 6px;
          justify-content: flex-end;
          align-items: center;
        }
        .aact .cta {
          flex-grow: 1;
          max-width: 160px;
          min-height: 40px;
        }

        .btn {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 8px;
          min-height: 44px;
          padding: 0 16px;
          border-radius: 12px;
          border: 1.5px solid transparent;
          font-size: 13px;
          font-weight: 700;
          white-space: nowrap;
          background: none;
          cursor: pointer;
          transition: background-color 0.15s, border-color 0.15s;
        }
        .btn-lg {
          min-height: 50px;
          padding: 0 24px;
          border-radius: 14px;
          font-size: 15px;
        }
        .btn-navy {
          background: #0e2f73;
          color: #ffffff;
        }
        .btn-navy:hover {
          background: #0b2560;
        }
        .btn-lg.btn-navy {
          box-shadow: 0 10px 24px rgba(14, 47, 115, 0.22);
        }
        .btn-red {
          background: #d80b0b;
          color: #ffffff;
        }
        .btn-red:hover {
          background: #b80909;
        }
        .btn-outline {
          background: #ffffff;
          border-color: #c7d2e6;
          color: #0e2f73;
        }
        .btn-outline:hover {
          border-color: #0e2f73;
        }
        .btn-soft {
          background: #eef2f9;
          color: #0e2f73;
        }
        .btn-soft:hover {
          background: #e1e8f5;
        }
        .btn-ghost {
          color: #0e2f73;
        }
        .btn-ghost:hover {
          background: #f2f5fb;
        }
        .btn-danger {
          background: #b42318;
          color: #ffffff;
        }
        .btn-sm {
          min-height: 44px;
          padding: 0 14px;
        }
        .btn-xs {
          min-height: 34px;
          padding: 0 12px;
          border-radius: 10px;
          font-size: 12px;
        }
        .icon-btn {
          position: relative;
          flex-shrink: 0;
          width: 44px;
          height: 44px;
          border-radius: 12px;
          border: 0;
          background: transparent;
          color: #3a4866;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          cursor: pointer;
        }
        .icon-btn.bordered {
          border: 1px solid #d5ddea;
          background: #ffffff;
          color: #0e2f73;
        }
        .icon-btn:hover {
          background: #eef2f9;
        }

        .empty-demo {
          position: relative;
          display: flex;
          align-items: center;
          gap: 16px;
          flex-wrap: wrap;
          padding: 22px;
          border: 1.5px dashed #c7d2e6;
          border-radius: 22px;
          background: #f8fafd;
        }
        .empty-ill {
          width: 60px;
          height: 60px;
          border-radius: 18px;
          background: #eaf0fb;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .ref-tag {
          position: absolute;
          top: -10px;
          left: 18px;
          padding: 2px 8px;
          border-radius: 6px;
          background: #edf1f8;
          font-size: 11px;
          font-weight: 600;
          color: #5b6886;
        }

        .overlay {
          position: fixed;
          inset: 0;
          z-index: 9999;
          display: flex;
          justify-content: flex-end;
        }
        .overlay.center {
          justify-content: center;
          align-items: center;
          padding: 16px;
        }
        .scrim {
          position: absolute;
          inset: 0;
          border: 0;
          background: rgba(8, 22, 56, 0.45);
          cursor: pointer;
        }
        .drawer {
          position: relative;
          width: 520px;
          max-width: 100%;
          height: 100%;
          overflow-y: auto;
          background: #ffffff;
          border-radius: 24px 0 0 24px;
          padding: 24px;
          display: flex;
          flex-direction: column;
          gap: 18px;
          box-shadow: -20px 0 50px rgba(8, 22, 56, 0.25);
          animation: slide 0.22s ease-out;
        }
        @keyframes slide {
          from {
            transform: translateX(24px);
            opacity: 0.6;
          }
          to {
            transform: none;
            opacity: 1;
          }
        }
        .dhead {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 12px;
        }
        .dwho {
          display: flex;
          gap: 14px;
          align-items: center;
        }
        .dwho h2 {
          font-size: 21px;
          font-weight: 800;
          color: #0e2f73;
          margin: 0 0 4px;
        }
        .dstate {
          display: flex;
          align-items: center;
          gap: 10px;
          flex-wrap: wrap;
        }
        .dtabs {
          display: flex;
          gap: 2px;
          border-bottom: 1px solid #e1e7f1;
          overflow-x: auto;
        }
        .dtab {
          min-height: 42px;
          padding: 0 12px;
          border: 0;
          background: none;
          font-size: 13px;
          font-weight: 700;
          color: #4a5878;
          border-bottom: 2px solid transparent;
          white-space: nowrap;
          cursor: pointer;
        }
        .dtab[aria-selected="true"] {
          color: #0e2f73;
          border-bottom-color: #0e2f73;
        }
        .dtbody {
          display: flex;
          flex-direction: column;
          gap: 16px;
        }
        .dacts {
          position: sticky;
          bottom: -24px;
          margin: auto -24px -24px;
          padding: 16px 24px;
          background: #ffffff;
          border-top: 1px solid #eef1f7;
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
        }

        .kv2 {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 8px;
        }
        .kv2 div {
          display: flex;
          flex-direction: column;
          gap: 3px;
          padding: 12px;
          border: 1px solid #e1e7f1;
          border-radius: 14px;
        }
        .kv2 span {
          font-size: 11px;
          font-weight: 700;
          letter-spacing: 0.06em;
          text-transform: uppercase;
          color: #5b6886;
        }
        .kv2 strong {
          font-size: 13.5px;
          color: #0b1b3f;
        }
        .scopebox {
          display: flex;
          flex-direction: column;
          align-items: flex-start;
          gap: 5px;
          padding: 14px;
          border: 1px solid #e1e7f1;
          border-radius: 14px;
          width: 100%;
        }
        .scopetag {
          font-size: 11px;
          font-weight: 800;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: #1d3f8f;
          background: #eef3ff;
          padding: 3px 8px;
          border-radius: 6px;
        }
        .route {
          display: flex;
          gap: 8px;
          align-items: center;
          font-size: 14px;
          font-weight: 700;
          color: #0e2f73;
        }
        .dsec {
          display: flex;
          flex-direction: column;
          gap: 10px;
        }
        .dsec h3 {
          font-size: 12.5px;
          font-weight: 800;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: #3a4866;
        }
        .dalert,
        .dinfo {
          display: flex;
          gap: 10px;
          padding: 12px 14px;
          border-radius: 14px;
          font-size: 13.5px;
          line-height: 1.45;
          font-weight: 600;
        }
        .dalert {
          background: #fdecec;
          color: #b42318;
        }
        .dalert.warn {
          background: #fff4e0;
          color: #93370d;
        }
        .dinfo {
          background: #eef3ff;
          color: #1d3f8f;
        }
        .ferr {
          color: #b42318;
          font-size: 13px;
          font-weight: 700;
        }
        .amlist {
          list-style: none;
          margin: 0;
          padding: 0;
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .amlist li {
          display: flex;
          gap: 8px;
          align-items: center;
          font-size: 13.5px;
          font-weight: 600;
          color: #0b1b3f;
        }
        .gates {
          list-style: none;
          margin: 0;
          padding: 0;
          border: 1px solid #e1e7f1;
          border-radius: 16px;
        }
        .gate {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 10px 14px;
          border-bottom: 1px solid #eef1f7;
        }
        .gate:last-child {
          border-bottom: 0;
        }
        .gico {
          width: 24px;
          height: 24px;
          border-radius: 7px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          flex-shrink: 0;
        }
        .gname {
          font-size: 13.5px;
          font-weight: 700;
          flex-grow: 1;
          display: flex;
          flex-direction: column;
        }
        .gstate {
          font-size: 12.5px;
          font-weight: 700;
        }
        .evl {
          list-style: none;
          margin: 0;
          padding: 0;
          border: 1px solid #e1e7f1;
          border-radius: 16px;
        }
        .evi {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 12px 14px;
          border-bottom: 1px solid #eef1f7;
          font-size: 13.5px;
        }
        .evi:last-child {
          border-bottom: 0;
        }
        .evico {
          width: 34px;
          height: 34px;
          border-radius: 10px;
          background: #eef2f9;
          color: #0e2f73;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          flex-shrink: 0;
        }
        .tl {
          list-style: none;
          margin: 0;
          padding: 0 0 0 4px;
        }
        .tl li {
          position: relative;
          display: flex;
          gap: 12px;
          padding: 0 0 14px;
        }
        .tl li:not(:last-child)::after {
          content: "";
          position: absolute;
          left: 5px;
          top: 18px;
          bottom: 2px;
          width: 2px;
          background: #e1e7f1;
        }
        .tdot {
          flex-shrink: 0;
          width: 12px;
          height: 12px;
          border-radius: 50%;
          background: #0e2f73;
          margin-top: 4px;
          box-shadow: 0 0 0 3px #eaf0fb;
        }
        .dialog {
          position: relative;
          background: #ffffff;
          border-radius: 20px;
          padding: 24px;
          max-width: 440px;
          width: 100%;
          display: flex;
          flex-direction: column;
          gap: 12px;
          box-shadow: 0 24px 60px rgba(8, 22, 56, 0.3);
          max-height: calc(100vh - 32px);
          overflow-y: auto;
        }
        .dialog.wide {
          max-width: 640px;
        }
        .dialog h2 {
          font-size: 20px;
          font-weight: 800;
          color: #0b1b3f;
          margin: 0;
        }
        .fhead {
          display: flex;
          flex-direction: column;
          gap: 2px;
        }
        .fprog {
          height: 6px;
          border-radius: 3px;
          background: #eef1f7;
          overflow: hidden;
          margin: 4px 0 8px;
        }
        .fprog span {
          display: block;
          height: 100%;
          background: #0e2f73;
          transition: width 0.2s ease-in-out;
        }
        .roles {
          border: 0;
          margin: 0;
          padding: 0;
          display: flex;
          flex-direction: column;
          gap: 10px;
        }
        .roles legend {
          font-size: 15px;
          font-weight: 800;
          color: #0b1b3f;
          margin-bottom: 8px;
          padding: 0;
        }
        .radio {
          position: relative;
          display: flex;
          align-items: center;
          gap: 12px;
          min-height: 56px;
          padding: 12px 14px;
          border-radius: 14px;
          border: 1.5px solid #d5ddea;
          font-size: 14px;
          font-weight: 600;
          cursor: pointer;
        }
        .radio input {
          position: absolute;
          opacity: 0;
          width: 1px;
          height: 1px;
        }
        .rdot {
          flex-shrink: 0;
          width: 20px;
          height: 20px;
          border-radius: 50%;
          border: 2px solid #9aa7bf;
        }
        .radio:has(input:checked) {
          border-color: #0e2f73;
          background: #f5f8fe;
        }
        .radio:has(input:checked) .rdot {
          border: 6px solid #0e2f73;
        }
        .fgrid2 {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 10px;
        }
        @media (max-width: 767px) {
          .fgrid2 {
            grid-template-columns: 1fr;
          }
        }
        .tinput {
          width: 100%;
          height: 44px;
          padding: 0 12px;
          border-radius: 12px;
          border: 1px solid #d5ddea;
          font-size: 14px;
          font-family: inherit;
          outline: none;
        }
        select.tinput {
          border-radius: 9999px;
          padding: 0 16px;
          cursor: pointer;
        }
        .dlg-acts {
          display: flex;
          justify-content: flex-end;
          align-items: center;
          gap: 8px;
          margin-top: 10px;
        }
        .toast {
          position: fixed;
          left: 50%;
          bottom: 24px;
          transform: translateX(-50%);
          z-index: 10000;
          background: #0b1b3f;
          color: #ffffff;
          padding: 12px 20px;
          border-radius: 12px;
          font-size: 13.5px;
          font-weight: 600;
          box-shadow: 0 12px 30px rgba(8, 22, 56, 0.3);
          animation: pop 0.2s ease-out;
        }
        @keyframes pop {
          from {
            transform: translate(-50%, 10px);
            opacity: 0;
          }
          to {
            transform: translate(-50%, 0);
            opacity: 1;
          }
        }
      `}</style>

      <div className="flex flex-col gap-[18px] min-w-0">
        {/* Page Header */}
        <section className="flex justify-between items-end gap-5 flex-wrap pt-2 px-1">
          <div>
            <div className="text-xs font-bold uppercase tracking-wider text-[#D80B0B]">
              Renter workspace
            </div>
            <h1 className="text-[34px] sm:text-[38px] font-extrabold text-[#0E2F73] tracking-tight leading-tight mt-1">
              Sublet Requests
            </h1>
            <p className="text-[14px] sm:text-[15px] text-[#4A5878] mt-1.5">
              Request permission to sublet, respond to questions, review decisions and track approved sublets.
            </p>
          </div>
          <button
            type="button"
            className="btn btn-navy btn-lg"
            onClick={() => openCreateWizard()}
          >
            <SvgIcon name="plus" size={16} strokeWidth={2.4} />
            Request to Sublet
          </button>
        </section>

        {/* 4 Summary Metric Cards */}
        <section
          className="scards"
          aria-label="Sublet request summary — each card filters current requests"
        >
          {/* Drafts */}
          <button
            type="button"
            className="scard"
            aria-pressed={viewTab === "current" && activeCard === "draft"}
            onClick={() => {
              setViewTab("current");
              setActiveCard((prev) => (prev === "draft" ? "" : "draft"));
            }}
          >
            <span className="snum" style={{ background: "#F0F2F6", color: "#344054" }}>
              {counts.draft}
            </span>
            <span className="stext">
              <span className="stitle">Drafts</span>
              <span className="shint">Private — not sent</span>
            </span>
            <span className="sico" style={{ color: "#344054" }}>
              <SvgIcon name="pencil" size={18} strokeWidth={2.2} />
            </span>
          </button>

          {/* Under Review */}
          <button
            type="button"
            className="scard"
            aria-pressed={viewTab === "current" && activeCard === "review"}
            onClick={() => {
              setViewTab("current");
              setActiveCard((prev) => (prev === "review" ? "" : "review"));
            }}
          >
            <span className="snum" style={{ background: "#EEF3FF", color: "#1D3F8F" }}>
              {counts.review}
            </span>
            <span className="stext">
              <span className="stitle">Under Review</span>
              <span className="shint">With the decision-maker</span>
            </span>
            <span className="sico" style={{ color: "#1D3F8F" }}>
              <SvgIcon name="eye" size={18} strokeWidth={2.2} />
            </span>
          </button>

          {/* Approved */}
          <button
            type="button"
            className="scard"
            aria-pressed={viewTab === "current" && activeCard === "approved"}
            onClick={() => {
              setViewTab("current");
              setActiveCard((prev) => (prev === "approved" ? "" : "approved"));
            }}
          >
            <span className="snum" style={{ background: "#E7F8EF", color: "#05603A" }}>
              {counts.approved}
            </span>
            <span className="stext">
              <span className="stitle">Approved</span>
              <span className="shint">Permission granted</span>
            </span>
            <span className="sico" style={{ color: "#05603A" }}>
              <SvgIcon name="check" size={18} strokeWidth={2.2} />
            </span>
          </button>

          {/* Action Required */}
          <button
            type="button"
            className="scard scard--action"
            aria-pressed={viewTab === "current" && activeCard === "action"}
            onClick={() => {
              setViewTab("current");
              setActiveCard((prev) => (prev === "action" ? "" : "action"));
            }}
          >
            <span className="snum" style={{ background: "#FDECEC", color: "#B42318" }}>
              {counts.action}
            </span>
            <span className="stext">
              <span className="stitle">Action Required</span>
              <span className="shint">Needs your response</span>
            </span>
            <span className="sico" style={{ color: "#B42318" }}>
              <SvgIcon name="alert" size={18} strokeWidth={2.2} />
            </span>
          </button>
        </section>

        {/* Action Strip Banner (only shown if any request requires attention) */}
        {actionItems.length > 0 && (
          <section className="astrip" aria-labelledby="att-title">
            <div className="astrip-title">
              <SvgIcon name="alert" size={18} strokeWidth={2.2} color="#B42318" />
              <strong id="att-title">
                {actionItems.length} sublet request{actionItems.length > 1 ? "s" : ""} need{actionItems.length === 1 ? "s" : ""} your attention
              </strong>
            </div>
            <div className="aitems">
              {actionItems.map((item) => (
                <div key={item.id} className="aitem">
                  <span className="atext">
                    The Host requested information for Request #ZR-SR-{item.id}
                    {item.infoRequestNote ? `: "${item.infoRequestNote}"` : "."}
                  </span>
                  <button
                    type="button"
                    className="btn btn-red btn-sm"
                    onClick={() => openDrawer(item, "info")}
                  >
                    Respond
                  </button>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Search & Filter Bar */}
        <section className="filters-wrap" aria-label="Search and filters">
          <div className="searchline">
            <div className="search-box">
              <SvgIcon name="search" size={16} strokeWidth={2.2} color="#4A5878" />
              <input
                type="search"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search property, room, Rental ID, Request ID or subtenant"
              />
            </div>
            <button
              type="button"
              className="btn btn-outline btn-sm filter-toggle"
              id="ftoggle"
              aria-expanded={mobileFiltersOpen}
              aria-controls="fpanel"
              onClick={() => setMobileFiltersOpen(true)}
            >
              <SvgIcon name="card" size={16} /> Filters
            </button>
          </div>

          <div className={`fpanel ${mobileFiltersOpen ? "open" : ""}`}>
            {mobileFiltersOpen && (
              <div className="flex justify-between items-center text-lg font-bold pb-2 border-b border-[#E1E7F1] sm:hidden">
                <span>Filters</span>
                <button
                  type="button"
                  className="icon-btn"
                  onClick={() => setMobileFiltersOpen(false)}
                >
                  <SvgIcon name="x" size={18} strokeWidth={2.4} />
                </button>
              </div>
            )}

            {/* Status */}
            <RoundedFilterDropdown
              label="Status"
              value={statusFilter}
              onChange={setStatusFilter}
              options={[
                { value: "all", label: "All statuses" },
                { value: "draft", label: "Draft" },
                { value: "review", label: "Under Review" },
                { value: "info", label: "Information Requested" },
                { value: "approved", label: "Approved" },
              ]}
              size="md"
            />

            {/* Parent Rental */}
            <RoundedFilterDropdown
              label="Parent Rental"
              value={rentalFilter}
              onChange={setRentalFilter}
              options={[
                { value: "all", label: "All rentals" },
                ...occupancies.map((occ) => ({
                  value: String(occ.id),
                  label: `${occ.listingName || `Rental #${occ.id}`} · Room #${occ.roomId}`,
                })),
              ]}
              size="md"
            />

            {/* Dates */}
            <RoundedFilterDropdown
              label="Requested Dates"
              value={datesFilter}
              onChange={setDatesFilter}
              options={[
                { value: "all", label: "Any dates" },
                { value: "2026", label: "Starting in 2026" },
                { value: "2027", label: "Starting in 2027" },
              ]}
              size="md"
            />

            {/* Scope */}
            <RoundedFilterDropdown
              label="Scope"
              value={scopeFilter}
              onChange={setScopeFilter}
              options={[
                { value: "all", label: "Any scope" },
                { value: "named", label: "Named Subtenant" },
                { value: "principle", label: "Permission in Principle" },
              ]}
              size="md"
            />

            {/* Sort */}
            <RoundedFilterDropdown
              label="Sort"
              value={sortFilter}
              onChange={(val) => setSortFilter(val as "action" | "start" | "updated")}
              options={[
                { value: "action", label: "Action required first" },
                { value: "start", label: "Requested start" },
                { value: "updated", label: "Recently updated" },
              ]}
              align="right"
              size="md"
            />

            {mobileFiltersOpen && (
              <button
                type="button"
                className="btn btn-navy mt-3 w-full"
                onClick={() => setMobileFiltersOpen(false)}
              >
                Show results
              </button>
            )}
          </div>
        </section>

        {/* Workspace Table Box */}
        <section className="workspace" aria-labelledby="ws-count">
          <div className="ws-bar">
            <div className="tabs" role="tablist" aria-label="Request views">
              <button
                type="button"
                role="tab"
                className="tab"
                aria-selected={viewTab === "current"}
                onClick={() => {
                  setViewTab("current");
                }}
              >
                Current ({counts.currentTotal})
              </button>
              <button
                type="button"
                role="tab"
                className="tab"
                aria-selected={viewTab === "history"}
                onClick={() => {
                  setViewTab("history");
                  setActiveCard("");
                }}
              >
                History ({counts.historyTotal})
              </button>
            </div>

            <div className="flex items-center gap-2.5">
              <span id="ws-count" className="muted text-xs sm:text-sm">
                {filteredRequests.length} request{filteredRequests.length === 1 ? "" : "s"}
              </span>
              {hasActiveFilters && (
                <button
                  type="button"
                  className="btn btn-soft btn-xs"
                  onClick={clearAllFilters}
                >
                  Clear filters
                </button>
              )}
            </div>
          </div>

          {/* Table Column Header */}
          <div className="acols" aria-hidden="true">
            <div>Rental / property</div>
            <div>Request</div>
            <div>Requested term</div>
            <div>Scope / subtenant</div>
            <div>Status</div>
            <div className="r">Next action</div>
          </div>

          {/* Loading Skeleton */}
          {loading && (
            <div className="p-6 space-y-4">
              <div className="h-12 bg-slate-100 rounded-xl animate-pulse" />
              <div className="h-12 bg-slate-100 rounded-xl animate-pulse" />
              <div className="h-12 bg-slate-100 rounded-xl animate-pulse" />
            </div>
          )}

          {/* Load error */}
          {!loading && loadError && (
            <div className="p-10 text-center flex flex-col items-center gap-3">
              <p className="text-sm text-red-600 font-semibold">{loadError}</p>
              <button type="button" className="btn btn-outline btn-sm" onClick={loadData}>
                Retry
              </button>
            </div>
          )}

          {/* Rows List */}
          {!loading && !loadError && (
            <div>
              {filteredRequests.length === 0 ? (
                <div className="flex flex-col items-center gap-3 py-12 px-4 text-center">
                  <strong className="text-base text-[#0B1B3F]">
                    {hasActiveFilters
                      ? "No sublet requests match these filters."
                      : viewTab === "history"
                      ? "No past sublet requests in History."
                      : "No current sublet requests."}
                  </strong>
                  {hasActiveFilters && (
                    <button
                      type="button"
                      className="btn btn-navy btn-sm"
                      onClick={clearAllFilters}
                    >
                      Clear filters
                    </button>
                  )}
                </div>
              ) : (
                filteredRequests.map((x) => {
                  const occ = occupancyMap.get(x.currentOccupancyId);
                  const propTitle =
                    x.listingName ||
                    occ?.listingName ||
                    `Rental #${x.currentOccupancyId}`;
                  const roomSubtitle = occ?.roomId ? ` · Room #${occ.roomId}` : "";
                  const meta = STATUS_META[x.status] || STATUS_META.draft;
                  const isAction = x.status === "more_information_requested";
                  const isHistory = HISTORY_STATUSES.has(x.status);
                  const isNamed = Boolean(x.proposedRenterPartyId || x.proposedRenterName);

                  // Button CTA configuration
                  let ctaText = "View";
                  let ctaVariant: "outline" | "navy" | "red" = "outline";
                  let ctaAction = () => openDrawer(x, "overview");

                  if (isHistory) {
                    ctaText = "View";
                    ctaVariant = "outline";
                    ctaAction = () => openDrawer(x, "overview");
                  } else if (x.status === "more_information_requested") {
                    ctaText = "Respond";
                    ctaVariant = "red";
                    ctaAction = () => openDrawer(x, "info");
                  } else if (x.status === "draft") {
                    ctaText = "Continue Draft";
                    ctaVariant = "navy";
                    ctaAction = () => openCreateWizard(x.currentOccupancyId);
                  } else if (x.status === "approved") {
                    ctaText = "View Approval";
                    ctaVariant = "outline";
                    ctaAction = () => openDrawer(x, "approval");
                  } else {
                    ctaText = "View Request";
                    ctaVariant = "outline";
                    ctaAction = () => openDrawer(x, "overview");
                  }

                  return (
                    <article
                      key={x.id}
                      className={`arow srq ${isAction ? "arow--action" : ""} ${isHistory ? "arow--closed" : ""}`}
                    >
                      {/* Column 1: Rental / Property */}
                      <button
                        type="button"
                        className="who"
                        onClick={() => openDrawer(x, "overview")}
                      >
                        <span className="thumbsm" aria-hidden="true">
                          <SvgIcon name="swap" size={18} strokeWidth={2} />
                        </span>
                        <span className="whot">
                          <span className="aname">
                            {propTitle}
                            {roomSubtitle}
                          </span>
                          <span className="muted">Parent Rental #ZR-R-{x.currentOccupancyId}</span>
                        </span>
                      </button>

                      {/* Column 2: Request */}
                      <div className="aroom">
                        <span className="zl">Request</span>
                        <strong className="mono">#ZR-SR-{x.id}</strong>
                        <span className="muted">
                          {x.status === "draft"
                            ? `Saved ${formatDate(x.createdAt)} · not sent`
                            : x.decidedAt
                            ? `Decided ${formatDate(x.decidedAt)}`
                            : `Submitted ${formatDate(x.createdAt)}`}
                        </span>
                      </div>

                      {/* Column 3: Requested Term */}
                      <div className="areq">
                        <span className="zl">Requested term</span>
                        <strong>
                          {x.proposedStartDate ? formatDate(x.proposedStartDate) : "—"} –{" "}
                          {x.proposedEndDate ? formatDate(x.proposedEndDate) : "—"}
                        </strong>
                        <span className="muted">
                          {computeTermDuration(x.proposedStartDate, x.proposedEndDate)}
                        </span>
                      </div>

                      {/* Column 4: Scope / Subtenant */}
                      <div className="aagr">
                        <span className="zl">Scope / subtenant</span>
                        {isNamed ? (
                          <>
                            <strong>Named Subtenant</strong>
                            <span className="muted">
                              {x.proposedRenterName || `Party #${x.proposedRenterPartyId}`}
                            </span>
                          </>
                        ) : (
                          <>
                            <strong>Permission in Principle</strong>
                            <span className="muted">Subtenant not yet named</span>
                          </>
                        )}
                      </div>

                      {/* Column 5: Status */}
                      <div className="astat">
                        <span
                          className="abadge"
                          style={{
                            color: meta.fg,
                            backgroundColor: meta.bg,
                            borderColor: meta.bd,
                          }}
                        >
                          <SvgIcon name={meta.icon} size={13} strokeWidth={2.4} color={meta.fg} />
                          {meta.label}
                        </span>
                        {x.status === "approved" && (
                          <span className="vflag">
                            <SvgIcon name="swap" size={12} strokeWidth={2.2} />
                            Sublet: {downstreamLabel(x)}
                          </span>
                        )}
                        {isHistory && (
                          <span className="text-xs text-[#4A5878] mt-0.5">
                            {x.status === "withdrawn"
                              ? "You withdrew this request."
                              : x.status === "rejected"
                              ? "Request ended without approval."
                              : x.status === "expired"
                              ? "The approval expired before start."
                              : x.status === "cancelled_by_authority"
                              ? "Cancelled by authority."
                              : "Request superseded."}
                          </span>
                        )}
                      </div>

                      {/* Column 6: Next Action */}
                      <div className="aact">
                        <button
                          type="button"
                          className={`btn btn-${ctaVariant} cta`}
                          onClick={ctaAction}
                        >
                          {ctaText}
                        </button>
                      </div>
                    </article>
                  );
                })
              )}
            </div>
          )}
        </section>

        {/* Footnote */}
        <p className="text-xs sm:text-[13px] text-[#4A5878] px-1">
          You request permission; your landlord/owner or their authorized agent decides. Zoiko Rooms routes and records the request — it doesn’t grant consent. Platform fee on sublet rent: ₹0.
        </p>

        {/* Empty State Reference Banner (shown when total requests count is 0) */}
        {!loading && requests.length === 0 && (
          <section className="empty-demo" aria-label="Empty state reference">
            <div className="empty-ill">
              <SvgIcon name="swap" size={30} strokeWidth={1.8} color="#0E2F73" />
            </div>
            <div className="flex-grow">
              <strong className="text-base text-[#0B1B3F]">No sublet requests yet</strong>
              <div className="muted mt-1">
                You have not submitted a sublet request. Eligible rentals will show a Request to Sublet option.
              </div>
            </div>
            <Link href="/account/rentals" className="btn btn-outline btn-sm">
              View My Rentals
            </Link>
            <span className="ref-tag">Empty state · shown when you have no requests</span>
          </section>
        )}
      </div>

      {/* Slide-out Detail Drawer (Mounted via createPortal directly on document.body) */}
      {typeof document !== "undefined" &&
        activeRequest &&
        createPortal(
          <div className="overlay" role="dialog" aria-modal="true" aria-labelledby="d-name">
            <button
              type="button"
              className="scrim"
              aria-label="Close"
              onClick={closeDrawer}
            />
            <aside className="drawer adrawer">
              {/* Drawer Header */}
              <div className="dhead">
                <div className="dwho">
                  <span className="thumbsm lg" aria-hidden="true">
                    <SvgIcon name="swap" size={22} strokeWidth={2} />
                  </span>
                  <div>
                    <div className="text-xs font-bold uppercase tracking-wider text-[#D80B0B]">
                      Sublet Request #ZR-SR-{activeRequest.id}
                    </div>
                    <h2 id="d-name">
                      {activeRequest.listingName || `Rental #${activeRequest.currentOccupancyId}`}
                      {activeRequest.roomType ? ` — ${activeRequest.roomType}` : ""}
                    </h2>
                    <div className="muted">Parent Rental #ZR-R-{activeRequest.currentOccupancyId}</div>
                  </div>
                </div>
                <button
                  type="button"
                  className="icon-btn bordered"
                  aria-label="Close request"
                  onClick={closeDrawer}
                >
                  <SvgIcon name="x" size={18} strokeWidth={2.4} />
                </button>
              </div>

              {/* Status Badge & Flags */}
              <div className="dstate">
                <span
                  className="abadge"
                  style={{
                    color: STATUS_META[activeRequest.status]?.fg || "#344054",
                    backgroundColor: STATUS_META[activeRequest.status]?.bg || "#FFFFFF",
                    borderColor: STATUS_META[activeRequest.status]?.bd || "#C3CAD6",
                  }}
                >
                  <SvgIcon
                    name={STATUS_META[activeRequest.status]?.icon || "dot"}
                    size={13}
                    strokeWidth={2.4}
                    color={STATUS_META[activeRequest.status]?.fg}
                  />
                  {STATUS_META[activeRequest.status]?.label || activeRequest.status}
                </span>
                {activeRequest.status === "approved" && (
                  <span className="vflag">
                    <SvgIcon name="swap" size={13} strokeWidth={2.2} />
                    Sublet: {downstreamLabel(activeRequest)}
                  </span>
                )}
              </div>

              {/* Tabs */}
              <div className="dtabs" role="tablist">
                {[
                  ["overview", "Overview"],
                  ["info", "Information Requests"],
                  ["subtenant", "Proposed Subtenant"],
                  ["approval", "Approval"],
                  ["documents", "Documents"],
                  ["timeline", "Timeline"],
                  ["messages", "Messages"],
                ].map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    role="tab"
                    className="dtab"
                    aria-selected={drawerTab === key}
                    onClick={() => setDrawerTab(key as typeof drawerTab)}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {/* Tab Content Body */}
              <div className="dtbody" role="tabpanel">
                {/* 1. OVERVIEW TAB */}
                {drawerTab === "overview" && (
                  <>
                    {activeRequest.status === "more_information_requested" && (
                      <div className="dalert">
                        <SvgIcon name="alert" size={16} strokeWidth={2.2} />
                        <span>
                          Action required: The Host requested proposed subtenant details.
                          {activeRequest.infoRequestDueAt ? ` Due ${formatDate(activeRequest.infoRequestDueAt)}.` : ""}
                        </span>
                      </div>
                    )}

                    {activeRequest.status === "draft" && (
                      <div className="dinfo">
                        <SvgIcon name="lock" size={16} strokeWidth={2.2} />
                        <span>This draft is private. It hasn’t been sent to your host.</span>
                      </div>
                    )}

                    {HISTORY_STATUSES.has(activeRequest.status) && (
                      <div className="dinfo">
                        <SvgIcon name="info" size={16} strokeWidth={2.2} />
                        <span>This request is closed and saved in History.</span>
                      </div>
                    )}

                    {/* Parent Rental & Term */}
                    <div className="dsec">
                      <h3>Parent rental &amp; requested term</h3>
                      <div className="kv2">
                        <div>
                          <span>Parent Rental</span>
                          <strong>
                            #ZR-R-{activeRequest.currentOccupancyId}
                            {occupancyMap.get(activeRequest.currentOccupancyId)?.status
                              ? ` · ${occupancyMap.get(activeRequest.currentOccupancyId)?.status}`
                              : ""}
                          </strong>
                        </div>
                        <div>
                          <span>Parent Rental term</span>
                          <strong>
                            {safeFormatDate(
                              occupancyMap.get(activeRequest.currentOccupancyId)?.leaseStartDate ||
                                occupancyMap.get(activeRequest.currentOccupancyId)?.moveInDate
                            )}{" "}
                            →{" "}
                            {safeFormatDate(occupancyMap.get(activeRequest.currentOccupancyId)?.expectedEndDate)}
                          </strong>
                        </div>
                        <div>
                          <span>Requested term</span>
                          <strong>
                            {activeRequest.proposedStartDate ? formatDate(activeRequest.proposedStartDate) : "—"} →{" "}
                            {activeRequest.proposedEndDate ? formatDate(activeRequest.proposedEndDate) : "—"}
                          </strong>
                        </div>
                        <div>
                          <span>Check</span>
                          <strong className="text-[#05603A] inline-flex items-center gap-1">
                            <SvgIcon name="check" size={13} strokeWidth={2.6} color="#05603A" />
                            Within Parent Rental term
                          </strong>
                        </div>
                      </div>
                    </div>

                    {/* Request Scope */}
                    <div className="dsec">
                      <h3>Request scope</h3>
                      <div className="scopebox">
                        <span className="scopetag">
                          {activeRequest.proposedRenterPartyId || activeRequest.proposedRenterName
                            ? "Named subtenant"
                            : "Permission in principle"}
                        </span>
                        {activeRequest.proposedRenterPartyId || activeRequest.proposedRenterName ? (
                          <>
                            <strong>{activeRequest.proposedRenterName || `Party #${activeRequest.proposedRenterPartyId}`}</strong>
                            <span className="muted small">Proposed subtenant details recorded</span>
                          </>
                        ) : (
                          <span className="muted text-xs">
                            Subtenant not yet named. Named-person checks will be required later.
                          </span>
                        )}
                      </div>
                    </div>

                    {/* Proposed Terms */}
                    <div className="dsec">
                      <h3>Proposed terms</h3>
                      <div className="kv2">
                        <div>
                          <span>Proposed rent</span>
                          <strong>
                            {activeRequest.reason?.includes("rent:")
                              ? activeRequest.reason
                              : "As agreed with host"}
                          </strong>
                        </div>
                        <div>
                          <span>Proposed payee</span>
                          <strong>Tenant / Sublessor (you)</strong>
                        </div>
                        <div>
                          <span>Proposed deposit</span>
                          <strong>Not specified</strong>
                        </div>
                        <div>
                          <span>Platform fee on sublet rent</span>
                          <strong>₹0</strong>
                        </div>
                      </div>
                      <p className="muted small">
                        Terms stay “proposed” until approval and a sublet agreement. Zoiko Rooms won’t receive or hold sublet rent or deposits.
                      </p>
                    </div>

                    {/* Decision Route */}
                    <div className="dsec">
                      <h3>Decision route</h3>
                      <p className="route">
                        <SvgIcon name="user" size={15} strokeWidth={2.2} />
                        Property Owner / Landlord
                      </p>
                      <p className="muted small">
                        The authorized decision-maker decides. Zoiko Rooms doesn’t grant consent.
                      </p>
                    </div>
                  </>
                )}

                {/* 2. INFORMATION REQUESTS TAB */}
                {drawerTab === "info" && (
                  <>
                    {activeRequest.status === "more_information_requested" ? (
                      <div className="space-y-4">
                        <div className="dsec">
                          <h3>The Host / Authorized Agent needs</h3>
                          <div className="p-3 bg-amber-50 rounded-xl border border-amber-200 text-sm text-amber-900 font-medium">
                            &ldquo;{activeRequest.infoRequestNote || "Please provide the proposed subtenant details and verified dates."}&rdquo;
                          </div>
                          {activeRequest.infoRequestedDocumentTypes.length > 0 && (
                            <ul className="amlist mt-2">
                              {activeRequest.infoRequestedDocumentTypes.map((doc, idx) => (
                                <li key={idx}>
                                  <SvgIcon name="file" size={14} strokeWidth={2.2} />
                                  {doc}
                                </li>
                              ))}
                            </ul>
                          )}
                          {activeRequest.infoRequestDueAt && (
                            <p className="muted small mt-1">Due by {formatDate(activeRequest.infoRequestDueAt)}</p>
                          )}
                        </div>

                        <div className="space-y-3">
                          <div className="field">
                            <label htmlFor="i-name">Proposed subtenant full name</label>
                            <input
                              id="i-name"
                              className="tinput"
                              value={infoFormName}
                              onChange={(e) => setInfoFormName(e.target.value)}
                              placeholder="e.g. Maya Lewis"
                            />
                          </div>

                          <div className="fgrid2">
                            <div className="field">
                              <label htmlFor="i-from">Occupancy from</label>
                              <input
                                id="i-from"
                                type="date"
                                className="tinput"
                                value={infoFormFrom}
                                onChange={(e) => setInfoFormFrom(e.target.value)}
                              />
                            </div>
                            <div className="field">
                              <label htmlFor="i-to">Occupancy to</label>
                              <input
                                id="i-to"
                                type="date"
                                className="tinput"
                                value={infoFormTo}
                                onChange={(e) => setInfoFormTo(e.target.value)}
                              />
                            </div>
                          </div>

                          <div className="field">
                            <label htmlFor="i-occ">Confirmed occupant count</label>
                            <select
                              id="i-occ"
                              className="tinput"
                              value={infoFormOcc}
                              onChange={(e) => setInfoFormOcc(e.target.value)}
                            >
                              <option value="1">1</option>
                              <option value="2">2</option>
                            </select>
                          </div>

                          <div className="field">
                            <label htmlFor="i-notes">Additional notes / explanation</label>
                            <textarea
                              id="i-notes"
                              className="tinput h-20 p-2.5 resize-none"
                              value={infoFormNotes}
                              onChange={(e) => setInfoFormNotes(e.target.value)}
                              placeholder="Add any details to answer the host's question..."
                            />
                          </div>

                          {infoError && <p className="ferr">{infoError}</p>}

                          <div className="dalert warn">
                            <SvgIcon name="lock" size={16} strokeWidth={2.2} />
                            <span>
                              Don’t send identity documents through ordinary chat. If verification is required, Zoiko Rooms will provide a secure flow.
                            </span>
                          </div>

                          <button
                            type="button"
                            className="btn btn-navy w-full"
                            disabled={infoSubmitting}
                            onClick={() => handleSubmitInfoResponse(activeRequest.id)}
                          >
                            {infoSubmitting ? "Submitting..." : "Submit Response"}
                          </button>
                        </div>
                      </div>
                    ) : activeRequest.infoResponseNote ? (
                      <div className="space-y-3">
                        <p className="text-emerald-700 font-bold text-sm inline-flex items-center gap-1.5">
                          <SvgIcon name="check" size={16} strokeWidth={2.4} color="#05603A" />
                          You responded on {activeRequest.infoRespondedAt ? formatDate(activeRequest.infoRespondedAt) : "recent date"}. Back under review.
                        </p>
                        <div className="p-3 bg-slate-50 border border-slate-200 rounded-xl text-sm text-[#0B1B3F]">
                          <span className="text-xs uppercase font-bold text-slate-400 block mb-1">Your Response Note:</span>
                          {activeRequest.infoResponseNote}
                        </div>
                      </div>
                    ) : (
                      <p className="muted">No information has been requested for this sublet request.</p>
                    )}
                  </>
                )}

                {/* 3. PROPOSED SUBTENANT TAB */}
                {drawerTab === "subtenant" && (
                  <div className="space-y-4">
                    {activeRequest.proposedRenterPartyId || activeRequest.proposedRenterName ? (
                      <>
                        <div className="kv2">
                          <div>
                            <span>Proposed subtenant</span>
                            <strong>{activeRequest.proposedRenterName || `Party #${activeRequest.proposedRenterPartyId}`}</strong>
                          </div>
                          <div>
                            <span>Status</span>
                            <strong className="text-emerald-700">Identity Recorded</strong>
                          </div>
                          <div>
                            <span>Zoiko account</span>
                            <strong>Invite sent upon host approval</strong>
                          </div>
                          <div>
                            <span>Occupant Risk Tier</span>
                            <strong>{activeRequest.occupantRiskTier || "Standard"}</strong>
                          </div>
                        </div>

                        <div className="dinfo">
                          <SvgIcon name="lock" size={15} strokeWidth={2.2} />
                          <span>
                            You see subtenant status only — never their private ID documents or biometric verification data.
                          </span>
                        </div>
                      </>
                    ) : (
                      <p className="muted">Permission in principle — no subtenant named yet.</p>
                    )}
                  </div>
                )}

                {/* MESSAGES TAB -- renter <-> named subtenant */}
                {drawerTab === "messages" && (
                  <div className="space-y-4">
                    {activeRequest.status !== "draft" && activeRequest.proposedRenterPartyId ? (
                      <SubletChatThread
                        subletRequestId={activeRequest.id}
                        counterpartName={activeRequest.proposedRenterName || "your subtenant"}
                      />
                    ) : (
                      <p className="muted">Messaging opens once you&apos;ve submitted the request with a named subtenant.</p>
                    )}
                  </div>
                )}

                {/* 4. APPROVAL TAB */}
                {drawerTab === "approval" && (
                  <div className="space-y-4">
                    {activeRequest.status === "approved" ? (
                      <>
                        <div className="dsec">
                          <h3>Approved Decision · #ZR-SR-{activeRequest.id}</h3>
                          <div className="kv2">
                            <div>
                              <span>Approved period</span>
                              <strong>
                                {activeRequest.proposedStartDate ? formatDate(activeRequest.proposedStartDate) : "—"} →{" "}
                                {activeRequest.proposedEndDate ? formatDate(activeRequest.proposedEndDate) : "—"}
                              </strong>
                            </div>
                            <div>
                              <span>Approval scope</span>
                              <strong>
                                {activeRequest.proposedRenterName ||
                                (activeRequest.proposedRenterPartyId
                                  ? `Party #${activeRequest.proposedRenterPartyId}`
                                  : "Permission in Principle")}
                              </strong>
                            </div>
                            <div>
                              <span>Downstream sublet</span>
                              <strong>{downstreamLabel(activeRequest)}</strong>
                            </div>
                            <div>
                              <span>Approval expires</span>
                              <strong>
                                {activeRequest.approvalExpiresAt ? formatDate(activeRequest.approvalExpiresAt) : "N/A"}
                              </strong>
                            </div>
                          </div>
                        </div>

                        {/* Conditions List */}
                        {activeRequest.approvalConditionList && activeRequest.approvalConditionList.length > 0 && (
                          <div className="dsec">
                            <h3>Approval Conditions</h3>
                            <ul className="gates">
                              {activeRequest.approvalConditionList.map((cond, i) => (
                                <li key={i} className="gate">
                                  <span className="gico" style={{ background: "#E7F8EF", color: "#05603A" }}>
                                    <SvgIcon name="check" size={13} strokeWidth={2.6} color="#05603A" />
                                  </span>
                                  <span className="gname">{cond}</span>
                                  <span className="gstate text-[#05603A]">Met</span>
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {activeRequest.approvalConditions && (
                          <div className="p-3 bg-emerald-50 border border-emerald-200 rounded-xl text-xs text-emerald-900">
                            <strong>Host Notes:</strong> {activeRequest.approvalConditions}
                          </div>
                        )}

                        {/* Download decision record */}
                        <div>
                          <a
                            href={tenantSubletDecisionRecordUrl(activeRequest.id)}
                            target="_blank"
                            rel="noreferrer"
                            className="btn btn-outline btn-sm w-full"
                          >
                            <SvgIcon name="file" size={16} />
                            Download Decision Record (PDF)
                          </a>
                        </div>

                        {/* Sublet Payments Panel */}
                        {activeRequest.newAgreementId && (
                          <SubletPaymentsPanel subletId={activeRequest.id} />
                        )}

                        <div className="dinfo">
                          <SvgIcon name="info" size={16} strokeWidth={2.2} />
                          <span>
                            <strong>Important:</strong> Approval does not by itself create the sublet agreement or authorize occupancy before the required conditions are complete. You remain the renter on the Parent Rental.
                          </span>
                        </div>
                      </>
                    ) : (
                      <p className="muted">No decision yet. You’ll be notified when the decision-maker responds.</p>
                    )}
                  </div>
                )}

                {/* 5. DOCUMENTS TAB */}
                {drawerTab === "documents" && (
                  <div className="space-y-4">
                    <ul className="evl">
                      <li className="evi">
                        <span className="evico">
                          <SvgIcon name="file" size={17} strokeWidth={2} />
                        </span>
                        <span className="flex-grow">
                          <strong>Sublet request #ZR-SR-{activeRequest.id}</strong>
                          <span className="muted block text-xs">
                            {activeRequest.status === "draft"
                              ? "Draft"
                              : `Submitted ${formatDate(activeRequest.createdAt)}`}
                          </span>
                        </span>
                        {activeRequest.status === "approved" && (
                          <a
                            href={tenantSubletDecisionRecordUrl(activeRequest.id)}
                            target="_blank"
                            rel="noreferrer"
                            className="btn btn-xs btn-outline"
                          >
                            Download
                          </a>
                        )}
                      </li>

                      {drawerDocs.map((doc) => (
                        <li key={doc.id} className="evi">
                          <span className="evico">
                            <SvgIcon name="file" size={17} strokeWidth={2} />
                          </span>
                          <span className="flex-grow">
                            <strong>{doc.originalFilename}</strong>
                            <span className="muted block text-xs">
                              {formatDate(doc.createdAt)} · {(doc.fileSize / 1024).toFixed(1)} KB
                            </span>
                          </span>
                          <a
                            href={doc.downloadUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="btn btn-xs btn-outline"
                          >
                            Download
                          </a>
                        </li>
                      ))}
                    </ul>

                    {loadingDocs && <p className="text-xs text-slate-400">Loading documents...</p>}

                    {/* Upload new document */}
                    <div className="pt-2">
                      <label className="flex items-center justify-center gap-2 p-4 border-2 border-dashed border-[#AFC2EE] bg-[#F7F9FD] rounded-xl cursor-pointer hover:border-[#0E2F73] transition-colors text-sm font-semibold text-[#0E2F73]">
                        <SvgIcon name="upload" size={18} strokeWidth={2} />
                        {uploadingDoc ? "Uploading document..." : "Upload additional document (PDF, PNG, JPG)"}
                        <input
                          type="file"
                          className="sr-only"
                          disabled={uploadingDoc}
                          onChange={(e) => {
                            if (e.target.files && e.target.files[0]) {
                              handleDocumentUpload(e.target.files[0]);
                            }
                          }}
                        />
                      </label>
                    </div>
                  </div>
                )}

                {/* 6. TIMELINE TAB */}
                {drawerTab === "timeline" && (
                  <ol className="tl">
                    {activeRequest.status === "draft" ? (
                      <li>
                        <span className="tdot" />
                        <span>
                          <strong>Draft saved</strong>
                          <span className="muted">{formatDate(activeRequest.createdAt)}</span>
                        </span>
                      </li>
                    ) : (
                      <li>
                        <span className="tdot" />
                        <span>
                          <strong>Request submitted</strong>
                          <span className="muted">
                            {formatDate(activeRequest.createdAt)} · Route: Property Owner / Landlord
                          </span>
                        </span>
                      </li>
                    )}

                    {activeRequest.status !== "draft" && (
                      <li>
                        <span className="tdot" />
                        <span>
                          <strong>Review started</strong>
                          <span className="muted">With the decision-maker</span>
                        </span>
                      </li>
                    )}

                    {activeRequest.infoRequestNote && (
                      <li>
                        <span className="tdot" />
                        <span>
                          <strong>Information requested by Host</strong>
                          <span className="muted">
                            {activeRequest.infoRequestedAt ? formatDate(activeRequest.infoRequestedAt) : "Requested"}
                          </span>
                        </span>
                      </li>
                    )}

                    {activeRequest.infoResponseNote && (
                      <li>
                        <span className="tdot" />
                        <span>
                          <strong>Response submitted by Tenant</strong>
                          <span className="muted">
                            {activeRequest.infoRespondedAt ? formatDate(activeRequest.infoRespondedAt) : "Submitted"}
                          </span>
                        </span>
                      </li>
                    )}

                    {activeRequest.decidedAt && (
                      <li>
                        <span className="tdot" />
                        <span>
                          <strong>
                            Decision: {STATUS_META[activeRequest.status]?.label || activeRequest.status}
                          </strong>
                          <span className="muted">{formatDate(activeRequest.decidedAt)}</span>
                        </span>
                      </li>
                    )}

                    {activeRequest.newAgreementId && (
                      <li>
                        <span className="tdot" />
                        <span>
                          <strong>Sublet agreement generated</strong>
                          <span className="muted">Agreement #{activeRequest.newAgreementId}</span>
                        </span>
                      </li>
                    )}
                  </ol>
                )}
              </div>

              {/* Drawer Footer Actions */}
              <div className="dacts">
                {HISTORY_STATUSES.has(activeRequest.status) ? (
                  <p className="muted small">Read-only history.</p>
                ) : activeRequest.status === "draft" ? (
                  <>
                    <button
                      type="button"
                      className="btn btn-navy"
                      onClick={() => {
                        closeDrawer();
                        openCreateWizard(activeRequest.currentOccupancyId);
                      }}
                    >
                      Continue Draft
                    </button>
                    <span className="flex-grow" />
                    <button
                      type="button"
                      className="btn btn-ghost text-red-600"
                      onClick={() => setWithdrawTarget(activeRequest)}
                    >
                      Delete draft
                    </button>
                  </>
                ) : activeRequest.status === "approved" ? (
                  <>
                    <a
                      href={tenantSubletDecisionRecordUrl(activeRequest.id)}
                      target="_blank"
                      rel="noreferrer"
                      className="btn btn-navy"
                    >
                      View Next Steps
                    </a>
                  </>
                ) : (
                  <>
                    {activeRequest.status === "more_information_requested" && (
                      <button
                        type="button"
                        className="btn btn-red"
                        onClick={() => setDrawerTab("info")}
                      >
                        Respond
                      </button>
                    )}
                    <span className="flex-grow" />
                    <button
                      type="button"
                      className="btn btn-ghost text-red-600"
                      onClick={() => setWithdrawTarget(activeRequest)}
                    >
                      Withdraw Request
                    </button>
                  </>
                )}
              </div>
            </aside>
          </div>,
          document.body
        )}

      {/* Creation Wizard Flow Modal (Mounted via createPortal directly on document.body) */}
      {typeof document !== "undefined" &&
        isWizardOpen &&
        createPortal(
          <div className="overlay center" role="dialog" aria-modal="true" aria-labelledby="w-title">
            <button
              type="button"
              className="scrim"
              aria-label="Cancel"
              onClick={() => setIsWizardOpen(false)}
            />
            <div className="dialog wide">
              <div className="fhead">
                <h2 id="w-title">Request to Sublet</h2>
                <span className="muted small">
                  Step {wizardStep} of 7 ·{" "}
                  {
                    [
                      "Choose rental",
                      "Request scope",
                      "Requested dates",
                      "Proposed subtenant",
                      "Proposed terms",
                      "Rules & checks",
                      "Review & submit",
                    ][wizardStep - 1]
                  }
                  {wizardStep > 1 ? " · Draft saved" : ""}
                </span>
              </div>
              <div className="fprog" aria-hidden="true">
                <span style={{ width: `${(wizardStep / 7) * 100}%` }} />
              </div>

              {/* STEP 1: Choose Rental */}
              {wizardStep === 1 && (
                <div className="space-y-3">
                  <fieldset className="roles">
                    <legend>Which rental do you want to sublet?</legend>
                    {occupancies.length === 0 ? (
                      <p className="text-sm text-slate-500">
                        No active rentals found. You must have an active occupancy to request a sublet.
                      </p>
                    ) : (
                      occupancies.map((occ) => {
                        const blocked = subletBlockReason(occ);
                        return (
                        <label key={occ.id} className="radio" style={blocked ? { opacity: 0.55, cursor: "not-allowed" } : undefined}>
                          <input
                            type="radio"
                            name="wizard_occ"
                            value={occ.id}
                            disabled={Boolean(blocked)}
                            checked={wizardRentalId === occ.id}
                            onChange={() => {
                              setWizardRentalId(occ.id);
                              setWizardFrom(occ.moveInDate && occ.moveInDate > todayIso ? occ.moveInDate : todayIso);
                              if (occ.expectedEndDate) setWizardTo(occ.expectedEndDate);
                            }}
                          />
                          <span className="rdot" />
                          <span>
                            <strong>
                              {occ.listingName || `Rental #${occ.id}`} · Room #{occ.roomId}
                            </strong>
                            <br />
                            <span className="muted small">
                              Parent Rental #ZR-R-{occ.id} · {occ.status} ·{" "}
                              {occ.moveInDate ? formatDate(occ.moveInDate) : "—"} →{" "}
                              {occ.expectedEndDate ? formatDate(occ.expectedEndDate) : "—"}
                            </span>
                            {blocked && (
                              <>
                                <br />
                                <span className="small" style={{ color: "#B42318" }}>{blocked}</span>
                              </>
                            )}
                          </span>
                        </label>
                        );
                      })
                    )}
                  </fieldset>
                  <p className="muted small">
                    Rentals that can&apos;t be sublet are shown greyed out with the reason.
                  </p>
                </div>
              )}

              {/* STEP 2: Request Scope */}
              {wizardStep === 2 && (
                <div className="space-y-3">
                  <fieldset className="roles">
                    <legend>What are you asking permission for?</legend>
                    <label className="radio">
                      <input
                        type="radio"
                        name="wizard_scope"
                        value="named"
                        checked={wizardScope === "named"}
                        onChange={() => setWizardScope("named")}
                      />
                      <span className="rdot" />
                      <span>
                        <strong>Named subtenant</strong>
                        <br />
                        <span className="muted small">Permission for a specific person</span>
                      </span>
                    </label>
                    <label className="radio">
                      <input
                        type="radio"
                        name="wizard_scope"
                        value="principle"
                        checked={wizardScope === "principle"}
                        onChange={() => setWizardScope("principle")}
                      />
                      <span className="rdot" />
                      <span>
                        <strong>Permission in principle</strong>
                        <br />
                        <span className="muted small">Find a suitable subtenant later</span>
                      </span>
                    </label>
                  </fieldset>
                  {(() => {
                    const roomHasSpace = Boolean(
                      occupancies.find((o) => o.id === wizardRentalId)?.roomHasSpaceForCoTenant
                    );
                    const options: { value: SubletArrangementType; title: string; hint: string; needsSpace: boolean }[] = [
                      { value: "ASSIGNMENT_FULL", title: "Full handover", hint: "You move out and they take over the room completely", needsSpace: false },
                      { value: "REPLACEMENT_OCCUPANT", title: "Replacement occupant", hint: "They replace you as the occupant", needsSpace: false },
                      { value: "SUBLEASE_PARTIAL", title: "Sublease part of the room", hint: "You stay on; they get their own agreement", needsSpace: true },
                      { value: "ADD_CO_TENANT", title: "Add a co-tenant", hint: "You both hold your own tenancy", needsSpace: true },
                    ];
                    return (
                      <fieldset className="roles">
                        <legend>How will the room be used?</legend>
                        {options.map((opt) => {
                          const disabled = opt.needsSpace && !roomHasSpace;
                          return (
                            <label key={opt.value} className="radio" style={disabled ? { opacity: 0.5 } : undefined}>
                              <input
                                type="radio"
                                name="wizard_arrangement"
                                value={opt.value}
                                disabled={disabled}
                                checked={wizardArrangement === opt.value}
                                onChange={() => setWizardArrangement(opt.value)}
                              />
                              <span className="rdot" />
                              <span>
                                <strong>{opt.title}</strong>
                                <br />
                                <span className="muted small">
                                  {disabled ? "Not available — this room is already at its occupant limit" : opt.hint}
                                </span>
                              </span>
                            </label>
                          );
                        })}
                      </fieldset>
                    );
                  })()}
                </div>
              )}

              {/* STEP 3: Requested Dates */}
              {wizardStep === 3 && (
                <div className="space-y-3">
                  {occupancies.find((o) => o.id === wizardRentalId) && (() => {
                    const occ = occupancies.find((o) => o.id === wizardRentalId)!;
                    return (
                      <>
                        {/* The host-renter agreement's own term, not the move-in date. */}
                        <p className="muted">
                          Parent Rental term:{" "}
                          <strong>
                            {safeFormatDate(occ.leaseStartDate || occ.moveInDate)} → {safeFormatDate(occ.expectedEndDate)}
                          </strong>
                        </p>
                        <p className="muted small">
                          A sublet can run from today until the agreement ends ({safeFormatDate(occ.expectedEndDate)}) --
                          it can&apos;t start in the past or go beyond your own agreement.
                        </p>
                      </>
                    );
                  })()}
                  <div className="fgrid2">
                    <div className="field">
                      <label htmlFor="f-from">From</label>
                      <input
                        id="f-from"
                        type="date"
                        className="tinput"
                        value={wizardFrom}
                        onChange={(e) => setWizardFrom(e.target.value)}
                      />
                    </div>
                    <div className="field">
                      <label htmlFor="f-to">To</label>
                      <input
                        id="f-to"
                        type="date"
                        className="tinput"
                        value={wizardTo}
                        onChange={(e) => setWizardTo(e.target.value)}
                      />
                    </div>
                  </div>
                </div>
              )}

              {/* STEP 4: Proposed Subtenant */}
              {wizardStep === 4 && (
                <div className="space-y-3">
                  {wizardScope === "principle" ? (
                    <p className="muted">
                      You chose permission in principle, so no subtenant is named now. If approved, you’ll add the person later and they’ll go through any required checks.
                    </p>
                  ) : (
                    <>
                      <div className="field">
                        <label htmlFor="f-subtenant-email">Look up registered user by email (Optional)</label>
                        <div className="flex gap-2">
                          <input
                            id="f-subtenant-email"
                            type="email"
                            className="tinput"
                            value={wizardSubtenantEmail}
                            onChange={(e) => setWizardSubtenantEmail(e.target.value)}
                            placeholder="user@example.com"
                          />
                          <button
                            type="button"
                            className="btn btn-outline btn-sm"
                            disabled={lookupLoading}
                            onClick={handleLookupEmail}
                          >
                            {lookupLoading ? "Looking up..." : "Check"}
                          </button>
                        </div>
                        {lookupMessage && <p className="text-xs text-[#0E2F73] font-medium">{lookupMessage}</p>}
                      </div>

                      <div className="field">
                        <label htmlFor="f-name">Proposed subtenant full name</label>
                        <input
                          id="f-name"
                          className="tinput"
                          value={wizardSubtenantName}
                          onChange={(e) => setWizardSubtenantName(e.target.value)}
                          placeholder="e.g. Maya Lewis"
                        />
                      </div>
                      <p className="muted small">
                        Typing a name doesn’t create an account. If approved, we’ll send a secure invitation. Only share what’s needed — never ID documents.
                      </p>
                    </>
                  )}
                </div>
              )}

              {/* STEP 5: Proposed Terms */}
              {wizardStep === 5 && (
                <div className="space-y-3">
                  <div className="fgrid2">
                    <div className="field">
                      <label htmlFor="f-rent">Proposed rent / month (₹)</label>
                      <input
                        id="f-rent"
                        className="tinput"
                        inputMode="numeric"
                        value={wizardRent}
                        onChange={(e) => setWizardRent(e.target.value)}
                        placeholder="e.g. 24000"
                      />
                    </div>
                    <div className="field">
                      <label htmlFor="f-payee">Proposed payee</label>
                      <select
                        id="f-payee"
                        className="tinput"
                        value={wizardPayee}
                        onChange={(e) => setWizardPayee(e.target.value)}
                      >
                        <option>Tenant / Sublessor (you)</option>
                        <option>Landlord / Agent</option>
                      </select>
                    </div>
                    <div className="field">
                      <label htmlFor="f-dep">Proposed deposit (optional)</label>
                      <input
                        id="f-dep"
                        className="tinput"
                        value={wizardDeposit}
                        onChange={(e) => setWizardDeposit(e.target.value)}
                        placeholder="Leave blank if none"
                      />
                    </div>
                    <div className="field">
                      <label htmlFor="f-occ">Occupants</label>
                      <select
                        id="f-occ"
                        className="tinput"
                        value={wizardOccupants}
                        onChange={(e) => setWizardOccupants(e.target.value)}
                      >
                        <option>1</option>
                        <option>2</option>
                      </select>
                    </div>
                  </div>
                  <p className="muted small">
                    All terms are proposed until approved. Platform fee on sublet rent: ₹0.
                  </p>
                </div>
              )}

              {/* STEP 6: Rules & Checks */}
              {wizardStep === 6 && (
                <div className="space-y-3">
                  <ul className="gates">
                    {[
                      ["Parent rental", "pass", "Current and eligible"],
                      ["Requested dates", "pass", "Within Parent Rental term"],
                      ["Agreement rule", "info", "Prior approval required from your landlord"],
                      ["Existing sublets", "pass", "No overlapping approved sublet"],
                      ["Joint renter consent", "na", "Not applicable — you’re the only renter"],
                    ].map(([title, state, sub], idx) => {
                      const isPass = state === "pass";
                      const isInfo = state === "info";
                      return (
                        <li key={idx} className="gate">
                          <span
                            className="gico"
                            style={{
                              background: isPass ? "#E7F8EF" : isInfo ? "#EEF3FF" : "#F0F2F6",
                              color: isPass ? "#05603A" : isInfo ? "#1D3F8F" : "#5B6886",
                            }}
                          >
                            <SvgIcon
                              name={isPass ? "check" : isInfo ? "info" : "minus"}
                              size={13}
                              strokeWidth={2.6}
                            />
                          </span>
                          <span className="gname">
                            {title}
                            <span className="muted small font-normal">{sub}</span>
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                  <p className="muted small">These are platform checks, not legal advice.</p>
                </div>
              )}

              {/* STEP 7: Review & Submit */}
              {wizardStep === 7 && (
                <div className="space-y-3">
                  <div className="kv2">
                    <div>
                      <span>Rental</span>
                      <strong>
                        {occupancies.find((o) => o.id === wizardRentalId)?.listingName || `Rental #${wizardRentalId}`}
                      </strong>
                    </div>
                    <div>
                      <span>Scope</span>
                      <strong>
                        {wizardScope === "named" ? `Named: ${wizardSubtenantName}` : "Permission in principle"}
                      </strong>
                    </div>
                    <div>
                      <span>Requested term</span>
                      <strong>
                        {formatDate(wizardFrom)} → {formatDate(wizardTo)}
                      </strong>
                    </div>
                    <div>
                      <span>Proposed rent</span>
                      <strong>{wizardRent ? `₹${wizardRent} / month` : "Not specified"}</strong>
                    </div>
                    <div>
                      <span>Proposed payee</span>
                      <strong>{wizardPayee}</strong>
                    </div>
                    <div>
                      <span>Occupants</span>
                      <strong>{wizardOccupants}</strong>
                    </div>
                  </div>

                  <p className="route">
                    <SvgIcon name="user" size={15} strokeWidth={2.2} />
                    Decision route: Property Owner / Landlord
                  </p>

                  <div className="dinfo">
                    <SvgIcon name="info" size={16} strokeWidth={2.2} />
                    <span>
                      Submitting is a request for permission. No sublet may begin until the decision-maker approves and any conditions and agreement are complete. Zoiko Rooms doesn’t grant consent.
                    </span>
                  </div>
                </div>
              )}

              {wizardError && <p className="ferr">{wizardError}</p>}

              {/* Wizard Footer Navigation */}
              <div className="dlg-acts">
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={handleWizardSaveDraft}
                >
                  Save &amp; exit
                </button>
                <span className="flex-grow" />
                {wizardStep > 1 && (
                  <button
                    type="button"
                    className="btn btn-outline btn-sm"
                    onClick={() => {
                      setWizardError("");
                      setWizardStep((prev) => prev - 1);
                    }}
                  >
                    Back
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn-navy btn-sm"
                  disabled={wizardSubmitting}
                  onClick={handleWizardNext}
                >
                  {wizardSubmitting
                    ? "Submitting..."
                    : wizardStep === 7
                    ? "Submit request"
                    : "Continue"}
                </button>
              </div>
            </div>
          </div>,
          document.body
        )}

      {/* Withdrawal Confirmation Dialog (Mounted via createPortal directly on document.body) */}
      {typeof document !== "undefined" &&
        withdrawTarget &&
        createPortal(
          <div className="overlay center" role="alertdialog" aria-modal="true" aria-labelledby="m-title">
            <button
              type="button"
              className="scrim"
              aria-label="Cancel"
              onClick={() => setWithdrawTarget(null)}
            />
            <div className="dialog">
              <h2 id="m-title">Withdraw sublet request?</h2>
              <p className="text-sm text-[#4A5878] leading-relaxed">
                This will stop the current permission request for Request #ZR-SR-{withdrawTarget.id}. Any decision already made or downstream agreement may require a different cancellation process and will not be deleted.
              </p>
              <div className="dlg-acts">
                <button
                  type="button"
                  className="btn btn-outline btn-sm"
                  onClick={() => setWithdrawTarget(null)}
                >
                  Keep request
                </button>
                <button
                  type="button"
                  className="btn btn-danger btn-sm"
                  disabled={withdrawing}
                  onClick={handleConfirmWithdraw}
                >
                  {withdrawing ? "Withdrawing..." : "Withdraw request"}
                </button>
              </div>
            </div>
          </div>,
          document.body
        )}

      {/* Toast Notification */}
      {toastMessage && (
        <div className="toast" role="status" aria-live="polite">
          {toastMessage}
        </div>
      )}
    </>
  );
}
