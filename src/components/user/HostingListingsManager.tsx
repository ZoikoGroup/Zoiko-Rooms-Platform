"use client";

import { useCallback, useEffect, useMemo, useState, useRef, type FormEvent } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  Check,
  Clock,
  RotateCcw,
  DoorOpen,
  Download,
  Eye,
  FileText,
  Pause,
  PauseCircle,
  Pencil,
  PenLine,
  Plus,
  Search,
  ShieldCheck,
  X,
  MoreVertical,
  Bed,
  Calendar,
  Minus,
  ArrowRight,
  Receipt,
} from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import {
  Application,
  HostedListing,
  ListingAgreementDetails,
  Occupancy,
  Property,
  Room,
} from "@/lib/types";
import { formatCurrency, formatDate, resolveImageUrl } from "@/lib/utils";
import {
  HostedListingInput,
  createHostedListing,
  errorMessage,
  listHostedApplications,
  listHostedListings,
  listHostedProperties,
  listHostedRoomOccupancies,
  listHostedRooms,
  resolveListingFeeCheckoutSession,
  submitHostedListingForReview,
  updateHostedListing,
  pauseHostedListing,
  resumeHostedListing,
  archiveHostedListing,
} from "@/lib/user-api";
import { ListARoomWizard, submitOutcomeMessage } from "@/components/user/ListARoomWizard";
import { useUserSession } from "@/components/user/UserSessionContext";
import { Field, Toast, inputClass, selectClass, useToast } from "@/components/user/ui";
import { HostMoveInSteps } from "@/components/user/HostMoveInSteps";
import { toAgreementDetailsForm } from "@/components/user/ListingAgreementDetailsFields";
import { ImageGalleryUploader } from "@/components/admin/ImageGalleryUploader";
import { AmenitiesPicker } from "@/components/ui/AmenitiesPicker";
import { RoundedFilterDropdown } from "@/components/ui/RoundedFilterDropdown";
import { ListingFeeCheckout, StripeReturn } from "@/components/user/ListingFeeCheckout";
import { PropertyVerificationWizard } from "@/components/user/PropertyVerificationWizard";
import { AuthorityVerificationWizard } from "@/components/user/AuthorityVerificationWizard";
import {
  PropertyVerificationState,
  getPropertyVerificationForProperty,
  getPropertyVerificationForRoom,
} from "@/lib/property-verification";
import { AuthorityState, listPropertyAuthority } from "@/lib/authority-verification";

const MAX_LISTING_IMAGES = 10;
const SUPPORTED_CURRENCIES = ["INR", "GBP", "USD", "EUR", "CAD", "AUD", "AED", "SGD", "NZD"];

const DEFAULT_LISTING_IMAGES = [
  "https://images.unsplash.com/photo-1522771739844-6a9f6d5f14af?auto=format&fit=crop&w=320&q=80",
  "https://images.unsplash.com/photo-1598928506311-c55ded91a20c?auto=format&fit=crop&w=320&q=80",
  "https://images.unsplash.com/photo-1505693416388-ac5ce068fe85?auto=format&fit=crop&w=320&q=80",
  "https://images.unsplash.com/photo-1560448204-e02f11c3d0e2?auto=format&fit=crop&w=320&q=80",
  "https://images.unsplash.com/photo-1512917774080-9991f1c4c750?auto=format&fit=crop&w=320&q=80",
  "https://images.unsplash.com/photo-1540518614846-7ede433c4b13?auto=format&fit=crop&w=320&q=80",
  "https://images.unsplash.com/photo-1560185127-6ed189bf02f4?auto=format&fit=crop&w=320&q=80",
  "https://images.unsplash.com/photo-1595526114035-0d45ed16cfbf?auto=format&fit=crop&w=320&q=80",
  "https://images.unsplash.com/photo-1583847268964-b28dc8f51f92?auto=format&fit=crop&w=320&q=80",
];

interface ListingFormState {
  id: string | null;
  name: string;
  roomId: string;
  roomType: string;
  city: string;
  location: string;
  pricePerNight: string;
  currency: string;
  guests: string;
  bedrooms: string;
  bathrooms: string;
  size: string;
  minStayNights: string;
  availableFrom: string;
  description: string;
  amenities: string[];
  images: string[];
  contactName: string;
  contactPhone: string;
  contactEmail: string;
  agreementDetails: ListingAgreementDetails;
}

// HostedListing has no typed createdAt; read it defensively when the API sends one.
function listingCreatedAt(listing: HostedListing): string | undefined {
  return (listing as HostedListing & { createdAt?: string }).createdAt;
}

function toFormState(listing: HostedListing): ListingFormState {
  return {
    id: listing.id,
    name: listing.name,
    roomId: listing.roomId === null ? "" : String(listing.roomId),
    roomType: listing.roomType,
    city: listing.city,
    location: listing.location,
    pricePerNight: String(listing.pricePerNight),
    currency: listing.currency,
    guests: String(listing.guests),
    bedrooms: String(listing.bedrooms),
    bathrooms: String(listing.bathrooms),
    size: String(listing.size),
    minStayNights: String(listing.minStayNights),
    availableFrom: listing.availableFrom ?? "",
    description: listing.description,
    amenities: listing.amenities,
    images: listing.images,
    contactName: listing.contactName,
    contactPhone: listing.contactPhone,
    contactEmail: listing.contactEmail,
    agreementDetails: toAgreementDetailsForm(listing.agreementDetails),
  };
}

export function HostingListingsManager() {
  const { user } = useUserSession();
  const { toast, showToast } = useToast();
  const router = useRouter();
  const searchParams = useSearchParams();

  // Dynamic Backend State
  const [listings, setListings] = useState<HostedListing[]>([]);
  const [properties, setProperties] = useState<Property[]>([]);
  const [roomsByProperty, setRoomsByProperty] = useState<Record<number, Room[]>>({});
  const [occupanciesByRoom, setOccupanciesByRoom] = useState<Record<number, Occupancy[]>>({});
  const [applications, setApplications] = useState<Application[]>([]);
  const [loading, setLoading] = useState(true);

  // Verifications per property
  const [propertyStatus, setPropertyStatus] = useState<
    Record<number, { state: PropertyVerificationState; message: string }>
  >({});
  const [authorityByProperty, setAuthorityByProperty] = useState<
    Record<number, { state: AuthorityState; message: string }>
  >({});

  // Filter & Search Controls matching Figma Frame
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL_ACTIVE");
  const [roomStatusFilter, setRoomStatusFilter] = useState("ALL");
  const [appFilter, setAppFilter] = useState("ALL");
  const [propertyFilter, setPropertyFilter] = useState("ALL");
  const [sortOption, setSortOption] = useState("ACTION_REQUIRED");

  // Secondary "More Filters" State
  const [showMoreFilters, setShowMoreFilters] = useState(false);
  const [verifFilter, setVerifFilter] = useState("ALL");
  const [feeFilter, setFeeFilter] = useState("ALL");
  const [minPriceFilter, setMinPriceFilter] = useState("");
  const [maxPriceFilter, setMaxPriceFilter] = useState("");
  const [roomTypeFilter, setRoomTypeFilter] = useState("ALL");
  const [minStayFilter, setMinStayFilter] = useState("ALL");

  // Interactive Modals State
  const [form, setForm] = useState<ListingFormState | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const [payingFeeListingId, setPayingFeeListingId] = useState<string | null>(null);
  const [returningCheckoutSessionId, setReturningCheckoutSessionId] = useState<string | null>(null);
  const [stripeReturn, setStripeReturn] = useState<StripeReturn | undefined>(undefined);
  const [propertyVerificationRoomId, setPropertyVerificationRoomId] = useState<number | null>(null);
  const [authorityRecordRoomId, setAuthorityRecordRoomId] = useState<number | null>(null);

  // Slide-out Inspection Drawer state
  const [selectedListingForDrawer, setSelectedListingForDrawer] = useState<(typeof mappedListings)[number] | null>(null);
  const [drawerTab, setDrawerTab] = useState<
    "overview" | "content" | "availability" | "verification" | "applications" | "activity"
  >("overview");
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  // Lock body scroll and handle Escape key when drawer is open
  useEffect(() => {
    if (!selectedListingForDrawer) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelectedListingForDrawer(null);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener("keydown", onKey);
    };
  }, [selectedListingForDrawer]);

  // Portal-based Three-dots menu state
  const [menuAnchor, setMenuAnchor] = useState<{
    key: string;
    listing: any;
    rect: DOMRect;
  } | null>(null);
  const portalMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuAnchor) return;

    function handleClickOutside(event: MouseEvent | TouchEvent) {
      const target = event.target as Node;
      if (portalMenuRef.current && !portalMenuRef.current.contains(target)) {
        setMenuAnchor(null);
      }
    }

    function handleScrollOrResize() {
      setMenuAnchor(null);
    }

    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("touchstart", handleClickOutside);
    window.addEventListener("scroll", handleScrollOrResize, true);
    window.addEventListener("resize", handleScrollOrResize);

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("touchstart", handleClickOutside);
      window.removeEventListener("scroll", handleScrollOrResize, true);
      window.removeEventListener("resize", handleScrollOrResize);
    };
  }, [menuAnchor]);

  // Handle Stripe return checkout session
  useEffect(() => {
    const checkoutSessionId = searchParams.get("checkoutSessionId");
    if (!checkoutSessionId) return;
    const returnedVia = searchParams.get("stripeCheckout");
    resolveListingFeeCheckoutSession(checkoutSessionId)
      .then((payment) => {
        setPayingFeeListingId(payment.listingId);
        setReturningCheckoutSessionId(checkoutSessionId);
        setStripeReturn(returnedVia === "cancel" ? "cancel" : "success");
      })
      .catch(() =>
        showToast("Could not confirm your Listing Fee payment. Please try again from your listing.", "error")
      )
      .finally(() => router.replace("/account/host/listings"));
  }, [searchParams, router, showToast]);

  // Load Real Data from FastAPI + PostgreSQL
  const load = useCallback(async () => {
    if (!user) return;
    try {
      const [owned, mine, hostedApps] = await Promise.all([
        listHostedProperties(),
        listHostedListings(),
        listHostedApplications().catch(() => [] as Application[]),
      ]);

      setProperties(owned);
      setListings(mine);
      setApplications(hostedApps);

      const roomLists = await Promise.all(
        owned.map((property) => listHostedRooms(property.id).catch(() => [] as Room[]))
      );
      setRoomsByProperty(Object.fromEntries(owned.map((property, i) => [property.id, roomLists[i]])));

      const verification = await Promise.all(
        owned.map((property) => getPropertyVerificationForProperty(property.id).catch(() => null))
      );
      setPropertyStatus(
        Object.fromEntries(
          owned.flatMap((property, i) => {
            const v = verification[i];
            if (v === null) return [];
            const message =
              v.state === "INVALIDATED" && v.verification?.reasonCodes.includes("ADDRESS_CHANGED")
                ? "The location changed, so the existing verification can't carry over."
                : v.verification?.message ?? "";
            return [[property.id, { state: v.state, message }]];
          })
        )
      );

      const authority = await Promise.all(
        owned.map((property) => listPropertyAuthority(property.id).catch(() => null))
      );
      setAuthorityByProperty(
        Object.fromEntries(
          owned.flatMap((property, i) => {
            const rows = authority[i];
            if (rows === null) return [];
            const current = rows.find((a) => a.state !== "SUPERSEDED");
            return [
              [
                property.id,
                { state: current?.state ?? "NOT_STARTED", message: current?.reason.message ?? "" },
              ],
            ];
          })
        )
      );

      const allRooms = roomLists.flat();
      const occupancyLists = await Promise.all(
        allRooms.map((room) => listHostedRoomOccupancies(room.id).catch(() => [] as Occupancy[]))
      );
      setOccupanciesByRoom(Object.fromEntries(allRooms.map((room, i) => [room.id, occupancyLists[i]])));
    } catch (err) {
      showToast(errorMessage(err, "Could not load listings from backend."), "error");
    } finally {
      setLoading(false);
    }
  }, [user, showToast]);

  useEffect(() => {
    load();
  }, [load]);

  // Lookup Maps
  const propertyByRoomId = useMemo(() => {
    const map = new Map<number, Property>();
    for (const prop of properties) {
      for (const room of roomsByProperty[prop.id] ?? []) {
        map.set(room.id, prop);
      }
    }
    return map;
  }, [properties, roomsByProperty]);

  const roomById = useMemo(() => {
    const map = new Map<number, Room>();
    for (const prop of properties) {
      for (const room of roomsByProperty[prop.id] ?? []) {
        map.set(room.id, room);
      }
    }
    return map;
  }, [properties, roomsByProperty]);

  // Mapped Listings with Derived Figma Attributes
  const mappedListings = useMemo(() => {
    return listings.filter((l) => l.state !== "ARCHIVED").map((l, idx) => {
      const room = l.roomId !== null ? roomById.get(l.roomId) : undefined;
      const property = l.roomId !== null ? propertyByRoomId.get(l.roomId) : undefined;
      const propVerif = property ? propertyStatus[property.id] : undefined;
      const authVerif = property ? authorityByProperty[property.id] : undefined;

      // Applications submitted to this listing
      const listingApps = applications.filter((a) => a.listingId === l.id);
      const newAppsCount = listingApps.filter((a) => a.status === "SUBMITTED").length;
      const hasOffer = listingApps.some(
        (a) => a.offer && (a.offer.status === "SENT" || a.offer.status === "DRAFT")
      );

      // Occupancies for this room
      const occupancies = l.roomId !== null ? occupanciesByRoom[l.roomId] ?? [] : [];
      const currentOcc = occupancies.find(
        (o) => o.status === "ACTIVE" || o.status === "PENDING_MOVE_IN"
      );

      // 1. Compute Listing Status matching Figma
      let listingStatus: "LIVE" | "IN_PROGRESS" | "PAUSED" | "ACTION_REQUIRED" = "IN_PROGRESS";
      let listingStatusLabel = "In Progress";
      let listingStatusSubtext = "Review, fee or fixes";
      let listingStatusTone: "live" | "paused" | "review" | "draft" | "fee" | "changes" | "blocked" = "review";

      // Only verifications that have actually lapsed or failed block a listing.
      // EXPIRING_SOON / MANUAL_REVIEW / IN_PROGRESS are still valid, and treating
      // them as blocked used to hide "Pay Listing Fee" on approved listings.
      const isAuthorityExpired =
        authVerif?.state === "EXPIRED" || authVerif?.state === "REVOKED" || authVerif?.state === "ACTION_REQUIRED";
      const isPropertyBlocked =
        propVerif?.state === "EXPIRED" ||
        propVerif?.state === "REJECTED" ||
        propVerif?.state === "INVALIDATED" ||
        propVerif?.state === "ACTION_REQUIRED";

      if (l.state === "PUBLISHED") {
        listingStatus = "LIVE";
        listingStatusLabel = "Live";
        listingStatusSubtext = listingCreatedAt(l)
          ? `Published ${formatDate(listingCreatedAt(l)!)}`
          : "Live on marketplace";
        listingStatusTone = "live";
      } else if (l.state === "PAUSED" || l.state === "WITHDRAWN" || l.state === "SUSPENDED") {
        listingStatus = "PAUSED";
        listingStatusLabel = l.state === "SUSPENDED" ? "Suspended" : l.state === "WITHDRAWN" ? "Withdrawn" : "Paused";
        listingStatusSubtext = currentOcc ? "Paused — occupied" : "Not publicly visible";
        listingStatusTone = "paused";
      } else if (isAuthorityExpired || isPropertyBlocked) {
        listingStatus = "ACTION_REQUIRED";
        listingStatusLabel = "Approved · Blocked";
        listingStatusSubtext = "Authority verification expired";
        listingStatusTone = "blocked";
      } else if (l.state === "APPROVED") {
        listingStatus = "IN_PROGRESS";
        listingStatusLabel = "Listing Fee Due";
        listingStatusSubtext = "Review approved";
        listingStatusTone = "fee";
      } else if (l.state === "REJECTED") {
        listingStatus = "ACTION_REQUIRED";
        listingStatusLabel = "Changes Required";
        listingStatusSubtext = "Issues to resolve";
        listingStatusTone = "changes";
      } else if (l.state === "REVIEW") {
        listingStatus = "IN_PROGRESS";
        listingStatusLabel = "Under Review";
        listingStatusSubtext = "Submitted for review";
        listingStatusTone = "review";
      } else {
        listingStatus = "IN_PROGRESS";
        listingStatusLabel = "Draft";
        listingStatusSubtext = "Ready to submit";
        listingStatusTone = "draft";
      }

      // 2. Compute Room Status
      let roomStatus: "AVAILABLE" | "UNDER_OFFER" | "OCCUPIED" | "NOTICE_GIVEN" = "AVAILABLE";
      let roomStatusSubtext = "Available now";

      if (currentOcc) {
        if (currentOcc.expectedEndDate) {
          const endDate = new Date(currentOcc.expectedEndDate);
          const now = new Date();
          const daysLeft = (endDate.getTime() - now.getTime()) / (1000 * 3600 * 24);
          if (daysLeft >= 0 && daysLeft <= 30) {
            roomStatus = "NOTICE_GIVEN";
            roomStatusSubtext = `Available from ${formatDate(currentOcc.expectedEndDate)}`;
          } else {
            roomStatus = "OCCUPIED";
            roomStatusSubtext = `Available from ${formatDate(currentOcc.expectedEndDate)}`;
          }
        } else {
          roomStatus = "OCCUPIED";
          roomStatusSubtext = "Occupied";
        }
      } else if (hasOffer) {
        roomStatus = "UNDER_OFFER";
        roomStatusSubtext = "Offer in progress";
      } else {
        roomStatus = "AVAILABLE";
        roomStatusSubtext = "Available now";
      }

      // 3. Applications Column Text
      let appsStatusType: "OPEN" | "PAUSED_AUTO" | "CLOSED" = "CLOSED";
      let appsMainText = "Closed";
      let appsSubText = "Opens after publish";

      if (l.state === "PUBLISHED") {
        if (hasOffer) {
          appsStatusType = "PAUSED_AUTO";
          appsMainText = "Paused automatically";
          appsSubText = `Offer in progress · ${listingApps.length} applications`;
        } else {
          appsStatusType = "OPEN";
          appsMainText = "Open";
          appsSubText =
            listingApps.length === 0
              ? "0 applications"
              : `${listingApps.length} applications · ${newAppsCount} new`;
        }
      } else if (listingApps.length > 0) {
        appsStatusType = "CLOSED";
        appsMainText = "Closed";
        appsSubText = `${listingApps.length} applications`;
      }

      // 4. Price & Term
      const monthlyPrice = Math.round(l.pricePerNight * 30);
      const minMonths = Math.max(1, Math.round(l.minStayNights / 30));

      // 5. Action Button Label
      let actionLabel = "View Listing";
      let actionStyle: "primary_red" | "primary_navy" | "outline" = "outline";

      if (currentOcc?.status === "PENDING_MOVE_IN") {
        // Opens the drawer, where the move-in handover steps live.
        actionLabel = "Complete Move-in";
        actionStyle = "primary_red";
      } else if (listingStatusTone === "blocked") {
        actionLabel = "Renew verification";
        actionStyle = "primary_red";
      } else if (listingStatusTone === "changes") {
        actionLabel = "Fix Listing";
        actionStyle = "primary_navy";
      } else if (listingStatusTone === "fee") {
        actionLabel = "Pay Listing Fee";
        actionStyle = "primary_navy";
      } else if (listingStatusTone === "paused" && l.state === "PAUSED") {
        actionLabel = "Resume Listing";
        actionStyle = "primary_navy";
      } else if (listingStatusTone === "draft") {
        actionLabel = "Submit for Review";
        actionStyle = "primary_navy";
      } else if (listingStatusTone === "review") {
        actionLabel = "View Review";
        actionStyle = "outline";
      } else {
        actionLabel = "View Listing";
        actionStyle = "outline";
      }

      const image =
        l.images && l.images.length > 0
          ? l.images[0]
          : DEFAULT_LISTING_IMAGES[idx % DEFAULT_LISTING_IMAGES.length];

      return {
        id: l.id,
        rawListing: l,
        title: l.name,
        propertyName: property ? property.address.split(",")[0] : l.location,
        fullAddress: property ? `${property.address}, ${property.city}` : l.location,
        roomNumber: room ? `Room #${room.id}` : "Room",
        roomType: l.roomType || (room?.hasEnsuite ? "En-suite" : "Single"),
        image,
        listingStatus,
        listingStatusLabel,
        listingStatusSubtext,
        listingStatusTone,
        roomStatus,
        roomStatusSubtext,
        appsStatusType,
        appsMainText,
        appsSubText,
        appsTotalCount: listingApps.length,
        newAppsCount,
        listingApps,
        monthlyPrice,
        currency: l.currency || "USD",
        minMonths,
        actionLabel,
        actionStyle,
        propertyId: property?.id,
        roomId: l.roomId,
        occupancyId: currentOcc?.id,
        occupancyStatus: currentOcc?.status,
        propVerif,
        authVerif,
        blockReason:
          listingStatusTone === "blocked"
            ? "Listing Authority Verification expired. Renew it in Verification to publish."
            : null,
      };
    });
  }, [
    listings,
    properties,
    propertyByRoomId,
    roomById,
    propertyStatus,
    authorityByProperty,
    applications,
    occupanciesByRoom,
  ]);

  // Derived 4 KPI Counts matching Figma Frame
  const metrics = useMemo(() => {
    let live = 0;
    let inProgress = 0;
    let paused = 0;
    let actionRequired = 0;

    mappedListings.forEach((l) => {
      if (l.listingStatus === "LIVE") live++;
      else if (l.listingStatus === "IN_PROGRESS") inProgress++;
      else if (l.listingStatus === "PAUSED") paused++;
      else if (l.listingStatus === "ACTION_REQUIRED") actionRequired++;
    });

    return { live, inProgress, paused, actionRequired, total: mappedListings.length };
  }, [mappedListings]);

  // Find any urgent listing that needs attention
  const urgentListing = useMemo(() => {
    return mappedListings.find(
      (l) => l.listingStatus === "ACTION_REQUIRED" || l.listingStatusTone === "blocked"
    );
  }, [mappedListings]);

  // Filtered Listings
  const filteredListings = useMemo(() => {
    let result = [...mappedListings];

    // Search query
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase().trim();
      result = result.filter(
        (l) =>
          l.title.toLowerCase().includes(q) ||
          l.id.toLowerCase().includes(q) ||
          l.propertyName.toLowerCase().includes(q) ||
          l.fullAddress.toLowerCase().includes(q) ||
          l.roomNumber.toLowerCase().includes(q)
      );
    }

    // Status Filter
    if (statusFilter === "ALL_ACTIVE") {
      result = result.filter((l) => l.listingStatus !== "PAUSED");
    } else if (statusFilter === "LIVE") {
      result = result.filter((l) => l.listingStatus === "LIVE");
    } else if (statusFilter === "IN_PROGRESS") {
      result = result.filter((l) => l.listingStatus === "IN_PROGRESS");
    } else if (statusFilter === "PAUSED") {
      result = result.filter((l) => l.listingStatus === "PAUSED");
    } else if (statusFilter === "ACTION_REQUIRED") {
      result = result.filter((l) => l.listingStatus === "ACTION_REQUIRED");
    }

    // Room Status Filter
    if (roomStatusFilter !== "ALL") {
      result = result.filter((l) => l.roomStatus === roomStatusFilter);
    }

    // Applications Filter
    if (appFilter === "OPEN") {
      result = result.filter((l) => l.appsStatusType === "OPEN");
    } else if (appFilter === "CLOSED") {
      result = result.filter((l) => l.appsStatusType === "CLOSED");
    } else if (appFilter === "PAUSED_AUTO") {
      result = result.filter((l) => l.appsStatusType === "PAUSED_AUTO");
    }

    // Property Filter
    if (propertyFilter !== "ALL") {
      result = result.filter((l) => l.propertyName === propertyFilter);
    }

    // Verification Filter (More Filters)
    if (verifFilter === "ACTION") {
      result = result.filter(
        (l) => l.authVerif?.state !== "VERIFIED" || l.propVerif?.state !== "VERIFIED"
      );
    } else if (verifFilter === "OK") {
      result = result.filter(
        (l) => l.authVerif?.state === "VERIFIED" && l.propVerif?.state === "VERIFIED"
      );
    }

    // Fee State Filter (More Filters)
    if (feeFilter === "PAID") {
      result = result.filter((l) => l.rawListing.state === "PUBLISHED");
    } else if (feeFilter === "DUE") {
      result = result.filter((l) => l.rawListing.state === "APPROVED");
    } else if (feeFilter === "NOT_DUE") {
      result = result.filter(
        (l) => l.rawListing.state !== "APPROVED" && l.rawListing.state !== "PUBLISHED"
      );
    }

    // Min Price Filter
    if (minPriceFilter.trim()) {
      const minP = Number(minPriceFilter);
      if (!isNaN(minP) && minP > 0) {
        result = result.filter((l) => l.monthlyPrice >= minP);
      }
    }

    // Max Price Filter
    if (maxPriceFilter.trim()) {
      const maxP = Number(maxPriceFilter);
      if (!isNaN(maxP) && maxP > 0) {
        result = result.filter((l) => l.monthlyPrice <= maxP);
      }
    }

    // Room Type Filter
    if (roomTypeFilter !== "ALL") {
      result = result.filter((l) => l.roomType.toLowerCase().includes(roomTypeFilter.toLowerCase()));
    }

    // Min Stay Filter
    if (minStayFilter === "SHORT") {
      result = result.filter((l) => l.minMonths <= 3);
    } else if (minStayFilter === "MEDIUM") {
      result = result.filter((l) => l.minMonths > 3 && l.minMonths <= 6);
    } else if (minStayFilter === "LONG") {
      result = result.filter((l) => l.minMonths > 6);
    }

    // Sort Options
    if (sortOption === "ACTION_REQUIRED") {
      result.sort((a, b) => {
        const aScore = a.listingStatus === "ACTION_REQUIRED" ? 2 : a.listingStatus === "IN_PROGRESS" ? 1 : 0;
        const bScore = b.listingStatus === "ACTION_REQUIRED" ? 2 : b.listingStatus === "IN_PROGRESS" ? 1 : 0;
        return bScore - aScore;
      });
    } else if (sortOption === "UPDATED") {
      result.sort((a, b) => b.id.localeCompare(a.id));
    } else if (sortOption === "PRICE_HIGH") {
      result.sort((a, b) => b.monthlyPrice - a.monthlyPrice);
    } else if (sortOption === "PRICE_LOW") {
      result.sort((a, b) => a.monthlyPrice - b.monthlyPrice);
    } else if (sortOption === "APPS") {
      result.sort((a, b) => b.appsTotalCount - a.appsTotalCount);
    }

    return result;
  }, [
    mappedListings,
    searchQuery,
    statusFilter,
    roomStatusFilter,
    appFilter,
    propertyFilter,
    verifFilter,
    feeFilter,
    sortOption,
    minPriceFilter,
    maxPriceFilter,
    roomTypeFilter,
    minStayFilter,
  ]);

  // Reset all filters helper
  function handleResetFilters() {
    setSearchQuery("");
    setStatusFilter("ALL_ACTIVE");
    setRoomStatusFilter("ALL");
    setAppFilter("ALL");
    setPropertyFilter("ALL");
    setVerifFilter("ALL");
    setFeeFilter("ALL");
    setSortOption("ACTION_REQUIRED");
    setMinPriceFilter("");
    setMaxPriceFilter("");
    setRoomTypeFilter("ALL");
    setMinStayFilter("ALL");
    showToast("Filters reset to default.");
  }

  // Unique properties for dropdown
  const uniquePropertyNames = useMemo(() => {
    const set = new Set<string>();
    properties.forEach((p) => {
      const name = p.address.split(",")[0];
      if (name) set.add(name);
    });
    return Array.from(set);
  }, [properties]);

  // Rounded Filter Dropdown Options
  const listingStatusOptions = useMemo(
    () => [
      { value: "ALL_ACTIVE", label: "All active" },
      { value: "ALL", label: "All listings" },
      { value: "LIVE", label: "Live" },
      { value: "IN_PROGRESS", label: "In Progress" },
      { value: "PAUSED", label: "Paused" },
      { value: "ACTION_REQUIRED", label: "Action Required" },
    ],
    []
  );

  const roomStatusOptions = useMemo(
    () => [
      { value: "ALL", label: "Any room status" },
      { value: "AVAILABLE", label: "Available" },
      { value: "UNDER_OFFER", label: "Under Offer" },
      { value: "OCCUPIED", label: "Occupied" },
      { value: "NOTICE_GIVEN", label: "Notice Given" },
    ],
    []
  );

  const appFilterOptions = useMemo(
    () => [
      { value: "ALL", label: "Any" },
      { value: "OPEN", label: "Open" },
      { value: "CLOSED", label: "Closed" },
      { value: "PAUSED_AUTO", label: "Paused automatically" },
    ],
    []
  );

  const propertyOptions = useMemo(
    () => [
      { value: "ALL", label: "All properties" },
      ...uniquePropertyNames.map((name) => ({ value: name, label: name })),
    ],
    [uniquePropertyNames]
  );

  const verifFilterOptions = useMemo(
    () => [
      { value: "ALL", label: "Any verification" },
      { value: "OK", label: "All verified" },
      { value: "ACTION", label: "Action required" },
    ],
    []
  );

  const feeFilterOptions = useMemo(
    () => [
      { value: "ALL", label: "Any fee state" },
      { value: "PAID", label: "Paid" },
      { value: "DUE", label: "Due" },
      { value: "NOT_DUE", label: "Not due" },
    ],
    []
  );

  const sortOptions = useMemo(
    () => [
      { value: "ACTION_REQUIRED", label: "Action required first" },
      { value: "UPDATED", label: "Recently updated" },
      { value: "PRICE_HIGH", label: "Price, high to low" },
      { value: "PRICE_LOW", label: "Price, low to high" },
      { value: "APPS", label: "Most applications" },
    ],
    []
  );

  const roomTypeOptions = useMemo(
    () => [
      { value: "ALL", label: "All room types" },
      { value: "studio", label: "Studio" },
      { value: "single", label: "Single" },
      { value: "double", label: "Double" },
      { value: "ensuite", label: "En-suite" },
    ],
    []
  );

  const minStayOptions = useMemo(
    () => [
      { value: "ALL", label: "Any stay" },
      { value: "SHORT", label: "Up to 3 months" },
      { value: "MEDIUM", label: "3 – 6 months" },
      { value: "LONG", label: "6+ months" },
    ],
    []
  );

  // Modal Handlers
  function closeListingFee() {
    setPayingFeeListingId(null);
    setReturningCheckoutSessionId(null);
    setStripeReturn(undefined);
    load();
  }

  function handleActionClick(l: (typeof mappedListings)[0]) {
    if (l.actionLabel === "Renew verification") {
      setSelectedListingForDrawer(null);
      if (l.roomId) {
        setAuthorityRecordRoomId(l.roomId);
      } else {
        router.push("/account/host/verification");
      }
    } else if (l.actionLabel === "Pay Listing Fee") {
      setSelectedListingForDrawer(null);
      setPayingFeeListingId(l.id);
    } else if (l.actionLabel === "Fix Listing") {
      setSelectedListingForDrawer(null);
      setForm(toFormState(l.rawListing));
    } else if (l.actionLabel === "Resume Listing") {
      setSelectedListingForDrawer(null);
      resumeHostedListing(l.id)
        .then(() => {
          showToast(`Listing ${l.title} is live again.`);
          load();
        })
        .catch((err) => showToast(errorMessage(err, "Could not resume listing"), "error"));
    } else if (l.actionLabel === "Submit for Review") {
      setSelectedListingForDrawer(null);
      submitHostedListingForReview(l.id)
        .then((updated) => {
          showToast(
            updated.state === "APPROVED"
              ? `Listing ${l.title} approved — pay the Listing Fee to go live.`
              : `Listing ${l.title} submitted.`
          );
          setSelectedListingForDrawer(null);
          load();
        })
        .catch((err) => showToast(errorMessage(err, "Could not submit listing"), "error"));
    } else {
      // Opens Slide-out Drawer
      setSelectedListingForDrawer(l);
      setDrawerTab("overview");
    }
  }

  // Export listings to CSV
  function handleExport() {
    if (filteredListings.length === 0) {
      showToast("No listings available to export.", "error");
      return;
    }

    const headers = [
      "Listing ID",
      "Listing Title",
      "Property",
      "Room",
      "Full Address",
      "Listing Status",
      "Room Status",
      "Applications Total",
      "Monthly Price",
      "Currency",
      "Min Stay (Months)",
      "Next Action",
    ];

    const escapeCsv = (val: string | number | null | undefined) => {
      if (val === null || val === undefined) return '""';
      const str = String(val).replace(/"/g, '""');
      return `"${str}"`;
    };

    const rows = filteredListings.map((l) => [
      escapeCsv(l.id),
      escapeCsv(l.title),
      escapeCsv(l.propertyName),
      escapeCsv(l.roomNumber),
      escapeCsv(l.fullAddress),
      escapeCsv(l.listingStatusLabel),
      escapeCsv(l.roomStatus),
      escapeCsv(l.appsTotalCount),
      escapeCsv(l.monthlyPrice),
      escapeCsv(l.currency),
      escapeCsv(l.minMonths),
      escapeCsv(l.actionLabel),
    ]);

    const csvContent = [headers.join(","), ...rows.map((r) => r.join(","))].join("\r\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const today = new Date().toISOString().split("T")[0];
    link.setAttribute("href", url);
    link.setAttribute("download", `host_listings_${today}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
    showToast(`Exported ${filteredListings.length} listing(s) to CSV!`);
  }

  // Handle Edit/Create Listing submit
  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!form || !user) return;

    const roomId = Number(form.roomId);
    if (!Number.isInteger(roomId) || roomId <= 0) {
      setError("Pick one of your rooms — a hosted listing must be linked to a room you own.");
      return;
    }
    if (!form.name.trim() || !form.city.trim() || !form.location.trim()) {
      setError("Name, city and area are all required.");
      return;
    }
    const minStay = Number(form.minStayNights);
    if (!Number.isFinite(minStay) || minStay < 30) {
      setError("Zoiko is a long-stay marketplace — the minimum stay must be at least 30 nights.");
      return;
    }
    const price = Number(form.pricePerNight);
    if (!Number.isFinite(price) || price <= 0) {
      setError("Enter a nightly price greater than zero.");
      return;
    }

    const payload: HostedListingInput = {
      name: form.name.trim(),
      roomType: form.roomType.trim() || "Private room",
      city: form.city.trim(),
      location: form.location.trim(),
      pricePerNight: price,
      currency: form.currency,
      guests: Math.max(1, Number(form.guests) || 1),
      bedrooms: Number(form.bedrooms) || 0,
      bathrooms: Number(form.bathrooms) || 1,
      size: Number(form.size) || 0,
      description: form.description.trim(),
      amenities: form.amenities,
      images: form.images,
      minStayNights: Math.round(minStay),
      availableFrom: form.availableFrom || null,
      roomId,
      contactName: form.contactName.trim(),
      contactPhone: form.contactPhone.trim(),
      contactEmail: form.contactEmail.trim(),
      agreementDetails: form.agreementDetails,
    };

    setError("");
    setSubmitting(true);
    try {
      if (form.id === null) {
        await createHostedListing(payload);
        showToast("Draft listing created.");
      } else {
        await updateHostedListing(form.id, payload);
        showToast("Listing updated.");
      }
      setForm(null);
      await load();
    } catch (err) {
      setError(errorMessage(err, "Could not save the listing."));
    } finally {
      setSubmitting(false);
    }
  }

  // Publication gates for the drawer
  function getPublicationGates(l: (typeof mappedListings)[0]) {
    const isAuthOk = l.authVerif?.state === "VERIFIED";
    const isPropOk = l.propVerif?.state === "VERIFIED";
    const isIdentityOk = Boolean(user?.id);
    const isContentOk = Boolean(
      l.title && l.rawListing.description && l.rawListing.images && l.rawListing.images.length > 0
    );
    const isReviewApproved =
      l.rawListing.state === "APPROVED" || l.rawListing.state === "PUBLISHED";
    const isReviewPending = l.rawListing.state === "REVIEW";
    const isReviewRejected = l.rawListing.state === "REJECTED";
    const isFeePaid = l.rawListing.state === "PUBLISHED";
    const isFeeDue = l.rawListing.state === "APPROVED";

    return [
      {
        name: "Identity verified",
        state: isIdentityOk ? "pass" : "fail",
        desc: isIdentityOk ? "Passed" : "Renewal required",
      },
      {
        name: "Property verified",
        state:
          isPropOk
            ? "pass"
            : l.propVerif?.state === "MANUAL_REVIEW" || l.propVerif?.state === "IN_PROGRESS"
            ? "wait"
            : "fail",
        desc: isPropOk ? "Passed" : "Action required",
      },
      {
        name: "Listing authority verified",
        state:
          isAuthOk
            ? "pass"
            : l.authVerif?.state === "MANUAL_REVIEW" || l.authVerif?.state === "SUBMITTED"
            ? "wait"
            : "fail",
        desc: isAuthOk ? "Passed" : "Blocking",
      },
      {
        name: "Room eligible",
        state: l.roomId ? "pass" : "fail",
        desc: l.roomId ? "Passed" : "Room unlinked",
      },
      {
        name: "Content complete",
        state: isContentOk ? "pass" : "wait",
        desc: isContentOk ? "Passed" : "Pending details",
      },
      {
        name: "Jurisdiction rules met",
        state: "pass",
        desc: "Passed",
      },
      {
        name: "Review approved",
        state: isReviewApproved
          ? "pass"
          : isReviewPending
          ? "wait"
          : isReviewRejected
          ? "fail"
          : "na",
        desc: isReviewApproved
          ? "Passed"
          : isReviewPending
          ? "Under Review"
          : isReviewRejected
          ? "Blocking"
          : "Not yet",
      },
      {
        name: "Listing Fee paid",
        state: isFeePaid ? "pass" : isFeeDue ? "fail" : "na",
        desc: isFeePaid ? "Passed" : isFeeDue ? "Due" : "Not yet",
      },
      {
        name: "No blocking platform issue",
        state: "pass",
        desc: "Passed",
      },
      {
        name: "Schedule reached",
        state: "pass",
        desc: "Passed",
      },
    ];
  }

  // Render Listing Status Badge
  function renderListingStatusBadge(l: (typeof mappedListings)[0]) {
    return (
      <div className="space-y-0.5">
        {l.listingStatusTone === "live" && (
          <div className="px-2.5 py-1 rounded-full bg-[#E7F8EF] text-[#05603A] border border-[#8FD9B4] text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5 w-fit">
            <Eye className="size-3 text-[#05603A]" />
            <span>Live</span>
          </div>
        )}
        {l.listingStatusTone === "blocked" && (
          <div className="px-2.5 py-1 rounded-full bg-[#FDECEC] text-[#B42318] border border-[#F4A9A3] text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5 w-fit">
            <AlertTriangle className="size-3 text-[#B42318]" />
            <span>Approved · Blocked</span>
          </div>
        )}
        {l.listingStatusTone === "changes" && (
          <div className="px-2.5 py-1 rounded-full bg-[#FFF4E0] text-[#93370D] border border-[#F2B866] text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5 w-fit">
            <Pencil className="size-3 text-[#93370D]" />
            <span>Changes Required</span>
          </div>
        )}
        {l.listingStatusTone === "fee" && (
          <div className="px-2.5 py-1 rounded-full bg-[#FFF4E0] text-[#93370D] border border-[#F2B866] text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5 w-fit">
            <CreditCardIcon className="size-3 text-[#93370D]" />
            <span>Listing Fee Due</span>
          </div>
        )}
        {l.listingStatusTone === "review" && (
          <div className="px-2.5 py-1 rounded-full bg-[#EEF3FF] text-[#1D3F8F] border border-[#AFC2EE] text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5 w-fit">
            <Clock className="size-3 text-[#1D3F8F]" />
            <span>Under Review</span>
          </div>
        )}
        {l.listingStatusTone === "draft" && (
          <div className="px-2.5 py-1 rounded-full bg-[#F0F2F6] text-[#344054] border border-[#C3CAD6] text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5 w-fit">
            <PenLine className="size-3 text-[#344054]" />
            <span>Draft</span>
          </div>
        )}
        {l.listingStatusTone === "paused" && (
          <div className="px-2.5 py-1 rounded-full bg-[#F0F2F6] text-[#344054] border border-[#C3CAD6] text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5 w-fit">
            <Pause className="size-3 text-[#344054]" />
            <span>Paused</span>
          </div>
        )}
        <p
          className={`text-xs ${
            l.listingStatusTone === "blocked"
              ? "text-[#B42318] font-bold"
              : "text-slate-500 dark:text-slate-400"
          }`}
        >
          {l.listingStatusSubtext}
        </p>
      </div>
    );
  }

  // Render Room Status Badge
  function renderRoomStatusBadge(l: (typeof mappedListings)[0]) {
    return (
      <div className="space-y-0.5">
        {l.roomStatus === "AVAILABLE" && (
          <div className="px-2.5 py-1 rounded-lg bg-white dark:bg-slate-800 border border-[#12A15E] text-[#05603A] dark:text-emerald-400 text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5 w-fit shadow-2xs">
            <Check className="size-3 text-[#05603A]" />
            <span>Available</span>
          </div>
        )}
        {l.roomStatus === "OCCUPIED" && (
          <div className="px-2.5 py-1 rounded-lg bg-[#0E2F73] text-white text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5 w-fit">
            <DoorOpen className="size-3 text-white" />
            <span>Occupied</span>
          </div>
        )}
        {l.roomStatus === "UNDER_OFFER" && (
          <div className="px-2.5 py-1 rounded-lg bg-white dark:bg-slate-800 border border-[#E08A2B] text-[#93370D] dark:text-amber-400 text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5 w-fit shadow-2xs">
            <Clock className="size-3 text-[#93370D]" />
            <span>Under Offer</span>
          </div>
        )}
        {l.roomStatus === "NOTICE_GIVEN" && (
          <div className="px-2.5 py-1 rounded-lg bg-white dark:bg-slate-800 border border-[#0E2F73] text-[#0E2F73] dark:text-sky-300 text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5 w-fit shadow-2xs">
            <Calendar className="size-3 text-[#0E2F73]" />
            <span>Notice Given</span>
          </div>
        )}
        <p className="text-xs text-slate-500 dark:text-slate-400">{l.roomStatusSubtext}</p>
      </div>
    );
  }

  // Render Action Button
  function renderActionButton(l: (typeof mappedListings)[0], fullWidth = false) {
    const widthClass = fullWidth ? "w-full justify-center" : "";
    if (l.actionStyle === "primary_red") {
      return (
        <button
          onClick={(e) => {
            e.stopPropagation();
            handleActionClick(l);
          }}
          className={`h-10 px-4 bg-[#D80B0B] hover:bg-red-700 text-white text-xs font-bold font-heading rounded-full shadow-xs transition cursor-pointer flex items-center ${widthClass}`}
        >
          {l.actionLabel}
        </button>
      );
    }
    if (l.actionStyle === "primary_navy") {
      return (
        <button
          onClick={(e) => {
            e.stopPropagation();
            handleActionClick(l);
          }}
          className={`h-10 px-4 bg-[#0E2F73] hover:bg-[#0a2358] text-white text-xs font-bold font-heading rounded-full shadow-xs transition cursor-pointer flex items-center ${widthClass}`}
        >
          {l.actionLabel}
        </button>
      );
    }
    return (
      <button
        onClick={(e) => {
          e.stopPropagation();
          handleActionClick(l);
        }}
        className={`h-10 px-4 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-[#0E2F73] dark:text-white text-xs font-bold font-heading rounded-full shadow-2xs transition cursor-pointer flex items-center ${widthClass}`}
      >
        {l.actionLabel}
      </button>
    );
  }

  // Render Three Dots Button (Portal-anchored, shared for Desktop & Mobile)
  function renderThreeDotsButton(
    l: (typeof mappedListings)[0],
    isMobile = false
  ) {
    if (l.actionLabel === "View Review") return null;

    const menuKey = isMobile ? `m-${l.id}` : String(l.id);

    return (
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          if (menuAnchor?.key === menuKey) {
            setMenuAnchor(null);
          } else {
            const btnRect = (e.currentTarget as HTMLElement).getBoundingClientRect();
            setMenuAnchor({
              key: menuKey,
              listing: l,
              rect: btnRect,
            });
          }
        }}
        className={
          isMobile
            ? "size-10 rounded-full border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 hover:bg-slate-50 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-300 transition cursor-pointer flex items-center justify-center shrink-0 shadow-2xs"
            : "size-9 rounded-full flex items-center justify-center hover:bg-slate-100 dark:hover:bg-slate-800 text-slate-500 hover:text-slate-900 dark:hover:text-white transition cursor-pointer"
        }
        aria-label="More actions"
      >
        <MoreVertical className="size-4" />
      </button>
    );
  }

  // Render Portal Dropdown Menu attached to document.body
  // Completely immune to parent overflow-hidden or overflow-x-auto clipping
  function renderPortalMenu() {
    if (typeof window === "undefined" || !menuAnchor) return null;

    const { listing: l, rect } = menuAnchor;
    const spaceBelow = window.innerHeight - rect.bottom;
    const spaceAbove = rect.top;
    const menuHeightEstimate = 220;
    const openUpward = spaceBelow < menuHeightEstimate && spaceAbove > spaceBelow;
    const rightOffset = Math.max(12, window.innerWidth - rect.right);

    const style: React.CSSProperties = {
      position: "fixed",
      right: `${rightOffset}px`,
      zIndex: 9999,
      ...(openUpward
        ? { bottom: `${window.innerHeight - rect.top + 6}px` }
        : { top: `${rect.bottom + 6}px` }),
    };

    return createPortal(
      <div
        ref={portalMenuRef}
        onClick={(e) => e.stopPropagation()}
        style={style}
        className="w-48 bg-white dark:bg-slate-900 rounded-2xl border border-slate-200 dark:border-slate-800 shadow-2xl py-1.5 animate-in fade-in zoom-in-95 duration-100"
      >
        <button
          type="button"
          onClick={() => {
            setMenuAnchor(null);
            setForm(toFormState(l.rawListing));
          }}
          className="w-full text-left px-4 py-2.5 text-xs font-semibold text-[#0E2F73] dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800 flex items-center gap-2 cursor-pointer"
        >
          <Pencil className="size-3.5 text-slate-400" />
          <span>Edit Listing</span>
        </button>

        <button
          type="button"
          onClick={() => {
            setMenuAnchor(null);
            router.push("/account/host/applications");
          }}
          className="w-full text-left px-4 py-2.5 text-xs font-semibold text-[#0E2F73] dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800 flex items-center gap-2 cursor-pointer"
        >
          <FileText className="size-3.5 text-slate-400" />
          <span>View Applications</span>
        </button>

        {l.rawListing.state === "APPROVED" && (
          <button
            type="button"
            onClick={() => {
              setMenuAnchor(null);
              setPayingFeeListingId(l.id);
            }}
            className="w-full text-left px-4 py-2.5 text-xs font-semibold text-[#0E2F73] dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800 flex items-center gap-2 cursor-pointer"
          >
            <Receipt className="size-3.5 text-slate-400" />
            <span>Pay Listing Fee</span>
          </button>
        )}

        {/* View Public Listing commented out as requested
        {l.listingStatus === "LIVE" && (
          <Link
            href={`/listings/${l.id}`}
            target="_blank"
            onClick={() => setMenuAnchor(null)}
            className="w-full text-left px-4 py-2.5 text-xs font-semibold text-[#0E2F73] dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800 flex items-center gap-2"
          >
            <ExternalLink className="size-3.5 text-slate-400" />
            <span>View Public Listing</span>
          </Link>
        )}
        */}

        {l.listingStatus === "LIVE" && (
          <button
            type="button"
            onClick={async () => {
              setMenuAnchor(null);
              try {
                await pauseHostedListing(l.id);
                showToast(`Listing #${l.id} paused.`);
                load();
              } catch (err) {
                showToast(errorMessage(err, "Could not pause listing"), "error");
              }
            }}
            className="w-full text-left px-4 py-2.5 text-xs font-semibold text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800 flex items-center gap-2 cursor-pointer"
          >
            <Pause className="size-3.5 text-slate-400" />
            <span>Pause Listing</span>
          </button>
        )}

        {l.rawListing.state === "PAUSED" && (
          <button
            type="button"
            onClick={async () => {
              setMenuAnchor(null);
              try {
                await resumeHostedListing(l.id);
                showToast(`Listing #${l.id} is live again.`);
                load();
              } catch (err) {
                showToast(errorMessage(err, "Could not resume listing"), "error");
              }
            }}
            className="w-full text-left px-4 py-2.5 text-xs font-semibold text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800 flex items-center gap-2 cursor-pointer"
          >
            <Eye className="size-3.5 text-slate-400" />
            <span>Resume Listing</span>
          </button>
        )}

        <button
          type="button"
          onClick={async () => {
            setMenuAnchor(null);
            try {
              await archiveHostedListing(l.id);
              showToast(`Listing #${l.id} archived.`);
              load();
            } catch (err) {
              showToast(errorMessage(err, "Could not archive listing"), "error");
            }
          }}
          className="w-full text-left px-4 py-2.5 text-xs font-semibold text-[#B42318] hover:bg-red-50 dark:hover:bg-red-950/20 border-t border-slate-100 dark:border-slate-800 flex items-center gap-2 mt-1 cursor-pointer"
        >
          <Minus className="size-3.5 text-[#B42318]" />
          <span>Archive</span>
        </button>
      </div>,
      document.body
    );
  }

  if (loading) {
    return <Loader label="Loading listings..." />;
  }

  return (
    <div className="w-full space-y-5 font-sans">
      {renderPortalMenu()}
      {/* 1. Page Header Section matching Figma Frame */}
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 pt-1">
        <div className="space-y-1.5">
          <p className="text-[11px] font-bold text-[#D80B0B] uppercase tracking-widest font-heading">
            Host workspace
          </p>
          <h1 className="text-2xl sm:text-3xl lg:text-4xl font-extrabold text-[#0E2F73] dark:text-white leading-tight font-heading">
            My Listings
          </h1>
          <p className="text-xs sm:text-sm md:text-base text-slate-500 dark:text-slate-400 font-normal max-w-3xl">
            Manage listing readiness, publication, availability, applications and pricing for your rooms.
          </p>
        </div>

        {/* Action Buttons top right */}
        <div className="flex items-center gap-3 shrink-0 flex-wrap">
          <button
            onClick={handleExport}
            className="h-11 px-5 rounded-full border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 hover:bg-slate-50 text-xs sm:text-sm font-bold text-[#0E2F73] dark:text-slate-200 transition flex items-center gap-2 cursor-pointer shadow-2xs"
          >
            <Download className="size-4 text-[#0E2F73] dark:text-slate-200" />
            <span>Export</span>
          </button>

          <button
            onClick={() => setWizardOpen(true)}
            className="h-11 px-6 rounded-full bg-[#0E2F73] hover:bg-[#0a2358] text-white font-bold text-xs sm:text-sm shadow-md transition flex items-center justify-center gap-2 cursor-pointer"
          >
            <Plus className="size-4 text-white" />
            <span>List a Room</span>
          </button>
        </div>
      </div>

      {/* 2. Four KPI Metric Cards - Interactive filter buttons matching Figma */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5 sm:gap-3.5 pt-1">
        {/* Card 1: Live */}
        <button
          type="button"
          onClick={() => setStatusFilter((prev) => (prev === "LIVE" ? "ALL_ACTIVE" : "LIVE"))}
          aria-pressed={statusFilter === "LIVE"}
          className={`text-left rounded-3xl border-2 p-4 sm:p-5 flex items-start justify-between transition cursor-pointer shadow-2xs ${
            statusFilter === "LIVE"
              ? "border-[#0E2F73] dark:border-sky-400 bg-[#F5F8FE] dark:bg-slate-800"
              : "border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 hover:border-slate-300"
          }`}
        >
          <div className="flex items-center gap-3.5 min-w-0">
            <div className="size-12 rounded-2xl bg-[#E7F8EF] flex items-center justify-center text-[#05603A] font-extrabold text-2xl font-heading shrink-0">
              {metrics.live}
            </div>
            <div className="space-y-0.5 min-w-0">
              <p className="text-base font-extrabold text-[#0E2F73] dark:text-white font-heading truncate">
                Live
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400 truncate">Published now</p>
            </div>
          </div>
          <Eye className="size-4 text-[#05603A] shrink-0 opacity-80 mt-0.5" />
        </button>

        {/* Card 2: In Progress */}
        <button
          type="button"
          onClick={() =>
            setStatusFilter((prev) => (prev === "IN_PROGRESS" ? "ALL_ACTIVE" : "IN_PROGRESS"))
          }
          aria-pressed={statusFilter === "IN_PROGRESS"}
          className={`text-left rounded-3xl border-2 p-4 sm:p-5 flex items-start justify-between transition cursor-pointer shadow-2xs ${
            statusFilter === "IN_PROGRESS"
              ? "border-[#0E2F73] dark:border-sky-400 bg-[#F5F8FE] dark:bg-slate-800"
              : "border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 hover:border-slate-300"
          }`}
        >
          <div className="flex items-center gap-3.5 min-w-0">
            <div className="size-12 rounded-2xl bg-[#EEF3FF] flex items-center justify-center text-[#1D3F8F] font-extrabold text-2xl font-heading shrink-0">
              {metrics.inProgress}
            </div>
            <div className="space-y-0.5 min-w-0">
              <p className="text-base font-extrabold text-[#0E2F73] dark:text-white font-heading truncate">
                In Progress
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400 truncate">
                Review, fee or fixes
              </p>
            </div>
          </div>
          <PenLine className="size-4 text-[#1D3F8F] shrink-0 opacity-80 mt-0.5" />
        </button>

        {/* Card 3: Paused */}
        <button
          type="button"
          onClick={() => setStatusFilter((prev) => (prev === "PAUSED" ? "ALL_ACTIVE" : "PAUSED"))}
          aria-pressed={statusFilter === "PAUSED"}
          className={`text-left rounded-3xl border-2 p-4 sm:p-5 flex items-start justify-between transition cursor-pointer shadow-2xs ${
            statusFilter === "PAUSED"
              ? "border-[#0E2F73] dark:border-sky-400 bg-[#F5F8FE] dark:bg-slate-800"
              : "border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 hover:border-slate-300"
          }`}
        >
          <div className="flex items-center gap-3.5 min-w-0">
            <div className="size-12 rounded-2xl bg-[#F0F2F6] flex items-center justify-center text-[#344054] font-extrabold text-2xl font-heading shrink-0">
              {metrics.paused}
            </div>
            <div className="space-y-0.5 min-w-0">
              <p className="text-base font-extrabold text-[#0E2F73] dark:text-white font-heading truncate">
                Paused
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400 truncate">
                Not publicly visible
              </p>
            </div>
          </div>
          <PauseCircle className="size-4 text-[#344054] shrink-0 opacity-80 mt-0.5" />
        </button>

        {/* Card 4: Action Required */}
        <button
          type="button"
          onClick={() =>
            setStatusFilter((prev) => (prev === "ACTION_REQUIRED" ? "ALL_ACTIVE" : "ACTION_REQUIRED"))
          }
          aria-pressed={statusFilter === "ACTION_REQUIRED"}
          className={`text-left rounded-3xl border-2 p-4 sm:p-5 flex items-start justify-between transition cursor-pointer shadow-2xs ${
            statusFilter === "ACTION_REQUIRED"
              ? "border-[#B42318] dark:border-red-400 bg-[#FFF7F7] dark:bg-red-950/20"
              : "border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 hover:border-slate-300"
          }`}
        >
          <div className="flex items-center gap-3.5 min-w-0">
            <div className="size-12 rounded-2xl bg-[#FDECEC] flex items-center justify-center text-[#B42318] font-extrabold text-2xl font-heading shrink-0">
              {metrics.actionRequired}
            </div>
            <div className="space-y-0.5 min-w-0">
              <p className="text-base font-extrabold text-[#0E2F73] dark:text-white font-heading truncate">
                Action Required
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400 truncate">
                Publication blocked
              </p>
            </div>
          </div>
          <AlertTriangle className="size-4 text-[#B42318] shrink-0 opacity-80 mt-0.5" />
        </button>
      </div>

      {/* 3. Urgent Attention Alert Banner matching Figma exactly */}
      {urgentListing && (
        <div className="rounded-2xl border border-[#F3C2C2] bg-[#FFF5F5] dark:bg-red-950/20 p-4 sm:p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4 shadow-2xs">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <AlertTriangle className="size-4 text-[#B42318] shrink-0" />
              <span className="text-sm font-bold text-[#B42318] font-heading">
                1 listing needs your attention
              </span>
            </div>
            <p className="text-xs text-slate-700 dark:text-slate-300">
              Listing for{" "}
              <strong className="font-bold text-[#0E2F73] dark:text-white">
                {urgentListing.roomNumber} at {urgentListing.propertyName}
              </strong>{" "}
              is approved but cannot publish until Listing Authority Verification is renewed.
            </p>
          </div>
          <button
            onClick={() => handleActionClick(urgentListing)}
            className="h-10 px-5 rounded-full bg-[#D80B0B] hover:bg-red-700 text-white text-xs font-bold font-heading shadow-sm transition flex items-center justify-center shrink-0 cursor-pointer"
          >
            Renew verification
          </button>
        </div>
      )}

      {/* 4. Filter & Search Bar matching Figma Frame exactly */}
      <div className="w-full bg-white dark:bg-slate-900 rounded-3xl border border-slate-200/80 dark:border-slate-800 p-4 sm:p-5 shadow-2xs space-y-3">
        <div className="flex flex-col lg:flex-row lg:items-end gap-3 sm:gap-3.5">
          {/* Search Box with left icon */}
          <div className="w-full lg:w-72 shrink-0">
            <div className="relative">
              <Search className="size-4 text-slate-400 absolute left-4 top-1/2 -translate-y-1/2 pointer-events-none" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search title, listing ID, property, room or address"
                className="w-full h-11 pl-10 pr-4 bg-[#F4F6FB] dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-full text-xs text-[#0E2F73] dark:text-slate-200 placeholder:text-slate-400 outline-none focus:ring-2 focus:ring-[#0E2F73] font-normal shadow-2xs"
              />
            </div>
          </div>

          {/* Filter Dropdowns & More Filters */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:flex lg:flex-wrap lg:items-end gap-2.5 sm:gap-3 w-full lg:w-auto">
            {/* 1. Listing Status */}
            <RoundedFilterDropdown
              label="Listing Status"
              value={statusFilter}
              onChange={setStatusFilter}
              options={listingStatusOptions}
              size="sm"
            />

            {/* 2. Room Status */}
            <RoundedFilterDropdown
              label="Room Status"
              value={roomStatusFilter}
              onChange={setRoomStatusFilter}
              options={roomStatusOptions}
              size="sm"
            />

            {/* 3. Applications */}
            <RoundedFilterDropdown
              label="Applications"
              value={appFilter}
              onChange={setAppFilter}
              options={appFilterOptions}
              size="sm"
            />

            {/* 4. Property */}
            <RoundedFilterDropdown
              label="Property"
              value={propertyFilter}
              onChange={setPropertyFilter}
              options={propertyOptions}
              size="sm"
            />

            {/* 5. Sort */}
            <RoundedFilterDropdown
              label="Sort"
              value={sortOption}
              onChange={setSortOption}
              options={sortOptions}
              align="right"
              size="sm"
              className="col-span-2 sm:col-span-1"
            />

            {/* + More filters button */}
            <button
              type="button"
              onClick={() => setShowMoreFilters((v) => !v)}
              className="flex items-center gap-1.5 text-xs font-bold text-[#0E2F73] dark:text-slate-200 hover:text-blue-700 transition cursor-pointer shrink-0 self-end pb-2.5 px-2"
            >
              <Plus className="size-3.5 text-[#0E2F73] dark:text-slate-200" />
              <span>{showMoreFilters ? "Fewer filters" : "More filters"}</span>
            </button>
          </div>
        </div>

        {/* Expandable "More Filters" Drawer */}
        {showMoreFilters && (
          <div className="pt-3.5 mt-1 border-t border-slate-100 dark:border-slate-800 grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3 animate-fade-in">
            {/* Verification */}
            <RoundedFilterDropdown
              label="Verification"
              value={verifFilter}
              onChange={setVerifFilter}
              options={verifFilterOptions}
            />

            {/* Fee State */}
            <RoundedFilterDropdown
              label="Fee State"
              value={feeFilter}
              onChange={setFeeFilter}
              options={feeFilterOptions}
            />

            {/* Room Type */}
            <RoundedFilterDropdown
              label="Room Type"
              value={roomTypeFilter}
              onChange={setRoomTypeFilter}
              options={roomTypeOptions}
            />

            {/* Min Stay */}
            <RoundedFilterDropdown
              label="Min Stay"
              value={minStayFilter}
              onChange={setMinStayFilter}
              options={minStayOptions}
            />

            {/* Min Rent */}
            <div className="flex flex-col gap-1">
              <span className="text-xs font-bold text-[#0E2F73] dark:text-slate-300 font-heading">
                Min Rent ($/month)
              </span>
              <input
                type="number"
                placeholder="e.g. 500"
                value={minPriceFilter}
                onChange={(e) => setMinPriceFilter(e.target.value)}
                className="w-full h-11 px-4 bg-[#F4F6FB] dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-full text-xs text-[#0E2F73] dark:text-slate-200 outline-none focus:ring-2 focus:ring-[#0E2F73] font-normal"
              />
            </div>

            {/* Max Rent */}
            <div className="flex flex-col gap-1">
              <span className="text-xs font-bold text-[#0E2F73] dark:text-slate-300 font-heading">
                Max Rent ($/month)
              </span>
              <input
                type="number"
                placeholder="e.g. 2500"
                value={maxPriceFilter}
                onChange={(e) => setMaxPriceFilter(e.target.value)}
                className="w-full h-11 px-4 bg-[#F4F6FB] dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-full text-xs text-[#0E2F73] dark:text-slate-200 outline-none focus:ring-2 focus:ring-[#0E2F73] font-normal"
              />
            </div>

            {/* Reset Button */}
            <div className="flex items-end sm:col-span-2">
              <button
                type="button"
                onClick={handleResetFilters}
                className="h-11 px-5 rounded-full border border-slate-200 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800 text-xs font-bold text-[#0E2F73] dark:text-slate-300 flex items-center justify-center gap-2 cursor-pointer transition shadow-2xs"
              >
                <RotateCcw className="size-3.5" />
                <span>Reset all filters</span>
              </button>
            </div>
          </div>
        )}

        {/* Counter bottom and Clear filters */}
        <div className="flex items-center justify-between pt-1">
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Showing {filteredListings.length} of {metrics.total} listings
          </p>
          {(searchQuery ||
            statusFilter !== "ALL_ACTIVE" ||
            roomStatusFilter !== "ALL" ||
            appFilter !== "ALL" ||
            propertyFilter !== "ALL" ||
            verifFilter !== "ALL" ||
            feeFilter !== "ALL" ||
            minPriceFilter ||
            maxPriceFilter ||
            roomTypeFilter !== "ALL" ||
            minStayFilter !== "ALL") && (
            <button
              type="button"
              onClick={handleResetFilters}
              className="text-xs font-bold text-[#D80B0B] hover:underline cursor-pointer"
            >
              Clear filters
            </button>
          )}
        </div>
      </div>

      {/* 5. Listings Display: Dual Mobile Card & Desktop Table View */}
      {filteredListings.length === 0 ? (
        /* Empty State Card matching Figma Frame */
        <div className="p-8 sm:p-12 text-center bg-white dark:bg-slate-900 rounded-3xl border border-dashed border-slate-300 dark:border-slate-800 flex flex-col items-center justify-center gap-4">
          <div className="size-16 rounded-2xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-slate-400 shadow-inner">
            <Bed className="size-8 text-[#0E2F73] dark:text-white" />
          </div>
          <div className="space-y-1">
            <h3 className="text-slate-900 dark:text-white font-extrabold text-base sm:text-lg font-heading">
              {metrics.total === 0 ? "No listings yet" : "No listings match these filters"}
            </h3>
            <p className="text-slate-500 dark:text-slate-400 text-xs max-w-md mx-auto">
              {metrics.total === 0
                ? "List a room to create your first listing."
                : "Try adjusting your search or filters to see available listings."}
            </p>
          </div>
          {metrics.total === 0 ? (
            <button
              onClick={() => setWizardOpen(true)}
              className="mt-2 px-6 py-2.5 bg-[#0E2F73] hover:bg-[#0a2358] text-white text-xs font-bold font-heading rounded-full shadow-sm transition flex items-center gap-2 cursor-pointer"
            >
              <Plus className="size-4" />
              <span>List a Room</span>
            </button>
          ) : (
            <button
              onClick={handleResetFilters}
              className="mt-2 px-5 py-2 bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 text-[#0E2F73] dark:text-white text-xs font-bold rounded-full transition cursor-pointer"
            >
              Clear filters
            </button>
          )}
        </div>
      ) : (
        <div className="bg-white dark:bg-slate-900 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden">
          {/* A. Mobile & Tablet Card Layout (block lg:hidden) */}
          <div className="block lg:hidden divide-y divide-slate-100 dark:divide-slate-800">
            {filteredListings.map((l) => (
              <div
                key={l.id}
                onClick={() => {
                  setSelectedListingForDrawer(l);
                  setDrawerTab("overview");
                }}
                className={`p-4 sm:p-5 space-y-3.5 transition cursor-pointer ${
                  l.listingStatus === "ACTION_REQUIRED"
                    ? "bg-[#FFF8F8] dark:bg-red-950/10 hover:bg-red-50/40"
                    : "hover:bg-slate-50/60 dark:hover:bg-slate-800/30"
                }`}
              >
                {/* 1. Header: Image + Title + Location */}
                <div className="flex items-start gap-3">
                  <img
                    src={resolveImageUrl(l.image)}
                    alt={l.title}
                    className="size-16 rounded-xl object-cover bg-slate-100 shrink-0 border border-slate-200/60 dark:border-slate-700"
                  />
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-sm font-extrabold text-[#0E2F73] dark:text-white font-heading truncate leading-tight hover:text-[#D80B0B]">
                        {l.title}
                      </p>
                      <span className="shrink-0 text-[10px] font-bold px-2 py-0.5 rounded-md bg-slate-100 dark:bg-slate-800 text-slate-500">
                        #ZR-L-{l.id}
                      </span>
                    </div>
                    <p className="text-xs text-slate-500 dark:text-slate-400 truncate">
                      {l.propertyName} · {l.roomNumber} · {l.roomType}
                    </p>
                    <p className="text-[11px] text-slate-400 truncate">{l.fullAddress}</p>
                  </div>
                </div>

                {/* 2. Badges Row: Listing Status + Room Status */}
                <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-slate-100 dark:border-slate-800/60">
                  {renderListingStatusBadge(l)}
                  {renderRoomStatusBadge(l)}
                </div>

                {/* 3. Key Metrics: Price & Applications */}
                <div className="grid grid-cols-2 gap-3 p-3 rounded-xl bg-slate-50/70 dark:bg-slate-800/40 text-xs">
                  <div>
                    <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block font-heading">
                      Price / Term
                    </span>
                    <p className="font-extrabold text-sm text-[#0E2F73] dark:text-white font-heading mt-0.5">
                      {formatCurrency(l.monthlyPrice, l.currency)}{" "}
                      <span className="text-[11px] font-normal text-slate-500">/ mo</span>
                    </p>
                    <p className="text-[11px] text-slate-500 dark:text-slate-400">
                      Min {l.minMonths} {l.minMonths === 1 ? "month" : "months"}
                    </p>
                  </div>

                  <div>
                    <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block font-heading">
                      Applications
                    </span>
                    <div className="flex items-center gap-1.5 mt-0.5">
                      <span
                        className={`size-2 rounded-full ${
                          l.appsStatusType === "OPEN"
                            ? "bg-[#12A15E]"
                            : l.appsStatusType === "PAUSED_AUTO"
                            ? "bg-[#E08A2B]"
                            : "bg-slate-400"
                        }`}
                      />
                      <span
                        className={`text-xs font-bold font-heading truncate ${
                          l.appsStatusType === "OPEN"
                            ? "text-[#05603A]"
                            : l.appsStatusType === "PAUSED_AUTO"
                            ? "text-[#93370D]"
                            : "text-slate-600 dark:text-slate-300"
                        }`}
                      >
                        {l.appsMainText}
                      </span>
                    </div>
                    <p className="text-[11px] text-slate-500 dark:text-slate-400 truncate mt-0.5">
                      {l.appsTotalCount > 0 ? (
                        <span className="font-semibold text-[#0E2F73]">
                          {l.appsTotalCount} {l.appsTotalCount === 1 ? "application" : "applications"}
                          {l.newAppsCount > 0 ? ` · ${l.newAppsCount} new` : ""}
                        </span>
                      ) : (
                        l.appsSubText
                      )}
                    </p>
                  </div>
                </div>

                {/* 4. Action Button & Three Dots Menu (Mobile) */}
                <div className="pt-1 flex items-center gap-2">
                  <div className="flex-1 min-w-0">{renderActionButton(l, true)}</div>
                  {renderThreeDotsButton(l, true)}
                </div>
              </div>
            ))}
          </div>

          {/* B. Desktop Table Layout (hidden lg:block) */}
          <div className="hidden lg:block w-full overflow-x-auto">
            {/* Table Header Row */}
            <div className="min-w-[1020px] px-6 py-3.5 bg-[#F7F9FD] dark:bg-slate-800/50 border-b border-slate-100 dark:border-slate-800 flex items-center text-[11px] font-bold text-slate-500 font-heading uppercase tracking-wider">
              <div className="w-72 shrink-0">Listing / room</div>
              <div className="w-44 shrink-0">Listing status</div>
              <div className="w-36 shrink-0">Room status</div>
              <div className="w-48 shrink-0">Applications</div>
              <div className="w-36 shrink-0">Price / term</div>
              <div className="flex-1 text-right">Next action</div>
            </div>

            {/* Listing Rows */}
            <div className="divide-y divide-slate-100 dark:divide-slate-800 min-w-[1020px] min-h-[220px]">
              {filteredListings.map((l, index) => (
                <div
                  key={l.id}
                  onClick={() => {
                    setSelectedListingForDrawer(l);
                    setDrawerTab("overview");
                  }}
                  className={`px-6 py-4 flex items-center transition cursor-pointer ${
                    l.listingStatus === "ACTION_REQUIRED"
                      ? "bg-[#FFF8F8] dark:bg-red-950/10 hover:bg-red-50/40"
                      : "hover:bg-slate-50/60 dark:hover:bg-slate-800/30"
                  }`}
                >
                  {/* 1. Listing / Room */}
                  <div className="w-72 shrink-0 flex items-center gap-3.5">
                    <img
                      src={resolveImageUrl(l.image)}
                      alt={l.title}
                      className="w-16 h-13 rounded-xl object-cover bg-slate-100 shrink-0 border border-slate-200/60 dark:border-slate-700"
                    />
                    <div className="space-y-0.5 min-w-0">
                      <p className="text-sm font-extrabold text-[#0E2F73] dark:text-white font-heading truncate leading-tight hover:text-[#D80B0B]">
                        {l.title}
                      </p>
                      <p className="text-xs text-slate-500 dark:text-slate-400 truncate">
                        {l.propertyName} · {l.roomNumber} · {l.roomType}
                      </p>
                      <p className="text-[11px] font-medium text-slate-400">#ZR-L-{l.id}</p>
                    </div>
                  </div>

                  {/* 2. Listing Status */}
                  <div className="w-44 shrink-0">{renderListingStatusBadge(l)}</div>

                  {/* 3. Room Status */}
                  <div className="w-36 shrink-0">{renderRoomStatusBadge(l)}</div>

                  {/* 4. Applications */}
                  <div className="w-48 shrink-0 space-y-0.5">
                    <div className="flex items-center gap-1.5">
                      <span
                        className={`size-2 rounded-full ${
                          l.appsStatusType === "OPEN"
                            ? "bg-[#12A15E]"
                            : l.appsStatusType === "PAUSED_AUTO"
                            ? "bg-[#E08A2B]"
                            : "bg-slate-400"
                        }`}
                      />
                      <span
                        className={`text-sm font-bold font-heading ${
                          l.appsStatusType === "OPEN"
                            ? "text-[#05603A]"
                            : l.appsStatusType === "PAUSED_AUTO"
                            ? "text-[#93370D]"
                            : "text-slate-600 dark:text-slate-300"
                        }`}
                      >
                        {l.appsMainText}
                      </span>
                    </div>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      {l.appsTotalCount > 0 ? (
                        <Link
                          href="/account/host/applications"
                          onClick={(e) => e.stopPropagation()}
                          className="font-semibold text-[#0E2F73] hover:underline"
                        >
                          {l.appsTotalCount} {l.appsTotalCount === 1 ? "application" : "applications"}
                          {l.newAppsCount > 0 && <b className="text-[#D80B0B]"> · {l.newAppsCount} new</b>}
                        </Link>
                      ) : (
                        l.appsSubText
                      )}
                    </p>
                  </div>

                  {/* 5. Price / Term */}
                  <div className="w-36 shrink-0 space-y-0.5">
                    <p className="text-sm font-extrabold text-[#0E2F73] dark:text-white font-heading">
                      {formatCurrency(l.monthlyPrice, l.currency)}{" "}
                      <span className="text-xs font-normal text-slate-500">/ month</span>
                    </p>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      Min {l.minMonths} {l.minMonths === 1 ? "month" : "months"}
                    </p>
                  </div>

                  {/* 6. Action Button & Three dots */}
                  <div className="flex-1 flex items-center justify-end gap-2 relative">
                    {renderActionButton(l, false)}
                    {renderThreeDotsButton(l, false)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* 6. Footer Disclaimer from Figma Frame */}
      <div className="pt-2">
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Archived and expired listings stay available under Listing Status. The Listing Fee is a Zoiko Rooms platform fee — it is never mixed with rent or deposits.
        </p>
      </div>

      {/* 7. Slide-out Inspection Drawer matching zoiko-rooms-my-listings.html exactly (Portaled to document.body to avoid parent CSS transform clipping and position-fixed displacement) */}
      {mounted && selectedListingForDrawer && createPortal(
        <div className="fixed inset-0 z-50 flex justify-end">
          {/* Backdrop Scrim */}
          <div
            className="absolute inset-0 bg-slate-900/40 backdrop-blur-xs transition-opacity"
            onClick={() => setSelectedListingForDrawer(null)}
          />

          {/* Drawer Panel */}
          <aside
            className="relative w-full max-w-[560px] h-full bg-white dark:bg-slate-900 shadow-2xl flex flex-col z-10 overflow-y-auto animate-in slide-in-from-right duration-200"
            role="dialog"
            aria-modal="true"
          >
            {/* Drawer Header */}
            <div className="p-6 border-b border-slate-100 dark:border-slate-800 space-y-3">
              <div className="flex items-start justify-between gap-4">
                <div className="space-y-1">
                  <span className="text-[11px] font-bold text-[#D80B0B] uppercase tracking-wider font-heading">
                    Listing #{selectedListingForDrawer.id}
                  </span>
                  <h2 className="text-xl sm:text-2xl font-extrabold text-[#0E2F73] dark:text-white font-heading">
                    {selectedListingForDrawer.title}
                  </h2>
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    {selectedListingForDrawer.propertyName} · {selectedListingForDrawer.roomNumber} —{" "}
                    {selectedListingForDrawer.roomType}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setSelectedListingForDrawer(null)}
                  className="size-9 rounded-full border border-slate-200 dark:border-slate-700 flex items-center justify-center text-slate-500 hover:text-slate-900 dark:hover:text-white hover:bg-slate-50 transition cursor-pointer"
                >
                  <X className="size-4" />
                </button>
              </div>

              {/* Badges Strip */}
              <div className="flex flex-wrap items-center gap-2 pt-1">
                {renderListingStatusBadge(selectedListingForDrawer)}
                <span className="text-xs text-slate-400">Room:</span>
                {renderRoomStatusBadge(selectedListingForDrawer)}
              </div>

              {/* Tabs Bar */}
              <div className="flex items-center gap-1 border-b border-slate-100 dark:border-slate-800 pt-2 overflow-x-auto no-scrollbar">
                {[
                  { id: "overview", label: "Overview" },
                  { id: "content", label: "Content" },
                  { id: "availability", label: "Availability" },
                  { id: "verification", label: "Verification" },
                  { id: "applications", label: "Applications" },
                  { id: "activity", label: "Activity" },
                ].map((tab) => (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setDrawerTab(tab.id as typeof drawerTab)}
                    className={`h-10 px-3.5 text-xs font-bold transition whitespace-nowrap cursor-pointer border-b-2 ${
                      drawerTab === tab.id
                        ? "border-[#0E2F73] text-[#0E2F73] dark:border-sky-400 dark:text-white font-extrabold"
                        : "border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-300"
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Drawer Body Content */}
            <div className="flex-1 p-6 space-y-5">
              {/* TAB 1: OVERVIEW */}
              {drawerTab === "overview" && (
                <div className="space-y-5">
                  {selectedListingForDrawer.occupancyId &&
                    selectedListingForDrawer.occupancyStatus === "PENDING_MOVE_IN" && (
                      <HostMoveInSteps
                        occupancyId={selectedListingForDrawer.occupancyId}
                        showToast={showToast}
                        onMovedIn={() => {
                          setSelectedListingForDrawer(null);
                          load();
                        }}
                      />
                    )}
                  {/* KV Grid */}
                  <div className="grid grid-cols-2 gap-2.5">
                    <div className="p-3.5 rounded-2xl border border-slate-200 dark:border-slate-800 space-y-1">
                      <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block font-heading">
                        Listing status
                      </span>
                      <p className="text-xs font-extrabold text-[#0E2F73] dark:text-white">
                        {selectedListingForDrawer.listingStatusLabel} ·{" "}
                        {selectedListingForDrawer.listingStatusSubtext}
                      </p>
                    </div>

                    <div className="p-3.5 rounded-2xl border border-slate-200 dark:border-slate-800 space-y-1">
                      <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block font-heading">
                        Room
                      </span>
                      <p className="text-xs font-extrabold text-[#0E2F73] dark:text-white">
                        {selectedListingForDrawer.roomStatusSubtext}
                      </p>
                    </div>

                    <div className="p-3.5 rounded-2xl border border-slate-200 dark:border-slate-800 space-y-1">
                      <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block font-heading">
                        Applications
                      </span>
                      <p className="text-xs font-extrabold text-[#0E2F73] dark:text-white">
                        {selectedListingForDrawer.appsMainText} ·{" "}
                        {selectedListingForDrawer.appsTotalCount} total
                        {selectedListingForDrawer.newAppsCount > 0 &&
                          ` · ${selectedListingForDrawer.newAppsCount} new`}
                      </p>
                    </div>

                    <div className="p-3.5 rounded-2xl border border-slate-200 dark:border-slate-800 space-y-1">
                      <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block font-heading">
                        Price
                      </span>
                      <p className="text-xs font-extrabold text-[#0E2F73] dark:text-white">
                        {formatCurrency(selectedListingForDrawer.monthlyPrice, selectedListingForDrawer.currency)} / month · Min{" "}
                        {selectedListingForDrawer.minMonths} months
                      </p>
                    </div>
                  </div>

                  {/* Block / Alert Notice */}
                  {selectedListingForDrawer.blockReason && (
                    <div className="p-4 rounded-2xl bg-[#FDECEC] border border-[#F4A9A3] text-[#B42318] text-xs font-semibold flex items-start gap-3">
                      <AlertTriangle className="size-4 shrink-0 mt-0.5" />
                      <span>{selectedListingForDrawer.blockReason}</span>
                    </div>
                  )}

                  {selectedListingForDrawer.listingStatusTone === "fee" && (
                    <div className="p-4 rounded-2xl bg-[#EEF3FF] border border-[#AFC2EE] text-[#1D3F8F] text-xs font-semibold flex items-start gap-3">
                      <CreditCardIcon className="size-4 shrink-0 mt-0.5" />
                      <span>
                        Review is approved. The Zoiko Rooms Listing Fee is now due. Paying it is one
                        gate only — it does not override any other gate.
                      </span>
                    </div>
                  )}

                  {/* Publication Gates List */}
                  <div className="space-y-2.5">
                    <h3 className="text-xs font-extrabold uppercase tracking-wider text-slate-600 dark:text-slate-400 font-heading">
                      Publication gates
                    </h3>
                    <div className="rounded-2xl border border-slate-200 dark:border-slate-800 divide-y divide-slate-100 dark:divide-slate-800 overflow-hidden">
                      {getPublicationGates(selectedListingForDrawer).map((g, idx) => (
                        <div
                          key={idx}
                          className={`p-3.5 flex items-center justify-between gap-3 text-xs ${
                            g.state === "fail"
                              ? "bg-[#FFF7F7] dark:bg-red-950/20"
                              : "bg-white dark:bg-slate-900"
                          }`}
                        >
                          <div className="flex items-center gap-2.5">
                            <span
                              className={`size-6 rounded-lg flex items-center justify-center shrink-0 ${
                                g.state === "pass"
                                  ? "bg-[#E7F8EF] text-[#05603A]"
                                  : g.state === "fail"
                                  ? "bg-[#FDECEC] text-[#B42318]"
                                  : g.state === "wait"
                                  ? "bg-[#EEF3FF] text-[#1D3F8F]"
                                  : "bg-slate-100 text-slate-500"
                              }`}
                            >
                              {g.state === "pass" && <Check className="size-3.5" />}
                              {g.state === "fail" && <X className="size-3.5" />}
                              {g.state === "wait" && <Clock className="size-3.5" />}
                              {g.state === "na" && <Minus className="size-3.5" />}
                            </span>
                            <span className="font-bold text-[#0E2F73] dark:text-white">
                              {g.name}
                            </span>
                          </div>
                          <span
                            className={`font-extrabold text-[11px] ${
                              g.state === "pass"
                                ? "text-[#05603A]"
                                : g.state === "fail"
                                ? "text-[#B42318]"
                                : g.state === "wait"
                                ? "text-[#1D3F8F]"
                                : "text-slate-400"
                            }`}
                          >
                            {g.desc}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 2: CONTENT */}
              {drawerTab === "content" && (
                <div className="space-y-4">
                  <div className="space-y-2">
                    <h3 className="text-xs font-extrabold uppercase tracking-wider text-slate-600 dark:text-slate-400 font-heading">
                      Listing content
                    </h3>
                    <p className="text-xs leading-relaxed text-slate-600 dark:text-slate-300">
                      {selectedListingForDrawer.rawListing.description ||
                        `${selectedListingForDrawer.title} — a private room in ${selectedListingForDrawer.propertyName}. Furnished, shared kitchen, high-speed internet.`}
                    </p>
                  </div>

                  {/* Photos Grid */}
                  <div className="space-y-2">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400 block font-heading">
                      Photos ({selectedListingForDrawer.rawListing.images?.length || 0})
                    </span>
                    <div className="grid grid-cols-3 gap-2">
                      {(selectedListingForDrawer.rawListing.images?.length > 0
                        ? selectedListingForDrawer.rawListing.images
                        : DEFAULT_LISTING_IMAGES.slice(0, 3)
                      ).map((imgUrl: string, i: number) => (
                        <img
                          key={i}
                          src={resolveImageUrl(imgUrl)}
                          alt="Room photo"
                          className="w-full h-24 rounded-xl object-cover border border-slate-200 dark:border-slate-700 bg-slate-100"
                        />
                      ))}
                    </div>
                  </div>

                  {/* Amenities */}
                  {selectedListingForDrawer.rawListing.amenities?.length > 0 && (
                    <div className="space-y-2">
                      <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400 block font-heading">
                        Amenities
                      </span>
                      <div className="flex flex-wrap gap-1.5">
                        {selectedListingForDrawer.rawListing.amenities.map(
                          (a: string, i: number) => (
                            <span
                              key={i}
                              className="px-2.5 py-1 rounded-full bg-slate-100 dark:bg-slate-800 text-[11px] font-semibold text-[#0E2F73] dark:text-slate-200"
                            >
                              {a}
                            </span>
                          )
                        )}
                      </div>
                    </div>
                  )}

                  <p className="text-[11px] text-slate-400 leading-normal pt-2">
                    Complex edits open the full Listing Editor. Material edits to a Live listing create a
                    new revision for review.
                  </p>
                </div>
              )}

              {/* TAB 3: AVAILABILITY */}
              {drawerTab === "availability" && (
                <div className="space-y-4">
                  <div className="grid grid-cols-2 gap-3">
                    <div className="p-3.5 rounded-2xl border border-slate-200 dark:border-slate-800 space-y-1">
                      <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block font-heading">
                        Room status
                      </span>
                      <p className="text-xs font-extrabold text-[#0E2F73] dark:text-white">
                        {selectedListingForDrawer.roomStatusSubtext}
                      </p>
                    </div>

                    <div className="p-3.5 rounded-2xl border border-slate-200 dark:border-slate-800 space-y-1">
                      <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block font-heading">
                        Availability
                      </span>
                      <p className="text-xs font-extrabold text-[#0E2F73] dark:text-white">
                        {selectedListingForDrawer.roomStatusSubtext}
                      </p>
                    </div>

                    <div className="p-3.5 rounded-2xl border border-slate-200 dark:border-slate-800 space-y-1">
                      <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block font-heading">
                        Term
                      </span>
                      <p className="text-xs font-extrabold text-[#0E2F73] dark:text-white">
                        Min {selectedListingForDrawer.minMonths} months
                      </p>
                    </div>

                    <div className="p-3.5 rounded-2xl border border-slate-200 dark:border-slate-800 space-y-1">
                      <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block font-heading">
                        Application acceptance
                      </span>
                      <p className="text-xs font-extrabold text-[#0E2F73] dark:text-white">
                        {selectedListingForDrawer.appsMainText}
                      </p>
                    </div>
                  </div>

                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    Room status comes from Properties &amp; Rooms. A Live listing never means the room is
                    available now.
                  </p>
                </div>
              )}

              {/* TAB 4: VERIFICATION */}
              {drawerTab === "verification" && (
                <div className="space-y-4">
                  <div className="rounded-2xl border border-slate-200 dark:border-slate-800 divide-y divide-slate-100 dark:divide-slate-800 overflow-hidden">
                    {[
                      { name: "Identity", state: Boolean(user?.id) ? "pass" : "fail" },
                      {
                        name: "Property",
                        state:
                          selectedListingForDrawer.propVerif?.state === "VERIFIED"
                            ? "pass"
                            : "fail",
                      },
                      {
                        name: "Listing authority",
                        state:
                          selectedListingForDrawer.authVerif?.state === "VERIFIED"
                            ? "pass"
                            : "fail",
                      },
                    ].map((v, i) => (
                      <div
                        key={i}
                        className="p-3.5 flex items-center justify-between text-xs bg-white dark:bg-slate-900"
                      >
                        <div className="flex items-center gap-2.5">
                          <span
                            className={`size-6 rounded-lg flex items-center justify-center ${
                              v.state === "pass"
                                ? "bg-[#E7F8EF] text-[#05603A]"
                                : "bg-[#FDECEC] text-[#B42318]"
                            }`}
                          >
                            {v.state === "pass" ? (
                              <Check className="size-3.5" />
                            ) : (
                              <X className="size-3.5" />
                            )}
                          </span>
                          <span className="font-bold text-[#0E2F73] dark:text-white">{v.name}</span>
                        </div>
                        <span
                          className={`font-extrabold text-[11px] ${
                            v.state === "pass" ? "text-[#05603A]" : "text-[#B42318]"
                          }`}
                        >
                          {v.state === "pass" ? "Verified" : "Renewal required"}
                        </span>
                      </div>
                    ))}
                  </div>

                  <p className="text-xs text-slate-400 flex items-center gap-2">
                    <ShieldCheck className="size-4 text-[#0E2F73]" />
                    <span>
                      Verification evidence stays in the Verification workspace and is never shown here.
                    </span>
                  </p>
                </div>
              )}

              {/* TAB 5: APPLICATIONS */}
              {drawerTab === "applications" && (
                <div className="space-y-4">
                  {selectedListingForDrawer.appsTotalCount > 0 ? (
                    <div className="p-4 rounded-2xl border border-slate-200 dark:border-slate-800 bg-[#F7F9FD] dark:bg-slate-800/40 space-y-3">
                      <div className="flex items-center justify-between">
                        <strong className="text-sm font-extrabold text-[#0E2F73] dark:text-white">
                          {selectedListingForDrawer.appsTotalCount} applications
                        </strong>
                        {selectedListingForDrawer.newAppsCount > 0 && (
                          <span className="text-xs font-bold text-[#D80B0B]">
                            {selectedListingForDrawer.newAppsCount} new
                          </span>
                        )}
                      </div>
                      <Link
                        href={`/account/host/applications`}
                        className="h-10 px-4 rounded-full bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 text-xs font-bold text-[#0E2F73] dark:text-white flex items-center justify-center gap-1.5 shadow-2xs hover:bg-slate-50 transition"
                      >
                        <span>Open in Applications</span>
                        <ArrowRight className="size-3.5" />
                      </Link>
                    </div>
                  ) : (
                    <p className="text-xs text-slate-500">
                      No applications yet
                      {selectedListingForDrawer.listingStatus === "LIVE"
                        ? " — that’s normal for a new listing."
                        : ". Applications open once the listing is Live."}
                    </p>
                  )}
                </div>
              )}

              {/* TAB 6: ACTIVITY */}
              {drawerTab === "activity" && (
                <div className="space-y-3">
                  <ol className="relative border-l border-slate-200 dark:border-slate-700 ml-3 space-y-4 text-xs">
                    <li className="mb-4 ml-4">
                      <div className="absolute -left-1.5 mt-1.5 size-3 rounded-full border border-white bg-[#0E2F73]" />
                      <time className="text-[10px] font-normal leading-none text-slate-400">
                        {listingCreatedAt(selectedListingForDrawer.rawListing)
                          ? formatDate(listingCreatedAt(selectedListingForDrawer.rawListing)!)
                          : "Recently"}
                      </time>
                      <h4 className="font-extrabold text-[#0E2F73] dark:text-white mt-0.5">
                        Listing created
                      </h4>
                      <p className="text-slate-500">You created this listing draft.</p>
                    </li>

                    {selectedListingForDrawer.rawListing.state !== "DRAFT" && (
                      <li className="mb-4 ml-4">
                        <div className="absolute -left-1.5 mt-1.5 size-3 rounded-full border border-white bg-[#0E2F73]" />
                        <time className="text-[10px] font-normal leading-none text-slate-400">
                          {listingCreatedAt(selectedListingForDrawer.rawListing)
                            ? formatDate(listingCreatedAt(selectedListingForDrawer.rawListing)!)
                            : "Recently"}
                        </time>
                        <h4 className="font-extrabold text-[#0E2F73] dark:text-white mt-0.5">
                          Submitted for review
                        </h4>
                        <p className="text-slate-500">Listing submitted to Zoiko Rooms.</p>
                      </li>
                    )}

                    {selectedListingForDrawer.listingStatus === "LIVE" && (
                      <li className="ml-4">
                        <div className="absolute -left-1.5 mt-1.5 size-3 rounded-full border border-white bg-[#05603A]" />
                        <time className="text-[10px] font-normal leading-none text-slate-400">
                          {listingCreatedAt(selectedListingForDrawer.rawListing)
                            ? formatDate(listingCreatedAt(selectedListingForDrawer.rawListing)!)
                            : "Recently"}
                        </time>
                        <h4 className="font-extrabold text-[#05603A] mt-0.5">Published</h4>
                        <p className="text-slate-500">Listing is live and accepting applications.</p>
                      </li>
                    )}
                  </ol>
                </div>
              )}
            </div>

            {/* Sticky Drawer Footer Action Buttons */}
            <div className="sticky bottom-0 p-5 bg-white dark:bg-slate-900 border-t border-slate-100 dark:border-slate-800 flex items-center gap-2.5 flex-wrap">
              {selectedListingForDrawer.actionLabel !== "View Listing" && (
                <button
                  type="button"
                  onClick={() => handleActionClick(selectedListingForDrawer)}
                  className="h-10 px-5 rounded-full bg-[#0E2F73] hover:bg-[#0a2358] text-white text-xs font-bold font-heading shadow-md transition cursor-pointer"
                >
                  {selectedListingForDrawer.actionLabel}
                </button>
              )}

              {selectedListingForDrawer.rawListing.state === "APPROVED" &&
                selectedListingForDrawer.actionLabel !== "Pay Listing Fee" && (
                  <button
                    type="button"
                    onClick={() => setPayingFeeListingId(selectedListingForDrawer.id)}
                    className="h-10 px-5 rounded-full border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 hover:bg-slate-50 text-[#0E2F73] dark:text-white text-xs font-bold font-heading transition cursor-pointer flex items-center gap-1.5"
                  >
                    <Receipt className="size-3.5" /> Pay Listing Fee
                  </button>
                )}

              {/* View Public Listing commented out as requested
              {selectedListingForDrawer.listingStatus === "LIVE" && (
                <Link
                  href={`/listings/${selectedListingForDrawer.id}`}
                  target="_blank"
                  className="h-10 px-4 rounded-full border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-xs font-bold text-[#0E2F73] dark:text-white flex items-center gap-1.5 shadow-2xs transition"
                >
                  <ExternalLink className="size-3.5" />
                  <span>View Public Listing</span>
                </Link>
              )}
              */}

              <button
                type="button"
                onClick={() => {
                  setSelectedListingForDrawer(null);
                  setForm(toFormState(selectedListingForDrawer.rawListing));
                }}
                className="h-10 px-4 rounded-full border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-xs font-bold text-[#0E2F73] dark:text-white flex items-center gap-1.5 shadow-2xs transition cursor-pointer"
              >
                <Pencil className="size-3.5" />
                <span>Edit Listing</span>
              </button>
            </div>
          </aside>
        </div>,
        document.body
      )}

      {/* 8. Modals: Edit Listing */}
      <Modal
        open={Boolean(form)}
        onClose={() => setForm(null)}
        title={form?.id === null ? "Create listing" : "Edit listing"}
        size="xl"
      >
        <form onSubmit={handleSubmit} className="space-y-4">
          <Field label="Listing headline">
            <input
              value={form?.name ?? ""}
              onChange={(e) => setForm((f) => (f ? { ...f, name: e.target.value } : f))}
              placeholder="e.g. Bright ensuite in quiet townhouse"
              className={inputClass}
            />
          </Field>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="City">
              <input
                value={form?.city ?? ""}
                onChange={(e) => setForm((f) => (f ? { ...f, city: e.target.value } : f))}
                className={inputClass}
              />
            </Field>
            <Field label="Neighborhood / area">
              <input
                value={form?.location ?? ""}
                onChange={(e) => setForm((f) => (f ? { ...f, location: e.target.value } : f))}
                className={inputClass}
              />
            </Field>
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Field label="Nightly rate">
              <input
                type="number"
                value={form?.pricePerNight ?? ""}
                onChange={(e) => setForm((f) => (f ? { ...f, pricePerNight: e.target.value } : f))}
                className={inputClass}
              />
            </Field>
            <Field label="Currency">
              <select
                value={form?.currency ?? "USD"}
                onChange={(e) => setForm((f) => (f ? { ...f, currency: e.target.value } : f))}
                className={selectClass}
              >
                {SUPPORTED_CURRENCIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Minimum stay (nights)">
              <input
                type="number"
                value={form?.minStayNights ?? "30"}
                onChange={(e) => setForm((f) => (f ? { ...f, minStayNights: e.target.value } : f))}
                className={inputClass}
              />
            </Field>
            <Field label="Available from (optional)">
              <input
                type="date"
                min={new Date().toISOString().slice(0, 10)}
                value={form?.availableFrom ?? ""}
                onChange={(e) => setForm((f) => (f ? { ...f, availableFrom: e.target.value } : f))}
                className={inputClass}
              />
            </Field>
          </div>

          {Number(form?.pricePerNight) > 0 && (
            <div className="rounded-xl border border-primary-200 bg-primary-50/60 p-3 text-xs text-primary-900 dark:border-primary-900/40 dark:bg-primary-950/20 dark:text-white">
              <span className="font-semibold">Calculated Total: </span>
              {formatCurrency(Number(form?.pricePerNight) * (Number(form?.minStayNights) || 30), form?.currency || "USD")} ({form?.minStayNights || 30} nights)
              <span className="mx-2 text-slate-400">·</span>
              <span className="font-semibold">Monthly: </span>
              {formatCurrency(Number(form?.pricePerNight) * 30, form?.currency || "USD")}/mo
            </div>
          )}

          <Field label="Description">
            <textarea
              rows={4}
              value={form?.description ?? ""}
              onChange={(e) => setForm((f) => (f ? { ...f, description: e.target.value } : f))}
              className={inputClass}
            />
          </Field>

          <Field label="Photos">
            <ImageGalleryUploader
              images={form?.images ?? []}
              onChange={(images) => setForm((f) => (f ? { ...f, images } : f))}
              maxImages={MAX_LISTING_IMAGES}
            />
          </Field>

          <Field label="Amenities">
            <AmenitiesPicker
              value={form?.amenities ?? []}
              onChange={(amenities) => setForm((f) => (f ? { ...f, amenities } : f))}
            />
          </Field>

          {error && (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-xs font-medium text-red-700 ring-1 ring-red-200">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={() => setForm(null)}>
              Cancel
            </Button>
            <Button type="submit" loading={submitting}>
              {form?.id === null ? "Create draft" : "Save changes"}
            </Button>
          </div>
        </form>
      </Modal>

      {/* 9. Wizard: List a Room */}
      <ListARoomWizard
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        contact={{ name: user?.fullName ?? "", phone: user?.phone ?? "", email: user?.email ?? "" }}
        onCreated={async (outcome) => {
          setWizardOpen(false);
          await load();
          const { text, tone } = submitOutcomeMessage(outcome);
          showToast(text, tone);
        }}
      />

      {/* 10. Modal: Listing Fee Checkout */}
      <Modal open={Boolean(payingFeeListingId)} onClose={closeListingFee} title="Listing fee">
        {payingFeeListingId && (
          <ListingFeeCheckout
            listingId={payingFeeListingId}
            returningCheckoutSessionId={returningCheckoutSessionId ?? undefined}
            stripeReturn={stripeReturn}
            onDone={closeListingFee}
          />
        )}
      </Modal>

      {/* 11. Modal: Property Verification */}
      <Modal
        open={propertyVerificationRoomId !== null}
        onClose={() => setPropertyVerificationRoomId(null)}
        title="Property verification"
        size="xl"
      >
        {propertyVerificationRoomId !== null && (
          <RoomPropertyVerification
            roomId={propertyVerificationRoomId}
            onClose={() => {
              setPropertyVerificationRoomId(null);
              void load();
            }}
          />
        )}
      </Modal>

      {/* 12. Modal: Authority Verification */}
      <Modal
        open={authorityRecordRoomId !== null}
        onClose={() => setAuthorityRecordRoomId(null)}
        title="Authority to list"
        size="xl"
      >
        {authorityRecordRoomId !== null && (
          <RoomAuthorityVerification
            roomId={authorityRecordRoomId}
            onClose={() => {
              setAuthorityRecordRoomId(null);
              void load();
            }}
          />
        )}
      </Modal>

      <Toast toast={toast} />
    </div>
  );
}

function CreditCardIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect width="20" height="14" x="2" y="5" rx="2" />
      <line x1="2" x2="22" y1="10" y2="10" />
    </svg>
  );
}

function RoomAuthorityVerification({ roomId, onClose }: { roomId: number; onClose: () => void }) {
  const [target, setTarget] = useState<{ propertyId: number; propertyLabel: string } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    getPropertyVerificationForRoom(roomId)
      .then((r) => setTarget({ propertyId: r.propertyId, propertyLabel: r.propertyLabel }))
      .catch((err) => setError(errorMessage(err, "Could not load this property.")));
  }, [roomId]);
  if (error) return <p className="text-sm text-red-600">{error}</p>;
  if (!target) return <p className="text-sm text-slate-400">Loading authority verification...</p>;
  return (
    <AuthorityVerificationWizard
      propertyId={target.propertyId}
      propertyLabel={target.propertyLabel}
      onClose={onClose}
    />
  );
}

function RoomPropertyVerification({ roomId, onClose }: { roomId: number; onClose: () => void }) {
  const [target, setTarget] = useState<{ propertyId: number; propertyLabel: string } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    getPropertyVerificationForRoom(roomId)
      .then((r) => setTarget({ propertyId: r.propertyId, propertyLabel: r.propertyLabel }))
      .catch((err) => setError(errorMessage(err, "Could not load this property's verification.")));
  }, [roomId]);
  if (error) return <p className="text-sm text-red-600">{error}</p>;
  if (!target) return <p className="text-sm text-slate-400">Loading property verification...</p>;
  return (
    <PropertyVerificationWizard
      propertyId={target.propertyId}
      propertyLabel={target.propertyLabel}
      onClose={onClose}
    />
  );
}
