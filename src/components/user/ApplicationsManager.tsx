"use client";

import React, { useState, useEffect, useMemo, useCallback } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import {
  UserApplication,
  Offer,
  DisclosureRequirement,
  PublicListing,
  BookingChangeRequest,
  PaymentPreview,
  RentalPaymentObligation,
  RentalMessage,
} from "@/lib/types";
import {
  listRentalApplications,
  getOwnOffer,
  acceptOwnOffer,
  declineOwnOffer,
  counterOwnOffer,
  listOwnAgreementDisclosures,
  acknowledgeOwnDisclosure,
  signOwnAgreement,
  withdrawRentalApplication,
  tenantAgreementPdfUrl,
  listAllPublicListings,
  listMyChangeRequests,
  errorMessage,
  getOwnAgreementPaymentPreview,
  getObligationAvailableMethods,
  listMyRentalPaymentObligations,
  payOwnObligation,
  listRenterApplicationMessages,
  listRenterConversations,
  sendRenterApplicationMessage,
} from "@/lib/user-api";
import { matchRentalPaymentObligation } from "@/lib/rentalPaymentMatch";
import { SingleObligationPaymentCard } from "@/components/user/RentalPaymentsManager";
import { DirectPaymentNotice, usePaymentCapabilities } from "@/components/user/DirectPaymentNotice";
import { RoundedFilterDropdown } from "@/components/ui/RoundedFilterDropdown";
import { formatCurrency, formatDate, formatDateTime } from "@/lib/utils";
import { ApiError } from "@/lib/api-client";

function safeFormatDate(date?: string | null): string {
  if (!date) return "—";
  try {
    return formatDate(date);
  } catch {
    return String(date);
  }
}

/** Latest version of an offer's terms (the one the renter accepts / signs). */
function latestOfferTerms(offer: Offer): Offer["terms"][number] | null {
  return offer.terms.reduce<Offer["terms"][number] | null>(
    (best, t) => (!best || t.version > best.version ? t : best),
    null
  );
}

/** Display strings for an offer, built from its real latest terms. */
function toOfferView(offer: Offer) {
  const t = latestOfferTerms(offer);
  if (!t) return null;
  const pendingCounter = offer.counterProposals.some((c) => c.status === "PENDING");
  return {
    id: `ZR-O-${String(offer.id).padStart(5, "0")} · v${t.version}`,
    rent: `${formatCurrency(t.monthlyRent, t.currency)} / month`,
    start: safeFormatDate(t.startDate),
    term: `${t.termMonths} ${t.termMonths === 1 ? "month" : "months"} (fixed)`,
    deposit: formatCurrency(t.depositAmount, t.currency),
    currency: t.currency,
    exp: pendingCounter
      ? "Your counter-proposal is awaiting the host"
      : offer.status === "SENT"
      ? "Awaiting your response"
      : offer.status.charAt(0) + offer.status.slice(1).toLowerCase(),
  };
}

// Coordinate paths from Figma prototype
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
    x: "M18 6 6 18M6 6l12 12",
    info: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 16v-4M12 8h.01",
    lock: "M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4",
    pause: "M10 15V9M14 15V9M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z",
    msg: "M4 4h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H8l-4 4V6a2 2 0 0 1 2-2z",
    key: "m15.5 7.5 2.3 2.3a1 1 0 0 0 1.4 0l2.1-2.1a1 1 0 0 0 0-1.4L19 4M21 2l-9.6 9.6M7.5 21a5.5 5.5 0 1 0 0-11 5.5 5.5 0 0 0 0 11z",
    file: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M9 13h6M9 17h4",
    swap: "M17 2l4 4-4 4M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4M21 13v2a3 3 0 0 1-3 3H3",
    card: "M3 5h18a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM2 10h20M6 15h4",
    home: "M3 10l9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z",
    plus: "M12 5v14M5 12h14",
    minus: "M5 12h14",
    upload: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12",
    search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3",
    pin: "M12 22s7-6.1 7-12a7 7 0 1 0-14 0c0 5.9 7 12 7 12zM12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z",
    building: "M3 21h18M5 21V7l7-4 7 4v14M9 21v-6h6v6M9 10h.01M15 10h.01",
    filters: "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6",
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

interface AppItem {
  id: string; // "ZR-A-28491"
  rawId: number;
  p: string; // Property name e.g. "Banjara Hills"
  room: string; // Room label e.g. "Room #8"
  type: string; // e.g. "En-suite"
  city: string; // e.g. "Hyderabad"
  lid: string; // Listing ID e.g. "ZR-L-11823"
  ltitle: string;
  applied: string; // e.g. "Sep 30"
  mi: string; // move-in date string
  dur: string; // duration e.g. "6 months"
  rent: string; // e.g. "₹24,000"
  st: string; // status code
  stage: "application" | "offer" | "agreement";
  unread: number;
  actionText?: string | null;
  outText?: string | null;
  updatedNotice?: string | null;
  offer?: {
    id: string;
    rent: string;
    start: string;
    term: string;
    deposit: string;
    exp: string;
  } | null;
  agr?: {
    id: string;
    note: string;
  } | null;
  rawApp: UserApplication;
}

const RENTER_SIGNABLE_AGREEMENT_STATUSES = ["SENT", "PARTIALLY_EXECUTED", "AMENDMENT_PENDING"];
// Both parties have signed; the renter's move-in payment is what's left.
const RENTER_PAYABLE_AGREEMENT_STATUSES = ["PAYMENT_IN_PROGRESS", "PAYMENT_PENDING"];

// Human-readable labels for the backend's normalized method classes -- the
// actual available set per obligation is always resolved server-side.
const PAYMENT_METHOD_LABELS: Record<string, string> = {
  CARD: "Card",
  BANK_DEBIT: "Bank debit",
  BANK_TRANSFER: "Bank transfer",
  PAY_BY_BANK: "Pay by bank",
  DIGITAL_WALLET: "Digital wallet",
  LOCAL_REAL_TIME: "UPI / instant transfer",
  EXTERNAL: "Cash / cheque (recorded by host)",
};

export function ApplicationsManager() {
  const [applications, setApplications] = useState<UserApplication[]>([]);
  const [publicListings, setPublicListings] = useState<PublicListing[]>([]);
  const [, setChangeRequests] = useState<BookingChangeRequest[]>([]);
  const [loading, setLoading] = useState(true);

  // Filter States
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedCard, setSelectedCard] = useState<string>("");
  const [selectedStage, setSelectedStage] = useState<string>("all");
  const [selectedStatus, setSelectedStatus] = useState<string>("all");
  const [selectedLoc, setSelectedLoc] = useState<string>("all");
  const [selectedMove, setSelectedMove] = useState<string>("all");
  const [selectedSort, setSelectedSort] = useState<string>("action");
  const [activeView, setActiveView] = useState<"current" | "history">("current");
  const [isFilterSheetOpen, setIsFilterSheetOpen] = useState(false);

  // Drawer & Tab States
  const [selectedApp, setSelectedApp] = useState<AppItem | null>(null);
  const [activeDrawerTab, setActiveDrawerTab] = useState<"overview" | "application" | "messages" | "offer" | "history">("overview");
  const [offerDetails, setOfferDetails] = useState<Offer | null>(null);
  const [offerLoading, setOfferLoading] = useState(false);
  const capabilities = usePaymentCapabilities();
  const [paymentPreview, setPaymentPreview] = useState<PaymentPreview | null>(null);
  const [rentalPaymentObligations, setRentalPaymentObligations] = useState<RentalPaymentObligation[]>([]);
  const [obligationMethods, setObligationMethods] = useState<Record<number, string[]>>({});
  const [selectedMethod, setSelectedMethod] = useState<Record<number, string>>({});
  const [payingObligationId, setPayingObligationId] = useState<number | null>(null);

  // The real latest offer terms (and any counter the renter has pending), from
  // getOwnOffer. Falls back to the list-row summary until the offer loads.
  const latestTerms = useMemo(
    () =>
      offerDetails && offerDetails.applicationId === selectedApp?.rawApp.id
        ? latestOfferTerms(offerDetails)
        : null,
    [offerDetails, selectedApp]
  );
  const displayOffer = useMemo(() => {
    if (!selectedApp?.offer) return null;
    if (!offerDetails || offerDetails.applicationId !== selectedApp.rawApp.id) return selectedApp.offer;
    return toOfferView(offerDetails) ?? selectedApp.offer;
  }, [selectedApp, offerDetails]);
  const [disclosures, setDisclosures] = useState<DisclosureRequirement[]>([]);
  const [acknowledgingId, setAcknowledgingId] = useState<number | null>(null);

  // Info Request Form
  const [infoDate, setInfoDate] = useState("2026-11-01");
  const [infoSubmitted, setInfoSubmitted] = useState(false);

  // Messages Thread State
  const [chatMessages, setChatMessages] = useState<Record<string, Array<{ from: "host" | "user"; text: string; time: string }>>>({});
  // Unread host messages per application, from the server.
  const [unreadByApp, setUnreadByApp] = useState<Record<number, number>>({});
  const [messageInput, setMessageInput] = useState("");

  // Modals
  const [acceptModalApp, setAcceptModalApp] = useState<AppItem | null>(null);
  // The accept modal is opened from the drawer, so it shows the same real terms.
  const acceptOffer =
    acceptModalApp && acceptModalApp.rawApp.id === selectedApp?.rawApp.id
      ? displayOffer
      : acceptModalApp?.offer ?? null;
  const [declineModalApp, setDeclineModalApp] = useState<AppItem | null>(null);
  const [withdrawModalApp, setWithdrawModalApp] = useState<AppItem | null>(null);
  const [signModalApp, setSignModalApp] = useState<AppItem | null>(null);
  // The sign modal can open straight from a list row, before the drawer has
  // loaded the offer -- fetch it so the card shows the real agreed terms.
  const [signOffer, setSignOffer] = useState<Offer | null>(null);
  const [signActionError, setSignActionError] = useState("");
  useEffect(() => {
    if (!signModalApp) {
      setSignOffer(null);
      setSignActionError("");
      return;
    }
    const agreementId = signModalApp.rawApp.agreementId;
    if (agreementId) {
      listOwnAgreementDisclosures(agreementId)
        .then((loaded) => setDisclosures(loaded))
        .catch(() => undefined);
    }
    if (offerDetails && offerDetails.applicationId === signModalApp.rawApp.id) {
      setSignOffer(offerDetails);
      return;
    }
    let cancelled = false;
    getOwnOffer(signModalApp.rawApp.id)
      .then((loaded) => {
        if (!cancelled) {
          setSignOffer(loaded);
          if (loaded.agreement?.id && !agreementId) {
            listOwnAgreementDisclosures(loaded.agreement.id)
              .then((discs) => {
                if (!cancelled) setDisclosures(discs);
              })
              .catch(() => undefined);
          }
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [signModalApp, offerDetails]);
  const signOfferView = signOffer ? toOfferView(signOffer) : null;
  const signAgreementLabel = signOffer?.agreement
    ? `ZR-AGR-${String(signOffer.agreement.id).padStart(5, "0")} · v${signOffer.agreement.version}`
    : null;
  const [signAck, setSignAck] = useState(false);
  const [signError, setSignError] = useState(false);
  const [counterModalApp, setCounterModalApp] = useState<AppItem | null>(null);
  const [counterForm, setCounterForm] = useState({
    monthlyRent: "",
    depositAmount: "",
    startDate: "",
    termMonths: "6",
    message: "",
  });
  const [counterError, setCounterError] = useState("");
  const [actionBusy, setActionBusy] = useState(false);
  const [overlapMessage, setOverlapMessage] = useState("");
  const [overrideReasonInput, setOverrideReasonInput] = useState("");

  // Toast
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const showToast = useCallback((msg: string) => {
    setToastMessage(msg);
    setTimeout(() => {
      setToastMessage(null);
    }, 3200);
  }, []);

  // Lock body scroll when drawer or modals open
  useEffect(() => {
    const isAnyOpen = Boolean(
      selectedApp ||
      acceptModalApp ||
      declineModalApp ||
      withdrawModalApp ||
      signModalApp ||
      counterModalApp ||
      isFilterSheetOpen
    );
    if (typeof document !== "undefined") {
      document.body.style.overflow = isAnyOpen ? "hidden" : "";
    }
    return () => {
      if (typeof document !== "undefined") {
        document.body.style.overflow = "";
      }
    };
  }, [
    selectedApp,
    acceptModalApp,
    declineModalApp,
    withdrawModalApp,
    signModalApp,
    counterModalApp,
    isFilterSheetOpen,
  ]);

  // Load real applications and listings
  const loadData = useCallback(async () => {
    try {
      const [appsData, listingsData, changeReqs, conversations] = await Promise.all([
        listRentalApplications().catch(() => []),
        listAllPublicListings().then((res) => res.items).catch(() => []),
        listMyChangeRequests().catch(() => []),
        listRenterConversations().catch(() => []),
      ]);
      setUnreadByApp(Object.fromEntries(conversations.map((c) => [c.applicationId, c.unreadCount])));
      setApplications(appsData);
      setPublicListings(listingsData);
      setChangeRequests(changeReqs);
    } catch (err) {
      showToast(errorMessage(err, "Could not load applications."));
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Transform raw UserApplication into AppItem
  const appItems = useMemo<AppItem[]>(() => {
    return applications.map((raw) => {
      const listing = publicListings.find((l) => l.id === raw.listingId);
      const propertyName = listing?.name || raw.listingName || `Listing #${raw.listingId}`;
      const roomType = listing?.roomType || "Room";
      const city = listing?.city || "";
      const roomLabel = listing?.roomId ? `Room #${listing.roomId}` : "Room";
      const appliedDate = safeFormatDate(raw.submittedAt);
      const moveInDate = raw.desiredMoveIn ? raw.desiredMoveIn.split("T")[0] : "";
      const rentAmount = listing ? formatCurrency(listing.pricePerNight * 30, listing.currency) : "—";

      // Status resolution
      let st = "submitted";
      let stage: "application" | "offer" | "agreement" = "application";
      let actionText: string | null = null;
      let outText: string | null = null;

      // An approved application is also DECIDED, and stays DECIDED while its
      // offer/agreement is in flight -- only treat DECIDED as closed when there
      // is no live offer or unsigned agreement, otherwise the renter loses the
      // Accept / Propose Different Terms / Sign actions.
      const hasLiveDeal =
        (raw.offerStatus === "SENT" || raw.offerStatus === "ACCEPTED" || raw.offerStatus === "COUNTERED") &&
        raw.agreementStatus !== "SIGNED" &&
        raw.agreementStatus !== "ACTIVE";

      const tenancyStarted = raw.agreementStatus === "SIGNED" || raw.agreementStatus === "ACTIVE";

      if (raw.status === "WITHDRAWN") {
        st = "withdrawn";
        outText = `You withdrew this application on ${appliedDate}.`;
      } else if (raw.decision === "REJECTED" && !tenancyStarted) {
        // The host declined -- show it as such, with the host's own message.
        st = "notsel";
        const declinedOn = raw.decidedAt ? ` on ${safeFormatDate(raw.decidedAt)}` : "";
        outText = raw.decisionNote?.trim()
          ? `The host declined your application${declinedOn}: "${raw.decisionNote.trim()}"`
          : `The host declined your application${declinedOn}.`;
      } else if (raw.status === "DECIDED" && raw.decision === "APPROVED" && !raw.offerStatus && !raw.agreementStatus) {
        // Shortlisted/selected, no offer sent yet -- still in progress, not closed.
        st = "review";
      } else if (raw.status === "DECIDED" && !hasLiveDeal) {
        if (raw.offerStatus === "DECLINED") {
          st = "odecl";
          outText = "You declined the offer.";
        } else if (raw.offerStatus === "EXPIRED") {
          st = "oexp";
          outText = "The offer expired before it was accepted.";
        } else if (raw.agreementStatus === "SIGNED" || raw.agreementStatus === "ACTIVE") {
          st = "closed";
          outText = "Agreement completed and tenancy active.";
        } else {
          st = "notsel";
          outText = "This application is no longer active.";
        }
      } else {
        // Status SUBMITTED, or DECIDED (approved) with a live offer/agreement
        if (raw.agreementStatus) {
          stage = "agreement";
          if (RENTER_SIGNABLE_AGREEMENT_STATUSES.includes(raw.agreementStatus)) {
            st = "sign";
            actionText = `Your agreement for ${roomLabel} at ${propertyName} is ready to review and sign.`;
          } else if (RENTER_PAYABLE_AGREEMENT_STATUSES.includes(raw.agreementStatus)) {
            st = "pay";
            actionText = `Both parties signed the agreement for ${roomLabel} at ${propertyName}. Pay your move-in amount to confirm it.`;
          } else if (raw.agreementStatus === "SIGNED") {
            st = "formal";
          } else {
            st = "agrpending";
          }
        } else if (raw.offerStatus) {
          if (raw.offerStatus === "SENT") {
            st = "offer";
            stage = "offer";
            actionText = `You have an offer for ${roomLabel} at ${propertyName}. Review it to proceed to agreement.`;
          } else if (raw.offerStatus === "ACCEPTED") {
            st = "agrpending";
            stage = "agreement";
          } else if (raw.offerStatus === "COUNTERED") {
            st = "review";
            stage = "offer";
          } else if (raw.offerStatus === "DECLINED") {
            st = "odecl";
            outText = "You declined the offer.";
          } else if (raw.offerStatus === "EXPIRED") {
            st = "oexp";
            outText = "The offer expired.";
          }
        } else {
          // Standard review stages
          stage = "application";
          // Waiting on the host. A request for information arrives as a chat message.
          st = "submitted";
        }
      }

      return {
        id: `ZR-A-${raw.id}`,
        rawId: raw.id,
        p: propertyName,
        room: roomLabel,
        type: roomType,
        city: city,
        lid: `ZR-L-${raw.listingId}`,
        ltitle: listing?.name || propertyName,
        applied: appliedDate,
        mi: moveInDate,
        // The term is only fixed by an offer; until then show the listing's minimum stay.
        dur: listing?.minStayNights ? `Min. stay ${Math.max(1, Math.round(listing.minStayNights / 30))} months` : "Flexible",
        rent: rentAmount,
        st,
        stage,
        unread: unreadByApp[raw.id] ?? 0,
        actionText,
        outText,
        updatedNotice: null,
        rawApp: raw,
        offer: raw.offerId
          ? {
              id: `ZR-O-${String(raw.offerId).padStart(5, "0")}`,
              rent: rentAmount + " / month",
              start: safeFormatDate(moveInDate),
              term: "—",
              deposit: "—",
              exp: "—",
            }
          : null,
        agr: raw.agreementId
          ? {
              id: `ZR-AGR-${String(raw.agreementId).padStart(5, "0")}`,
              note: "After you sign, the host signs. Depending on where the property is, stamp duty or registration may still be required before the agreement is effective.",
            }
          : null,
      };
    });
  }, [applications, publicListings]);

  // Current vs History lists
  const currentList = useMemo(() => {
    return appItems.filter((x) => !["notsel", "withdrawn", "closed", "oexp", "odecl"].includes(x.st));
  }, [appItems]);

  const historyList = useMemo(() => {
    return appItems.filter((x) => ["notsel", "withdrawn", "closed", "oexp", "odecl"].includes(x.st));
  }, [appItems]);

  // Counts for summary cards
  const summaryCounts = useMemo(() => {
    let submitted = 0;
    let review = 0;
    let offers = 0;
    let action = 0;

    currentList.forEach((x) => {
      if (x.st === "submitted") submitted++;
      if (x.st === "review" || x.st === "info") review++;
      if (["offer", "sign", "pay", "agrpending", "formal"].includes(x.st)) offers++;
      if (x.actionText) action++;
    });

    return { submitted, review, offers, action };
  }, [currentList]);

  // Unique locations from applications
  const uniqueLocations = useMemo(() => {
    const set = new Set<string>();
    appItems.forEach((x) => {
      if (x.p) set.add(x.p);
    });
    return Array.from(set);
  }, [appItems]);

  // Action strip items
  const attentionItems = useMemo(() => {
    return currentList.filter((x) => x.actionText);
  }, [currentList]);

  // Filter and sort the active list
  const filteredList = useMemo(() => {
    const baseList = activeView === "current" ? currentList : historyList;

    return baseList
      .filter((x) => {
        // Card filter (current view only)
        if (activeView === "current" && selectedCard) {
          if (selectedCard === "action" && !x.actionText) return false;
          if (selectedCard === "offers" && !["offer", "sign", "pay", "agrpending", "formal"].includes(x.st)) return false;
          if (selectedCard === "review" && x.st !== "review" && x.st !== "info") return false;
          if (selectedCard === "submitted" && x.st !== "submitted") return false;
        }

        // Stage filter
        if (selectedStage !== "all" && x.stage !== selectedStage) return false;

        // Status filter
        if (selectedStatus !== "all") {
          if (selectedStatus === "agr" && x.stage !== "agreement") return false;
          if (selectedStatus !== "agr" && x.st !== selectedStatus) return false;
        }

        // Location filter
        if (selectedLoc !== "all" && x.p !== selectedLoc) return false;

        // Move-in filter
        if (selectedMove === "this_month" && !x.mi.includes("-10-")) return false;
        if (selectedMove === "next_month" && !x.mi.includes("-11-")) return false;

        // Search Query
        if (searchQuery.trim()) {
          const q = searchQuery.toLowerCase();
          const matchTarget = `${x.p} ${x.room} ${x.id} ${x.city} ${x.type}`.toLowerCase();
          if (!matchTarget.includes(q)) return false;
        }

        return true;
      })
      .sort((a, b) => {
        if (selectedSort === "applied") return b.rawId - a.rawId;
        if (selectedSort === "movein") return a.mi.localeCompare(b.mi);
        // Default "action": items with action first
        const aPrio = a.actionText ? (a.st === "offer" ? 0 : a.st === "info" ? 1 : 2) : a.st === "review" ? 3 : 4;
        const bPrio = b.actionText ? (b.st === "offer" ? 0 : b.st === "info" ? 1 : 2) : b.st === "review" ? 3 : 4;
        return aPrio - bPrio;
      });
  }, [
    activeView,
    currentList,
    historyList,
    selectedCard,
    selectedStage,
    selectedStatus,
    selectedLoc,
    selectedMove,
    searchQuery,
    selectedSort,
  ]);

  // Move-in payment for a fully signed agreement: what's due now, each
  // obligation's server-resolved payment methods, and the rental-payment
  // obligations (Stripe checkout) they map onto.
  const loadAgreementPayments = useCallback((agreementId: number) => {
    listMyRentalPaymentObligations(undefined, { agreementId })
      .then((page) => setRentalPaymentObligations(page.items))
      .catch(() => undefined);
    getOwnAgreementPaymentPreview(agreementId)
      .then((preview) => {
        const filteredPreview = {
          ...preview,
          amountDueNow: (preview.amountDueNow || []).filter((o) => Number(o.amount) > 0),
        };
        setPaymentPreview(filteredPreview);
        filteredPreview.amountDueNow.forEach((o) => {
          getObligationAvailableMethods(o.id)
            .then(({ methodClasses }) => {
              setObligationMethods((prev) => ({ ...prev, [o.id]: methodClasses }));
              setSelectedMethod((prev) => ({ ...prev, [o.id]: prev[o.id] ?? methodClasses[0] }));
            })
            .catch(() => undefined);
        });
      })
      .catch(() => undefined);
  }, []);

  const handlePayObligation = async (obligationId: number) => {
    if (!offerDetails?.agreement || !selectedApp) return;
    setPayingObligationId(obligationId);
    try {
      await payOwnObligation(obligationId, selectedMethod[obligationId] ?? "CARD");
      showToast("Payment successful.");
      setOfferDetails(await getOwnOffer(selectedApp.rawApp.id));
      loadAgreementPayments(offerDetails.agreement.id);
      loadData();
    } catch (err) {
      showToast(errorMessage(err, "Payment failed."));
    } finally {
      setPayingObligationId(null);
    }
  };

  // Open Drawer and load real offer / disclosures
  const handleOpenDrawer = async (item: AppItem, tab: "overview" | "application" | "messages" | "offer" | "history" = "overview") => {
    setSelectedApp(item);
    setActiveDrawerTab(tab);
    setOfferDetails(null);
    setDisclosures([]);
    setInfoSubmitted(false);
    setPaymentPreview(null);
    setRentalPaymentObligations([]);
    setObligationMethods({});

    if (item.rawApp.offerId || item.st === "offer" || item.stage === "agreement") {
      setOfferLoading(true);
      try {
        const loaded = await getOwnOffer(item.rawApp.id);
        setOfferDetails(loaded);
        if (loaded.agreement) {
          loadAgreementPayments(loaded.agreement.id);
          const loadedDisclosures = await listOwnAgreementDisclosures(loaded.agreement.id);
          setDisclosures(loadedDisclosures);
        }
      } catch {
        // Fallback or empty offer
      } finally {
        setOfferLoading(false);
      }
    }
  };

  // Close Drawer
  const handleCloseDrawer = () => {
    setSelectedApp(null);
  };

  // Clear filters
  const handleClearFilters = () => {
    setSearchQuery("");
    setSelectedCard("");
    setSelectedStage("all");
    setSelectedStatus("all");
    setSelectedLoc("all");
    setSelectedMove("all");
  };

  const hasActiveFilters = Boolean(
    searchQuery ||
    selectedCard ||
    selectedStage !== "all" ||
    selectedStatus !== "all" ||
    selectedLoc !== "all" ||
    selectedMove !== "all"
  );

  // Offer Actions
  const handleAcceptOfferAction = async () => {
    if (!acceptModalApp) return;
    setActionBusy(true);
    try {
      if (acceptModalApp.rawApp.offerId) {
        await acceptOwnOffer(acceptModalApp.rawApp.offerId, overrideReasonInput.trim() || undefined);
      }
      showToast("Offer accepted. Next: the agreement. This isn’t a rental or move-in yet.");
      setAcceptModalApp(null);
      setOverlapMessage("");
      setOverrideReasonInput("");
      await loadData();
      if (selectedApp?.id === acceptModalApp.id) {
        handleOpenDrawer(acceptModalApp, "overview");
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && err.message.includes("overlapping active booking")) {
        setOverlapMessage(err.message);
      } else {
        showToast(errorMessage(err, "Could not accept offer."));
      }
    } finally {
      setActionBusy(false);
    }
  };

  const handleDeclineOfferAction = async () => {
    if (!declineModalApp) return;
    setActionBusy(true);
    try {
      if (declineModalApp.rawApp.offerId) {
        await declineOwnOffer(declineModalApp.rawApp.offerId);
      }
      showToast("Offer declined. It’s kept in History.");
      setDeclineModalApp(null);
      handleCloseDrawer();
      await loadData();
    } catch (err) {
      showToast(errorMessage(err, "Could not decline offer."));
    } finally {
      setActionBusy(false);
    }
  };

  const handleWithdrawAction = async () => {
    if (!withdrawModalApp) return;
    setActionBusy(true);
    try {
      await withdrawRentalApplication(withdrawModalApp.rawId);
      showToast("Application withdrawn. The host has been notified.");
      setWithdrawModalApp(null);
      handleCloseDrawer();
      await loadData();
    } catch (err) {
      showToast(errorMessage(err, "Could not withdraw application."));
    } finally {
      setActionBusy(false);
    }
  };

  const handleSignAgreementAction = async () => {
    if (!signModalApp) return;
    if (!signAck) {
      setSignError(true);
      return;
    }
    setActionBusy(true);
    setSignError(false);
    setSignActionError("");
    try {
      const agreementId = signModalApp.rawApp.agreementId || signOffer?.agreement?.id;
      if (agreementId) {
        await signOwnAgreement(agreementId);
      }
      showToast("Signed. Waiting for the host and required formalities — not yet effective.");
      setSignModalApp(null);
      await loadData();
      if (selectedApp?.id === signModalApp.id) {
        handleOpenDrawer(signModalApp, "overview");
      }
    } catch (err) {
      const msg = errorMessage(err, "Could not sign agreement.");
      setSignActionError(msg);
      showToast(msg);
    } finally {
      setActionBusy(false);
    }
  };

  const handleSendCounterAction = async () => {
    if (!counterModalApp || !counterModalApp.rawApp.offerId) return;
    const monthlyRent = Number(counterForm.monthlyRent);
    const depositAmount = Number(counterForm.depositAmount);
    const termMonths = Number(counterForm.termMonths);

    if (!Number.isFinite(monthlyRent) || monthlyRent <= 0) {
      setCounterError("Enter the monthly rent you can pay (greater than zero).");
      return;
    }
    if (!Number.isFinite(depositAmount) || depositAmount < 0) {
      setCounterError("Enter a valid deposit amount.");
      return;
    }
    if (!counterForm.startDate) {
      setCounterError("Pick a start date.");
      return;
    }

    setActionBusy(true);
    setCounterError("");
    try {
      await counterOwnOffer(counterModalApp.rawApp.offerId, {
        monthlyRent,
        depositAmount,
        startDate: counterForm.startDate,
        termMonths,
        message: counterForm.message.trim(),
      });
      showToast("Your proposal was sent to the host.");
      setCounterModalApp(null);
      await loadData();
    } catch (err) {
      setCounterError(errorMessage(err, "Could not send proposal."));
    } finally {
      setActionBusy(false);
    }
  };

  const handleAcknowledgeDisclosure = async (discId: number) => {
    const agrId = signModalApp?.rawApp.agreementId || selectedApp?.rawApp.agreementId;
    if (!agrId) return;
    setAcknowledgingId(discId);
    try {
      const updated = await acknowledgeOwnDisclosure(agrId, discId);
      setDisclosures((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
      showToast("Disclosure acknowledged.");
      setSignActionError("");
    } catch (err) {
      const msg = errorMessage(err, "Could not acknowledge disclosure.");
      showToast(msg);
      setSignActionError(msg);
    } finally {
      setAcknowledgingId(null);
    }
  };

  const toChatMessage = (m: RentalMessage) => ({
    from: m.senderRole === "HOST" ? ("host" as const) : ("user" as const),
    text: m.body,
    time: formatDateTime(m.createdAt),
  });

  // Load the real thread (and mark the host's messages read) whenever the
  // Messages tab is opened for an application.
  useEffect(() => {
    if (activeDrawerTab !== "messages" || !selectedApp) return;
    const key = selectedApp.id;
    const applicationId = selectedApp.rawApp.id;
    const refresh = () =>
      listRenterApplicationMessages(applicationId)
        .then((items) => {
          setChatMessages((prev) => ({ ...prev, [key]: items.map(toChatMessage) }));
          setUnreadByApp((prev) => ({ ...prev, [applicationId]: 0 }));  // opening marks them read
        })
        .catch(() => setChatMessages((prev) => ({ ...prev, [key]: prev[key] ?? [] })));
    refresh();
    // New replies show up without reloading the page.
    const timer = window.setInterval(refresh, 15000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeDrawerTab, selectedApp?.id]);

  const handleSendMessage = async (appId: string) => {
    const text = messageInput.trim();
    if (!text || !selectedApp || selectedApp.id !== appId) return;
    try {
      const sent = await sendRenterApplicationMessage(selectedApp.rawApp.id, text);
      setChatMessages((prev) => ({ ...prev, [appId]: [...(prev[appId] || []), toChatMessage(sent)] }));
      setMessageInput("");
      showToast("Message sent to the host.");
    } catch (err) {
      showToast(errorMessage(err, "Could not send your message."));
    }
  };

  // Get status badge metadata
  const getBadgeMeta = (st: string) => {
    const meta: Record<string, { l: string; fg: string; bg: string; bd: string; i: string }> = {
      submitted: { l: "Submitted", fg: "#1D3F8F", bg: "#FFFFFF", bd: "#AFC2EE", i: "send" },
      review: { l: "Under Review", fg: "#1D3F8F", bg: "#EEF3FF", bd: "#AFC2EE", i: "eye" },
      info: { l: "Information Requested", fg: "#93370D", bg: "#FFF4E0", bd: "#F2B866", i: "file" },
      offer: { l: "Offer Received", fg: "#05603A", bg: "#E7F8EF", bd: "#8FD9B4", i: "file" },
      agrpending: { l: "Agreement Pending", fg: "#1D3F8F", bg: "#EEF3FF", bd: "#AFC2EE", i: "clock" },
      sign: { l: "Signature Required", fg: "#93370D", bg: "#FFF4E0", bd: "#F2B866", i: "file" },
      pay: { l: "Payment Due", fg: "#93370D", bg: "#FFF4E0", bd: "#F2B866", i: "file" },
      formal: { l: "Formalities Pending", fg: "#1D3F8F", bg: "#EEF3FF", bd: "#AFC2EE", i: "clock" },
      notsel: { l: "Not Selected", fg: "#344054", bg: "#F0F2F6", bd: "#C3CAD6", i: "xcirc" },
      withdrawn: { l: "Withdrawn", fg: "#344054", bg: "#F0F2F6", bd: "#C3CAD6", i: "undo" },
      closed: { l: "Closed", fg: "#344054", bg: "#F0F2F6", bd: "#C3CAD6", i: "pause" },
      oexp: { l: "Offer Expired", fg: "#344054", bg: "#F0F2F6", bd: "#C3CAD6", i: "clock" },
      odecl: { l: "Offer Declined", fg: "#344054", bg: "#F0F2F6", bd: "#C3CAD6", i: "xcirc" },
    };
    return meta[st] || meta.submitted;
  };

  return (
    <div className="w-full flex flex-col gap-5 text-[#0B1B3F]">
      {/* Scoped CSS from Prototype */}
      <style jsx global>{`
        .eyebrow {
          font-size: 12px;
          font-weight: 700;
          letter-spacing: 0.1em;
          text-transform: uppercase;
          color: #d80b0b;
        }
        .head {
          display: flex;
          justify-content: space-between;
          align-items: flex-end;
          gap: 20px;
          flex-wrap: wrap;
          padding: 8px 4px 0;
        }
        .head h1 {
          font-size: 38px;
          line-height: 1.05;
          font-weight: 800;
          letter-spacing: -0.02em;
          color: #0e2f73;
        }
        .head p {
          margin-top: 8px;
          font-size: 15px;
          color: #4a5878;
          max-width: 720px;
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
          cursor: pointer;
          transition: background 0.15s, border-color 0.15s;
        }
        .btn-lg {
          min-height: 50px;
          padding: 0 24px;
          border-radius: 14px;
          font-size: 15px;
        }
        .btn-red {
          background: #d80b0b;
          color: #ffffff;
        }
        .btn-red:hover {
          background: #b80909;
        }
        .btn-navy {
          background: #0e2f73;
          color: #ffffff;
        }
        .btn-navy:hover {
          background: #0b2560;
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
          background: none;
        }
        .btn-ghost:hover {
          background: #ffffff;
        }
        .btn-danger {
          background: #b42318;
          color: #ffffff;
        }
        .btn-danger:hover {
          background: #931d14;
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
        .danger-t {
          color: #b42318 !important;
        }

        /* Status summary cards */
        .scards {
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 14px;
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
        }
        .shint {
          font-size: 12.5px;
          color: #4a5878;
        }
        .sico {
          align-self: flex-start;
          opacity: 0.85;
        }

        /* Action strip */
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
        }
        .aitems {
          flex-grow: 1;
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .aitem {
          flex-grow: 1;
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

        /* Filters wrap */
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
        .searchline {
          display: flex;
          gap: 10px;
          align-items: center;
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
          color: #0b1b3f;
        }
        .filter-toggle {
          display: none;
        }
        .fpanel {
          display: flex;
          gap: 10px;
          align-items: end;
        }
        .fsheet-head,
        .fapply {
          display: none;
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
        .field select {
          height: 46px;
          min-width: 136px;
          padding: 0 16px;
          border-radius: 9999px;
          border: 1px solid #d5ddea;
          background: #ffffff;
          font-size: 13.5px;
          color: #0b1b3f;
          cursor: pointer;
        }

        /* Workspace & table */
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
        .ws-right {
          display: flex;
          align-items: center;
          gap: 10px;
        }
        .acols,
        .arow.mapp {
          display: grid;
          grid-template-columns: minmax(200px, 1.4fr) minmax(120px, 0.8fr) minmax(150px, 1fr) minmax(170px, 1fr) 110px 170px;
          gap: 14px;
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
        }
        .arow:last-child {
          border-bottom: 0;
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
        .mono {
          font-variant-numeric: tabular-nums;
          font-size: 13px;
        }
        .stage {
          font-size: 10.5px;
          font-weight: 800;
          letter-spacing: 0.1em;
          color: #5b6886;
          text-transform: uppercase;
        }
        .abadge {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          min-height: 28px;
          padding: 0 11px 0 9px;
          border-radius: 999px;
          border: 1.5px solid;
          font-size: 11.5px;
          font-weight: 800;
          letter-spacing: 0.05em;
          text-transform: uppercase;
          white-space: nowrap;
        }
        .aflag {
          display: inline-flex;
          align-items: center;
          gap: 5px;
          font-size: 12px;
          font-weight: 700;
          color: #b42318;
        }
        .aflag.amber {
          color: #93370d;
        }
        .vflag {
          display: inline-flex;
          align-items: center;
          gap: 5px;
          font-size: 12px;
          font-weight: 600;
          color: #3a4866;
        }
        .upd {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          font-size: 11.5px;
          font-weight: 700;
          color: #1d3f8f;
        }
        .upd2 {
          font-size: 12.5px;
          font-weight: 700;
          color: #93370d;
        }
        .unread {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          min-height: 32px;
          padding: 0 10px;
          border-radius: 999px;
          border: 0;
          background: #eef3ff;
          color: #1d3f8f;
          font-size: 12.5px;
          font-weight: 800;
          cursor: pointer;
        }
        .unread:hover {
          background: #dfe8fb;
        }
        .unread.static {
          min-height: 24px;
          padding: 0 8px;
          font-size: 12px;
          cursor: default;
        }
        .dash {
          font-size: 16px;
          color: #5b6886;
        }
        .aact {
          display: flex;
          gap: 6px;
          justify-content: flex-end;
          align-items: center;
        }
        .aact .cta {
          flex-grow: 1;
          max-width: 170px;
          min-height: 42px;
        }
        .zl {
          display: none;
        }

        /* Overlay & Drawer */
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
          cursor: default;
        }
        .drawer {
          position: relative;
          width: 540px;
          max-width: 100%;
          height: 100%;
          background: #ffffff;
          border-radius: 24px 0 0 24px;
          display: flex;
          flex-direction: column;
          box-shadow: -20px 0 50px rgba(8, 22, 56, 0.25);
          animation: slide 0.22s ease-out;
          overflow: hidden;
        }
        .dhead-sticky {
          flex-shrink: 0;
          background: #ffffff;
          padding: 24px 24px 0 24px;
          display: flex;
          flex-direction: column;
          gap: 14px;
          border-bottom: 1px solid #e1e7f1;
          position: sticky;
          top: 0;
          z-index: 10;
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
          flex-shrink: 0;
        }
        .dhead h2 {
          font-size: 21px;
          font-weight: 800;
          color: #0e2f73;
          margin: 0 0 4px;
        }
        .dwho {
          display: flex;
          gap: 14px;
          align-items: center;
        }
        .dstate {
          display: flex;
          align-items: center;
          gap: 10px;
          flex-wrap: wrap;
          flex-shrink: 0;
        }
        .tracker {
          list-style: none;
          margin: 0;
          padding: 0;
          display: flex;
          gap: 6px;
          flex-wrap: wrap;
          flex-shrink: 0;
        }
        .tracker li {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          font-size: 12.5px;
          font-weight: 700;
          color: #5b6886;
        }
        .tracker li + li::before {
          content: "";
          width: 16px;
          height: 2px;
          background: #d5ddea;
          margin-right: 2px;
        }
        .tracker li span {
          width: 22px;
          height: 22px;
          border-radius: 50%;
          border: 1.5px solid #c7d2e6;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          font-size: 11px;
        }
        .tracker li.done {
          color: #05603a;
        }
        .tracker li.done span {
          background: #e7f8ef;
          border-color: #12a15e;
          color: #05603a;
        }
        .tracker li.cur {
          color: #0e2f73;
        }
        .tracker li.cur span {
          background: #0e2f73;
          border-color: #0e2f73;
          color: #ffffff;
        }
        .dtabs {
          display: flex;
          gap: 4px;
          overflow-x: auto;
          scrollbar-width: none;
          -ms-overflow-style: none;
          flex-shrink: 0;
          margin-bottom: -1px;
        }
        .dtabs::-webkit-scrollbar {
          display: none;
        }
        .dtab {
          height: 42px;
          padding: 0 14px;
          border: 0;
          background: none;
          font-size: 13px;
          font-weight: 700;
          color: #4a5878;
          border-bottom: 2px solid transparent;
          white-space: nowrap;
          cursor: pointer;
          flex-shrink: 0;
          transition: all 0.15s ease;
        }
        .dtab:hover {
          color: #0e2f73;
        }
        .dtab[aria-selected="true"] {
          color: #0e2f73;
          border-bottom-color: #0e2f73;
          font-weight: 800;
        }
        .dtbody {
          flex: 1;
          overflow-y: auto;
          padding: 20px 24px 28px;
          display: flex;
          flex-direction: column;
          gap: 18px;
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
        .dsec {
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .dsec h3 {
          font-size: 12.5px;
          font-weight: 800;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: #3a4866;
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
          background: #ffffff;
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
        .tl li > span:last-child {
          display: flex;
          flex-direction: column;
          gap: 2px;
          font-size: 13.5px;
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
        .tl li:not(:last-child)::after {
          content: "";
          position: absolute;
          left: 5px;
          top: 18px;
          bottom: 2px;
          width: 2px;
          background: #e1e7f1;
        }
        .thread {
          display: flex;
          flex-direction: column;
          gap: 8px;
          max-height: 320px;
          overflow-y: auto;
          padding-right: 4px;
        }
        .bub {
          max-width: 85%;
          padding: 10px 14px;
          border-radius: 16px;
          font-size: 13.5px;
          line-height: 1.45;
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        .bub time {
          font-size: 11px;
          opacity: 0.75;
        }
        .bub.in {
          background: #f0f3f9;
          border-bottom-left-radius: 4px;
          align-self: flex-start;
          color: #0b1b3f;
        }
        .bub.out {
          background: #0e2f73;
          color: #ffffff;
          border-bottom-right-radius: 4px;
          align-self: flex-end;
        }
        .composer {
          display: flex;
          gap: 8px;
          align-items: center;
          margin-top: 8px;
        }
        .composer input {
          flex-grow: 1;
          height: 46px;
          padding: 0 14px;
          border-radius: 14px;
          border: 1px solid #d5ddea;
          font-size: 14px;
        }
        .dacts {
          position: sticky;
          bottom: -24px;
          margin: 0 -24px -24px;
          padding: 16px 24px;
          background: #ffffff;
          border-top: 1px solid #eef1f7;
          display: flex;
          gap: 8px;
          flex-wrap: wrap;
          align-items: center;
        }
        .dialog {
          position: relative;
          background: #ffffff;
          border-radius: 20px;
          padding: 24px;
          max-width: 480px;
          width: 100%;
          display: flex;
          flex-direction: column;
          gap: 12px;
          box-shadow: 0 24px 60px rgba(8, 22, 56, 0.3);
          max-height: calc(100vh - 32px);
          overflow-y: auto;
        }
        .dialog h2 {
          font-size: 19px;
          font-weight: 800;
          color: #0e2f73;
          margin: 0;
        }
        .dlg-acts {
          display: flex;
          justify-content: flex-end;
          gap: 8px;
          margin-top: 12px;
        }
        .toast {
          position: fixed;
          left: 50%;
          bottom: 24px;
          transform: translateX(-50%);
          z-index: 10005;
          background: #0b1b3f;
          color: #ffffff;
          padding: 12px 18px;
          border-radius: 12px;
          font-size: 13.5px;
          font-weight: 600;
          box-shadow: 0 12px 30px rgba(8, 22, 56, 0.4);
          max-width: 90vw;
          text-align: center;
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
        .icon-btn:hover {
          background: #eef2f9;
        }
        .icon-btn.bordered {
          border: 1px solid #d5ddea;
          background: #ffffff;
          color: #0e2f73;
        }
        .drop {
          display: flex;
          align-items: center;
          gap: 14px;
          padding: 20px;
          border-radius: 16px;
          border: 2px dashed #afc2ee;
          background: #f7f9fd;
          color: #0e2f73;
          cursor: pointer;
        }
        .drop:hover {
          border-color: #0e2f73;
        }
        .drop > span {
          display: flex;
          flex-direction: column;
          gap: 3px;
        }
        .check {
          display: flex;
          gap: 12px;
          align-items: flex-start;
          font-size: 14px;
          font-weight: 600;
          cursor: pointer;
          margin-top: 8px;
        }
        .cbox {
          flex-shrink: 0;
          width: 22px;
          height: 22px;
          border-radius: 6px;
          border: 2px solid #9aa7bf;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          color: transparent;
        }
        .check input:checked + .cbox {
          background: #0e2f73;
          border-color: #0e2f73;
          color: #ffffff;
        }
        .price {
          font-size: 13.5px;
          font-weight: 700;
          margin-top: 2px;
        }
        .price small {
          font-weight: 500;
          color: #4a5878;
        }

        @media (max-width: 1199px) {
          .filters-wrap {
            grid-template-columns: 1fr;
            gap: 12px;
          }
          .fpanel {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            width: 100%;
            gap: 8px;
          }
          .field select {
            min-width: 0;
            width: 100%;
            font-size: 12px;
            padding: 0 8px;
          }
          .acols {
            display: none;
          }
          .arow.mapp {
            grid-template-columns: repeat(3, minmax(0, 1fr));
          }
          .arow.mapp .who {
            grid-column: 1 / 3;
          }
          .arow.mapp .aact {
            grid-column: 3;
            grid-row: 1;
          }
          .zl {
            display: block;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: #5b6886;
          }
        }

        @media (max-width: 767px) {
          .head h1 {
            font-size: 30px;
          }
          .scards {
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
          }
          .sico {
            display: none;
          }
          .snum {
            width: 44px;
            height: 44px;
            font-size: 20px;
          }
          .astrip {
            flex-direction: column;
            align-items: stretch;
          }
          .arow.mapp {
            display: flex;
            flex-direction: column;
            align-items: stretch;
            gap: 10px;
          }
          .arow.mapp .aact .cta {
            width: 100%;
            max-width: none;
          }
          .drawer {
            width: 100%;
            border-radius: 0;
          }
          .dhead-sticky {
            padding: 16px 16px 0 16px;
            gap: 12px;
          }
          .dtbody {
            padding: 16px 16px 24px;
          }
          .filters-wrap {
            grid-template-columns: 1fr;
            padding: 12px;
          }
          .filter-toggle {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            height: 46px;
            padding: 0 16px;
            border-radius: 9999px;
            font-size: 13.5px;
            font-weight: 700;
            white-space: nowrap;
          }
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
            max-height: 85vh;
            overflow-y: auto;
          }
          .fpanel.open .field {
            width: 100%;
          }
          .fpanel.open .field select {
            width: 100%;
            min-width: 0;
          }
          .fsheet-head {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 8px;
            border-bottom: 1px solid #e1e7f1;
          }
          .fsheet-head strong {
            font-size: 16px;
            font-weight: 800;
            color: #0b1b3f;
          }
          .fapply {
            display: flex;
            align-items: center;
            justify-content: center;
            width: 100%;
            margin-top: 8px;
            height: 46px;
            border-radius: 14px;
            font-size: 14px;
            font-weight: 700;
          }
        }
      `}</style>

      {/* Page Header */}
      <section className="head" aria-labelledby="title">
        <div>
          <div className="eyebrow">Renter workspace</div>
          <h1 id="title">My Applications</h1>
          <p>
            Track your room applications, respond to requests, review offers and continue to agreement when selected.
          </p>
        </div>
        <Link href="/find-a-room" className="btn btn-outline btn-lg">
          <SvgIcon name="search" size={16} strokeWidth={2.2} />
          Find a Room
        </Link>
      </section>

      {/* 4 Summary Cards */}
      <section className="scards" aria-label="Application summary">
        <button
          type="button"
          className="scard"
          aria-pressed={selectedCard === "submitted"}
          onClick={() => {
            setSelectedCard(selectedCard === "submitted" ? "" : "submitted");
            setActiveView("current");
          }}
        >
          <span className="snum" style={{ background: "#EEF3FF", color: "#1D3F8F" }}>
            {summaryCounts.submitted}
          </span>
          <span className="stext">
            <span className="stitle">Submitted</span>
            <span className="shint">Received by the host</span>
          </span>
          <span className="sico" style={{ color: "#1D3F8F" }}>
            <SvgIcon name="send" size={18} strokeWidth={2.2} />
          </span>
        </button>

        <button
          type="button"
          className="scard"
          aria-pressed={selectedCard === "review"}
          onClick={() => {
            setSelectedCard(selectedCard === "review" ? "" : "review");
            setActiveView("current");
          }}
        >
          <span className="snum" style={{ background: "#EEF3FF", color: "#1D3F8F" }}>
            {summaryCounts.review}
          </span>
          <span className="stext">
            <span className="stitle">Under Review</span>
            <span className="shint">Host is reviewing</span>
          </span>
          <span className="sico" style={{ color: "#1D3F8F" }}>
            <SvgIcon name="eye" size={18} strokeWidth={2.2} />
          </span>
        </button>

        <button
          type="button"
          className="scard"
          aria-pressed={selectedCard === "offers"}
          onClick={() => {
            setSelectedCard(selectedCard === "offers" ? "" : "offers");
            setActiveView("current");
          }}
        >
          <span className="snum" style={{ background: "#E7F8EF", color: "#05603A" }}>
            {summaryCounts.offers}
          </span>
          <span className="stext">
            <span className="stitle">Offers</span>
            <span className="shint">Offers &amp; agreements</span>
          </span>
          <span className="sico" style={{ color: "#05603A" }}>
            <SvgIcon name="file" size={18} strokeWidth={2.2} />
          </span>
        </button>

        <button
          type="button"
          className="scard scard--action"
          aria-pressed={selectedCard === "action"}
          onClick={() => {
            setSelectedCard(selectedCard === "action" ? "" : "action");
            setActiveView("current");
          }}
        >
          <span className="snum" style={{ background: "#FDECEC", color: "#B42318" }}>
            {summaryCounts.action}
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

      {/* Action Strip Banner */}
      {attentionItems.length > 0 && (
        <section className="astrip" aria-labelledby="att-title">
          <div className="astrip-title">
            <SvgIcon name="alert" size={18} strokeWidth={2.2} />
            <strong id="att-title">
              {attentionItems.length === 1 ? "1 application needs" : `${attentionItems.length} applications need`} your attention
            </strong>
          </div>
          <div className="aitems">
            {attentionItems.slice(0, 2).map((item) => (
              <div key={item.id} className="aitem">
                <span className="atext">{item.actionText}</span>
                {item.st === "offer" ? (
                  <button
                    type="button"
                    className="btn btn-red btn-sm"
                    onClick={() => handleOpenDrawer(item, "offer")}
                  >
                    Review Offer
                  </button>
                ) : item.st === "sign" ? (
                  <button
                    type="button"
                    className="btn btn-navy btn-sm"
                    onClick={() => setSignModalApp(item)}
                  >
                    Review &amp; Sign
                  </button>
                ) : item.st === "pay" ? (
                  <button
                    type="button"
                    className="btn btn-red btn-sm"
                    onClick={() => handleOpenDrawer(item, "offer")}
                  >
                    Pay Now
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn btn-navy btn-sm"
                    onClick={() => handleOpenDrawer(item, "application")}
                  >
                    Respond
                  </button>
                )}
              </div>
            ))}
            {attentionItems.length > 2 && (
              <button
                type="button"
                className="text-left text-sm font-bold text-[#b42318] hover:underline"
                onClick={() => setSelectedCard("action")}
              >
                + {attentionItems.length - 2} more
              </button>
            )}
          </div>
        </section>
      )}

      {/* Search and Filters Bar */}
      <section className="filters-wrap" aria-label="Search and filters">
        <div className="searchline">
          <div className="search-box">
            <SvgIcon name="search" size={16} strokeWidth={2.2} color="#4A5878" />
            <input
              type="search"
              placeholder="Search property, room, application ID or location"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>
          <button
            type="button"
            className="btn btn-outline btn-sm filter-toggle"
            aria-expanded={isFilterSheetOpen}
            onClick={() => setIsFilterSheetOpen(!isFilterSheetOpen)}
          >
            <SvgIcon name="filters" size={16} strokeWidth={2.2} />
            Filters
          </button>
        </div>

        {/* Mobile Filter Sheet Backdrop */}
        {isFilterSheetOpen && (
          <div
            className="fixed inset-0 bg-black/40 z-50 md:hidden"
            onClick={() => setIsFilterSheetOpen(false)}
          />
        )}

        {/* Filters Panel: inline row on desktop, bottom sheet drawer on mobile */}
        <div className={`fpanel ${isFilterSheetOpen ? "open" : ""}`}>
          <div className="fsheet-head">
            <strong>Filters</strong>
            <button
              type="button"
              className="icon-btn"
              aria-label="Close filters"
              onClick={() => setIsFilterSheetOpen(false)}
            >
              <SvgIcon name="x" size={18} strokeWidth={2.4} />
            </button>
          </div>

          {/* Stage */}
          <RoundedFilterDropdown
            label="Stage"
            value={selectedStage}
            onChange={setSelectedStage}
            options={[
              { value: "all", label: "All stages" },
              { value: "application", label: "Application" },
              { value: "offer", label: "Offer" },
              { value: "agreement", label: "Agreement" },
            ]}
            size="md"
          />

          {/* Status */}
          <RoundedFilterDropdown
            label="Status"
            value={selectedStatus}
            onChange={setSelectedStatus}
            options={[
              { value: "all", label: "All statuses" },
              { value: "submitted", label: "Submitted" },
              { value: "review", label: "Under Review" },
              { value: "info", label: "Information Requested" },
              { value: "offer", label: "Offer Received" },
              { value: "agr", label: "Agreement" },
            ]}
            size="md"
          />

          {/* Location */}
          <RoundedFilterDropdown
            label="Location"
            value={selectedLoc}
            onChange={setSelectedLoc}
            options={[
              { value: "all", label: "All locations" },
              ...uniqueLocations.map((loc) => ({ value: loc, label: loc })),
            ]}
            size="md"
          />

          {/* Move-in */}
          <RoundedFilterDropdown
            label="Move-in"
            value={selectedMove}
            onChange={setSelectedMove}
            options={[
              { value: "all", label: "Any time" },
              { value: "this_month", label: "This month" },
              { value: "next_month", label: "Next month" },
            ]}
            size="md"
          />

          {/* Sort */}
          <RoundedFilterDropdown
            label="Sort"
            value={selectedSort}
            onChange={setSelectedSort}
            options={[
              { value: "action", label: "Action required first" },
              { value: "applied", label: "Recently applied" },
              { value: "movein", label: "Earliest move-in" },
            ]}
            align="right"
            size="md"
          />

          <button
            type="button"
            className="btn btn-navy fapply"
            onClick={() => setIsFilterSheetOpen(false)}
          >
            Show results
          </button>
        </div>
      </section>

      {/* Main Workspace Table */}
      <section className="workspace" aria-labelledby="ws-count">
        <div className="ws-bar">
          <div className="tabs" role="tablist">
            <button
              type="button"
              role="tab"
              className="tab"
              aria-selected={activeView === "current"}
              onClick={() => {
                setActiveView("current");
                setSelectedCard("");
              }}
            >
              Current <span>({currentList.length})</span>
            </button>
            <button
              type="button"
              role="tab"
              className="tab"
              aria-selected={activeView === "history"}
              onClick={() => {
                setActiveView("history");
                setSelectedCard("");
              }}
            >
              History <span>({historyList.length})</span>
            </button>
          </div>

          <div className="ws-right">
            <span id="ws-count" className="text-sm text-[#4a5878]">
              {filteredList.length} {filteredList.length === 1 ? "application" : "applications"}
            </span>
            {hasActiveFilters && (
              <button
                type="button"
                className="btn btn-soft btn-xs"
                onClick={handleClearFilters}
              >
                Clear filters
              </button>
            )}
          </div>
        </div>

        {/* Column Headers */}
        <div className="acols" aria-hidden="true">
          <div>Property / room</div>
          <div>Application</div>
          <div>Requested terms</div>
          <div>Stage / status</div>
          <div>Updates</div>
          <div className="r">Next action</div>
        </div>

        {/* Loading skeleton */}
        {loading && (
          <div className="p-8 text-center text-sm text-[#4a5878]">
            Loading your applications...
          </div>
        )}

        {/* Table Rows */}
        {!loading && (
          <div id="list">
            {filteredList.map((x) => {
              const badgeMeta = getBadgeMeta(x.st);
              const isHist = activeView === "history";
              const isActionRow = Boolean(x.actionText);

              return (
                <article
                  key={x.id}
                  className={`arow mapp ${isActionRow ? "arow--action" : ""} ${isHist ? "arow--closed" : ""}`}
                >
                  {/* Property / Room */}
                  <button
                    type="button"
                    className="who"
                    onClick={() => handleOpenDrawer(x, "overview")}
                  >
                    <span className="thumbsm" aria-hidden="true">
                      <SvgIcon name="building" size={18} strokeWidth={2} />
                    </span>
                    <span className="whot">
                      <span className="aname">
                        {x.p} · {x.room}
                      </span>
                      <span className="text-xs text-[#4a5878]">
                        {x.type} · {x.city}
                      </span>
                    </span>
                  </button>

                  {/* Application ID */}
                  <div className="flex flex-col gap-1">
                    <span className="zl">Application</span>
                    <strong className="mono">#{x.id}</strong>
                    <span className="text-xs text-[#4a5878]">Applied {x.applied}</span>
                  </div>

                  {/* Requested Terms */}
                  <div className="flex flex-col gap-1">
                    <span className="zl">Requested terms</span>
                    <strong className="text-sm">
                      {safeFormatDate(x.mi)} · {x.dur}
                    </strong>
                    <span className="price">
                      {x.rent} <small>/ month</small>
                    </span>
                    {x.updatedNotice && !isHist && (
                      <span className="upd">
                        <SvgIcon name="info" size={12} strokeWidth={2.2} />
                        Listing updated
                      </span>
                    )}
                  </div>

                  {/* Stage / Status */}
                  <div className="flex flex-col items-start gap-1">
                    {!isHist && <span className="stage">{x.stage}</span>}
                    <span
                      className="abadge"
                      style={{
                        color: badgeMeta.fg,
                        background: badgeMeta.bg,
                        borderColor: badgeMeta.bd,
                      }}
                    >
                      <SvgIcon name={badgeMeta.i} size={13} strokeWidth={2.4} />
                      {badgeMeta.l}
                    </span>
                    {x.st === "offer" ? (
                      <span className="aflag">
                        <SvgIcon name="clock" size={13} strokeWidth={2.4} />
                        Expires in 5 days
                      </span>
                    ) : x.st === "info" ? (
                      <span className="aflag amber">
                        <SvgIcon name="clock" size={13} strokeWidth={2.4} />
                        Due Oct 7
                      </span>
                    ) : isHist && x.outText ? (
                      <span className="text-xs text-[#5b6886]">{x.outText}</span>
                    ) : null}
                  </div>

                  {/* Updates */}
                  <div className="flex flex-col gap-1">
                    <span className="zl">Updates</span>
                    {x.unread > 0 ? (
                      <button
                        type="button"
                        className="unread"
                        onClick={() => handleOpenDrawer(x, "messages")}
                      >
                        <SvgIcon name="msg" size={14} strokeWidth={2.2} />
                        {x.unread} unread
                      </button>
                    ) : x.st === "sign" ? (
                      <span className="upd2">Agreement ready</span>
                    ) : x.st === "pay" ? (
                      <span className="upd2">Payment due</span>
                    ) : (
                      <span className="dash">—</span>
                    )}
                  </div>

                  {/* Next Action */}
                  <div className="aact">
                    {isHist ? (
                      <button
                        type="button"
                        className="btn btn-outline cta"
                        onClick={() => handleOpenDrawer(x, "overview")}
                      >
                        View
                      </button>
                    ) : x.st === "offer" ? (
                      <button
                        type="button"
                        className="btn btn-red cta"
                        onClick={() => handleOpenDrawer(x, "offer")}
                      >
                        Review Offer
                      </button>
                    ) : x.st === "sign" ? (
                      <button
                        type="button"
                        className="btn btn-navy cta"
                        onClick={() => setSignModalApp(x)}
                      >
                        Review &amp; Sign
                      </button>
                    ) : x.st === "pay" ? (
                      <button
                        type="button"
                        className="btn btn-red cta"
                        onClick={() => handleOpenDrawer(x, "offer")}
                      >
                        Pay Now
                      </button>
                    ) : x.st === "info" ? (
                      <button
                        type="button"
                        className="btn btn-navy cta"
                        onClick={() => handleOpenDrawer(x, "application")}
                      >
                        Respond
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="btn btn-outline cta"
                        onClick={() => handleOpenDrawer(x, "overview")}
                      >
                        View Application
                      </button>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        )}

        {/* Empty state when filters return 0 */}
        {!loading && filteredList.length === 0 && (
          <div className="flex flex-col items-center justify-center p-12 text-center gap-3">
            <strong className="text-base text-[#0B1B3F]">No applications match these filters.</strong>
            <button
              type="button"
              className="btn btn-navy btn-sm"
              onClick={handleClearFilters}
            >
              Clear filters
            </button>
          </div>
        )}
      </section>

      {/* Footer Snapshot Disclaimer */}
      <p className="text-xs text-[#5b6886] px-1">
        Requested terms are a snapshot of the listing when you applied. Interest in rooms found outside Zoiko Rooms appears in Find a Room, not here.
      </p>

      {/* Empty State Reference Banner (Shown when user has no applications at all) */}
      {!loading && appItems.length === 0 && (
        <section className="empty-demo" aria-label="Empty state reference">
          <div className="empty-ill">
            <SvgIcon name="check" size={30} strokeWidth={1.8} color="#0E2F73" />
          </div>
          <div>
            <strong className="text-base text-[#0B1B3F]">No applications yet</strong>
            <div className="text-sm text-[#4a5878]">You have not applied for a room yet.</div>
          </div>
          <Link href="/find-a-room" className="btn btn-navy btn-sm ml-auto">
            <SvgIcon name="search" size={14} strokeWidth={2.4} />
            Find a Room
          </Link>
          <span className="ref-tag">Empty state · shown when you have no applications</span>
        </section>
      )}

      {/* Slide-out Application Drawer (Mounted via createPortal onto document.body) */}
      {typeof document !== "undefined" &&
        selectedApp &&
        createPortal(
          <div className="overlay" role="dialog" aria-modal="true" aria-labelledby="d-name">
            <button
              type="button"
              className="scrim"
              aria-label="Close"
              onClick={handleCloseDrawer}
            />
            <aside className="drawer">
              {/* Sticky Top Header Section (Header + Badges + Tracker + Tabs) */}
              <div className="dhead-sticky">
                {/* Drawer Header */}
                <div className="dhead">
                <div className="dwho">
                  <span className="thumbsm lg" aria-hidden="true">
                    <SvgIcon name="building" size={22} strokeWidth={2} />
                  </span>
                  <div>
                    <div className="eyebrow">Application #{selectedApp.id}</div>
                    <h2 id="d-name">
                      {selectedApp.p} · {selectedApp.room} — {selectedApp.type}
                    </h2>
                    <div className="text-xs text-[#4a5878]">
                      {selectedApp.city}
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  className="icon-btn bordered"
                  aria-label="Close application"
                  onClick={handleCloseDrawer}
                >
                  <SvgIcon name="x" size={18} strokeWidth={2.4} />
                </button>
              </div>

              {/* Status & Unread */}
              <div className="dstate">
                {(() => {
                  const m = getBadgeMeta(selectedApp.st);
                  return (
                    <span
                      className="abadge"
                      style={{ color: m.fg, background: m.bg, borderColor: m.bd }}
                    >
                      <SvgIcon name={m.i} size={13} strokeWidth={2.4} />
                      {m.l}
                    </span>
                  );
                })()}
                {selectedApp.unread > 0 && (
                  <span className="unread static">
                    <SvgIcon name="msg" size={13} strokeWidth={2.2} />
                    {selectedApp.unread} unread
                  </span>
                )}
              </div>

              {/* Progress Tracker (4 steps: Application -> Offer -> Agreement -> My Rentals) */}
              {!["notsel", "withdrawn", "closed", "oexp", "odecl"].includes(selectedApp.st) && (
                <ol className="tracker" aria-label="Application progress">
                  {(() => {
                    const stageIndex =
                      selectedApp.stage === "application"
                        ? 0
                        : selectedApp.stage === "offer"
                        ? 1
                        : 2;
                    const steps = ["Application", "Offer", "Agreement", "My Rentals"];
                    return steps.map((name, idx) => {
                      const state = idx < stageIndex ? "done" : idx === stageIndex ? "cur" : "todo";
                      return (
                        <li key={name} className={state}>
                          <span>
                            {state === "done" ? <SvgIcon name="check" size={12} strokeWidth={3} /> : idx + 1}
                          </span>
                          {name}
                        </li>
                      );
                    });
                  })()}
                </ol>
              )}

              {/* Drawer Tabs */}
              <div className="dtabs" role="tablist">
                {[
                  ["overview", "Overview"],
                  ["application", "Your Application"],
                  ["messages", "Messages"],
                  ["offer", "Offer"],
                  ["history", "History"],
                ].map(([tabKey, tabLabel]) => (
                  <button
                    key={tabKey}
                    type="button"
                    role="tab"
                    className="dtab"
                    aria-selected={activeDrawerTab === tabKey}
                    onClick={() => setActiveDrawerTab(tabKey as typeof activeDrawerTab)}
                  >
                    {tabLabel}
                  </button>
                ))}
                </div>
              </div>

              {/* Drawer Body */}
              <div className="dtbody">
                {/* 1. OVERVIEW TAB */}
                {activeDrawerTab === "overview" && (
                  <>
                    {selectedApp.st === "offer" && (
                      <div className="dalert">
                        <SvgIcon name="clock" size={16} strokeWidth={2.2} />
                        <span>Offer expires soon. Review to continue.</span>
                      </div>
                    )}
                    {selectedApp.st === "info" && (
                      <div className="dalert warn">
                        <SvgIcon name="file" size={16} strokeWidth={2.2} />
                        <span>The host requested information. Due soon.</span>
                      </div>
                    )}
                    {selectedApp.updatedNotice && (
                      <div className="dinfo">
                        <SvgIcon name="info" size={16} strokeWidth={2.2} />
                        <span>{selectedApp.updatedNotice}</span>
                      </div>
                    )}
                    {selectedApp.outText && (
                      <div className="dinfo">
                        <SvgIcon name="info" size={16} strokeWidth={2.2} />
                        <span>{selectedApp.outText}</span>
                      </div>
                    )}

                    <div className="dsec">
                      <h3>Your requested terms</h3>
                      <div className="kv2">
                        <div>
                          <span>Move-in</span>
                          <strong>{safeFormatDate(selectedApp.mi)}, 2026</strong>
                        </div>
                        <div>
                          <span>Duration</span>
                          <strong>{selectedApp.dur}</strong>
                        </div>
                        <div>
                          <span>Terms when applied</span>
                          <strong>{selectedApp.rent} / month</strong>
                        </div>
                        <div>
                          <span>Listing</span>
                          <strong>{selectedApp.ltitle} · #{selectedApp.lid}</strong>
                        </div>
                      </div>
                    </div>

                    {selectedApp.offer && selectedApp.st !== "offer" && (
                      <div className="dsec">
                        <h3>Accepted offer</h3>
                        <div className="kv2">
                          <div>
                            <span>Rent</span>
                            <strong>{displayOffer!.rent}</strong>
                          </div>
                          <div>
                            <span>Start</span>
                            <strong>{displayOffer!.start}</strong>
                          </div>
                          <div>
                            <span>Term</span>
                            <strong>{displayOffer!.term}</strong>
                          </div>
                          <div>
                            <span>Offer</span>
                            <strong>{displayOffer!.id}</strong>
                          </div>
                        </div>
                      </div>
                    )}

                    {selectedApp.agr && (
                      <div className="dsec">
                        <h3>Agreement</h3>
                        <div className="kv2">
                          <div>
                            <span>Agreement</span>
                            <strong>{selectedApp.agr.id}</strong>
                          </div>
                          <div>
                            <span>Status</span>
                            <strong>{getBadgeMeta(selectedApp.st).l}</strong>
                          </div>
                        </div>
                        <p className="text-xs text-[#5b6886] mt-1">{selectedApp.agr.note}</p>
                        {selectedApp.rawApp.agreementId && (
                          <a
                            href={tenantAgreementPdfUrl(selectedApp.rawApp.agreementId)}
                            target="_blank"
                            rel="noreferrer"
                            className="mt-2 inline-flex items-center gap-1 text-xs font-bold text-[#0e2f73] hover:underline"
                          >
                            <SvgIcon name="file" size={14} strokeWidth={2.2} />
                            Download agreement (PDF)
                          </a>
                        )}
                      </div>
                    )}
                  </>
                )}

                {/* 2. YOUR APPLICATION TAB */}
                {activeDrawerTab === "application" && (
                  <>
                    <div className="dsec">
                      <h3>What you submitted</h3>
                      <div className="kv2">
                        <div>
                          <span>Application</span>
                          <strong>#{selectedApp.id}</strong>
                        </div>
                        <div>
                          <span>Submitted</span>
                          <strong>{selectedApp.applied}, 2026</strong>
                        </div>
                        <div>
                          <span>Occupants</span>
                          <strong>1 adult</strong>
                        </div>
                        <div>
                          <span>Identity</span>
                          <strong className="text-[#05603A]">Verified (assertion only)</strong>
                        </div>
                      </div>
                      <p className="text-xs text-[#5b6886] flex items-center gap-1 mt-1">
                        <SvgIcon name="lock" size={13} strokeWidth={2.2} />
                        Identity documents stay in Verification and are never shared through applications.
                      </p>
                    </div>

                    {selectedApp.st === "info" && (
                      <div className="dsec">
                        <h3>Information requested</h3>
                        <p className="text-xs text-[#5b6886]">
                          Requested by: Host / Authorized Manager
                        </p>
                        <div className="field">
                          <label htmlFor="r-date">Updated requested move-in date</label>
                          <input
                            id="r-date"
                            type="date"
                            value={infoDate}
                            onChange={(e) => setInfoDate(e.target.value)}
                          />
                        </div>
                        <div className="field">
                          <span className="text-xs font-bold text-[#3a4866]">Rental reference</span>
                          <label className="drop" htmlFor="r-file">
                            <SvgIcon name="upload" size={20} strokeWidth={2} />
                            <span>
                              <strong>Upload reference</strong>
                              <span className="text-xs text-[#4a5878]">PDF or image · secure upload</span>
                            </span>
                          </label>
                          <input type="file" id="r-file" className="sr-only" />
                        </div>
                        {infoSubmitted ? (
                          <p className="text-sm font-bold text-[#05603A]">
                            Response submitted! Your application is back under review.
                          </p>
                        ) : (
                          <button
                            type="button"
                            className="btn btn-navy"
                            onClick={() => {
                              setInfoSubmitted(true);
                              showToast("Response submitted. Your application is back under review.");
                            }}
                          >
                            Submit Response
                          </button>
                        )}
                      </div>
                    )}
                  </>
                )}

                {/* 3. MESSAGES TAB */}
                {activeDrawerTab === "messages" && (
                  <>
                    <div className="thread">
                      {(chatMessages[selectedApp.id] ?? []).length === 0 && (
                        <p className="text-xs text-[#5b6886]">No messages yet. Ask the host anything about this room.</p>
                      )}
                      {(chatMessages[selectedApp.id] ?? []).map((m, idx) => (
                        <div key={idx} className={`bub ${m.from === "host" ? "in" : "out"}`}>
                          <span>{m.text}</span>
                          <time>{m.time}</time>
                        </div>
                      ))}
                    </div>

                    {/* Messaging stays open once the agreement is signed ("closed" = SIGNED/ACTIVE). */}
                    {!["notsel", "withdrawn", "oexp", "odecl"].includes(selectedApp.st) && (
                      <>
                        <div className="composer">
                          <input
                            type="text"
                            placeholder="Message the host or authorized manager…"
                            value={messageInput}
                            onChange={(e) => setMessageInput(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") handleSendMessage(selectedApp.id);
                            }}
                          />
                          <button
                            type="button"
                            className="icon-btn bordered"
                            style={{ background: "#0e2f73", color: "#ffffff" }}
                            aria-label="Send message"
                            onClick={() => handleSendMessage(selectedApp.id)}
                          >
                            <SvgIcon name="send" size={18} strokeWidth={2.2} />
                          </button>
                        </div>
                        <p className="text-xs text-[#5b6886]">
                          Keep conversations in Zoiko Rooms — no need to share your phone or email.
                        </p>
                      </>
                    )}
                  </>
                )}

                {/* 4. OFFER TAB */}
                {activeDrawerTab === "offer" && (
                  <>
                    {offerLoading ? (
                      <p className="text-sm text-[#5b6886]">Loading offer details...</p>
                    ) : selectedApp.offer ? (
                      <>
                        <div className="dsec">
                          <h3>
                            {selectedApp.st === "offer" ? "Current offer" : "Accepted offer"} · {displayOffer!.id}
                          </h3>
                          <div className="kv2">
                            <div>
                              <span>Proposed rent</span>
                              <strong>{displayOffer!.rent}</strong>
                            </div>
                            <div>
                              <span>Start date</span>
                              <strong>{displayOffer!.start}</strong>
                            </div>
                            <div>
                              <span>Term</span>
                              <strong>{displayOffer!.term}</strong>
                            </div>
                            <div>
                              <span>Deposit proposal</span>
                              <strong>{displayOffer!.deposit}</strong>
                            </div>
                            <div>
                              <span>Status</span>
                              <strong>{displayOffer!.exp}</strong>
                            </div>
                            <div>
                              <span>Next stage</span>
                              <strong>Agreement</strong>
                            </div>
                          </div>
                        </div>

                        {selectedApp.st === "offer" && (
                          <div className="dinfo">
                            <SvgIcon name="info" size={16} strokeWidth={2.2} />
                            <span>
                              <strong>Important:</strong> Accepting this offer does not create an active rental. If you accept, the transaction progresses to the Agreement stage.
                            </span>
                          </div>
                        )}

                        <p className="text-xs text-[#5b6886]">
                          The Zoiko Rooms Listing Fee is paid by the host and is separate from any rent or deposit.
                        </p>

                        {/* Move-in payment once both parties have signed. Also SENT with both
                            signatures: the checkout-expiry sweep resets an unpaid agreement to
                            SENT after its deadline, but the backend still accepts the payment. */}
                        {offerDetails?.agreement &&
                          (RENTER_PAYABLE_AGREEMENT_STATUSES.includes(offerDetails.agreement.status) ||
                            (offerDetails.agreement.status === "SENT" &&
                              !!offerDetails.agreement.signedByRenterAt &&
                              !!offerDetails.agreement.signedByProviderAt)) && (
                            <div className="dsec">
                              <h3>Payment due</h3>
                              <p className="text-xs text-[#5b6886]">
                                Both you and the host have signed. Pay the amounts below to confirm the agreement.
                              </p>
                              {paymentPreview === null ? (
                                <p className="text-sm text-[#5b6886]">Loading payment details...</p>
                              ) : paymentPreview.amountDueNow.length === 0 ? (
                                <p className="text-sm text-[#5b6886]">Nothing due right now.</p>
                              ) : (
                                <div className="space-y-3">
                                  {paymentPreview.amountDueNow.map((o) => {
                                    const match = matchRentalPaymentObligation(
                                      o,
                                      paymentPreview.amountDueNow,
                                      rentalPaymentObligations
                                    );
                                    if (match) {
                                      return (
                                        <div key={o.id} className="space-y-1.5">
                                          <SingleObligationPaymentCard
                                            obligation={match}
                                            onChanged={() => {
                                              if (offerDetails.agreement) loadAgreementPayments(offerDetails.agreement.id);
                                              loadData();
                                            }}
                                          />
                                          {match.obligationType === "DEPOSIT" && (
                                            <p className="text-xs text-[#5b6886]">
                                              Zoiko Rooms does not hold this deposit — it&apos;s held by your landlord or
                                              agent, per applicable local rules.
                                            </p>
                                          )}
                                        </div>
                                      );
                                    }
                                    return (
                                      <div key={o.id} className="flex flex-wrap items-center justify-between gap-2">
                                        <span className="text-sm font-semibold text-[#0E2F73]">
                                          {o.obligationType === "RENT"
                                            ? "Rent"
                                            : o.obligationType === "DEPOSIT"
                                            ? "Deposit"
                                            : o.obligationType}
                                          {" — "}
                                          {formatCurrency(o.amount, o.currency)}
                                        </span>
                                        {o.status === "PAID" ? (
                                          <span className="text-xs font-bold text-[#05603A]">Paid</span>
                                        ) : capabilities?.rent_collection_enabled ? (
                                          <div className="flex items-center gap-2">
                                            <select
                                              className="h-9 rounded-full border border-slate-300 px-3 text-xs cursor-pointer dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                                              value={selectedMethod[o.id] ?? ""}
                                              onChange={(e) =>
                                                setSelectedMethod((prev) => ({ ...prev, [o.id]: e.target.value }))
                                              }
                                              disabled={!obligationMethods[o.id]?.length}
                                            >
                                              {(obligationMethods[o.id] ?? []).map((m) => (
                                                <option key={m} value={m}>
                                                  {PAYMENT_METHOD_LABELS[m] ?? m}
                                                </option>
                                              ))}
                                            </select>
                                            <button
                                              type="button"
                                              className="btn btn-navy btn-sm"
                                              disabled={!selectedMethod[o.id] || payingObligationId === o.id}
                                              onClick={() => handlePayObligation(o.id)}
                                            >
                                              {payingObligationId === o.id ? "Paying..." : "Pay now"}
                                            </button>
                                          </div>
                                        ) : (
                                          <DirectPaymentNotice compact />
                                        )}
                                      </div>
                                    );
                                  })}
                                </div>
                              )}
                            </div>
                          )}
                      </>
                    ) : (
                      <p className="text-sm text-[#5b6886]">
                        There is no offer for this application yet. You’ll be notified if the host makes one.
                      </p>
                    )}
                  </>
                )}

                {/* 5. HISTORY TAB */}
                {activeDrawerTab === "history" && (
                  <ol className="tl">
                    <li>
                      <span className="tdot" />
                      <span>
                        <strong>Application submitted</strong>
                        <span className="text-xs text-[#5b6886]">{selectedApp.applied}</span>
                      </span>
                    </li>
                    {selectedApp.st !== "submitted" && (
                      <li>
                        <span className="tdot" />
                        <span>
                          <strong>Under review</strong>
                          <span className="text-xs text-[#5b6886]">In progress</span>
                        </span>
                      </li>
                    )}
                    {selectedApp.offer && (
                      <li>
                        <span className="tdot" />
                        <span>
                          <strong>Offer issued · {displayOffer!.id}</strong>
                          <span className="text-xs text-[#5b6886]">Recent</span>
                        </span>
                      </li>
                    )}
                    {selectedApp.agr && (
                      <li>
                        <span className="tdot" />
                        <span>
                          <strong>Agreement ready · {selectedApp.agr.id}</strong>
                          <span className="text-xs text-[#5b6886]">Pending signature</span>
                        </span>
                      </li>
                    )}
                  </ol>
                )}
              </div>

              {/* Drawer Footer Actions */}
              <div className="dacts">
                {["notsel", "withdrawn", "closed", "oexp", "odecl"].includes(selectedApp.st) ? (
                  <p className="text-xs text-[#5b6886]">This application is closed. History stays available.</p>
                ) : selectedApp.st === "offer" ? (
                  <>
                    <button
                      type="button"
                      className="btn btn-navy"
                      onClick={() => setAcceptModalApp(selectedApp)}
                    >
                      Accept Offer
                    </button>
                    <button
                      type="button"
                      className="btn btn-outline"
                      onClick={() => setDeclineModalApp(selectedApp)}
                    >
                      Decline Offer
                    </button>
                    <button
                      type="button"
                      className="btn btn-outline"
                      onClick={() => {
                        setCounterForm({
                          monthlyRent: latestTerms
                            ? String(latestTerms.monthlyRent)
                            : selectedApp.rent.replace(/[^\d]/g, ""),
                          depositAmount: latestTerms ? String(latestTerms.depositAmount) : "",
                          startDate: latestTerms?.startDate ?? selectedApp.mi,
                          termMonths: latestTerms ? String(latestTerms.termMonths) : "6",
                          message: "",
                        });
                        setCounterModalApp(selectedApp);
                      }}
                    >
                      Propose Different Terms
                    </button>
                    <div className="grow" />
                    <button
                      type="button"
                      className="btn btn-ghost"
                      onClick={() => setActiveDrawerTab("messages")}
                    >
                      Message Host
                    </button>
                  </>
                ) : selectedApp.st === "sign" ? (
                  <>
                    <button
                      type="button"
                      className="btn btn-navy"
                      onClick={() => setSignModalApp(selectedApp)}
                    >
                      Review &amp; Sign
                    </button>
                    <div className="grow" />
                    <button
                      type="button"
                      className="btn btn-ghost"
                      onClick={() => setActiveDrawerTab("messages")}
                    >
                      Message Host
                    </button>
                  </>
                ) : selectedApp.st === "pay" ? (
                  <>
                    <button
                      type="button"
                      className="btn btn-red"
                      onClick={() => setActiveDrawerTab("offer")}
                    >
                      Pay Now
                    </button>
                    <div className="grow" />
                    <button
                      type="button"
                      className="btn btn-ghost"
                      onClick={() => setActiveDrawerTab("messages")}
                    >
                      Message Host
                    </button>
                  </>
                ) : selectedApp.stage === "agreement" ? (
                  <>
                    <button
                      type="button"
                      className="btn btn-outline"
                      onClick={() => setActiveDrawerTab("messages")}
                    >
                      Message Host
                    </button>
                    <p className="text-xs text-[#5b6886] w-full mt-1">
                      Withdrawal no longer applies at the agreement stage — use the cancellation process if needed.
                    </p>
                  </>
                ) : (
                  <>
                    {selectedApp.st === "info" && (
                      <button
                        type="button"
                        className="btn btn-navy"
                        onClick={() => setActiveDrawerTab("application")}
                      >
                        Respond
                      </button>
                    )}
                    <button
                      type="button"
                      className="btn btn-outline"
                      onClick={() => setActiveDrawerTab("messages")}
                    >
                      Message Host
                    </button>
                    <div className="grow" />
                    <button
                      type="button"
                      className="btn btn-ghost danger-t"
                      onClick={() => setWithdrawModalApp(selectedApp)}
                    >
                      Withdraw application
                    </button>
                  </>
                )}
              </div>
            </aside>
          </div>,
          document.body
        )}

      {/* Accept Offer Modal */}
      {typeof document !== "undefined" &&
        acceptModalApp &&
        createPortal(
          <div className="overlay center" role="alertdialog" aria-modal="true" aria-labelledby="m-title">
            <button
              type="button"
              className="scrim"
              aria-label="Cancel"
              onClick={() => {
                setAcceptModalApp(null);
                setOverlapMessage("");
                setOverrideReasonInput("");
              }}
            />
            <div className="dialog">
              <h2 id="m-title">Accept offer?</h2>
              <p className="text-sm text-[#4a5878]">
                You are accepting the commercial terms shown for {acceptModalApp.p} · {acceptModalApp.room}. Accepting this offer progresses the transaction to the Agreement stage. It does not by itself create an active rental or confirm move-in.
              </p>
              <div className="kv2">
                <div>
                  <span>Rent</span>
                  <strong>{acceptOffer?.rent || acceptModalApp.rent}</strong>
                </div>
                <div>
                  <span>Start</span>
                  <strong>{acceptOffer?.start || safeFormatDate(acceptModalApp.mi)}</strong>
                </div>
                <div>
                  <span>Term</span>
                  <strong>{acceptOffer?.term || acceptModalApp.dur}</strong>
                </div>
                <div>
                  <span>Deposit</span>
                  <strong>{acceptOffer?.deposit || "—"}</strong>
                </div>
                <div>
                  <span>Offer Version</span>
                  <strong>{acceptOffer?.id || "v1"}</strong>
                </div>
              </div>

              {overlapMessage && (
                <div className="flex flex-col gap-2 rounded-xl bg-amber-50 p-3 text-xs text-amber-800">
                  <p className="font-bold">{overlapMessage}</p>
                  <p>
                    You can still accept if you have a genuine reason (e.g. other booking ending) — explain below.
                  </p>
                  <textarea
                    rows={2}
                    className="w-full border rounded-lg p-2 text-xs"
                    placeholder="Reason for accepting anyway..."
                    value={overrideReasonInput}
                    onChange={(e) => setOverrideReasonInput(e.target.value)}
                  />
                </div>
              )}

              <div className="dlg-acts">
                <button
                  type="button"
                  className="btn btn-outline btn-sm"
                  onClick={() => {
                    setAcceptModalApp(null);
                    setOverlapMessage("");
                    setOverrideReasonInput("");
                  }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn btn-navy btn-sm"
                  disabled={actionBusy || (Boolean(overlapMessage) && !overrideReasonInput.trim())}
                  onClick={handleAcceptOfferAction}
                >
                  {actionBusy ? "Accepting..." : "Accept offer"}
                </button>
              </div>
            </div>
          </div>,
          document.body
        )}

      {/* Decline Offer Modal */}
      {typeof document !== "undefined" &&
        declineModalApp &&
        createPortal(
          <div className="overlay center" role="alertdialog" aria-modal="true" aria-labelledby="dec-title">
            <button
              type="button"
              className="scrim"
              aria-label="Cancel"
              onClick={() => setDeclineModalApp(null)}
            />
            <div className="dialog">
              <h2 id="dec-title">Decline this offer?</h2>
              <p className="text-sm text-[#4a5878]">
                The host will be notified. You won’t be able to accept this offer later.
              </p>
              <div className="dlg-acts">
                <button
                  type="button"
                  className="btn btn-outline btn-sm"
                  onClick={() => setDeclineModalApp(null)}
                >
                  Keep offer
                </button>
                <button
                  type="button"
                  className="btn btn-danger btn-sm"
                  disabled={actionBusy}
                  onClick={handleDeclineOfferAction}
                >
                  {actionBusy ? "Declining..." : "Decline offer"}
                </button>
              </div>
            </div>
          </div>,
          document.body
        )}

      {/* Withdraw Application Modal */}
      {typeof document !== "undefined" &&
        withdrawModalApp &&
        createPortal(
          <div className="overlay center" role="alertdialog" aria-modal="true" aria-labelledby="w-title">
            <button
              type="button"
              className="scrim"
              aria-label="Cancel"
              onClick={() => setWithdrawModalApp(null)}
            />
            <div className="dialog">
              <h2 id="w-title">Withdraw application?</h2>
              <p className="text-sm text-[#4a5878]">
                This will end your active application for this room. The Host will be notified. Your application history will remain available in Zoiko Rooms.
              </p>
              <div className="dlg-acts">
                <button
                  type="button"
                  className="btn btn-outline btn-sm"
                  onClick={() => setWithdrawModalApp(null)}
                >
                  Keep application
                </button>
                <button
                  type="button"
                  className="btn btn-danger btn-sm"
                  disabled={actionBusy}
                  onClick={handleWithdrawAction}
                >
                  {actionBusy ? "Withdrawing..." : "Withdraw application"}
                </button>
              </div>
            </div>
          </div>,
          document.body
        )}

      {/* Review & Sign Agreement Modal */}
      {typeof document !== "undefined" &&
        signModalApp &&
        createPortal(
          <div className="overlay center" role="alertdialog" aria-modal="true" aria-labelledby="sg-title">
            <button
              type="button"
              className="scrim"
              aria-label="Cancel"
              onClick={() => {
                setSignModalApp(null);
                setSignAck(false);
                setSignError(false);
                setSignActionError("");
              }}
            />
            <div className="dialog">
              <h2 id="sg-title">Review &amp; sign agreement</h2>
              <p className="text-xs text-[#5b6886]">
                {signAgreementLabel || signModalApp.agr?.id || "Agreement"} · {signModalApp.p} · {signModalApp.room}
              </p>
              <div className="kv2">
                <div>
                  <span>Rent</span>
                  <strong>{signOfferView?.rent || "—"}</strong>
                </div>
                <div>
                  <span>Start</span>
                  <strong>{signOfferView?.start || "—"}</strong>
                </div>
                <div>
                  <span>Term</span>
                  <strong>{signOfferView?.term || "—"}</strong>
                </div>
                <div>
                  <span>Deposit</span>
                  <strong>{signOfferView?.deposit || "—"}</strong>
                </div>
              </div>

              {signModalApp.rawApp.agreementId && (
                <a
                  href={tenantAgreementPdfUrl(signModalApp.rawApp.agreementId)}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs font-bold text-[#0e2f73] hover:underline flex items-center gap-1"
                >
                  <SvgIcon name="file" size={14} strokeWidth={2.2} />
                  Open full agreement (PDF)
                </a>
              )}

              {/* Required disclosures acknowledgement */}
              {disclosures.length > 0 && (
                <div className="flex flex-col gap-2 rounded-xl bg-slate-50 p-3">
                  <span className="text-xs font-bold uppercase tracking-wide text-slate-500">
                    Required Disclosures
                  </span>
                  {disclosures.map((d) => (
                    <div key={d.id} className="flex items-center justify-between gap-2 text-xs">
                      <span>{d.title || d.disclosureType}</span>
                      {d.status === "ACKNOWLEDGED" ? (
                        <span className="font-bold text-[#05603A]">Acknowledged</span>
                      ) : (
                        <button
                          type="button"
                          className="btn btn-outline btn-xs"
                          disabled={acknowledgingId === d.id}
                          onClick={() => handleAcknowledgeDisclosure(d.id)}
                        >
                          {acknowledgingId === d.id ? "Saving..." : "Acknowledge"}
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}

              <div className="dinfo">
                <SvgIcon name="info" size={16} strokeWidth={2.2} />
                <span>
                  After you sign, the host signs. Depending on where the property is, stamp duty or registration may still be required before the agreement is effective.
                </span>
              </div>

              <label className="check">
                <input
                  type="checkbox"
                  className="sr-only"
                  checked={signAck}
                  onChange={(e) => setSignAck(e.target.checked)}
                />
                <span className="cbox">
                  <SvgIcon name="check" size={14} strokeWidth={3} />
                </span>
                <span>I have read the agreement and agree to sign it electronically.</span>
              </label>

              {signError && (
                <p className="text-xs font-bold text-[#b42318]">
                  Confirm you’ve read the agreement to sign.
                </p>
              )}

              {signActionError && (
                <div className="p-3 rounded-xl bg-red-50 border border-red-200 text-[#b42318] text-xs font-semibold flex items-center gap-2">
                  <SvgIcon name="info" size={16} strokeWidth={2.2} />
                  <span>{signActionError}</span>
                </div>
              )}

              <div className="dlg-acts">
                <button
                  type="button"
                  className="btn btn-outline btn-sm"
                  onClick={() => {
                    setSignModalApp(null);
                    setSignAck(false);
                    setSignError(false);
                  }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn btn-navy btn-sm"
                  disabled={actionBusy}
                  onClick={handleSignAgreementAction}
                >
                  {actionBusy ? "Signing..." : "Sign agreement"}
                </button>
              </div>
            </div>
          </div>,
          document.body
        )}

      {/* Propose Different Terms (Counter Proposal) Modal */}
      {typeof document !== "undefined" &&
        counterModalApp &&
        createPortal(
          <div className="overlay center" role="dialog" aria-modal="true" aria-labelledby="cnt-title">
            <button
              type="button"
              className="scrim"
              aria-label="Cancel"
              onClick={() => {
                setCounterModalApp(null);
                setCounterError("");
              }}
            />
            <div className="dialog">
              <h2 id="cnt-title">Propose different terms</h2>
              <p className="text-xs text-[#5b6886]">
                Tell the host what you can pay. They can accept it, decline it, or send you new terms.
              </p>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="field">
                  <label htmlFor="c-rent">Monthly rent you can pay{latestTerms ? ` (${latestTerms.currency})` : ""}</label>
                  <input
                    id="c-rent"
                    type="number"
                    value={counterForm.monthlyRent}
                    onChange={(e) => setCounterForm({ ...counterForm, monthlyRent: e.target.value })}
                  />
                </div>
                <div className="field">
                  <label htmlFor="c-dep">Deposit you can pay{latestTerms ? ` (${latestTerms.currency})` : ""}</label>
                  <input
                    id="c-dep"
                    type="number"
                    value={counterForm.depositAmount}
                    onChange={(e) => setCounterForm({ ...counterForm, depositAmount: e.target.value })}
                  />
                </div>
                <div className="field">
                  <label htmlFor="c-start">Start date</label>
                  <input
                    id="c-start"
                    type="date"
                    value={counterForm.startDate}
                    onChange={(e) => setCounterForm({ ...counterForm, startDate: e.target.value })}
                  />
                </div>
                <div className="field">
                  <label htmlFor="c-term">Term (months)</label>
                  <input
                    id="c-term"
                    type="number"
                    value={counterForm.termMonths}
                    onChange={(e) => setCounterForm({ ...counterForm, termMonths: e.target.value })}
                  />
                </div>
              </div>

              <div className="field">
                <label htmlFor="c-msg">Message to the host (optional)</label>
                <textarea
                  id="c-msg"
                  rows={2}
                  className="w-full border rounded-xl p-2.5 text-xs text-[#0b1b3f]"
                  placeholder="e.g. This is the most I can manage right now."
                  value={counterForm.message}
                  onChange={(e) => setCounterForm({ ...counterForm, message: e.target.value })}
                />
              </div>

              {counterError && (
                <p className="text-xs font-bold text-[#b42318]">{counterError}</p>
              )}

              <div className="dlg-acts">
                <button
                  type="button"
                  className="btn btn-outline btn-sm"
                  onClick={() => {
                    setCounterModalApp(null);
                    setCounterError("");
                  }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn btn-navy btn-sm"
                  disabled={actionBusy}
                  onClick={handleSendCounterAction}
                >
                  {actionBusy ? "Sending..." : "Send proposal"}
                </button>
              </div>
            </div>
          </div>,
          document.body
        )}

      {/* Floating Toast Notification (Portaled directly to document.body so it is always on top of modals and drawers) */}
      {typeof document !== "undefined" &&
        toastMessage &&
        createPortal(
          <div className="toast" role="status" aria-live="polite">
            {toastMessage}
          </div>,
          document.body
        )}
    </div>
  );
}
