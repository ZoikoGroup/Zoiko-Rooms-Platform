"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  BedDouble,
  Building2,
  Calendar,
  Check,
  ChevronDown,
  ChevronUp,
  Clock,
  DoorOpen,
  Home,
  Key,
  LayoutGrid,
  Plus,
  Search,
  ShieldCheck,
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
import { RentalTransactionRecord } from "@/components/user/RentalTransactionRecord";
import { useUserSession } from "@/components/user/UserSessionContext";
import { Field, Toast, inputClass, useToast } from "@/components/user/ui";
import { formatDate } from "@/lib/utils";

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
        if (currentOcc) {
          currentRentalOrOffer = currentOcc.guestName || "Renter";
          if (currentOcc.moveInDate && currentOcc.expectedEndDate) {
            currentRentalDate = `${formatDate(currentOcc.moveInDate)} – ${formatDate(currentOcc.expectedEndDate)}`;
          } else if (currentOcc.moveInDate) {
            currentRentalDate = `Since ${formatDate(currentOcc.moveInDate)}`;
          }
        } else if (appWithOffer) {
          currentRentalOrOffer = appWithOffer.guestName;
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
          nextMilestone,
          nextMilestoneTone,
          actionLabel,
          actionPrimary,
          image,
          occupancyId: currentOcc?.id,
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
  }, [properties, roomsByProperty, occupanciesByRoom, listings, applications, verificationByProperty, authorityByProperty]);

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
  }, [mappedProperties, searchQuery, roomStatusFilter, propertyFilter, listingStatusFilter, verificationFilter, sortOption]);

  const totalFilteredRooms = useMemo(() => {
    return filteredProperties.reduce((sum, p) => sum + p.rooms.length, 0);
  }, [filteredProperties]);

  // Toggle property accordion collapse
  function togglePropertyCollapse(id: number) {
    setCollapsedProperties((prev) => ({
      ...prev,
      [id]: !prev[id],
    }));
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

  if (loading) {
    return <Loader label="Loading properties and rooms..." />;
  }

  return (
    <div className="w-full space-y-5 font-sans">
      {/* 1. Page Header Section matching Figma Frame */}
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 pt-1">
        <div className="space-y-1.5">
          <p className="text-[11px] font-bold text-[#D80B0B] dark:text-red-400 uppercase tracking-widest font-heading">
            Host workspace
          </p>
          <h1 className="text-3xl sm:text-4xl font-extrabold text-[#0C2B4E] dark:text-white leading-tight font-heading">
            Properties &amp; Rooms
          </h1>
          <p className="text-sm sm:text-base text-slate-500 dark:text-slate-400 font-normal max-w-3xl">
            Manage your properties, rooms, availability, listings, applications, agreements and occupancy.
          </p>
        </div>

        {/* Action Buttons top right */}
        <div className="flex items-center gap-2.5 shrink-0 flex-wrap">
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
            className="h-12 px-6 rounded-2xl bg-white dark:bg-slate-900 border border-[#B8C9DC] dark:border-slate-700 text-[#0C2B4E] dark:text-white font-bold text-sm sm:text-base hover:bg-slate-50 dark:hover:bg-slate-800/80 shadow-2xs transition flex items-center justify-center gap-2 cursor-pointer"
          >
            <Plus className="size-4 text-[#0C2B4E] dark:text-white" />
            <span>Add Property</span>
          </button>

          <button
            onClick={() => setWizardOpen(true)}
            className="h-12 px-6 rounded-2xl bg-[#0C2B4E] hover:bg-[#09223e] text-white font-bold text-sm sm:text-base shadow-[0px_10px_24px_0px_rgba(14,47,115,0.22)] transition flex items-center justify-center gap-2 cursor-pointer"
          >
            <Plus className="size-4 text-white" />
            <span>List a Room</span>
          </button>
        </div>
      </div>

      {/* 2. Five KPI Metric Cards from Figma Frame */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3.5 pt-1">
        {/* Card 1: Total Rooms */}
        <div className="bg-white dark:bg-slate-900 rounded-[20px] border border-slate-200 dark:border-slate-800 p-4 sm:p-5 flex flex-col justify-between shadow-2xs">
          <div className="flex items-center gap-3">
            <div className="size-9 rounded-xl bg-[#F1F5F9] dark:bg-slate-800 flex items-center justify-center text-[#0C2B4E] dark:text-slate-200 shrink-0">
              <LayoutGrid className="size-4" />
            </div>
            <span className="text-3xl font-extrabold text-[#0C2B4E] dark:text-white font-heading leading-none">
              {metrics.totalRooms}
            </span>
          </div>
          <div className="mt-3.5 space-y-1">
            <p className="text-sm font-extrabold text-[#0C2B4E] dark:text-white font-heading leading-tight">
              Total Rooms
            </p>
            <p className="text-xs text-slate-500 dark:text-slate-400 leading-normal">
              {metrics.availableCount} Available · {metrics.underOfferCount} Under Offer · {metrics.occupiedCount} Occupied · {metrics.notMarketedCount} Not Marketed
            </p>
          </div>
        </div>

        {/* Card 2: Available */}
        <div className="bg-white dark:bg-slate-900 rounded-[20px] border border-slate-200 dark:border-slate-800 p-4 sm:p-5 flex flex-col justify-between shadow-2xs">
          <div className="flex items-center gap-3">
            <div className="size-9 rounded-xl bg-[#E8F8F0] dark:bg-emerald-950/40 flex items-center justify-center text-emerald-600 shrink-0">
              <Check className="size-4" />
            </div>
            <span className="text-3xl font-extrabold text-[#0C2B4E] dark:text-white font-heading leading-none">
              {metrics.availableCount}
            </span>
          </div>
          <div className="mt-3.5 space-y-1">
            <p className="text-sm font-extrabold text-[#0C2B4E] dark:text-white font-heading leading-tight">
              Available
            </p>
            <p className="text-xs text-slate-500 dark:text-slate-400">Ready to rent</p>
          </div>
        </div>

        {/* Card 3: Under Offer */}
        <div className="bg-white dark:bg-slate-900 rounded-[20px] border border-slate-200 dark:border-slate-800 p-4 sm:p-5 flex flex-col justify-between shadow-2xs">
          <div className="flex items-center gap-3">
            <div className="size-9 rounded-xl bg-[#FFF6EA] dark:bg-amber-950/40 flex items-center justify-center text-amber-600 shrink-0">
              <Clock className="size-4" />
            </div>
            <span className="text-3xl font-extrabold text-[#0C2B4E] dark:text-white font-heading leading-none">
              {metrics.underOfferCount}
            </span>
          </div>
          <div className="mt-3.5 space-y-1">
            <p className="text-sm font-extrabold text-[#0C2B4E] dark:text-white font-heading leading-tight">
              Under Offer
            </p>
            <p className="text-xs text-slate-500 dark:text-slate-400">Awaiting decision</p>
          </div>
        </div>

        {/* Card 4: Occupied */}
        <div className="bg-white dark:bg-slate-900 rounded-[20px] border border-slate-200 dark:border-slate-800 p-4 sm:p-5 flex flex-col justify-between shadow-2xs">
          <div className="flex items-center gap-3">
            <div className="size-9 rounded-xl bg-[#F1F5F9] dark:bg-slate-800 flex items-center justify-center text-[#0C2B4E] dark:text-slate-200 shrink-0">
              <DoorOpen className="size-4" />
            </div>
            <span className="text-3xl font-extrabold text-[#0C2B4E] dark:text-white font-heading leading-none">
              {metrics.occupiedCount}
            </span>
          </div>
          <div className="mt-3.5 space-y-1">
            <p className="text-sm font-extrabold text-[#0C2B4E] dark:text-white font-heading leading-tight">
              Occupied
            </p>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              {metrics.occupiedCount === 1 ? "1 currently active" : `${metrics.occupiedCount} currently active`}
            </p>
          </div>
        </div>

        {/* Card 5: Action Required */}
        <div className="bg-white dark:bg-slate-900 rounded-[20px] border border-slate-200 dark:border-slate-800 p-4 sm:p-5 flex flex-col justify-between shadow-2xs col-span-2 sm:col-span-1">
          <div className="flex items-center gap-3">
            <div className="size-9 rounded-xl bg-[#FEECEC] dark:bg-red-950/40 flex items-center justify-center text-red-600 shrink-0">
              <AlertTriangle className="size-4" />
            </div>
            <span className="text-3xl font-extrabold text-[#D80B0B] dark:text-red-400 font-heading leading-none">
              {metrics.actionRequiredCount}
            </span>
          </div>
          <div className="mt-3.5 space-y-1">
            <p className="text-sm font-extrabold text-[#0C2B4E] dark:text-white font-heading leading-tight">
              Action Required
            </p>
            <p className="text-xs text-slate-500 dark:text-slate-400">Needs your attention</p>
          </div>
        </div>
      </div>

      {/* 3. Urgent Attention Alert Banner (Rendered dynamically when action required) */}
      {metrics.actionRequiredCount > 0 && (
        <div className="rounded-2xl border border-red-200/90 dark:border-red-900/40 bg-[#FFF5F5] dark:bg-red-950/20 p-3.5 sm:p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-2xs">
          <div className="flex items-start sm:items-center gap-3">
            <div className="size-8 rounded-lg bg-red-100 dark:bg-red-900/50 flex items-center justify-center text-red-600 shrink-0 mt-0.5 sm:mt-0">
              <AlertTriangle className="size-4" />
            </div>
            <div className="space-y-0.5">
              <div className="flex items-center gap-2">
                <span className="text-sm font-bold text-red-600 dark:text-red-400 font-heading">
                  {metrics.actionRequiredCount} {metrics.actionRequiredCount === 1 ? "item needs" : "items need"} your attention
                </span>
                <span className="text-slate-300 dark:text-slate-600">•</span>
                <span className="text-sm font-bold text-[#0C2B4E] dark:text-white font-heading">
                  Pending applicant offers awaiting decision
                </span>
              </div>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Review applicant terms and finalize agreements before deadlines expire.
              </p>
            </div>
          </div>
          <Link
            href="/account/host/applications"
            className="h-11 px-4 rounded-xl bg-[#0C2B4E] hover:bg-[#09223e] text-white text-xs font-bold font-heading shadow-sm transition flex items-center justify-center shrink-0"
          >
            Review Offers
          </Link>
        </div>
      )}

      {/* 4. Filter & Search Bar matching Figma Frame */}
      <div className="w-full bg-white dark:bg-slate-900 rounded-[20px] border border-slate-200 dark:border-slate-800 p-4 sm:p-5 shadow-2xs space-y-3">
        <div className="flex flex-col lg:flex-row lg:items-end gap-3.5">
          {/* Search Box */}
          <div className="flex-1 min-w-[240px]">
            <div className="relative">
              <Search className="size-4 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search properties, rooms or addresses…"
                className="w-full h-11 pl-10 pr-4 bg-[#F1F5F9] dark:bg-slate-800 border border-slate-200/90 dark:border-slate-700 rounded-2xl text-xs text-[#0C2B4E] dark:text-slate-200 placeholder:text-slate-400 outline-none focus:ring-2 focus:ring-primary-500 font-normal"
              />
            </div>
          </div>

          {/* 1. Room Status Dropdown */}
          <div className="flex-1 min-w-[130px] flex flex-col gap-1">
            <span className="text-xs font-bold text-[#0C2B4E] dark:text-slate-300 font-heading">
              Room Status
            </span>
            <div className="relative">
              <select
                value={roomStatusFilter}
                onChange={(e) => setRoomStatusFilter(e.target.value)}
                className="w-full h-11 px-3.5 pr-8 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-2xl text-xs text-[#0C2B4E] dark:text-slate-200 outline-none appearance-none cursor-pointer focus:ring-2 focus:ring-primary-500 font-normal"
              >
                <option value="ALL">All statuses</option>
                <option value="AVAILABLE">Available</option>
                <option value="UNDER_OFFER">Under Offer</option>
                <option value="OCCUPIED">Occupied</option>
                <option value="NOTICE_GIVEN">Notice Given</option>
              </select>
              <ChevronDown className="size-3.5 text-slate-400 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            </div>
          </div>

          {/* 2. Property Dropdown */}
          <div className="flex-1 min-w-[130px] flex flex-col gap-1">
            <span className="text-xs font-bold text-[#0C2B4E] dark:text-slate-300 font-heading">
              Property
            </span>
            <div className="relative">
              <select
                value={propertyFilter}
                onChange={(e) => setPropertyFilter(e.target.value)}
                className="w-full h-11 px-3.5 pr-8 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-2xl text-xs text-[#0C2B4E] dark:text-slate-200 outline-none appearance-none cursor-pointer focus:ring-2 focus:ring-primary-500 font-normal"
              >
                <option value="ALL">All properties</option>
                {mappedProperties.map((p) => (
                  <option key={p.id} value={p.name}>
                    {p.name}
                  </option>
                ))}
              </select>
              <ChevronDown className="size-3.5 text-slate-400 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            </div>
          </div>

          {/* 3. Listing Status Dropdown */}
          <div className="flex-1 min-w-[130px] flex flex-col gap-1">
            <span className="text-xs font-bold text-[#0C2B4E] dark:text-slate-300 font-heading">
              Listing Status
            </span>
            <div className="relative">
              <select
                value={listingStatusFilter}
                onChange={(e) => setListingStatusFilter(e.target.value)}
                className="w-full h-11 px-3.5 pr-8 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-2xl text-xs text-[#0C2B4E] dark:text-slate-200 outline-none appearance-none cursor-pointer focus:ring-2 focus:ring-primary-500 font-normal"
              >
                <option value="ALL">Any listing</option>
                <option value="LIVE">Live</option>
                <option value="PAUSED">Paused</option>
              </select>
              <ChevronDown className="size-3.5 text-slate-400 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            </div>
          </div>

          {/* 4. Verification Status Dropdown */}
          <div className="flex-1 min-w-[130px] flex flex-col gap-1">
            <span className="text-xs font-bold text-[#0C2B4E] dark:text-slate-300 font-heading">
              Verification Status
            </span>
            <div className="relative">
              <select
                value={verificationFilter}
                onChange={(e) => setVerificationFilter(e.target.value)}
                className="w-full h-11 px-3.5 pr-8 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-2xl text-xs text-[#0C2B4E] dark:text-slate-200 outline-none appearance-none cursor-pointer focus:ring-2 focus:ring-primary-500 font-normal"
              >
                <option value="ALL">Any</option>
                <option value="VERIFIED">Verified</option>
                <option value="IN_REVIEW">In Review</option>
              </select>
              <ChevronDown className="size-3.5 text-slate-400 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            </div>
          </div>

          {/* 5. Sort Dropdown */}
          <div className="flex-1 min-w-[130px] flex flex-col gap-1">
            <span className="text-xs font-bold text-[#0C2B4E] dark:text-slate-300 font-heading">
              Sort
            </span>
            <div className="relative">
              <select
                value={sortOption}
                onChange={(e) => setSortOption(e.target.value)}
                className="w-full h-11 px-3.5 pr-8 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-2xl text-xs text-[#0C2B4E] dark:text-slate-200 outline-none appearance-none cursor-pointer focus:ring-2 focus:ring-primary-500 font-normal"
              >
                <option value="NEXT_ACTION">Next action first</option>
                <option value="NAME">Property name</option>
                <option value="MOST_ROOMS">Most rooms</option>
              </select>
              <ChevronDown className="size-3.5 text-slate-400 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            </div>
          </div>
        </div>

        {/* Counter bottom */}
        <p className="text-xs text-slate-500 dark:text-slate-400 pt-1">
          Showing {totalFilteredRooms} of {metrics.totalRooms} rooms
        </p>
      </div>

      {/* 5. Property Cards (Accordions) from Figma Frame */}
      {filteredProperties.length === 0 ? (
        /* Empty State Card matching Figma Frame */
        <div className="p-12 text-center bg-white dark:bg-slate-900 rounded-3xl border border-slate-200 dark:border-slate-800 flex flex-col items-center justify-center gap-4">
          <div className="size-16 rounded-3xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-slate-400 shadow-inner">
            <Home className="size-8 text-[#0C2B4E] dark:text-white" />
          </div>
          <div className="space-y-1">
            <h3 className="text-slate-900 dark:text-white font-extrabold text-lg font-heading">
              No properties yet
            </h3>
            <p className="text-slate-500 dark:text-slate-400 text-xs max-w-md mx-auto">
              Add a property or use List a Room to begin.
            </p>
          </div>
          <div className="flex items-center gap-2.5 mt-2">
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
              className="px-4 py-2.5 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 text-[#0C2B4E] dark:text-white text-xs font-bold font-heading rounded-xl shadow-2xs hover:bg-slate-50 transition cursor-pointer"
            >
              Add Property
            </button>
            <button
              onClick={() => setWizardOpen(true)}
              className="px-4 py-2.5 bg-[#0C2B4E] text-white text-xs font-bold font-heading rounded-xl shadow-sm hover:bg-[#09223e] transition cursor-pointer"
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
            const occupiedCount = prop.rooms.filter((r) => r.roomStatus === "OCCUPIED" || r.roomStatus === "NOTICE_GIVEN").length;
            const underOfferCount = prop.rooms.filter((r) => r.roomStatus === "UNDER_OFFER").length;
            const notMarketedCount = prop.rooms.filter((r) => r.listingStatusSubtext.toLowerCase().includes("not marketed")).length;

            return (
              <div
                key={prop.id}
                className="bg-white dark:bg-slate-900 rounded-3xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden"
              >
                {/* Property Accordion Header */}
                <div className="px-5 py-4 bg-[#F8FAFC] dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-800 flex flex-col md:flex-row md:items-start justify-between gap-3.5">
                  <div className="flex items-start gap-3.5 flex-1 min-w-0">
                    {/* Accordion Expand/Collapse button */}
                    <button
                      type="button"
                      onClick={() => togglePropertyCollapse(prop.id)}
                      className="size-8 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 flex items-center justify-center text-slate-500 hover:text-slate-900 dark:hover:white transition cursor-pointer mt-1 shrink-0"
                      title={isCollapsed ? "Expand property" : "Collapse property"}
                    >
                      {isCollapsed ? <ChevronDown className="size-4" /> : <ChevronUp className="size-4" />}
                    </button>

                    {/* Property icon */}
                    <div className="size-10 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center text-[#0C2B4E] dark:text-white shrink-0 mt-0.5">
                      <Building2 className="size-5" />
                    </div>

                    {/* Property details */}
                    <div className="space-y-1 flex-1 min-w-0">
                      <div className="flex items-baseline gap-2.5 flex-wrap">
                        <h2 className="text-lg font-extrabold text-[#0C2B4E] dark:text-white font-heading">
                          {prop.name}
                        </h2>
                        <span className="text-xs font-semibold text-slate-400">
                          Property #{prop.code}
                        </span>
                      </div>
                      <p className="text-xs text-slate-500 dark:text-slate-400 truncate">
                        {prop.address}
                      </p>

                      {/* Verified pills + Room Counts */}
                      <div className="flex items-center gap-2 pt-1 flex-wrap">
                        {prop.propertyVerified ? (
                          <div className="px-2.5 py-1 bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-400 rounded-full text-xs font-bold flex items-center gap-1.5 border border-emerald-200/60 dark:border-emerald-800/40">
                            <ShieldCheck className="size-3 text-emerald-600" />
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
                            className="px-2.5 py-1 bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-300 rounded-full text-xs font-bold flex items-center gap-1.5 border border-slate-200 dark:border-slate-700 cursor-pointer transition"
                          >
                            <Clock className="size-3 text-slate-500" />
                            <span>Property verification In Review</span>
                          </button>
                        )}

                        {prop.authorityVerified ? (
                          <div className="px-2.5 py-1 bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-400 rounded-full text-xs font-bold flex items-center gap-1.5 border border-emerald-200/60 dark:border-emerald-800/40">
                            <ShieldCheck className="size-3 text-emerald-600" />
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
                            className="px-2.5 py-1 bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-300 rounded-full text-xs font-bold flex items-center gap-1.5 border border-slate-200 dark:border-slate-700 cursor-pointer transition"
                          >
                            <Clock className="size-3 text-slate-500" />
                            <span>Listing authority In Review</span>
                          </button>
                        )}

                        <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">
                          {prop.rooms.length} {prop.rooms.length === 1 ? "room" : "rooms"}
                          {prop.rooms.length > 0 &&
                            ` · ${availableCount} Available · ${occupiedCount} Occupied · ${underOfferCount} Under Offer · ${notMarketedCount} Not Marketed`}
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Header Actions */}
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      onClick={() =>
                        setRoomForm({
                          propertyId: prop.id,
                          id: null,
                          size: "",
                          hasEnsuite: false,
                        })
                      }
                      className="h-11 px-3.5 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-[#0C2B4E] dark:text-white text-xs font-bold font-heading rounded-xl shadow-2xs transition flex items-center gap-1.5 cursor-pointer"
                    >
                      <Plus className="size-3.5 text-[#0C2B4E] dark:text-white" />
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
                      className="h-11 px-3.5 bg-[#F1F5F9] dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-[#0C2B4E] dark:text-white text-xs font-bold font-heading rounded-xl transition cursor-pointer"
                    >
                      Manage Property
                    </button>
                  </div>
                </div>

                {/* Property Accordion Body */}
                {!isCollapsed && (
                  <div className="w-full">
                    {/* In Review Warning Banner (Shown if verification is still pending) */}
                    {prop.isInReview && (
                      <div className="m-5 px-3.5 py-3 bg-blue-50 dark:bg-blue-950/30 text-[#0C2B4E] dark:text-sky-200 border border-blue-200/80 dark:border-blue-900/50 rounded-2xl text-sm font-semibold flex items-center gap-2.5">
                        <Clock className="size-4 shrink-0 text-blue-600 dark:text-blue-400" />
                        <span>
                          Property verification is in review. Adding rooms is available; publishing listings is blocked until verification completes.
                        </span>
                      </div>
                    )}

                    {/* No rooms added yet (Shown when 0 rooms in property) */}
                    {prop.rooms.length === 0 ? (
                      <div className="m-5 p-4 rounded-2xl border border-slate-200 dark:border-slate-800 flex flex-col sm:flex-row sm:items-center justify-between gap-3.5">
                        <div className="flex items-center gap-3">
                          <div className="size-10 rounded-xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-slate-400 shrink-0">
                            <BedDouble className="size-5 text-slate-500" />
                          </div>
                          <div>
                            <p className="text-base font-bold text-[#0C2B4E] dark:text-white font-heading">
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
                          className="h-11 px-4 rounded-xl bg-[#0C2B4E] hover:bg-[#09223e] text-white text-xs font-bold font-heading transition flex items-center justify-center gap-1.5 shrink-0 cursor-pointer"
                        >
                          <Plus className="size-3.5" />
                          <span>Add Room</span>
                        </button>
                      </div>
                    ) : (
                      /* Table of Real Rooms matching Figma design */
                      <div className="w-full overflow-x-auto">
                        {/* Table Header Row */}
                        <div className="min-w-[860px] px-5 py-2.5 border-b border-slate-100 dark:border-slate-800 flex items-center text-[11px] font-bold text-slate-400 font-heading uppercase tracking-wider">
                          <div className="w-48 shrink-0">Room</div>
                          <div className="w-36 shrink-0">Room status</div>
                          <div className="w-48 shrink-0">Listing status</div>
                          <div className="w-48 shrink-0">Current rental / offer</div>
                          <div className="w-44 shrink-0">Next milestone</div>
                          <div className="flex-1 text-right">Action</div>
                        </div>

                        {/* Room Rows */}
                        <div className="divide-y divide-slate-100 dark:divide-slate-800 min-w-[860px]">
                          {prop.rooms.map((room) => {
                            return (
                              <div
                                key={room.id}
                                className="px-5 py-3.5 flex items-center hover:bg-slate-50/60 dark:hover:bg-slate-800/30 transition"
                              >
                                {/* 1. Room info + thumbnail */}
                                <div className="w-48 shrink-0 flex items-center gap-3">
                                  <img
                                    src={room.image}
                                    alt={room.title}
                                    className="w-12 h-11 rounded-xl object-cover bg-slate-100 shrink-0 border border-slate-200/60 dark:border-slate-700"
                                  />
                                  <div className="space-y-0.5 min-w-0">
                                    <p className="text-base font-extrabold text-[#0C2B4E] dark:text-white font-heading truncate">
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
                                    <div className="px-2.5 py-1 rounded-full bg-[#FFF6EA] dark:bg-amber-950/40 text-amber-700 dark:text-amber-400 border border-amber-200/80 text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5 w-fit">
                                      <Clock className="size-3 text-amber-600" />
                                      <span>Under Offer</span>
                                    </div>
                                  )}
                                  {room.roomStatus === "AVAILABLE" && (
                                    <div className="px-2.5 py-1 rounded-full bg-[#E8F8F0] dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-400 border border-emerald-200/80 text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5 w-fit">
                                      <Check className="size-3 text-emerald-600" />
                                      <span>Available</span>
                                    </div>
                                  )}
                                  {room.roomStatus === "NOTICE_GIVEN" && (
                                    <div className="px-2.5 py-1 rounded-full bg-[#F1F5F9] dark:bg-sky-950/40 text-[#0C2B4E] dark:text-sky-300 border border-slate-200 text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5 w-fit">
                                      <Clock className="size-3 text-[#0C2B4E]" />
                                      <span>Notice Given</span>
                                    </div>
                                  )}
                                  {room.roomStatus === "OCCUPIED" && (
                                    <div className="px-2.5 py-1 rounded-full bg-[#0C2B4E] text-white text-[11px] font-extrabold uppercase tracking-wide flex items-center gap-1.5 w-fit">
                                      <Key className="size-3 text-white" />
                                      <span>Occupied</span>
                                    </div>
                                  )}
                                </div>

                                {/* 3. Listing Status */}
                                <div className="w-48 shrink-0 space-y-0.5">
                                  <div className="flex items-center gap-1.5">
                                    <span
                                      className={`size-2 rounded-full ${
                                        room.listingStatus === "LIVE" ? "bg-emerald-500" : "bg-slate-400"
                                      }`}
                                    />
                                    <span
                                      className={`text-sm font-bold font-heading ${
                                        room.listingStatus === "LIVE"
                                          ? "text-emerald-700 dark:text-emerald-400"
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
                                <div className="w-48 shrink-0 space-y-0.5">
                                  {room.currentRentalOrOffer === "—" ? (
                                    <span className="text-slate-400">—</span>
                                  ) : (
                                    <>
                                      <p className="text-sm font-bold text-[#0C2B4E] dark:text-white font-heading">
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
                                <div className="w-44 shrink-0">
                                  {room.nextMilestone === "—" ? (
                                    <span className="text-slate-400">—</span>
                                  ) : (
                                    <div className="flex items-center gap-1.5">
                                      {room.nextMilestoneTone === "urgent" && (
                                        <AlertTriangle className="size-3.5 text-red-600 shrink-0" />
                                      )}
                                      {room.nextMilestoneTone === "positive" && (
                                        <Calendar className="size-3.5 text-emerald-600 shrink-0" />
                                      )}
                                      <span
                                        className={`text-xs font-semibold ${
                                          room.nextMilestoneTone === "urgent"
                                            ? "text-red-600 dark:text-red-400 font-bold"
                                            : room.nextMilestoneTone === "positive"
                                            ? "text-emerald-700 dark:text-emerald-400 font-bold"
                                            : "text-slate-600 dark:text-slate-300"
                                        }`}
                                      >
                                        {room.nextMilestone}
                                      </span>
                                    </div>
                                  )}
                                </div>

                                {/* 6. Action Button */}
                                <div className="flex-1 text-right">
                                  {room.actionPrimary ? (
                                    <Link
                                      href="/account/host/applications"
                                      className="inline-flex items-center justify-center h-10 px-4 bg-[#0C2B4E] hover:bg-[#09223e] text-white text-xs font-bold font-heading rounded-xl shadow-xs transition"
                                    >
                                      {room.actionLabel}
                                    </Link>
                                  ) : (
                                    <button
                                      onClick={() => {
                                        if (room.actionLabel === "View Rental" && room.occupancyId) {
                                          setRecordOccupancyId(room.occupancyId);
                                        } else if (room.actionLabel === "Resume Listing") {
                                          showToast(`Manage listing for ${room.title} in My Listings`);
                                        } else {
                                          // Manage Room
                                          setRoomForm({
                                            propertyId: prop.id,
                                            id: room.id,
                                            size: String(room.size),
                                            hasEnsuite: room.hasEnsuite,
                                          });
                                        }
                                      }}
                                      className="h-10 px-4 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-[#0C2B4E] dark:text-white text-xs font-bold font-heading rounded-xl shadow-2xs transition cursor-pointer"
                                    >
                                      {room.actionLabel}
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

      {/* 7. Modals: Add / Edit Property (Real DB) */}
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

      {/* 8. Modals: Add / Edit Room (Real DB) */}
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

      {/* 10. Rental Transaction Record Modal */}
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

      {/* 11. Property Verification Modal */}
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

      {/* 12. Authority Verification Modal */}
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
