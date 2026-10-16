"use client";

import { useCallback, useEffect, useMemo, useState, useRef, type FormEvent } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Bed,
  BedDouble,
  Building2,
  Calendar,
  Check,
  ChevronDown,
  ChevronUp,
  Clock,
  DoorOpen,
  FileText,
  Home,
  Info,
  Key,
  LayoutGrid,
  MoreVertical,
  Plus,
  RotateCcw,
  Search,
  ShieldCheck,
  X,
  ArrowRight,
  ExternalLink,
} from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { Application, HostedListing, Occupancy, OpenJurisdiction, Property, Room } from "@/lib/types";
import { Switch } from "@/components/ui/Switch";
import {
  createHostedProperty,
  createHostedRoom,
  errorMessage,
  listHostedApplications,
  listHostedListings,
  listHostedProperties,
  listHostedRoomOccupancies,
  listHostedRooms,
  listOpenJurisdictions,
  updateHostedProperty,
  updateHostedRoom,
} from "@/lib/user-api";
import { ListARoomWizard, submitOutcomeMessage } from "@/components/user/ListARoomWizard";
import { PropertyVerificationWizard } from "@/components/user/PropertyVerificationWizard";
import { AuthorityVerificationWizard } from "@/components/user/AuthorityVerificationWizard";
import {
  AuthorityState,
  listPropertyAuthority,
} from "@/lib/authority-verification";
import {
  PropertyVerificationState,
  getPropertyVerificationForProperty,
} from "@/lib/property-verification";
import { RegionSelect } from "@/components/user/RegionSelect";
import { RoundedFilterDropdown } from "@/components/ui/RoundedFilterDropdown";
import { RentalTransactionRecord } from "@/components/user/RentalTransactionRecord";
import { HostMoveInSteps } from "@/components/user/HostMoveInSteps";
import { useUserSession } from "@/components/user/UserSessionContext";
import { Field, Toast, inputClass, useToast } from "@/components/user/ui";
import { formatDate, resolveImageUrl } from "@/lib/utils";

type PropertyForm = {
  id: number | null;
  address: string;
  city: string;
  landmark: string;
  jurisdictionCode: string;
  savedJurisdictionCode?: string;
  regionLocked: boolean;
};

type RoomForm = {
  propertyId: number;
  id: number | null;
  size: string;
  hasEnsuite: boolean;
};

// Default room imagery fallback when no custom photos have been uploaded yet
const DEFAULT_ROOM_IMAGES = [
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

export function HostingPropertiesManager() {
  const { user } = useUserSession();
  const { toast, showToast } = useToast();

  // 100% Dynamic Backend Data State
  const [properties, setProperties] = useState<Property[]>([]);
  const [regions, setRegions] = useState<OpenJurisdiction[]>([]);
  const [roomsByProperty, setRoomsByProperty] = useState<Record<number, Room[]>>({});
  const [occupanciesByRoom, setOccupanciesByRoom] = useState<Record<number, Occupancy[]>>({});
  const [listings, setListings] = useState<HostedListing[]>([]);
  const [applications, setApplications] = useState<Application[]>([]);
  const [verificationByProperty, setVerificationByProperty] = useState<
    Record<number, { state: PropertyVerificationState; message: string }>
  >({});
  const [authorityByProperty, setAuthorityByProperty] = useState<
    Record<number, { state: AuthorityState; message: string }>
  >({});
  const [loading, setLoading] = useState(true);

  // Accordion collapsed state per property
  const [collapsedProperties, setCollapsedProperties] = useState<Record<number, boolean>>({});

  // Filter & Search states
  const [searchQuery, setSearchQuery] = useState("");
  const [cardFilter, setCardFilter] = useState<"all" | "available" | "offer" | "occupied" | "action">("all");
  const [roomStatusFilter, setRoomStatusFilter] = useState("ALL");
  const [propertyFilter, setPropertyFilter] = useState("ALL");
  const [listingStatusFilter, setListingStatusFilter] = useState("ALL");
  const [verificationFilter, setVerificationFilter] = useState("ALL");
  const [sortOption, setSortOption] = useState("NEXT_ACTION");

  // Interactive Modals state
  const [propertyForm, setPropertyForm] = useState<PropertyForm | null>(null);
  const [roomForm, setRoomForm] = useState<RoomForm | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [recordOccupancyId, setRecordOccupancyId] = useState<number | null>(null);
  const [verifyProperty, setVerifyProperty] = useState<{ id: number; label: string } | null>(null);
  const [authorityProperty, setAuthorityProperty] = useState<{ id: number; label: string } | null>(null);

  // Room Inspection Drawer (Sidebar) state
  const [selectedRoomForDrawer, setSelectedRoomForDrawer] = useState<{
    room: (typeof filteredProperties)[number]["rooms"][number];
    property: (typeof filteredProperties)[number];
  } | null>(null);

  // Three-dots menus open state
  const [openPropertyMenuId, setOpenPropertyMenuId] = useState<number | null>(null);
  const [openRoomMenuId, setOpenRoomMenuId] = useState<number | null>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      const target = event.target as HTMLElement | null;
      if (!target?.closest("[data-property-menu]")) {
        setOpenPropertyMenuId(null);
      }
      if (!target?.closest("[data-room-menu]")) {
        setOpenRoomMenuId(null);
      }
    }
    if (openPropertyMenuId !== null || openRoomMenuId !== null) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [openPropertyMenuId, openRoomMenuId]);

  // Load live data from FastAPI + PostgreSQL backend
  const load = useCallback(async () => {
    try {
      const [owned, openRegions, hostedListings, hostedApps] = await Promise.all([
        listHostedProperties(),
        listOpenJurisdictions().catch(() => [] as OpenJurisdiction[]),
        listHostedListings().catch(() => [] as HostedListing[]),
        listHostedApplications().catch(() => [] as Application[]),
      ]);

      setProperties(owned);
      setRegions(openRegions);
      setListings(hostedListings);
      setApplications(hostedApps);

      const roomLists = await Promise.all(
        owned.map((property) => listHostedRooms(property.id).catch(() => [] as Room[]))
      );
      setRoomsByProperty(Object.fromEntries(owned.map((property, i) => [property.id, roomLists[i]])));

      const statuses = await Promise.all(
        owned.map((property) => getPropertyVerificationForProperty(property.id).catch(() => null))
      );
      setVerificationByProperty(
        Object.fromEntries(
          owned.map((property, i) => [
            property.id,
            {
              state: statuses[i]?.state ?? "NOT_STARTED",
              message: statuses[i]?.verification?.message ?? "",
            },
          ])
        )
      );

      const authority = await Promise.all(
        owned.map((property) => listPropertyAuthority(property.id).catch(() => []))
      );
      setAuthorityByProperty(
        Object.fromEntries(
          owned.map((property, i) => {
            const current = authority[i].find((a) => a.state !== "SUPERSEDED");
            return [
              property.id,
              { state: current?.state ?? "NOT_STARTED", message: current?.reason.message ?? "" },
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
      showToast(errorMessage(err, "Could not load properties from backend."), "error");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    load();
  }, [load]);

  // Dynamically map real properties and rooms from database
  const mappedProperties = useMemo(() => {
    return properties.map((prop) => {
      const rooms = roomsByProperty[prop.id] ?? [];
      const propVerif = verificationByProperty[prop.id]?.state === "VERIFIED";
      const authVerif = authorityByProperty[prop.id]?.state === "VERIFIED";
      const isInReview =
        verificationByProperty[prop.id]?.state === "MANUAL_REVIEW" ||
        verificationByProperty[prop.id]?.state === "IN_PROGRESS" ||
        authorityByProperty[prop.id]?.state === "MANUAL_REVIEW" ||
        authorityByProperty[prop.id]?.state === "SUBMITTED";

      const mappedRooms = rooms.map((room, roomIdx) => {
        const occupancies = occupanciesByRoom[room.id] ?? [];
        const activeOcc = occupancies.find((o) => o.status === "ACTIVE");
        const pendingOcc = occupancies.find((o) => o.status === "PENDING_MOVE_IN");
        const currentOcc = activeOcc || pendingOcc;

        // Associated listing for this room if host created one
        const listing = listings.find((l) => l.roomId === room.id);

        // Associated application with active offer
        const appWithOffer = listing
          ? applications.find(
              (a) =>
                a.listingId === listing.id &&
                a.offer &&
                (a.offer.status === "SENT" || a.offer.status === "DRAFT")
            )
          : undefined;

        // Dynamic Room Status
        let roomStatus: "UNDER_OFFER" | "AVAILABLE" | "NOTICE_GIVEN" | "OCCUPIED" = "AVAILABLE";
        if (currentOcc) {
          if (currentOcc.status === "ACTIVE" && currentOcc.expectedEndDate) {
            const endDate = new Date(currentOcc.expectedEndDate);
            const now = new Date();
            const daysLeft = (endDate.getTime() - now.getTime()) / (1000 * 3600 * 24);
            if (daysLeft >= 0 && daysLeft <= 30) {
              roomStatus = "NOTICE_GIVEN";
            } else {
              roomStatus = "OCCUPIED";
            }
          } else {
            roomStatus = "OCCUPIED";
          }
        } else if (appWithOffer) {
          roomStatus = "UNDER_OFFER";
        } else {
          roomStatus = "AVAILABLE";
        }

        // Dynamic Listing Status
        const isLive = listing?.state === "PUBLISHED";
        let listingStatusSubtext = "Not Marketed";
        if (isLive) {
          listingStatusSubtext =
            roomStatus === "OCCUPIED" || roomStatus === "UNDER_OFFER"
              ? "Not accepting new applications"
              : "Applications open";
        } else if (roomStatus === "OCCUPIED") {
          listingStatusSubtext = "Occupied";
        }

        // Dynamic Current Rental or Offer
        let currentRentalOrOffer = "—";
        let currentRentalDate: string | undefined = undefined;
        let renterName: string | undefined = undefined;
        if (currentOcc) {
          renterName = currentOcc.guestName || "Renter";
          currentRentalOrOffer = renterName;
          if (currentOcc.moveInDate && currentOcc.expectedEndDate) {
            currentRentalDate = `${formatDate(currentOcc.moveInDate)} – ${formatDate(currentOcc.expectedEndDate)}`;
          } else if (currentOcc.moveInDate) {
            currentRentalDate = `Since ${formatDate(currentOcc.moveInDate)}`;
          }
        } else if (appWithOffer) {
          renterName = appWithOffer.guestName;
          currentRentalOrOffer = renterName;
          currentRentalDate = `Offer received ${formatDate(appWithOffer.submittedAt)}`;
        }

        // Dynamic Next Milestone
        let nextMilestone = "—";
        let nextMilestoneTone: "urgent" | "positive" | "neutral" | undefined = undefined;
        if (appWithOffer) {
          nextMilestone = "Offer expires today · 23:59";
          nextMilestoneTone = "urgent";
        } else if (roomStatus === "NOTICE_GIVEN" && currentOcc?.expectedEndDate) {
          nextMilestone = `Available from ${formatDate(currentOcc.expectedEndDate)}`;
          nextMilestoneTone = "positive";
        } else if (currentOcc?.status === "PENDING_MOVE_IN") {
          nextMilestone = "Move-in pending · hand over keys";
          nextMilestoneTone = "urgent";
        } else if (currentOcc?.expectedEndDate) {
          nextMilestone = `Agreement ends ${formatDate(currentOcc.expectedEndDate)}`;
          nextMilestoneTone = "neutral";
        }

        // Room Action
        let actionLabel = "Manage Room";
        let actionPrimary = false;
        if (appWithOffer) {
          actionLabel = "Review Offer";
          actionPrimary = true;
        } else if (currentOcc?.status === "PENDING_MOVE_IN") {
          // Opens the room drawer, where the move-in handover steps live.
          actionLabel = "Complete Move-in";
        } else if (currentOcc) {
          actionLabel = "View Rental";
        } else if (listing && !isLive) {
          actionLabel = "Resume Listing";
        }

        const roomType = room.hasEnsuite
          ? "En-suite"
          : room.size > 220
          ? "Studio"
          : room.size > 140
          ? "Double"
          : "Single";

        const image =
          listing?.images && listing.images.length > 0
            ? listing.images[0]
            : DEFAULT_ROOM_IMAGES[roomIdx % DEFAULT_ROOM_IMAGES.length];

        return {
          id: room.id,
          title: `Room #${room.id}`,
          type: roomType,
          size: room.size,
          hasEnsuite: room.hasEnsuite,
          roomStatus,
          listingStatus: isLive ? ("LIVE" as const) : ("PAUSED" as const),
          listingStatusSubtext,
          currentRentalOrOffer,
          currentRentalDate,
          renterName,
          nextMilestone,
          nextMilestoneTone,
          actionLabel,
          actionPrimary,
          image,
          occupancyId: currentOcc?.id,
          occupancyStatus: currentOcc?.status,
          listing,
          appWithOffer,
        };
      });

      return {
        id: prop.id,
        code: `PRO-${String(prop.id).padStart(6, "0")}`,
        name: prop.address.split(",")[0] || prop.address,
        address: `${prop.address}, ${prop.city}`,
        propertyVerified: propVerif,
        authorityVerified: authVerif,
        isInReview,
        rooms: mappedRooms,
        rawProperty: prop,
      };
    });
  }, [
    properties,
    roomsByProperty,
    occupanciesByRoom,
    listings,
    applications,
    verificationByProperty,
    authorityByProperty,
  ]);

  // Derived Real KPI Metrics from Database
  const metrics = useMemo(() => {
    let totalRooms = 0;
    let availableCount = 0;
    let underOfferCount = 0;
    let occupiedCount = 0;
    let notMarketedCount = 0;

    mappedProperties.forEach((p) => {
      p.rooms.forEach((r) => {
        totalRooms++;
        if (r.roomStatus === "AVAILABLE") availableCount++;
        if (r.roomStatus === "UNDER_OFFER") underOfferCount++;
        if (r.roomStatus === "OCCUPIED" || r.roomStatus === "NOTICE_GIVEN") occupiedCount++;
        if (r.listingStatusSubtext.toLowerCase().includes("not marketed")) notMarketedCount++;
      });
    });

    const actionRequiredCount = underOfferCount;

    return {
      totalRooms,
      availableCount,
      underOfferCount,
      occupiedCount,
      notMarketedCount,
      actionRequiredCount,
    };
  }, [mappedProperties]);

  // Filtered properties and rooms based on user filter selections
  const filteredProperties = useMemo(() => {
    let result = mappedProperties.map((p) => {
      let rooms = [...p.rooms];

      // Card Filter from KPI buttons
      if (cardFilter === "available") {
        rooms = rooms.filter((r) => r.roomStatus === "AVAILABLE");
      } else if (cardFilter === "offer") {
        rooms = rooms.filter((r) => r.roomStatus === "UNDER_OFFER");
      } else if (cardFilter === "occupied") {
        rooms = rooms.filter((r) => r.roomStatus === "OCCUPIED" || r.roomStatus === "NOTICE_GIVEN");
      } else if (cardFilter === "action") {
        rooms = rooms.filter((r) => r.roomStatus === "UNDER_OFFER");
      }

      // Search Query
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase().trim();
        const matchesProp =
          p.name.toLowerCase().includes(q) ||
          p.address.toLowerCase().includes(q) ||
          p.code.toLowerCase().includes(q);

        rooms = rooms.filter(
          (r) =>
            matchesProp ||
            r.title.toLowerCase().includes(q) ||
            r.type.toLowerCase().includes(q) ||
            r.currentRentalOrOffer.toLowerCase().includes(q)
        );
      }

      // Room Status Dropdown
      if (roomStatusFilter !== "ALL") {
        rooms = rooms.filter((r) => r.roomStatus === roomStatusFilter);
      }

      // Listing Status Dropdown
      if (listingStatusFilter !== "ALL") {
        rooms = rooms.filter((r) => r.listingStatus === listingStatusFilter);
      }

      return {
        ...p,
        rooms,
      };
    });

    // Property Dropdown Filter
    if (propertyFilter !== "ALL") {
      result = result.filter((p) => p.name === propertyFilter || p.code === propertyFilter);
    }

    // Verification Filter
    if (verificationFilter === "VERIFIED") {
      result = result.filter((p) => p.propertyVerified && p.authorityVerified);
    } else if (verificationFilter === "IN_REVIEW") {
      result = result.filter((p) => p.isInReview);
    }

    // Sort Options
    if (sortOption === "MOST_ROOMS") {
      result.sort((a, b) => b.rooms.length - a.rooms.length);
    } else if (sortOption === "NAME") {
      result.sort((a, b) => a.name.localeCompare(b.name));
    }

    return result;
  }, [
    mappedProperties,
    searchQuery,
    cardFilter,
    roomStatusFilter,
    propertyFilter,
    listingStatusFilter,
    verificationFilter,
    sortOption,
  ]);

  const totalFilteredRooms = useMemo(() => {
    return filteredProperties.reduce((sum, p) => sum + p.rooms.length, 0);
  }, [filteredProperties]);

  // Rounded Filter Dropdown Options
  const roomStatusOptions = useMemo(
    () => [
      { value: "ALL", label: "All statuses" },
      { value: "AVAILABLE", label: "Available" },
      { value: "UNDER_OFFER", label: "Under Offer" },
      { value: "OCCUPIED", label: "Occupied" },
      { value: "NOTICE_GIVEN", label: "Notice Given" },
    ],
    []
  );

  const propertyOptions = useMemo(
    () => [
      { value: "ALL", label: "All properties" },
      ...mappedProperties.map((p) => ({ value: p.name, label: p.name })),
    ],
    [mappedProperties]
  );

  const listingStatusOptions = useMemo(
    () => [
      { value: "ALL", label: "Any listing" },
      { value: "LIVE", label: "Live" },
      { value: "PAUSED", label: "Paused" },
    ],
    []
  );

  const verificationOptions = useMemo(
    () => [
      { value: "ALL", label: "Any verification" },
      { value: "VERIFIED", label: "Verified" },
      { value: "IN_REVIEW", label: "In Review" },
    ],
    []
  );

  const sortOptions = useMemo(
    () => [
      { value: "NEXT_ACTION", label: "Next action first" },
      { value: "NAME", label: "Property name" },
      { value: "MOST_ROOMS", label: "Most rooms" },
    ],
    []
  );

  // Toggle property accordion collapse
  function togglePropertyCollapse(id: number) {
    setCollapsedProperties((prev) => ({
      ...prev,
      [id]: !prev[id],
    }));
  }

  // Reset filters helper
  function handleClearFilters() {
    setSearchQuery("");
    setCardFilter("all");
    setRoomStatusFilter("ALL");
    setPropertyFilter("ALL");
    setListingStatusFilter("ALL");
    setVerificationFilter("ALL");
    setSortOption("NEXT_ACTION");
    showToast("Filters reset to default.");
  }

  // Handle Property form submit (Real Backend DB via POST/PUT)
  async function handlePropertySubmit(e: FormEvent) {
    e.preventDefault();
    if (!propertyForm) return;
    if (!propertyForm.address.trim() || !propertyForm.city.trim()) {
      setError("Both an address and a city are required.");
      return;
    }
    if (!propertyForm.jurisdictionCode) {
      setError("Select the region this property is located in.");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      const payload = {
        address: propertyForm.address.trim(),
        city: propertyForm.city.trim(),
        landmark: propertyForm.landmark.trim() || undefined,
        jurisdictionCode: propertyForm.jurisdictionCode,
      };
      if (propertyForm.id === null) {
        await createHostedProperty(payload);
        showToast("Property added successfully!");
      } else {
        await updateHostedProperty(
          propertyForm.id,
          payload,
          properties.find((p) => p.id === propertyForm.id)?.locationVersion
        );
        showToast("Property updated.");
      }
      setPropertyForm(null);
      await load();
    } catch (err) {
      setError(errorMessage(err, "Could not save the property."));
    } finally {
      setSubmitting(false);
    }
  }

  // Handle Room form submit (Real Backend DB via POST/PUT)
  async function handleRoomSubmit(e: FormEvent) {
    e.preventDefault();
    if (!roomForm) return;
    const size = Number(roomForm.size || 0);
    if (!Number.isFinite(size) || size < 0) {
      setError("Room size must be a positive number of square feet.");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      const payload = { size: Math.round(size), hasEnsuite: roomForm.hasEnsuite };
      if (roomForm.id === null) {
        await createHostedRoom(roomForm.propertyId, payload);
        showToast("Room added successfully!");
      } else {
        await updateHostedRoom(roomForm.propertyId, roomForm.id, payload);
        showToast("Room updated.");
      }
      setRoomForm(null);
      await load();
    } catch (err) {
      setError(errorMessage(err, "Could not save the room."));
    } finally {
      setSubmitting(false);
    }
  }

  // Room status explanation string matching HTML
  function getRoomExplanation(room: (typeof mappedProperties)[number]["rooms"][number]) {
    if (room.roomStatus === "AVAILABLE") {
      return room.listingStatus === "LIVE"
        ? "Vacant and publicly marketed. Applications are open."
        : "Physically available, but the listing is paused and not marketed. Resume the listing to accept applications.";
    }
    if (room.roomStatus === "UNDER_OFFER") {
      return "A preferred applicant was selected and the offer is outstanding. The room is not occupied. The listing stays visible but does not accept new applications.";
    }
    if (room.roomStatus === "NOTICE_GIVEN") {
      return "Occupied with a confirmed end date. Future availability can be marketed where your policy permits.";
    }
    return "A current rental is in force. The listing is paused and does not accept applications.";
  }

  if (loading) {
    return <Loader label="Loading properties and rooms..." />;
  }

  return (
    <div className="w-full space-y-5 font-sans">
      {/* 1. Page Header Section matching Figma Frame (UNTOUCHED Add Property & List a Room buttons) */}
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 pt-1">
        <div className="space-y-1 sm:space-y-1.5">
          <p className="text-[10px] sm:text-[11px] font-bold text-[#D80B0B] dark:text-red-400 uppercase tracking-widest font-heading">
            Host workspace
          </p>
          <h1 className="text-2xl sm:text-3xl md:text-4xl font-extrabold text-[#0E2F73] dark:text-white leading-tight font-heading">
            Properties &amp; Rooms
          </h1>
          <p className="text-xs sm:text-sm md:text-base text-slate-500 dark:text-slate-400 font-normal max-w-3xl">
            Manage your properties, rooms, availability, listings, applications, agreements and occupancy.
          </p>
        </div>

        {/* Action Buttons top right - Responsive side-by-side on mobile, flex on desktop */}
        <div className="grid grid-cols-2 sm:flex sm:items-center gap-2.5 sm:gap-3 w-full sm:w-auto shrink-0">
          <button
            onClick={() =>
              setPropertyForm({
                id: null,
                address: "",
                city: "",
                landmark: "",
                jurisdictionCode: "",
                regionLocked: false,
              })
            }
            className="h-10 sm:h-11 px-3 sm:px-6 rounded-full bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 text-[#0E2F73] dark:text-white font-bold text-xs sm:text-sm hover:bg-slate-50 dark:hover:bg-slate-800 shadow-2xs transition flex items-center justify-center gap-1.5 sm:gap-2 cursor-pointer w-full sm:w-auto"
          >
            <Plus className="size-3.5 sm:size-4 text-[#0E2F73] dark:text-white shrink-0" />
            <span className="truncate">Add Property</span>
          </button>

          <button
            onClick={() => setWizardOpen(true)}
            className="h-10 sm:h-11 px-3 sm:px-6 rounded-full bg-[#0E2F73] hover:bg-[#0a2358] text-white font-bold text-xs sm:text-sm shadow-md transition flex items-center justify-center gap-1.5 sm:gap-2 cursor-pointer w-full sm:w-auto"
          >
            <Plus className="size-3.5 sm:size-4 text-white shrink-0" />
            <span className="truncate">List a Room</span>
          </button>
        </div>
      </div>

      {/* 2. Five KPI Metric Cards - Interactive filter buttons matching Figma */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2.5 sm:gap-3.5 pt-1">
        {/* Card 1: Total Rooms */}
        <button
          type="button"
          onClick={() => setCardFilter((prev) => (prev === "all" ? "all" : "all"))}
          aria-pressed={cardFilter === "all"}
          className={`text-left rounded-3xl border-2 p-3.5 sm:p-4 md:p-5 flex flex-col justify-between shadow-2xs transition cursor-pointer ${
            cardFilter === "all"
              ? "border-[#0E2F73] dark:border-sky-400 bg-[#F5F8FE] dark:bg-slate-800"
              : "border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 hover:border-slate-300"
          }`}
        >
          <div className="flex items-center gap-2.5 sm:gap-3">
            <div className="size-8 sm:size-9 rounded-xl bg-[#EAF0FB] flex items-center justify-center text-[#0E2F73] shrink-0">
              <LayoutGrid className="size-3.5 sm:size-4" />
            </div>
            <span className="text-2xl sm:text-3xl font-extrabold text-[#0B1B3F] dark:text-white font-heading leading-none">
              {metrics.totalRooms}
            </span>
          </div>
          <div className="mt-3 sm:mt-3.5 space-y-1">
            <p className="text-xs sm:text-sm font-extrabold text-[#0E2F73] dark:text-white font-heading leading-tight truncate">
              Total Rooms
            </p>
            <p className="text-[11px] sm:text-xs text-slate-500 dark:text-slate-400 leading-normal line-clamp-2">
              {metrics.availableCount} Available · {metrics.underOfferCount} Under Offer · {metrics.occupiedCount} Occupied
            </p>
          </div>
        </button>

        {/* Card 2: Available */}
        <button
          type="button"
          onClick={() => setCardFilter((prev) => (prev === "available" ? "all" : "available"))}
          aria-pressed={cardFilter === "available"}
          className={`text-left rounded-3xl border-2 p-3.5 sm:p-4 md:p-5 flex flex-col justify-between shadow-2xs transition cursor-pointer ${
            cardFilter === "available"
              ? "border-[#0E2F73] dark:border-sky-400 bg-[#F5F8FE] dark:bg-slate-800"
              : "border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 hover:border-slate-300"
          }`}
        >
          <div className="flex items-center gap-2.5 sm:gap-3">
            <div className="size-8 sm:size-9 rounded-xl bg-[#E7F8EF] flex items-center justify-center text-[#05603A] shrink-0">
              <Check className="size-3.5 sm:size-4" />
            </div>
            <span className="text-2xl sm:text-3xl font-extrabold text-[#0B1B3F] dark:text-white font-heading leading-none">
              {metrics.availableCount}
            </span>
          </div>
          <div className="mt-3 sm:mt-3.5 space-y-1">
            <p className="text-xs sm:text-sm font-extrabold text-[#0E2F73] dark:text-white font-heading leading-tight truncate">
              Available
            </p>
            <p className="text-[11px] sm:text-xs text-slate-500 dark:text-slate-400">Ready to rent</p>
          </div>
        </button>

        {/* Card 3: Under Offer */}
        <button
          type="button"
          onClick={() => setCardFilter((prev) => (prev === "offer" ? "all" : "offer"))}
          aria-pressed={cardFilter === "offer"}
          className={`text-left rounded-3xl border-2 p-3.5 sm:p-4 md:p-5 flex flex-col justify-between shadow-2xs transition cursor-pointer ${
            cardFilter === "offer"
              ? "border-[#0E2F73] dark:border-sky-400 bg-[#F5F8FE] dark:bg-slate-800"
              : "border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 hover:border-slate-300"
          }`}
        >
          <div className="flex items-center gap-2.5 sm:gap-3">
            <div className="size-8 sm:size-9 rounded-xl bg-[#FFF4E0] flex items-center justify-center text-[#93370D] shrink-0">
              <Clock className="size-3.5 sm:size-4" />
            </div>
            <span className="text-2xl sm:text-3xl font-extrabold text-[#0B1B3F] dark:text-white font-heading leading-none">
              {metrics.underOfferCount}
            </span>
          </div>
          <div className="mt-3 sm:mt-3.5 space-y-1">
            <p className="text-xs sm:text-sm font-extrabold text-[#0E2F73] dark:text-white font-heading leading-tight truncate">
              Under Offer
            </p>
            <p className="text-[11px] sm:text-xs text-slate-500 dark:text-slate-400">Awaiting decision</p>
          </div>
        </button>

        {/* Card 4: Occupied */}
        <button
          type="button"
          onClick={() => setCardFilter((prev) => (prev === "occupied" ? "all" : "occupied"))}
          aria-pressed={cardFilter === "occupied"}
          className={`text-left rounded-3xl border-2 p-3.5 sm:p-4 md:p-5 flex flex-col justify-between shadow-2xs transition cursor-pointer ${
            cardFilter === "occupied"
              ? "border-[#0E2F73] dark:border-sky-400 bg-[#F5F8FE] dark:bg-slate-800"
              : "border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 hover:border-slate-300"
          }`}
        >
          <div className="flex items-center gap-2.5 sm:gap-3">
            <div className="size-8 sm:size-9 rounded-xl bg-[#EAF0FB] flex items-center justify-center text-[#0E2F73] shrink-0">
              <DoorOpen className="size-3.5 sm:size-4" />
            </div>
            <span className="text-2xl sm:text-3xl font-extrabold text-[#0B1B3F] dark:text-white font-heading leading-none">
              {metrics.occupiedCount}
            </span>
          </div>
          <div className="mt-3 sm:mt-3.5 space-y-1">
            <p className="text-xs sm:text-sm font-extrabold text-[#0E2F73] dark:text-white font-heading leading-tight truncate">
              Occupied
            </p>
            <p className="text-[11px] sm:text-xs text-slate-500 dark:text-slate-400">
              {metrics.occupiedCount === 1 ? "1 currently active" : `${metrics.occupiedCount} active`}
            </p>
          </div>
        </button>

        {/* Card 5: Action Required */}
        <button
          type="button"
          onClick={() => setCardFilter((prev) => (prev === "action" ? "all" : "action"))}
          aria-pressed={cardFilter === "action"}
          className={`text-left rounded-3xl border-2 p-3.5 sm:p-4 md:p-5 flex flex-col justify-between shadow-2xs transition cursor-pointer col-span-2 sm:col-span-1 ${
            cardFilter === "action"
              ? "border-[#B42318] dark:border-red-400 bg-[#FFF7F7] dark:bg-red-950/20"
              : "border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 hover:border-slate-300"
          }`}
        >
          <div className="flex items-center gap-2.5 sm:gap-3">
            <div className="size-8 sm:size-9 rounded-xl bg-[#FDECEC] flex items-center justify-center text-[#B42318] shrink-0">
              <AlertTriangle className="size-3.5 sm:size-4" />
            </div>
            <span className="text-2xl sm:text-3xl font-extrabold text-[#B42318] font-heading leading-none">
              {metrics.actionRequiredCount}
            </span>
          </div>
          <div className="mt-3 sm:mt-3.5 space-y-1">
            <p className="text-xs sm:text-sm font-extrabold text-[#0E2F73] dark:text-white font-heading leading-tight truncate">
              Action Required
            </p>
            <p className="text-[11px] sm:text-xs text-slate-500 dark:text-slate-400">
              {metrics.actionRequiredCount > 0 ? "Needs your attention" : "All clear"}
            </p>
          </div>
        </button>
      </div>

      {/* 3. Urgent Attention Alert Banner matching HTML exactly */}
      {metrics.actionRequiredCount > 0 && (
        <div className="rounded-2xl border border-[#F3C2C2] bg-[#FFF5F5] dark:bg-red-950/20 p-4 sm:p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4 shadow-2xs">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <AlertTriangle className="size-4 text-[#B42318] shrink-0" />
              <span className="text-sm font-bold text-[#B42318] font-heading">
                {metrics.actionRequiredCount} item needs your attention
              </span>
            </div>
            <p className="text-xs text-slate-700 dark:text-slate-300">
              Applicant offers are outstanding. Review applicant terms and decide before deadlines expire.
            </p>
          </div>
          <Link
            href="/account/host/applications"
            className="h-10 px-5 rounded-full bg-[#0E2F73] hover:bg-[#0a2358] text-white text-xs font-bold font-heading shadow-sm transition flex items-center justify-center shrink-0 w-full sm:w-auto"
          >
            Review Offers
          </Link>
        </div>
      )}

      {/* 4. Filter & Search Bar matching Figma Frame */}
      <div className="w-full bg-white dark:bg-slate-900 rounded-3xl border border-slate-200 dark:border-slate-800 p-3.5 sm:p-5 shadow-2xs space-y-3">
        <div className="flex flex-col xl:flex-row xl:items-end gap-3 sm:gap-3.5">
          {/* Search Box */}
          <div className="w-full xl:flex-1 xl:min-w-[240px]">
            <div className="relative">
              <Search className="size-4 text-slate-400 absolute left-4 top-1/2 -translate-y-1/2 pointer-events-none" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search properties, rooms or addresses…"
                className="w-full h-10 sm:h-11 pl-10 pr-4 bg-[#F4F6FB] dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-full text-xs text-[#0E2F73] dark:text-slate-200 placeholder:text-slate-400 outline-none focus:ring-2 focus:ring-[#0E2F73] font-normal shadow-2xs"
              />
            </div>
          </div>

          {/* Filter Dropdowns Grid */}
          <div className="grid grid-cols-1 min-[480px]:grid-cols-2 md:grid-cols-3 xl:flex xl:items-end gap-2.5 sm:gap-3 w-full xl:w-auto">
            {/* 1. Room Status Dropdown */}
            <RoundedFilterDropdown
              label="Room Status"
              value={roomStatusFilter}
              onChange={setRoomStatusFilter}
              options={roomStatusOptions}
            />

            {/* 2. Property Dropdown */}
            <RoundedFilterDropdown
              label="Property"
              value={propertyFilter}
              onChange={setPropertyFilter}
              options={propertyOptions}
            />

            {/* 3. Listing Status Dropdown */}
            <RoundedFilterDropdown
              label="Listing Status"
              value={listingStatusFilter}
              onChange={setListingStatusFilter}
              options={listingStatusOptions}
            />

            {/* 4. Verification Status Dropdown */}
            <RoundedFilterDropdown
              label="Verification Status"
              value={verificationFilter}
              onChange={setVerificationFilter}
              options={verificationOptions}
            />

            {/* 5. Sort Dropdown */}
            <RoundedFilterDropdown
              label="Sort"
              value={sortOption}
              onChange={setSortOption}
              options={sortOptions}
              align="right"
            />
          </div>
        </div>

        {/* Counter bottom and Clear filters */}
        <div className="flex flex-wrap items-center justify-between gap-2 pt-1 border-t border-slate-100 dark:border-slate-800/60">
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Showing {totalFilteredRooms} of {metrics.totalRooms} rooms
          </p>
          {(searchQuery ||
            cardFilter !== "all" ||
            roomStatusFilter !== "ALL" ||
            propertyFilter !== "ALL" ||
            listingStatusFilter !== "ALL" ||
            verificationFilter !== "ALL" ||
            sortOption !== "NEXT_ACTION") && (
            <button
              type="button"
              onClick={handleClearFilters}
              className="text-xs font-bold text-[#D80B0B] hover:underline cursor-pointer"
            >
              Clear filters
            </button>
          )}
        </div>
      </div>

      {/* 5. Property Cards (Accordions) from Figma Frame */}
      {filteredProperties.length === 0 ? (
        /* Empty State Card matching Figma Frame */
        <div className="p-6 sm:p-12 text-center bg-white dark:bg-slate-900 rounded-3xl border border-dashed border-slate-300 dark:border-slate-800 flex flex-col items-center justify-center gap-4">
          <div className="size-14 sm:size-16 rounded-3xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-slate-400 shadow-inner">
            <Home className="size-7 sm:size-8 text-[#0E2F73] dark:text-white" />
          </div>
          <div className="space-y-1">
            <h3 className="text-slate-900 dark:text-white font-extrabold text-base sm:text-lg font-heading">
              {metrics.totalRooms === 0 ? "No properties yet" : "No rooms match these filters"}
            </h3>
            <p className="text-slate-500 dark:text-slate-400 text-xs max-w-md mx-auto">
              {metrics.totalRooms === 0
                ? "Add a property or use List a Room to begin."
                : "Try adjusting your search query or filters."}
            </p>
          </div>
          <div className="flex flex-wrap items-center justify-center gap-2.5 mt-2 w-full sm:w-auto">
            <button
              onClick={() =>
                setPropertyForm({
                  id: null,
                  address: "",
                  city: "",
                  landmark: "",
                  jurisdictionCode: "",
                  regionLocked: false,
                })
              }
              className="h-10 px-5 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 text-[#0E2F73] dark:text-white text-xs font-bold font-heading rounded-full shadow-2xs hover:bg-slate-50 transition cursor-pointer w-full sm:w-auto"
            >
              Add Property
            </button>
            <button
              onClick={() => setWizardOpen(true)}
              className="h-10 px-5 bg-[#0E2F73] text-white text-xs font-bold font-heading rounded-full shadow-sm hover:bg-[#0a2358] transition cursor-pointer w-full sm:w-auto"
            >
              List a Room
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-5">
          {filteredProperties.map((prop) => {
            const isCollapsed = Boolean(collapsedProperties[prop.id]);
            const availableCount = prop.rooms.filter((r) => r.roomStatus === "AVAILABLE").length;
            const occupiedCount = prop.rooms.filter(
              (r) => r.roomStatus === "OCCUPIED" || r.roomStatus === "NOTICE_GIVEN"
            ).length;
            const underOfferCount = prop.rooms.filter((r) => r.roomStatus === "UNDER_OFFER").length;
            const notMarketedCount = prop.rooms.filter((r) =>
              r.listingStatusSubtext.toLowerCase().includes("not marketed")
            ).length;

            return (
              <div
                key={prop.id}
                className="bg-white dark:bg-slate-900 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden"
              >
                {/* Property Accordion Header */}
                <div className="px-4 py-3.5 sm:px-5 sm:py-4 bg-[#F7F9FD] dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-800 flex flex-col md:flex-row md:items-start justify-between gap-3.5">
                  <div className="flex items-start gap-3 sm:gap-3.5 flex-1 min-w-0">
                    {/* Accordion Expand/Collapse button */}
                    <button
                      type="button"
                      onClick={() => togglePropertyCollapse(prop.id)}
                      className="size-8 sm:size-9 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 flex items-center justify-center text-[#0E2F73] hover:text-slate-900 transition cursor-pointer mt-0.5 sm:mt-1 shrink-0"
                      title={isCollapsed ? "Expand property" : "Collapse property"}
                    >
                      <ChevronDown
                        className={`size-4 transition-transform duration-200 ${
                          isCollapsed ? "-rotate-90" : ""
                        }`}
                      />
                    </button>

                    {/* Property icon */}
                    <div className="size-8 sm:size-10 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center text-[#0E2F73] dark:text-white shrink-0 mt-0.5">
                      <Building2 className="size-4 sm:size-5" />
                    </div>

                    {/* Property details */}
                    <div className="space-y-1 flex-1 min-w-0">
                      <div className="flex items-baseline gap-2 flex-wrap">
                        <h2 className="text-base sm:text-lg font-extrabold text-[#0E2F73] dark:text-white font-heading">
                          {prop.name}
                        </h2>
                        <span className="text-xs font-semibold text-slate-500">
                          Property #{prop.code}
                        </span>
                      </div>
                      <p className="text-xs text-slate-500 dark:text-slate-400 break-words line-clamp-2 sm:truncate">
                        {prop.address}
                      </p>

                      {/* Verified pills + Room Counts */}
                      <div className="flex items-center gap-1.5 sm:gap-2 pt-1 flex-wrap">
                        {prop.propertyVerified ? (
                          <div className="px-2.5 py-0.5 sm:py-1 bg-[#E7F8EF] text-[#05603A] rounded-full text-[11px] sm:text-xs font-bold flex items-center gap-1.5 border border-[#8FD9B4]">
                            <ShieldCheck className="size-3 text-[#05603A]" />
                            <span>Property Verified</span>
                          </div>
                        ) : (
                          <button
                            type="button"
                            onClick={() =>
                              setVerifyProperty({
                                id: prop.id,
                                label: `${prop.address} · Property #${prop.id}`,
                              })
                            }
                            className="px-2.5 py-0.5 sm:py-1 bg-[#EEF3FF] hover:bg-sky-100 text-[#1D3F8F] rounded-full text-[11px] sm:text-xs font-bold flex items-center gap-1.5 border border-[#AFC2EE] cursor-pointer transition"
                          >
                            <Clock className="size-3 text-[#1D3F8F]" />
                            <span>Property verification In Review</span>
                          </button>
                        )}

                        {prop.authorityVerified ? (
                          <div className="px-2.5 py-0.5 sm:py-1 bg-[#E7F8EF] text-[#05603A] rounded-full text-[11px] sm:text-xs font-bold flex items-center gap-1.5 border border-[#8FD9B4]">
                            <ShieldCheck className="size-3 text-[#05603A]" />
                            <span>Listing Authority Verified</span>
                          </div>
                        ) : (
                          <button
                            type="button"
                            onClick={() =>
                              setAuthorityProperty({
                                id: prop.id,
                                label: `${prop.address} · Property #${prop.id}`,
                              })
                            }
                            className="px-2.5 py-0.5 sm:py-1 bg-[#EEF3FF] hover:bg-sky-100 text-[#1D3F8F] rounded-full text-[11px] sm:text-xs font-bold flex items-center gap-1.5 border border-[#AFC2EE] cursor-pointer transition"
                          >
                            <Clock className="size-3 text-[#1D3F8F]" />
                            <span>Listing authority In Review</span>
                          </button>
                        )}

                        <span className="text-[11px] sm:text-xs font-semibold text-slate-600 dark:text-slate-300 w-full sm:w-auto mt-0.5 sm:mt-0">
                          {prop.rooms.length} {prop.rooms.length === 1 ? "room" : "rooms"}
                          {prop.rooms.length > 0 &&
                            ` · ${availableCount} Available · ${occupiedCount} Occupied · ${underOfferCount} Under Offer · ${notMarketedCount} Not Marketed`}
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Header Actions */}
                  <div className="flex items-center gap-2 shrink-0 relative w-full md:w-auto pt-2 md:pt-0 border-t md:border-t-0 border-slate-200/60 dark:border-slate-800 justify-between sm:justify-end">
                    <button
                      onClick={() =>
                        setRoomForm({
                          propertyId: prop.id,
                          id: null,
                          size: "",
                          hasEnsuite: false,
                        })
                      }
                      className="flex-1 sm:flex-initial h-9 sm:h-10 px-3 sm:px-4 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-[#0E2F73] dark:text-white text-xs font-bold font-heading rounded-full shadow-2xs transition flex items-center justify-center gap-1.5 cursor-pointer"
                    >
                      <Plus className="size-3.5 text-[#0E2F73] dark:text-white" />
                      <span>Add Room</span>
                    </button>
                    <button
                      onClick={() => {
                        setPropertyForm({
                          id: prop.rawProperty.id,
                          address: prop.rawProperty.address,
                          city: prop.rawProperty.city,
                          landmark: prop.rawProperty.landmark ?? "",
                          jurisdictionCode: prop.rawProperty.jurisdictionCode,
                          savedJurisdictionCode: prop.rawProperty.jurisdictionCode,
                          regionLocked: prop.rawProperty.regionLocked,
                        });
                      }}
                      className="flex-1 sm:flex-initial h-9 sm:h-10 px-3 sm:px-4 bg-[#EEF2F9] hover:bg-slate-200 dark:bg-slate-800 text-[#0E2F73] dark:text-white text-xs font-bold font-heading rounded-full transition flex items-center justify-center cursor-pointer"
                    >
                      Manage Property
                    </button>

                    {/* Three-dots menu trigger */}
                    <div
                      className="relative shrink-0"
                      data-property-menu
                    >
                      <button
                        type="button"
                        onClick={() =>
                          setOpenPropertyMenuId((prev) => (prev === prop.id ? null : prop.id))
                        }
                        className="size-9 rounded-full flex items-center justify-center hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-500 hover:text-slate-900 transition cursor-pointer"
                      >
                        <MoreVertical className="size-4" />
                      </button>

                      {openPropertyMenuId === prop.id && (
                        <div className="absolute right-0 top-10 z-30 w-48 bg-white dark:bg-slate-900 rounded-2xl border border-slate-200 dark:border-slate-800 shadow-xl py-1.5 animate-in fade-in zoom-in-95 duration-100">
                          <button
                            type="button"
                            onClick={() => {
                              setOpenPropertyMenuId(null);
                              setVerifyProperty({
                                id: prop.id,
                                label: `${prop.address} · Property #${prop.id}`,
                              });
                            }}
                            className="w-full text-left px-4 py-2 text-xs font-semibold text-[#0E2F73] dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800 flex items-center gap-2"
                          >
                            <ShieldCheck className="size-3.5 text-slate-400" />
                            <span>View verification</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setOpenPropertyMenuId(null);
                              setPropertyForm({
                                id: prop.rawProperty.id,
                                address: prop.rawProperty.address,
                                city: prop.rawProperty.city,
                                landmark: prop.rawProperty.landmark ?? "",
                                jurisdictionCode: prop.rawProperty.jurisdictionCode,
                                savedJurisdictionCode: prop.rawProperty.jurisdictionCode,
                                regionLocked: prop.rawProperty.regionLocked,
                              });
                            }}
                            className="w-full text-left px-4 py-2 text-xs font-semibold text-[#0E2F73] dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800 flex items-center gap-2"
                          >
                            <FileText className="size-3.5 text-slate-400" />
                            <span>Edit property details</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setOpenPropertyMenuId(null);
                              showToast(`Archiving property ${prop.name}...`);
                            }}
                            className="w-full text-left px-4 py-2 text-xs font-semibold text-[#B42318] hover:bg-red-50 dark:hover:bg-red-950/20 flex items-center gap-2 border-t border-slate-100 dark:border-slate-800 mt-1"
                          >
                            <X className="size-3.5 text-[#B42318]" />
                            <span>Archive property</span>
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                {/* Property Accordion Body */}
                {!isCollapsed && (
                  <div className="w-full">
                    {/* In Review Warning Banner */}
                    {prop.isInReview && (
                      <div className="m-5 px-4 py-3 bg-[#EEF3FF] dark:bg-blue-950/30 text-[#1D3F8F] dark:text-sky-200 border border-[#AFC2EE] rounded-2xl text-xs font-semibold flex items-center gap-2.5">
                        <Info className="size-4 shrink-0 text-[#1D3F8F]" />
                        <span>
                          Property verification is in review. Adding rooms is available; publishing listings is blocked until verification completes.
                        </span>
                      </div>
                    )}

                    {/* No rooms added yet */}
                    {prop.rooms.length === 0 ? (
                      <div className="m-5 p-5 rounded-2xl border border-dashed border-slate-300 dark:border-slate-800 flex flex-col sm:flex-row sm:items-center justify-between gap-3.5">
                        <div className="flex items-center gap-3">
                          <div className="size-11 rounded-xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-slate-400 shrink-0">
                            <Bed className="size-5 text-slate-500" />
                          </div>
                          <div>
                            <p className="text-sm font-bold text-[#0E2F73] dark:text-white font-heading">
                              No rooms added yet
                            </p>
                            <p className="text-xs text-slate-500 dark:text-slate-400">
                              Add a room to this property. You can prepare listings while verification is in review.
                            </p>
                          </div>
                        </div>
                        <button
                          onClick={() =>
                            setRoomForm({
                              propertyId: prop.id,
                              id: null,
                              size: "",
                              hasEnsuite: false,
                            })
                          }
                          className="h-10 px-5 rounded-full bg-[#0E2F73] hover:bg-[#0a2358] text-white text-xs font-bold font-heading transition flex items-center justify-center gap-1.5 shrink-0 cursor-pointer"
                        >
                          <Plus className="size-3.5" />
                          <span>Add Room</span>
                        </button>
                      </div>
                    ) : (
                      /* Rooms Display: Dual Mobile Card & Desktop Table View */
                      <div className="w-full">
                        {/* A. Mobile & Tablet Card Layout (block lg:hidden) */}
                        <div className="block lg:hidden divide-y divide-slate-100 dark:divide-slate-800">
                          {prop.rooms.map((room) => {
                            return (
                              <div
                                key={room.id}
                                onClick={() =>
                                  setSelectedRoomForDrawer({ room, property: prop })
                                }
                                className={`p-4 sm:p-5 space-y-3.5 transition cursor-pointer ${
                                  room.actionPrimary
                                    ? "bg-[#FFF8F8] dark:bg-red-950/10 hover:bg-red-50/40"
                                    : "hover:bg-slate-50/60 dark:hover:bg-slate-800/30"
                                }`}
                              >
                                {/* 1. Room info + thumbnail + 3-dots */}
                                <div className="flex items-start gap-3">
                                  <img
                                    src={resolveImageUrl(room.image)}
                                    alt={room.title}
                                    className="size-14 sm:size-16 rounded-xl object-cover bg-slate-100 shrink-0 border border-slate-200/60 dark:border-slate-700"
                                  />
                                  <div className="min-w-0 flex-1 space-y-1">
                                    <div className="flex items-center justify-between gap-2">
                                      <p className="text-sm font-extrabold text-[#0E2F73] dark:text-white font-heading truncate leading-tight hover:text-[#D80B0B]">
                                        {room.title}
                                      </p>

                                      {/* Three dots button */}
                                      <div
                                        className="relative shrink-0"
                                        data-room-menu
                                      >
                                        <button
                                          type="button"
                                          onClick={(e) => {
                                            e.stopPropagation();
                                            setOpenRoomMenuId((prev) =>
                                              prev === room.id ? null : room.id
                                            );
                                          }}
                                          className="size-8 rounded-full flex items-center justify-center hover:bg-slate-100 dark:hover:bg-slate-800 text-slate-500 hover:text-slate-900 transition cursor-pointer"
                                        >
                                          <MoreVertical className="size-4" />
                                        </button>

                                        {openRoomMenuId === room.id && (
                                          <div
                                            onClick={(e) => e.stopPropagation()}
                                            className="absolute right-0 top-8 z-30 w-44 bg-white dark:bg-slate-900 rounded-2xl border border-slate-200 dark:border-slate-800 shadow-xl py-1.5 animate-in fade-in zoom-in-95 duration-100"
                                          >
                                            <button
                                              type="button"
                                              onClick={() => {
                                                setOpenRoomMenuId(null);
                                                setRoomForm({
                                                  propertyId: prop.id,
                                                  id: room.id,
                                                  size: String(room.size),
                                                  hasEnsuite: room.hasEnsuite,
                                                });
                                              }}
                                              className="w-full text-left px-4 py-2 text-xs font-semibold text-[#0E2F73] dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800"
                                            >
                                              Edit room
                                            </button>
                                            {room.listing && (
                                              <Link
                                                href={`/account/host/listings`}
                                                className="block w-full text-left px-4 py-2 text-xs font-semibold text-[#0E2F73] dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800"
                                              >
                                                View Listing
                                              </Link>
                                            )}
                                            {room.occupancyId && (
                                              <button
                                                type="button"
                                                onClick={() => {
                                                  setOpenRoomMenuId(null);
                                                  setRecordOccupancyId(room.occupancyId ?? null);
                                                }}
                                                className="w-full text-left px-4 py-2 text-xs font-semibold text-[#0E2F73] dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800"
                                              >
                                                Rental Record
                                              </button>
                                            )}
                                          </div>
                                        )}
                                      </div>
                                    </div>
                                    <p className="text-xs text-slate-500 dark:text-slate-400">
                                      {room.type} {room.size > 0 ? `· ${room.size} sq ft` : ""}{room.hasEnsuite ? " · Ensuite" : ""}
                                    </p>
                                  </div>
                                </div>

                                {/* 2. Badges Row: Room Status + Listing Status */}
                                <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-slate-100 dark:border-slate-800/60">
                                  {/* Room Status Badge */}
                                  {room.roomStatus === "UNDER_OFFER" && (
                                    <div className="px-2.5 py-0.5 rounded-full bg-[#FFF4E0] text-[#93370D] border border-[#F2B866] text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5">
                                      <Clock className="size-3 text-[#93370D]" />
                                      <span>Under Offer</span>
                                    </div>
                                  )}
                                  {room.roomStatus === "AVAILABLE" && (
                                    <div className="px-2.5 py-0.5 rounded-full bg-[#E7F8EF] text-[#05603A] border border-[#8FD9B4] text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5">
                                      <Check className="size-3 text-[#05603A]" />
                                      <span>Available</span>
                                    </div>
                                  )}
                                  {room.roomStatus === "NOTICE_GIVEN" && (
                                    <div className="px-2.5 py-0.5 rounded-full bg-[#EAF0FB] text-[#0E2F73] border border-[#0E2F73] text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5">
                                      <Calendar className="size-3 text-[#0E2F73]" />
                                      <span>Notice Given</span>
                                    </div>
                                  )}
                                  {room.roomStatus === "OCCUPIED" && (
                                    <div className="px-2.5 py-0.5 rounded-full bg-[#0E2F73] text-white text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5">
                                      <Key className="size-3 text-white" />
                                      <span>Occupied</span>
                                    </div>
                                  )}

                                  {/* Listing Status Badge */}
                                  <div className="flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-[11px] font-medium text-slate-600 dark:text-slate-300">
                                    <span
                                      className={`size-2 rounded-full ${
                                        room.listingStatus === "LIVE" ? "bg-[#12A15E]" : "bg-slate-400"
                                      }`}
                                    />
                                    <span className="font-bold">
                                      {room.listingStatus === "LIVE" ? "Live" : "Paused"}
                                    </span>
                                    <span className="text-slate-400 dark:text-slate-500">· {room.listingStatusSubtext}</span>
                                  </div>
                                </div>

                                {/* 3. Key Details: Current Rental / Offer & Next Milestone */}
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 p-3 rounded-xl bg-slate-50/70 dark:bg-slate-800/40 text-xs">
                                  <div>
                                    <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block font-heading">
                                      Current Rental / Offer
                                    </span>
                                    <p className="font-extrabold text-sm text-[#0E2F73] dark:text-white font-heading mt-0.5">
                                      {room.currentRentalOrOffer}
                                    </p>
                                    {room.currentRentalDate && (
                                      <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
                                        {room.currentRentalDate}
                                      </p>
                                    )}
                                  </div>

                                  <div>
                                    <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block font-heading">
                                      Next Milestone
                                    </span>
                                    {room.nextMilestone === "—" ? (
                                      <span className="text-slate-400 text-xs block mt-0.5">—</span>
                                    ) : (
                                      <div className="flex items-center gap-1.5 mt-0.5">
                                        {room.nextMilestoneTone === "urgent" && (
                                          <AlertTriangle className="size-3.5 text-[#B42318] shrink-0" />
                                        )}
                                        {room.nextMilestoneTone === "positive" && (
                                          <Calendar className="size-3.5 text-[#05603A] shrink-0" />
                                        )}
                                        <span
                                          className={`text-xs font-semibold ${
                                            room.nextMilestoneTone === "urgent"
                                              ? "text-[#B42318] font-bold"
                                              : room.nextMilestoneTone === "positive"
                                              ? "text-[#05603A] font-bold"
                                              : "text-slate-600 dark:text-slate-300"
                                          }`}
                                        >
                                          {room.nextMilestone}
                                        </span>
                                      </div>
                                    )}
                                  </div>
                                </div>

                                {/* 4. Action Button (Full width on mobile/tablet) */}
                                <div className="pt-1">
                                  {room.actionPrimary ? (
                                    <Link
                                      href="/account/host/applications"
                                      onClick={(e) => e.stopPropagation()}
                                      className="w-full h-10 px-4 rounded-full bg-[#0E2F73] hover:bg-[#0a2358] text-white text-xs font-bold font-heading shadow-xs transition flex items-center justify-center cursor-pointer"
                                    >
                                      {room.actionLabel}
                                    </Link>
                                  ) : (
                                    <button
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        if (room.actionLabel === "View Rental" && room.occupancyId) {
                                          setRecordOccupancyId(room.occupancyId);
                                        } else if (room.actionLabel === "Resume Listing") {
                                          showToast(`Manage listing for ${room.title} in My Listings`);
                                        } else {
                                          setSelectedRoomForDrawer({ room, property: prop });
                                        }
                                      }}
                                      className="w-full h-10 px-4 rounded-full bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-[#0E2F73] dark:text-white text-xs font-bold font-heading shadow-2xs transition flex items-center justify-center cursor-pointer"
                                    >
                                      {room.actionLabel}
                                    </button>
                                  )}
                                </div>
                              </div>
                            );
                          })}
                        </div>

                        {/* B. Desktop Table Layout (hidden lg:block) */}
                        <div className="hidden lg:block w-full overflow-x-auto">
                          {/* Table Header Row */}
                          <div className="min-w-[920px] px-6 py-3 border-b border-slate-100 dark:border-slate-800 flex items-center text-[11px] font-bold text-slate-500 font-heading uppercase tracking-wider bg-[#F7F9FD] dark:bg-slate-800/40">
                            <div className="w-56 shrink-0">Room</div>
                            <div className="w-36 shrink-0">Room status</div>
                            <div className="w-52 shrink-0">Listing status</div>
                            <div className="w-52 shrink-0">Current rental / offer</div>
                            <div className="w-48 shrink-0">Next milestone</div>
                            <div className="flex-1 text-right">Action</div>
                          </div>

                          {/* Room Rows */}
                          <div className="divide-y divide-slate-100 dark:divide-slate-800 min-w-[920px]">
                            {prop.rooms.map((room) => {
                              return (
                                <div
                                  key={room.id}
                                  onClick={() =>
                                    setSelectedRoomForDrawer({ room, property: prop })
                                  }
                                  className={`px-6 py-3.5 flex items-center transition cursor-pointer ${
                                    room.actionPrimary
                                      ? "bg-[#FFF8F8] dark:bg-red-950/10 hover:bg-red-50/40"
                                      : "hover:bg-slate-50/60 dark:hover:bg-slate-800/30"
                                  }`}
                                >
                                  {/* 1. Room info + thumbnail */}
                                  <div className="w-56 shrink-0 flex items-center gap-3">
                                    <img
                                      src={resolveImageUrl(room.image)}
                                      alt={room.title}
                                      className="w-13 h-11 rounded-xl object-cover bg-slate-100 shrink-0 border border-slate-200/60 dark:border-slate-700"
                                    />
                                    <div className="space-y-0.5 min-w-0">
                                      <p className="text-sm font-extrabold text-[#0E2F73] dark:text-white font-heading truncate hover:text-[#D80B0B]">
                                        {room.title}
                                      </p>
                                      <p className="text-xs text-slate-500 dark:text-slate-400">
                                        {room.type} {room.size > 0 ? `· ${room.size} sq ft` : ""}
                                      </p>
                                    </div>
                                  </div>

                                  {/* 2. Room Status Badge */}
                                  <div className="w-36 shrink-0">
                                    {room.roomStatus === "UNDER_OFFER" && (
                                      <div className="px-2.5 py-1 rounded-full bg-[#FFF4E0] text-[#93370D] border border-[#F2B866] text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5 w-fit">
                                        <Clock className="size-3 text-[#93370D]" />
                                        <span>Under Offer</span>
                                      </div>
                                    )}
                                    {room.roomStatus === "AVAILABLE" && (
                                      <div className="px-2.5 py-1 rounded-full bg-[#E7F8EF] text-[#05603A] border border-[#8FD9B4] text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5 w-fit">
                                        <Check className="size-3 text-[#05603A]" />
                                        <span>Available</span>
                                      </div>
                                    )}
                                    {room.roomStatus === "NOTICE_GIVEN" && (
                                      <div className="px-2.5 py-1 rounded-full bg-[#EAF0FB] text-[#0E2F73] border border-[#0E2F73] text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5 w-fit">
                                        <Calendar className="size-3 text-[#0E2F73]" />
                                        <span>Notice Given</span>
                                      </div>
                                    )}
                                    {room.roomStatus === "OCCUPIED" && (
                                      <div className="px-2.5 py-1 rounded-full bg-[#0E2F73] text-white text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5 w-fit">
                                        <Key className="size-3 text-white" />
                                        <span>Occupied</span>
                                      </div>
                                    )}
                                  </div>

                                  {/* 3. Listing Status */}
                                  <div className="w-52 shrink-0 space-y-0.5">
                                    <div className="flex items-center gap-1.5">
                                      <span
                                        className={`size-2 rounded-full ${
                                          room.listingStatus === "LIVE" ? "bg-[#12A15E]" : "bg-slate-400"
                                        }`}
                                      />
                                      <span
                                        className={`text-sm font-bold font-heading ${
                                          room.listingStatus === "LIVE"
                                            ? "text-[#05603A]"
                                            : "text-slate-600 dark:text-slate-300"
                                        }`}
                                      >
                                        {room.listingStatus === "LIVE" ? "Live" : "Paused"}
                                      </span>
                                    </div>
                                    <p className="text-xs text-slate-500 dark:text-slate-400 leading-tight">
                                      {room.listingStatusSubtext}
                                    </p>
                                  </div>

                                  {/* 4. Current Rental / Offer */}
                                  <div className="w-52 shrink-0 space-y-0.5">
                                    {room.currentRentalOrOffer === "—" ? (
                                      <span className="text-slate-400">—</span>
                                    ) : (
                                      <>
                                        <p className="text-sm font-bold text-[#0E2F73] dark:text-white font-heading">
                                          {room.currentRentalOrOffer}
                                        </p>
                                        {room.currentRentalDate && (
                                          <p className="text-xs text-slate-500 dark:text-slate-400">
                                            {room.currentRentalDate}
                                          </p>
                                        )}
                                      </>
                                    )}
                                  </div>

                                  {/* 5. Next Milestone */}
                                  <div className="w-48 shrink-0">
                                    {room.nextMilestone === "—" ? (
                                      <span className="text-slate-400">—</span>
                                    ) : (
                                      <div className="flex items-center gap-1.5">
                                        {room.nextMilestoneTone === "urgent" && (
                                          <AlertTriangle className="size-3.5 text-[#B42318] shrink-0" />
                                        )}
                                        {room.nextMilestoneTone === "positive" && (
                                          <Calendar className="size-3.5 text-[#05603A] shrink-0" />
                                        )}
                                        <span
                                          className={`text-xs font-semibold ${
                                            room.nextMilestoneTone === "urgent"
                                              ? "text-[#B42318] font-bold"
                                              : room.nextMilestoneTone === "positive"
                                              ? "text-[#05603A] font-bold"
                                              : "text-slate-600 dark:text-slate-300"
                                          }`}
                                        >
                                          {room.nextMilestone}
                                        </span>
                                      </div>
                                    )}
                                  </div>

                                  {/* 6. Action Button & Three dots */}
                                  <div className="flex-1 flex items-center justify-end gap-2 relative">
                                    {room.actionPrimary ? (
                                      <Link
                                        href="/account/host/applications"
                                        onClick={(e) => e.stopPropagation()}
                                        className="inline-flex items-center justify-center h-9 px-4 bg-[#0E2F73] hover:bg-[#0a2358] text-white text-xs font-bold font-heading rounded-full shadow-xs transition"
                                      >
                                        {room.actionLabel}
                                      </Link>
                                    ) : (
                                      <button
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          if (room.actionLabel === "View Rental" && room.occupancyId) {
                                            setRecordOccupancyId(room.occupancyId);
                                          } else if (room.actionLabel === "Resume Listing") {
                                            showToast(`Manage listing for ${room.title} in My Listings`);
                                          } else {
                                            setSelectedRoomForDrawer({ room, property: prop });
                                          }
                                        }}
                                        className="h-9 px-4 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-[#0E2F73] dark:text-white text-xs font-bold font-heading rounded-full shadow-2xs transition cursor-pointer"
                                      >
                                        {room.actionLabel}
                                      </button>
                                    )}

                                    {/* Three dots button */}
                                    <div
                                      className="relative"
                                      data-room-menu
                                    >
                                      <button
                                        type="button"
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          setOpenRoomMenuId((prev) =>
                                            prev === room.id ? null : room.id
                                          );
                                        }}
                                        className="size-8 rounded-full flex items-center justify-center hover:bg-slate-100 dark:hover:bg-slate-800 text-slate-500 hover:text-slate-900 transition cursor-pointer"
                                      >
                                        <MoreVertical className="size-4" />
                                      </button>

                                      {openRoomMenuId === room.id && (
                                        <div
                                          onClick={(e) => e.stopPropagation()}
                                          className="absolute right-0 top-9 z-30 w-44 bg-white dark:bg-slate-900 rounded-2xl border border-slate-200 dark:border-slate-800 shadow-xl py-1.5 animate-in fade-in zoom-in-95 duration-100"
                                        >
                                          <button
                                            type="button"
                                            onClick={() => {
                                              setOpenRoomMenuId(null);
                                              setRoomForm({
                                                propertyId: prop.id,
                                                id: room.id,
                                                size: String(room.size),
                                                hasEnsuite: room.hasEnsuite,
                                              });
                                            }}
                                            className="w-full text-left px-4 py-2 text-xs font-semibold text-[#0E2F73] dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800"
                                          >
                                            Edit room
                                          </button>
                                          {room.listing && (
                                            <Link
                                              href={`/account/host/listings`}
                                              className="block w-full text-left px-4 py-2 text-xs font-semibold text-[#0E2F73] dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800"
                                            >
                                              View Listing
                                            </Link>
                                          )}
                                          {room.occupancyId && (
                                            <button
                                              type="button"
                                              onClick={() => {
                                                setOpenRoomMenuId(null);
                                                setRecordOccupancyId(room.occupancyId ?? null);
                                              }}
                                              className="w-full text-left px-4 py-2 text-xs font-semibold text-[#0E2F73] dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800"
                                            >
                                              Rental Record
                                            </button>
                                          )}
                                        </div>
                                      )}
                                    </div>
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* 6. Footer Disclaimer from Figma Frame */}
      <div className="pt-2">
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Zoiko Rooms collects the Listing Fee only. Rent and deposits are handled between you and your renters and shown in Rental Records.
        </p>
      </div>

      {/* 7. Slide-out Inspection Drawer (Sidebar) matching zoiko-rooms-properties-rooms.html exactly */}
      {selectedRoomForDrawer && (
        <div className="fixed inset-0 z-50 flex justify-end">
          {/* Backdrop Scrim */}
          <div
            className="absolute inset-0 bg-slate-900/40 backdrop-blur-xs transition-opacity"
            onClick={() => setSelectedRoomForDrawer(null)}
          />

          {/* Drawer Panel */}
          <aside
            className="relative w-full max-w-[480px] h-full bg-white dark:bg-slate-900 shadow-2xl flex flex-col z-10 overflow-y-auto animate-in slide-in-from-right duration-200 p-4 sm:p-6 space-y-4"
            role="dialog"
            aria-modal="true"
          >
            {/* Header */}
            <div className="flex items-start justify-between gap-4 border-b border-slate-100 dark:border-slate-800 pb-4">
              <div className="space-y-1">
                <span className="text-[11px] font-bold text-[#D80B0B] uppercase tracking-wider font-heading">
                  Room details
                </span>
                <h2 className="text-xl sm:text-2xl font-extrabold text-[#0E2F73] dark:text-white font-heading">
                  {selectedRoomForDrawer.room.title} · {selectedRoomForDrawer.room.type}
                </h2>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {selectedRoomForDrawer.property.name} · Property #{selectedRoomForDrawer.property.code}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setSelectedRoomForDrawer(null)}
                className="size-9 rounded-full border border-slate-200 dark:border-slate-700 flex items-center justify-center text-slate-500 hover:text-slate-900 dark:hover:white transition cursor-pointer"
              >
                <X className="size-4" />
              </button>
            </div>

            {/* Room Photo Thumbnail */}
            <div className="h-36 rounded-2xl bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex flex-col items-center justify-center gap-2 text-slate-400 overflow-hidden">
              <img
                src={resolveImageUrl(selectedRoomForDrawer.room.image)}
                alt="Room"
                className="w-full h-full object-cover"
              />
            </div>

            {/* Status Card & Explanation */}
            <div className="p-4 rounded-2xl bg-[#F7F9FD] dark:bg-slate-800/50 border border-slate-200 dark:border-slate-800 space-y-2.5">
              <div className="flex items-center gap-2">
                {selectedRoomForDrawer.room.roomStatus === "UNDER_OFFER" && (
                  <span className="px-2.5 py-1 rounded-full bg-[#FFF4E0] text-[#93370D] border border-[#F2B866] text-xs font-extrabold uppercase">
                    Under Offer
                  </span>
                )}
                {selectedRoomForDrawer.room.roomStatus === "AVAILABLE" && (
                  <span className="px-2.5 py-1 rounded-full bg-[#E7F8EF] text-[#05603A] border border-[#8FD9B4] text-xs font-extrabold uppercase">
                    Available
                  </span>
                )}
                {selectedRoomForDrawer.room.roomStatus === "OCCUPIED" && (
                  <span className="px-2.5 py-1 rounded-full bg-[#0E2F73] text-white text-xs font-extrabold uppercase">
                    Occupied
                  </span>
                )}
                {selectedRoomForDrawer.room.roomStatus === "NOTICE_GIVEN" && (
                  <span className="px-2.5 py-1 rounded-full bg-[#EAF0FB] text-[#0E2F73] border border-[#0E2F73] text-xs font-extrabold uppercase">
                    Notice Given
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-600 dark:text-slate-300 leading-relaxed">
                {getRoomExplanation(selectedRoomForDrawer.room)}
              </p>
              {selectedRoomForDrawer.room.nextMilestone !== "—" && (
                <div className="text-xs font-bold text-[#0E2F73] dark:text-sky-300 pt-1 flex items-center gap-1.5">
                  <Calendar className="size-3.5 text-[#0E2F73]" />
                  <span>Next milestone: {selectedRoomForDrawer.room.nextMilestone}</span>
                </div>
              )}
            </div>

            {/* Four Separate Truths */}
            <div className="space-y-2">
              <h3 className="text-xs font-extrabold uppercase tracking-wider text-slate-600 dark:text-slate-400 font-heading">
                Four separate truths
              </h3>
              <div className="grid grid-cols-2 gap-2">
                <div className="p-3 rounded-xl border border-slate-200 dark:border-slate-800 space-y-1">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block font-heading">
                    Room status
                  </span>
                  <p className="text-xs font-extrabold text-[#0E2F73] dark:text-white">
                    {selectedRoomForDrawer.room.roomStatus === "AVAILABLE" ||
                    selectedRoomForDrawer.room.roomStatus === "UNDER_OFFER"
                      ? "Vacant"
                      : selectedRoomForDrawer.room.roomStatus === "NOTICE_GIVEN"
                      ? "Occupied (notice given)"
                      : "Occupied"}
                  </p>
                </div>

                <div className="p-3 rounded-xl border border-slate-200 dark:border-slate-800 space-y-1">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block font-heading">
                    Listing
                  </span>
                  <p className="text-xs font-extrabold text-[#0E2F73] dark:text-white">
                    {selectedRoomForDrawer.room.listingStatus === "LIVE" ? "Live" : "Paused"} ·{" "}
                    {selectedRoomForDrawer.room.listingStatusSubtext}
                  </p>
                </div>

                <div className="p-3 rounded-xl border border-slate-200 dark:border-slate-800 space-y-1">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block font-heading">
                    Transaction
                  </span>
                  <p className="text-xs font-extrabold text-[#0E2F73] dark:text-white">
                    {selectedRoomForDrawer.room.roomStatus === "UNDER_OFFER"
                      ? `Offer · ${selectedRoomForDrawer.room.currentRentalOrOffer}`
                      : selectedRoomForDrawer.room.roomStatus === "OCCUPIED"
                      ? "Active rental"
                      : selectedRoomForDrawer.room.roomStatus === "NOTICE_GIVEN"
                      ? "Active rental · notice accepted"
                      : "None"}
                  </p>
                </div>

                <div className="p-3 rounded-xl border border-slate-200 dark:border-slate-800 space-y-1">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block font-heading">
                    Verification
                  </span>
                  <p className="text-xs font-extrabold text-[#0E2F73] dark:text-white">
                    {selectedRoomForDrawer.property.propertyVerified
                      ? "Property & authority verified"
                      : "In review"}
                  </p>
                </div>
              </div>
            </div>

            {/* Current Offer / Rental Section */}
            {selectedRoomForDrawer.room.renterName && (
              <div className="space-y-2">
                <h3 className="text-xs font-extrabold uppercase tracking-wider text-slate-600 dark:text-slate-400 font-heading">
                  {selectedRoomForDrawer.room.roomStatus === "UNDER_OFFER"
                    ? "Current offer"
                    : "Current rental"}
                </h3>
                <div className="p-3 rounded-xl border border-slate-200 dark:border-slate-800 flex items-center gap-3">
                  <div className="size-10 rounded-full bg-[#EAF0FB] text-[#0E2F73] font-extrabold text-sm flex items-center justify-center shrink-0">
                    {selectedRoomForDrawer.room.renterName
                      .split(" ")
                      .map((w: string) => w[0])
                      .join("")}
                  </div>
                  <div>
                    <strong className="text-xs font-extrabold text-[#0E2F73] dark:text-white block">
                      {selectedRoomForDrawer.room.renterName}
                    </strong>
                    <span className="text-[11px] text-slate-500">
                      {selectedRoomForDrawer.room.currentRentalDate}
                    </span>
                  </div>
                </div>
              </div>
            )}

            {/* Occupancy Notice */}
            {(selectedRoomForDrawer.room.roomStatus === "OCCUPIED" ||
              selectedRoomForDrawer.room.roomStatus === "NOTICE_GIVEN") && (
              <div className="p-3.5 rounded-2xl bg-[#EAF0FB] dark:bg-slate-800 text-[#0E2F73] dark:text-sky-200 text-xs flex items-start gap-2.5">
                <Info className="size-4 shrink-0 text-[#0E2F73] mt-0.5" />
                <span>
                  This status is managed from the current rental record. Rent and deposits are recorded in Rental Records — Zoiko Rooms does not collect them.
                </span>
              </div>
            )}

            {/* Actions List */}
            <div className="space-y-2 pt-2 border-t border-slate-100 dark:border-slate-800">
              <h3 className="text-xs font-extrabold uppercase tracking-wider text-slate-600 dark:text-slate-400 font-heading">
                Actions
              </h3>
              <div className="flex flex-col gap-2">
                {selectedRoomForDrawer.room.roomStatus === "UNDER_OFFER" && (
                  <Link
                    href="/account/host/applications"
                    className="h-11 px-4 rounded-full bg-[#0E2F73] hover:bg-[#0a2358] text-white text-xs font-bold font-heading flex items-center justify-center gap-2 shadow-sm transition"
                  >
                    <span>Review Offer</span>
                    <ArrowRight className="size-4" />
                  </Link>
                )}

                {selectedRoomForDrawer.room.occupancyId &&
                  selectedRoomForDrawer.room.occupancyStatus === "PENDING_MOVE_IN" && (
                    <HostMoveInSteps
                      occupancyId={selectedRoomForDrawer.room.occupancyId}
                      showToast={showToast}
                      onMovedIn={() => {
                        setSelectedRoomForDrawer(null);
                        load();
                      }}
                    />
                  )}

                {selectedRoomForDrawer.room.occupancyId && (
                  <button
                    type="button"
                    onClick={() => {
                      const occId = selectedRoomForDrawer.room.occupancyId;
                      setSelectedRoomForDrawer(null);
                      setRecordOccupancyId(occId ?? null);
                    }}
                    className="h-11 px-4 rounded-full bg-[#0E2F73] hover:bg-[#0a2358] text-white text-xs font-bold font-heading flex items-center justify-center gap-2 shadow-sm transition cursor-pointer"
                  >
                    <span>View Rental Record</span>
                    <FileText className="size-4" />
                  </button>
                )}

                <button
                  type="button"
                  onClick={() => {
                    const roomData = selectedRoomForDrawer.room;
                    const propId = selectedRoomForDrawer.property.id;
                    setSelectedRoomForDrawer(null);
                    setRoomForm({
                      propertyId: propId,
                      id: roomData.id,
                      size: String(roomData.size),
                      hasEnsuite: roomData.hasEnsuite,
                    });
                  }}
                  className="h-11 px-4 rounded-full border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-[#0E2F73] dark:text-white text-xs font-bold font-heading flex items-center justify-center gap-2 transition cursor-pointer"
                >
                  <span>Edit room</span>
                </button>

                <Link
                  href="/account/host/applications"
                  className="h-11 px-4 rounded-full border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-[#0E2F73] dark:text-white text-xs font-bold font-heading flex items-center justify-center gap-2 transition"
                >
                  <span>Applications workspace</span>
                </Link>
              </div>
            </div>
          </aside>
        </div>
      )}

      {/* 8. Modals: Add / Edit Property (Real DB) */}
      <Modal
        open={Boolean(propertyForm)}
        onClose={() => setPropertyForm(null)}
        title={propertyForm?.id === null ? "Add a property" : "Edit property"}
      >
        <form onSubmit={handlePropertySubmit} className="space-y-4">
          {propertyForm && propertyForm.id !== null && (
            <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 ring-1 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-200">
              Changing the address or city means the property and your authority to list it must be verified again, and listings can&apos;t go live until they are.
            </p>
          )}
          <Field label="Address">
            <input
              value={propertyForm?.address ?? ""}
              onChange={(e) => setPropertyForm((f) => (f ? { ...f, address: e.target.value } : f))}
              placeholder="e.g. 2–599 Madupally, Road No. 10"
              className={inputClass}
            />
          </Field>
          <Field label="City">
            <input
              value={propertyForm?.city ?? ""}
              onChange={(e) => setPropertyForm((f) => (f ? { ...f, city: e.target.value } : f))}
              placeholder="Hyderabad"
              className={inputClass}
            />
          </Field>
          <Field
            label="Nearby landmark (optional)"
            hint="Only used as a backup if a property verification document doesn't clearly match the full address."
          >
            <input
              value={propertyForm?.landmark ?? ""}
              onChange={(e) => setPropertyForm((f) => (f ? { ...f, landmark: e.target.value } : f))}
              placeholder="Near Banjara Hills"
              className={inputClass}
            />
          </Field>
          <RegionSelect
            regions={regions}
            value={propertyForm?.jurisdictionCode ?? ""}
            onChange={(code) => setPropertyForm((f) => (f ? { ...f, jurisdictionCode: code } : f))}
            currentCode={propertyForm?.savedJurisdictionCode}
            locked={propertyForm?.regionLocked}
          />

          {error && (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-xs font-medium text-red-700 ring-1 ring-red-200">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={() => setPropertyForm(null)}>
              Cancel
            </Button>
            <Button type="submit" loading={submitting}>
              Save property
            </Button>
          </div>
        </form>
      </Modal>

      {/* 9. Modals: Add / Edit Room (Real DB) */}
      <Modal
        open={Boolean(roomForm)}
        onClose={() => setRoomForm(null)}
        title={roomForm?.id === null ? "Add a room" : "Edit room"}
      >
        <form onSubmit={handleRoomSubmit} className="space-y-4">
          <p className="rounded-xl bg-slate-50 dark:bg-slate-800/60 px-4 py-3 text-xs text-slate-500 dark:text-slate-400">
            Zoiko only hosts private rooms for 30+ night stays, so every room is created as a private room.
          </p>

          <Field label="Size (sq ft)">
            <input
              inputMode="numeric"
              value={roomForm?.size ?? ""}
              onChange={(e) => setRoomForm((f) => (f ? { ...f, size: e.target.value } : f))}
              placeholder="180"
              className={inputClass}
            />
          </Field>

          <div className="flex items-center justify-between rounded-xl bg-slate-50 dark:bg-slate-800/60 px-4 py-3">
            <span className="text-sm font-medium text-slate-600 dark:text-slate-300">
              Has a private en-suite
            </span>
            <Switch
              checked={roomForm?.hasEnsuite ?? false}
              onChange={(checked) => setRoomForm((f) => (f ? { ...f, hasEnsuite: checked } : f))}
            />
          </div>

          {error && (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-xs font-medium text-red-700 ring-1 ring-red-200">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={() => setRoomForm(null)}>
              Cancel
            </Button>
            <Button type="submit" loading={submitting}>
              Save room
            </Button>
          </div>
        </form>
      </Modal>

      {/* 10. Wizard: List a Room (UNTOUCHED) */}
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

      {/* 11. Rental Transaction Record Modal */}
      <Modal
        open={recordOccupancyId !== null}
        onClose={() => setRecordOccupancyId(null)}
        title="Rental transaction record"
        size="xl"
      >
        {recordOccupancyId !== null && (
          <RentalTransactionRecord occupancyId={recordOccupancyId} role="host" />
        )}
      </Modal>

      {/* 12. Property Verification Modal */}
      {verifyProperty && (
        <Modal
          open
          onClose={() => {
            setVerifyProperty(null);
            void load();
          }}
          title="Property verification"
          size="xl"
        >
          <PropertyVerificationWizard
            propertyId={verifyProperty.id}
            propertyLabel={verifyProperty.label}
            onClose={() => {
              setVerifyProperty(null);
              void load();
            }}
            onContinueToAuthority={() => {
              setAuthorityProperty({ id: verifyProperty.id, label: verifyProperty.label });
              setVerifyProperty(null);
            }}
          />
        </Modal>
      )}

      {/* 13. Authority Verification Modal */}
      {authorityProperty && (
        <Modal
          open
          onClose={() => {
            setAuthorityProperty(null);
            void load();
          }}
          title="Authority to list"
          size="xl"
        >
          <AuthorityVerificationWizard
            propertyId={authorityProperty.id}
            propertyLabel={authorityProperty.label}
            onClose={() => {
              setAuthorityProperty(null);
              void load();
            }}
          />
        </Modal>
      )}

      <Toast toast={toast} />
    </div>
  );
}
