"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listingsWithOpenApplication } from "@/lib/applications";
import { createPortal } from "react-dom";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  Bookmark,
  Calendar,
  Check,
  Clock,
  FileText,
  Heart,
  Info,
  Lock,
  Map as MapIcon,
  MapPin,
  MessageSquare,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Star,
  X,
} from "lucide-react";
import { PublicListing, HostedListing } from "@/lib/types";
import { formatCurrency, resolveImageUrl, todayIsoDate } from "@/lib/utils";
import { applyFiltersToParams, filtersFromParams } from "@/lib/find-a-room-filters";
import {
  availabilityDate,
  deriveAccessibility,
  deriveFurnishedState,
  convertAmount,
  dominantCurrency,
  priceStepsFor,
  roomMatchesSearch,
} from "@/lib/find-a-room-search";
import {
  errorMessage,
  listHostedListings,
  getExchangeRates,
  listAllPublicListings,
  listRentalApplications,
  getUserPreferences,
  submitRentalApplication,
} from "@/lib/user-api";
import { useUserSession } from "@/components/user/UserSessionContext";
import { ListingReviews } from "@/components/user/ListingReviews";
import { RoundedFilterDropdown } from "@/components/ui/RoundedFilterDropdown";

/** Room representation dynamically mapped from backend listings */
export interface FindARoomItem {
  id: string;
  listingId: string;
  isBackendListing: boolean;
  title: string;
  area: string;
  city: string;
  roomNumber: string;
  roomType: string;
  monthlyRent: number;
  currency: string;
  minStayMonths: number;
  availableDate: string;
  roomState: "available" | "offer";
  applicationsState: "open" | "closed";
  furnishedState: "yes" | "part" | "no" | "unknown";
  bathroomType: "Private" | "Shared";
  amenities: string[];
  accessibility: string[];
  photos: string[];
  toneColor: string;
  freshnessScore: number;
  description?: string;
  houseRules?: string[];
  ownerName?: string;
  /** Average star rating and number of reviews from renters who have rented this room. */
  rating?: number;
  reviewCount?: number;
}

const DEFAULT_ROOM_PHOTOS = [
  "https://images.unsplash.com/photo-1522771739844-6a9f6d5f14af?auto=format&fit=crop&w=700&q=80",
  "https://images.unsplash.com/photo-1598928506311-c55ded91a20c?auto=format&fit=crop&w=700&q=80",
  "https://images.unsplash.com/photo-1505693416388-ac5ce068fe85?auto=format&fit=crop&w=700&q=80",
];

function mapListingToFindARoomItem(item: PublicListing | HostedListing, idx: number): FindARoomItem {
  const resolvedPhotos =
    item.images && item.images.length > 0
      ? (item.images.map(resolveImageUrl).filter(Boolean) as string[])
      : [];
  const photos = resolvedPhotos.length > 0 ? resolvedPhotos : DEFAULT_ROOM_PHOTOS;

  // Owner-facing fields only exist on HostedListing; PublicListing lacks them.
  const hosted = item as Partial<HostedListing>;
  const monthlyRent =
    hosted.defaultMonthlyRent != null && Number(hosted.defaultMonthlyRent) > 0
      ? Math.round(Number(hosted.defaultMonthlyRent))
      : Math.round((item.pricePerNight || 0) * 30);

  const currency = item.currency || "INR";
  const minStayMonths = Math.max(1, Math.round((item.minStayNights || 30) / 30));
  const bathroomType = item.bathrooms && item.bathrooms >= 1 ? "Private" : "Shared";

  // What the amenities and tags say about furnishing and accessibility (see lib/find-a-room-search.ts).
  const furnishedState = deriveFurnishedState(item.amenities || []);
  const accessibility = deriveAccessibility(item.tags || [], item.amenities || []);

  const isAvailable = hosted.available !== false;
  const isOffer = hosted.state === "REVIEW" || hosted.state === "APPROVED";

  const houseRules = hosted.agreementDetails?.houseRules
    ? Object.values(hosted.agreementDetails.houseRules as unknown as Record<string, string>).filter(Boolean)
    : [];

  return {
    id: String(item.id),
    listingId: String(item.id),
    isBackendListing: true,
    title: item.name || "Private room",
    area: item.location || item.city || "",
    city: item.city || "",
    roomNumber: item.roomId ? `Room #${item.roomId}` : `Room #${item.id.slice(0, 4)}`,
    roomType: item.roomType || "Private room",
    monthlyRent,
    currency,
    minStayMonths,
    availableDate: availabilityDate(item.availableFrom, todayIsoDate()),
    roomState: isOffer ? "offer" : "available",
    applicationsState: isAvailable ? "open" : "closed",
    furnishedState,
    bathroomType,
    amenities: item.amenities || [],
    accessibility,
    photos,
    toneColor: "#E3E9F6",
    freshnessScore: idx,
    description: item.description || "",
    houseRules,
    ownerName: hosted.contactName || "Host",
    rating: item.rating,
    reviewCount: item.reviewCount,
  };
}

export function RentBrowser() {
  // Anyone can browse rooms; applying needs a verified identity (the backend
  // enforces the same rule, so this only keeps the button honest).
  const { user, identityVerified, loading: sessionLoading } = useUserSession();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  // The filters live in the page address, so a refresh, the back button or a shared link keeps the search.
  const [initial] = useState(() => filtersFromParams(searchParams, todayIsoDate()));

  // Search Bar States
  const [locationInput, setLocationInput] = useState(initial.city);
  const [activeLocationQuery, setActiveLocationQuery] = useState(initial.city);
  const [moveInDate, setMoveInDate] = useState(initial.moveIn);
  const [stayMonths, setStayMonths] = useState(initial.stayMonths); // 0 = Flexible

  // Recent searches list
  const [recentSearches, setRecentSearches] = useState<string[]>([]);

  // Filter controls
  const [viewMode, setViewMode] = useState<"list" | "map">(initial.view);
  const [sortBy, setSortBy] = useState<"best" | "plow" | "phigh" | "soon">(initial.sortBy);
  // Settings > Privacy "Personalised recommendations" (on unless switched off).
  const [personalized, setPersonalized] = useState(false);
  useEffect(() => {
    if (!user) return;
    getUserPreferences()
      .then((p) => setPersonalized(p.personalizedRecommendations))
      .catch(() => setPersonalized(false));
  }, [user]);
  const [priceFilter, setPriceFilter] = useState(initial.price);
  const [roomTypeFilter, setRoomTypeFilter] = useState(initial.roomType);
  const [furnishedFilter, setFurnishedFilter] = useState(initial.furnished);
  const [availabilityFilter, setAvailabilityFilter] = useState(initial.availability);

  // More filters checkboxes (open if any of them was saved in the address)
  const [moreFiltersOpen, setMoreFiltersOpen] = useState(
    initial.bathrooms.length + initial.amenities.length + initial.accessibility.length > 0,
  );
  const [selectedBathrooms, setSelectedBathrooms] = useState<string[]>(initial.bathrooms);
  const [selectedAmenities, setSelectedAmenities] = useState<string[]>(initial.amenities);
  const [selectedAccessibility, setSelectedAccessibility] = useState<string[]>(initial.accessibility);

  // Write every change of a filter back into the address (replace, so the back button isn't flooded).
  useEffect(() => {
    const today = todayIsoDate();
    const next = applyFiltersToParams(
      new URLSearchParams(searchParams.toString()),
      {
        city: activeLocationQuery,
        price: priceFilter,
        roomType: roomTypeFilter,
        furnished: furnishedFilter,
        availability: availabilityFilter,
        moveIn: moveInDate,
        stayMonths,
        sortBy,
        view: viewMode,
        bathrooms: selectedBathrooms,
        amenities: selectedAmenities,
        accessibility: selectedAccessibility,
      },
      today,
    ).toString();
    if (next === searchParams.toString()) return;
    router.replace(next ? `${pathname}?${next}` : pathname, { scroll: false });
    // Only the filters should trigger this -- not the address they have just written.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    activeLocationQuery, priceFilter, roomTypeFilter, furnishedFilter, availabilityFilter, moveInDate,
    stayMonths, sortBy, viewMode, selectedBathrooms, selectedAmenities, selectedAccessibility,
  ]);

  // User interactions
  const [savedRooms, setSavedRooms] = useState<Record<string, boolean>>({});
  const [appliedRooms, setAppliedRooms] = useState<Record<string, boolean>>({});
  const [searchSaved, setSearchSaved] = useState(false);

  // Modals & Drawers
  const [drawerRoom, setDrawerRoom] = useState<FindARoomItem | null>(null);
  const [drawerTab, setDrawerTab] = useState<"overview" | "amenities" | "availability" | "rules" | "reviews" | "about">("overview");

  const [applyModalRoom, setApplyModalRoom] = useState<FindARoomItem | null>(null);
  const [applyMessage, setApplyMessage] = useState("");
  const [applyDesiredMoveIn, setApplyDesiredMoveIn] = useState(() => todayIsoDate());
  const [applySubmitting, setApplySubmitting] = useState(false);
  const [applyError, setApplyError] = useState("");


  // Toast
  const [toastMsg, setToastMsg] = useState<string | null>(null);
  const toastTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const showToast = useCallback((msg: string) => {
    setToastMsg(msg);
    if (toastTimeoutRef.current) clearTimeout(toastTimeoutRef.current);
    toastTimeoutRef.current = setTimeout(() => setToastMsg(null), 3000);
  }, []);

  // SSR Mount check for React Portals
  const [mounted, setMounted] = useState(false);
  const drawerBodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Lock body scroll and reset drawer scroll to top when drawer or modals open
  useEffect(() => {
    if (drawerRoom || applyModalRoom) {
      const originalStyle = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      if (drawerRoom && drawerBodyRef.current) {
        drawerBodyRef.current.scrollTop = 0;
      }
      return () => {
        document.body.style.overflow = originalStyle;
      };
    }
  }, [drawerRoom, applyModalRoom]);

  // Dynamic Backend State
  const [allRooms, setAllRooms] = useState<FindARoomItem[]>([]);
  const [loadingBackend, setLoadingBackend] = useState(true);

  // Load real data dynamically from FastAPI backend
  useEffect(() => {
    setLoadingBackend(true);
    Promise.all([
      listAllPublicListings().catch(() => null),
      listHostedListings().catch(() => []),
      listRentalApplications().catch(() => []),
    ])
      .then(([publicPage, hostedListings, applications]) => {
        // A host browsing Find a Room only sees other people's rooms -- their own
        // listings live under My Listings (the backend also blocks self-applying).
        const ownListingIds = new Set(
          (Array.isArray(hostedListings) ? hostedListings : []).map((item) => String(item.id)),
        );

        const roomsMap = new Map<string, FindARoomItem>();
        (publicPage?.items ?? []).forEach((item, idx) => {
          if (!ownListingIds.has(String(item.id))) {
            roomsMap.set(String(item.id), mapListingToFindARoomItem(item, idx));
          }
        });

        setAllRooms(Array.from(roomsMap.values()));

        // 3. Map user applications
        if (Array.isArray(applications) && applications.length > 0) {
          const appliedMap: Record<string, boolean> = {};
          listingsWithOpenApplication(applications).forEach((listingId) => {
            appliedMap[listingId] = true;
          });
          setAppliedRooms((prev) => ({ ...prev, ...appliedMap }));
        }
      })
      .finally(() => setLoadingBackend(false));
  }, []);

  // Search as the visitor types: apply the text a moment after they stop (Enter / Search still apply it at once).
  useEffect(() => {
    const typed = locationInput.trim();
    if (typed === activeLocationQuery) return;
    const timer = setTimeout(() => setActiveLocationQuery(typed), 350);
    return () => clearTimeout(timer);
  }, [locationInput, activeLocationQuery]);

  // Execute Search query
  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const q = locationInput.trim();
    setActiveLocationQuery(q);
    if (q && !recentSearches.includes(q)) {
      setRecentSearches((prev) => [q, ...prev.slice(0, 3)]);
    }
  };

  const handlePickRecent = (item: string) => {
    setLocationInput(item);
    setActiveLocationQuery(item);
  };

  const handleClearRecent = () => {
    setRecentSearches([]);
    showToast("Recent searches cleared");
  };

  // Discovered room types
  const availableRoomTypes = useMemo(() => {
    const set = new Set<string>(["En-suite", "Studio", "Single", "Double", "Private room"]);
    allRooms.forEach((r) => {
      if (r.roomType) set.add(r.roomType);
    });
    return Array.from(set);
  }, [allRooms]);

  // Filter & match logic
  // Cities the renter has shown interest in: rooms they applied to and places they searched.
  const preferredCities = useMemo(() => {
    if (!personalized) return new Set<string>();
    const cities = allRooms.filter((r) => appliedRooms[r.id]).map((r) => r.city);
    return new Set([...cities, ...recentSearches].map((c) => c.trim().toLowerCase()).filter(Boolean));
  }, [personalized, allRooms, appliedRooms, recentSearches]);

  // Indicative exchange rates, so a £ room can be compared with a ₹ budget. Without them (a failed
  // request), only rooms in the limit's own currency are compared.
  const [rates, setRates] = useState<{ usdPerUnit: Record<string, number>; asOf: string | null } | null>(null);
  useEffect(() => {
    getExchangeRates()
      .then((r) => setRates({ usdPerUnit: r.usdPerUnit, asOf: r.asOf }))
      .catch(() => setRates(null));
  }, []);

  // Price limits are offered in the currency of the rooms in the place being searched (£ for London,
  // ₹ for Mumbai), so a limit is always compared with prices in that same currency.
  const priceCurrency = useMemo(() => {
    const inPlace = allRooms.filter((r) =>
      roomMatchesSearch(r, {
        query: activeLocationQuery, price: "all", priceCurrency: "", roomType: "all", furnished: "all",
        availability: "all", moveIn: "", stayMonths: 0, bathrooms: [], amenities: [], accessibility: [],
      }),
    );
    return dominantCurrency(inPlace.length > 0 ? inPlace : allRooms);
  }, [allRooms, activeLocationQuery]);

  const filteredRooms = useMemo(() => {
    const criteria = {
      query: activeLocationQuery,
      price: priceFilter,
      priceCurrency,
      rates: rates?.usdPerUnit ?? null,
      roomType: roomTypeFilter,
      furnished: furnishedFilter,
      availability: availabilityFilter,
      moveIn: moveInDate,
      stayMonths,
      bathrooms: selectedBathrooms,
      amenities: selectedAmenities,
      accessibility: selectedAccessibility,
    };

    return allRooms
      .filter((r) => roomMatchesSearch(r, criteria))
      .sort((a, b) => {
        if (sortBy === "plow") return a.monthlyRent - b.monthlyRent;
        if (sortBy === "phigh") return b.monthlyRent - a.monthlyRent;
        if (sortBy === "soon") return a.availableDate.localeCompare(b.availableDate);

        // Best match algorithm. With personalised recommendations on, rooms in
        // cities the renter applied to or searched come first -- but never ahead
        // of a room that fits their stay or is still open to applications.
        const bonusA = preferredCities.has(a.city.trim().toLowerCase()) ? 4e9 : 0;
        const bonusB = preferredCities.has(b.city.trim().toLowerCase()) ? 4e9 : 0;
        const diffA = Math.abs(new Date(a.availableDate).getTime() - new Date(moveInDate).getTime());
        const diffB = Math.abs(new Date(b.availableDate).getTime() - new Date(moveInDate).getTime());
        const penaltyA = (a.minStayMonths > stayMonths && stayMonths ? 9e9 : 0) + (a.applicationsState === "closed" ? 2e10 : 0);
        const penaltyB = (b.minStayMonths > stayMonths && stayMonths ? 9e9 : 0) + (b.applicationsState === "closed" ? 2e10 : 0);
        return diffA + penaltyA - bonusA + a.freshnessScore * 1e7 - (diffB + penaltyB - bonusB + b.freshnessScore * 1e7);
      });
  }, [
    allRooms,
    preferredCities,
    activeLocationQuery,
    priceCurrency,
    rates,
    priceFilter,
    roomTypeFilter,
    furnishedFilter,
    availabilityFilter,
    selectedBathrooms,
    selectedAmenities,
    selectedAccessibility,
    sortBy,
    moveInDate,
    stayMonths,
  ]);

  const isZeroState = filteredRooms.length === 0;

  // "Up to £500" -- plus the saved limit itself, if it isn't one of the usual steps.
  const priceOptions = useMemo(() => {
    const steps = priceStepsFor(priceCurrency);
    const values = priceFilter !== "all" && !steps.includes(Number(priceFilter)) ? [...steps, Number(priceFilter)].sort((a, b) => a - b) : steps;
    return [
      { value: "all", label: "Any price" },
      ...values.map((v) => ({ value: String(v), label: `Up to ${formatCurrency(v, priceCurrency)}` })),
    ];
  }, [priceCurrency, priceFilter]);

  // With a limit set, rooms in another currency are compared at indicative rates -- or left out when
  // there is no rate for them. Say which, so the visitor isn't surprised.
  const { convertedByRate, hiddenByCurrency } = useMemo(() => {
    if (priceFilter === "all") return { convertedByRate: 0, hiddenByCurrency: 0 };
    const others = allRooms.filter((r) => r.currency !== priceCurrency);
    const hidden = others.filter((r) => convertAmount(r.monthlyRent, r.currency, priceCurrency, rates?.usdPerUnit) === null).length;
    return { convertedByRate: others.length - hidden, hiddenByCurrency: hidden };
  }, [allRooms, priceFilter, priceCurrency, rates]);

  const anyFilterActive =
    Boolean(activeLocationQuery.trim()) || priceFilter !== "all" || roomTypeFilter !== "all" || furnishedFilter !== "all" ||
    availabilityFilter !== "all" || selectedBathrooms.length + selectedAmenities.length + selectedAccessibility.length > 0;

  const resetFilters = () => {
    setLocationInput("");
    setActiveLocationQuery("");
    setPriceFilter("all");
    setRoomTypeFilter("all");
    setFurnishedFilter("all");
    setAvailabilityFilter("all");
    setSelectedBathrooms([]);
    setSelectedAmenities([]);
    setSelectedAccessibility([]);
  };

  // Toggle Save Room
  const handleToggleSave = (room: FindARoomItem, e?: React.MouseEvent) => {
    e?.stopPropagation();
    setSavedRooms((prev) => {
      const next = !prev[room.id];
      showToast(next ? "Saved to your rooms." : "Removed from saved rooms.");
      return { ...prev, [room.id]: next };
    });
  };

  // Open Room Details Drawer
  const handleOpenRoomDetails = (room: FindARoomItem) => {
    setDrawerRoom(room);
    setDrawerTab("overview");
  };

  // Open Apply Modal
  const handleOpenApply = (room: FindARoomItem, e?: React.MouseEvent) => {
    e?.stopPropagation();
    if (!user) {
      router.push("/account/login");
      return;
    }
    if (!identityVerified) return;
    setApplyModalRoom(room);
    setApplyMessage("");
    setApplyDesiredMoveIn(moveInDate);
    setApplyError("");
  };

  const handleConfirmApply = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!applyModalRoom) return;

    setApplySubmitting(true);
    setApplyError("");
    try {
      await submitRentalApplication({
        listingId: applyModalRoom.listingId,
        message: applyMessage,
        desiredMoveIn: applyDesiredMoveIn,
      });

      setAppliedRooms((prev) => ({ ...prev, [applyModalRoom.id]: true }));
      showToast(`Application submitted for ${applyModalRoom.title}!`);
      setApplyModalRoom(null);
    } catch (err) {
      setApplyError(errorMessage(err, "Failed to submit application. Please try again."));
    } finally {
      setApplySubmitting(false);
    }
  };

  // Map view areas computed dynamically from actual rooms
  const dynamicMapAreas = useMemo(() => {
    const counts: Record<string, number> = {};
    filteredRooms.forEach((r) => {
      const mainArea = r.area.split(" · ")[0].trim() || r.city || "Central";
      counts[mainArea] = (counts[mainArea] || 0) + 1;
    });
    return counts;
  }, [filteredRooms]);

  return (
    <div className="space-y-6">
      {/* 1. Hero Search Section */}
      <section className="rounded-3xl border border-slate-200 bg-white p-6 sm:p-8 shadow-xs dark:border-slate-800 dark:bg-slate-900">
        <h1 className="font-heading text-3xl sm:text-4xl font-extrabold text-[#0E2F73] dark:text-white tracking-tight">
          Find a Room
        </h1>
        <p className="mt-2 text-sm sm:text-base text-slate-600 dark:text-slate-400 max-w-2xl">
          Find verified rooms that match where you want to live, when you want to move, and how long you plan to stay.
        </p>

        {/* Unified Search Bar */}
        <form
          onSubmit={handleSearchSubmit}
          className="mt-6 grid grid-cols-1 md:grid-cols-[2fr_1fr_1fr_auto] rounded-2xl border border-slate-300 bg-white shadow-[0px_12px_30px_0px_rgba(14,47,115,0.08)] overflow-hidden dark:border-slate-700 dark:bg-slate-800/90"
        >
          {/* Location field */}
          <div className="flex flex-col justify-center px-4 py-3 sm:px-5">
            <label className="text-[11px] font-extrabold uppercase tracking-wide text-[#0E2F73] dark:text-blue-300">
              Location
            </label>
            <div className="mt-1 flex items-center gap-2">
              <MapPin className="h-4 w-4 text-[#0E2F73] shrink-0 dark:text-blue-400" />
              <input
                type="text"
                value={locationInput}
                onChange={(e) => setLocationInput(e.target.value)}
                placeholder="City, neighborhood or area"
                className="w-full bg-transparent text-sm sm:text-base font-bold text-slate-900 placeholder:text-slate-400 focus:outline-none dark:text-white"
              />
            </div>
          </div>

          {/* Move-in Date field */}
          <div className="border-t md:border-t-0 md:border-l border-slate-200 px-4 py-3 sm:px-5 flex flex-col justify-center dark:border-slate-700">
            <label className="text-[11px] font-extrabold uppercase tracking-wide text-[#0E2F73] dark:text-blue-300">
              Move-in
            </label>
            <div className="mt-1 flex items-center gap-2">
              <Calendar className="h-4 w-4 text-[#0E2F73] shrink-0 dark:text-blue-400" />
              <input
                type="date"
                value={moveInDate}
                onChange={(e) => setMoveInDate(e.target.value)}
                className="w-full bg-transparent text-sm sm:text-base font-bold text-slate-900 focus:outline-none dark:text-white"
              />
            </div>
          </div>

          {/* Stay Duration field */}
          <div className="border-t md:border-t-0 md:border-l border-slate-200 px-4 py-3 sm:px-5 flex flex-col justify-center dark:border-slate-700">
            <label className="text-[11px] font-extrabold uppercase tracking-wide text-[#0E2F73] dark:text-blue-300">
              Stay
            </label>
            <div className="mt-1 flex items-center gap-2">
              <Clock className="h-4 w-4 text-[#0E2F73] shrink-0 dark:text-blue-400" />
              <select
                value={stayMonths}
                onChange={(e) => setStayMonths(Number(e.target.value))}
                className="w-full bg-transparent text-sm sm:text-base font-bold text-slate-900 focus:outline-none dark:text-white"
              >
                <option value={0}>Flexible</option>
                <option value={3}>3 months</option>
                <option value={6}>6 months</option>
                <option value={12}>12 months</option>
              </select>
            </div>
          </div>

          {/* Search Button */}
          <div className="border-t md:border-t-0 p-2 flex items-center justify-center">
            <button
              type="submit"
              className="flex w-full md:w-auto items-center justify-center gap-2 rounded-xl bg-[#0E2F73] px-6 py-3.5 text-sm sm:text-base font-bold text-white shadow-xs transition-colors hover:bg-[#0B2560]"
            >
              <Search className="h-4 w-4" />
              <span>Search</span>
            </button>
          </div>
        </form>

        {/* Recent Searches Row */}
        {recentSearches.length > 0 && (
          <div className="mt-4 flex flex-wrap items-center gap-2 pt-1 text-xs">
            <span className="text-slate-500 dark:text-slate-400">Recent searches:</span>
            {recentSearches.map((term) => (
              <button
                key={term}
                type="button"
                onClick={() => handlePickRecent(term)}
                className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 font-bold text-[#0E2F73] transition-colors hover:border-[#0E2F73] dark:border-slate-700 dark:bg-slate-800 dark:text-blue-300"
              >
                {term}
              </button>
            ))}
            <button
              type="button"
              onClick={handleClearRecent}
              className="font-bold text-slate-500 underline hover:text-slate-800 dark:text-slate-400"
            >
              Clear
            </button>
          </div>
        )}

        {/* Trust Badges Strip */}
        <div className="mt-5 flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-slate-200 pt-4 text-xs font-bold text-[#0E2F73] dark:border-slate-800 dark:text-blue-300">
          <div className="flex items-center gap-1.5">
            <ShieldCheck className="h-4 w-4 text-[#0E2F73] dark:text-blue-400" />
            <span>Verified properties</span>
          </div>
          <div className="flex items-center gap-1.5">
            <Check className="h-4 w-4 text-[#0E2F73] dark:text-blue-400" />
            <span>Verified listing authority</span>
          </div>
          <div className="flex items-center gap-1.5">
            <MessageSquare className="h-4 w-4 text-[#0E2F73] dark:text-blue-400" />
            <span>Secure in-platform messaging</span>
          </div>
          <div className="flex items-center gap-1.5">
            <FileText className="h-4 w-4 text-[#0E2F73] dark:text-blue-400" />
            <span>Documented rental records</span>
          </div>
        </div>
      </section>

      {/* 2. Results Count, Views & Controls Bar */}
      <section className="space-y-3">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <h2 className="font-heading text-xl font-extrabold text-[#0E2F73] dark:text-white">
            {loadingBackend
              ? "Loading rooms..."
              : isZeroState
              ? "0 rooms match your search"
              : `${filteredRooms.length} room${filteredRooms.length === 1 ? "" : "s"} match your search`}
            {anyFilterActive && !loadingBackend && (
              <button
                type="button"
                onClick={resetFilters}
                className="ml-3 align-middle text-xs font-bold text-[#0E2F73] underline underline-offset-2 hover:text-[#D80B0B] dark:text-blue-300"
              >
                Clear filters
              </button>
            )}
          </h2>

          <div className="flex flex-wrap items-center gap-2.5">
            {/* View Segmented Toggle (List / Map) */}
            <div className="flex items-center rounded-xl bg-slate-200/80 p-1 dark:bg-slate-800">
              <button
                type="button"
                onClick={() => setViewMode("list")}
                className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-bold transition-all ${
                  viewMode === "list"
                    ? "bg-white text-[#0E2F73] shadow-xs dark:bg-slate-700 dark:text-white"
                    : "text-slate-600 hover:text-slate-900 dark:text-slate-400"
                }`}
              >
                <FileText className="h-3.5 w-3.5" />
                <span>List</span>
              </button>
              <button
                type="button"
                onClick={() => setViewMode("map")}
                className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-bold transition-all ${
                  viewMode === "map"
                    ? "bg-white text-[#0E2F73] shadow-xs dark:bg-slate-700 dark:text-white"
                    : "text-slate-600 hover:text-slate-900 dark:text-slate-400"
                }`}
              >
                <MapIcon className="h-3.5 w-3.5" />
                <span>Map</span>
              </button>
            </div>

            

            {/* Save Search Button */}
            <button
              type="button"
              onClick={() => {
                setSearchSaved((s) => !s);
                showToast(searchSaved ? "Search unsaved." : "Search saved. Alerts on for new verified matches.");
              }}
              className={`flex items-center gap-1.5 rounded-full border px-4 py-2 text-xs font-bold transition-colors ${
                searchSaved
                  ? "border-[#0E2F73] bg-[#0E2F73] text-white"
                  : "border-slate-200 bg-white text-[#0E2F73] hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-blue-300"
              }`}
            >
              <Bookmark className="h-3.5 w-3.5" />
              <span>{searchSaved ? "Search Saved" : "Save Search"}</span>
            </button>
          </div>
        </div>

        {convertedByRate > 0 && (
          <p className="text-xs font-medium text-slate-600 dark:text-slate-400" role="status">
            The price limit is in {priceCurrency}. Rooms listed in another currency are compared using approximate exchange
            rates{rates?.asOf ? ` (${rates.asOf})` : ""}, so treat the edge of the limit as a guide. Prices on each room stay in its own currency.
          </p>
        )}
        {hiddenByCurrency > 0 && (
          <p className="text-xs font-medium text-slate-600 dark:text-slate-400" role="status">
            {hiddenByCurrency} room{hiddenByCurrency === 1 ? "" : "s"} listed in a currency we can&apos;t compare with {priceCurrency}{" "}
            {hiddenByCurrency === 1 ? "is" : "are"} hidden. Choose &ldquo;Any price&rdquo; to see {hiddenByCurrency === 1 ? "it" : "them"}.
          </p>
        )}

        {/* Filter Pills / Dropdowns Row */}
        <div className="rounded-2xl border border-slate-200 bg-white p-3.5 shadow-xs dark:border-slate-800 dark:bg-slate-900">
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-6 gap-3 items-end">
            {/* Price Filter */}
            <RoundedFilterDropdown
              label="Price (per month)"
              value={priceFilter}
              onChange={setPriceFilter}
              options={priceOptions}
              size="sm"
            />

            {/* Room Type Filter */}
            <RoundedFilterDropdown
              label="Room Type"
              value={roomTypeFilter}
              onChange={setRoomTypeFilter}
              options={[
                { value: "all", label: "Any type" },
                ...availableRoomTypes.map((rt) => ({ value: rt, label: rt })),
              ]}
              size="sm"
            />

            {/* Furnished Filter */}
            <RoundedFilterDropdown
              label="Furnished"
              value={furnishedFilter}
              onChange={setFurnishedFilter}
              options={[
                { value: "all", label: "Any" },
                { value: "yes", label: "Furnished" },
                { value: "part", label: "Part furnished" },
                { value: "no", label: "Unfurnished" },
              ]}
              size="sm"
            />

            {/* Availability Filter */}
            <RoundedFilterDropdown
              label="Availability"
              value={availabilityFilter}
              onChange={setAvailabilityFilter}
              options={[
                { value: "all", label: "Any" },
                { value: "date", label: "By my move-in date" },
                { value: "future", label: "Later is fine" },
              ]}
              size="sm"
            />

            {/* Sort Dropdown */}
            <RoundedFilterDropdown
              label="Sort"
              value={sortBy}
              onChange={(val) => setSortBy(val as typeof sortBy)}
              options={[
                { value: "best", label: "Best Match" },
                { value: "plow", label: "Price, low to high" },
                { value: "phigh", label: "Price, high to low" },
                { value: "soon", label: "Soonest available" },
              ]}
              align="right"
              size="sm"
            />

            {/* More Filters Toggle */}
            <button
              type="button"
              onClick={() => setMoreFiltersOpen((o) => !o)}
              className="flex h-10 items-center justify-center gap-1.5 rounded-full border border-slate-200 bg-white px-4 text-xs font-bold text-[#0E2F73] hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-blue-300 self-end"
            >
              <SlidersHorizontal className="h-3.5 w-3.5" />
              <span>More Filters</span>
            </button>
          </div>

          {/* Expandable More Filters Sub-Panel */}
          {moreFiltersOpen && (
            <div className="mt-4 border-t border-slate-200 pt-4 grid grid-cols-1 sm:grid-cols-3 gap-4 dark:border-slate-800 text-xs">
              {/* Bathroom Type */}
              <div>
                <span className="font-extrabold uppercase tracking-wide text-slate-500 dark:text-slate-400">Bathroom</span>
                <div className="mt-2 space-y-2">
                  {["Private", "Shared"].map((b) => (
                    <label key={b} className="flex items-center gap-2 cursor-pointer font-semibold text-slate-800 dark:text-slate-200">
                      <input
                        type="checkbox"
                        checked={selectedBathrooms.includes(b)}
                        onChange={(e) => {
                          setSelectedBathrooms((prev) =>
                            e.target.checked ? [...prev, b] : prev.filter((x) => x !== b)
                          );
                        }}
                        className="rounded border-slate-300 text-[#0E2F73] focus:ring-[#0E2F73]"
                      />
                      <span>{b} bathroom</span>
                    </label>
                  ))}
                </div>
              </div>

              {/* Amenities */}
              <div>
                <span className="font-extrabold uppercase tracking-wide text-slate-500 dark:text-slate-400">Amenities</span>
                <div className="mt-2 space-y-2">
                  {["Wi-Fi", "Workspace", "Laundry", "Kitchen"].map((am) => (
                    <label key={am} className="flex items-center gap-2 cursor-pointer font-semibold text-slate-800 dark:text-slate-200">
                      <input
                        type="checkbox"
                        checked={selectedAmenities.includes(am)}
                        onChange={(e) => {
                          setSelectedAmenities((prev) =>
                            e.target.checked ? [...prev, am] : prev.filter((x) => x !== am)
                          );
                        }}
                        className="rounded border-slate-300 text-[#0E2F73] focus:ring-[#0E2F73]"
                      />
                      <span>{am}</span>
                    </label>
                  ))}
                </div>
              </div>

              {/* Accessibility */}
              <div>
                <span className="font-extrabold uppercase tracking-wide text-slate-500 dark:text-slate-400">Accessibility</span>
                <div className="mt-2 space-y-2">
                  {["Step-free entrance", "Lift access"].map((acc) => (
                    <label key={acc} className="flex items-center gap-2 cursor-pointer font-semibold text-slate-800 dark:text-slate-200">
                      <input
                        type="checkbox"
                        checked={selectedAccessibility.includes(acc)}
                        onChange={(e) => {
                          setSelectedAccessibility((prev) =>
                            e.target.checked ? [...prev, acc] : prev.filter((x) => x !== acc)
                          );
                        }}
                        className="rounded border-slate-300 text-[#0E2F73] focus:ring-[#0E2F73]"
                      />
                      <span>{acc}</span>
                    </label>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      </section>

      {/* 3. Interactive SVG Map View (Toggled) */}
      {viewMode === "map" && (
        <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-xs dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between pb-3 border-b border-slate-100 dark:border-slate-800">
            <h3 className="font-heading text-base font-bold text-[#0E2F73] dark:text-white">
              Area Distribution ({filteredRooms.length} rooms)
            </h3>
            <span className="text-xs text-slate-500">Click any area node to filter</span>
          </div>

          <div className="mt-4 relative w-full overflow-hidden rounded-2xl bg-gradient-to-b from-[#F7F9FD] to-[#EEF3FA] dark:from-slate-950 dark:to-slate-900 border border-slate-200 dark:border-slate-800">
            {Object.keys(dynamicMapAreas).length > 0 ? (
              <svg viewBox="0 0 900 380" className="w-full h-auto">
                <defs>
                  <radialGradient id="mapDotGrad" cx="50%" cy="50%" r="50%">
                    <stop offset="0%" stopColor="#D80B0B" stopOpacity="0.8" />
                    <stop offset="100%" stopColor="#0E2F73" stopOpacity="0.9" />
                  </radialGradient>
                </defs>

                {/* Grid guidelines */}
                <path d="M 50 190 Q 450 170 850 190" stroke="#CBD5E1" strokeWidth="1" strokeDasharray="4 4" fill="none" opacity="0.4" />
                <path d="M 200 60 Q 450 320 700 60" stroke="#CBD5E1" strokeWidth="1" strokeDasharray="4 4" fill="none" opacity="0.4" />

                {Object.entries(dynamicMapAreas).map(([areaName, count], idx) => {
                  const total = Object.keys(dynamicMapAreas).length;
                  const stepX = 700 / Math.max(1, total);
                  const x = 120 + idx * stepX;
                  const y = 140 + (idx % 2 === 0 ? -40 : 40);
                  const radius = Math.min(48, Math.max(26, 22 + count * 5));

                  return (
                    <g
                      key={areaName}
                      className="cursor-pointer transition-transform duration-200 hover:scale-105"
                      onClick={() => {
                        setLocationInput(areaName);
                        setActiveLocationQuery(areaName);
                        showToast(`Filtered by ${areaName}`);
                      }}
                    >
                      <circle cx={x} cy={y} r={radius} fill="url(#mapDotGrad)" className="opacity-80" />
                      <circle cx={x} cy={y} r={radius - 4} fill="#FFFFFF" opacity="0.9" />
                      <text x={x} y={y - 2} textAnchor="middle" fontSize="14" fontWeight="800" fill="#0E2F73">
                        {count}
                      </text>
                      <text x={x} y={y + 12} textAnchor="middle" fontSize="9" fontWeight="700" fill="#64748B">
                        ROOMS
                      </text>
                      <text
                        x={x}
                        y={y + radius + 16}
                        textAnchor="middle"
                        fontSize="12"
                        fontWeight="700"
                        fill="#0B1B3F"
                        className="dark:fill-white font-sans"
                      >
                        {areaName}
                      </text>
                    </g>
                  );
                })}
              </svg>
            ) : (
              <div className="py-20 flex flex-col items-center justify-center text-center">
                <MapPin className="h-10 w-10 text-slate-400" />
                <p className="mt-2 text-sm font-bold text-slate-600 dark:text-slate-400">
                  No active room locations to display.
                </p>
              </div>
            )}
          </div>
          <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
            ⓘ Circles show approximate areas only — exact addresses are verified and shared at later stages.
          </p>
        </section>
      )}

      {/* 4. Results Section: Cards Grid or Dynamic Empty State */}
      {isZeroState ? (
        <section className="rounded-3xl border border-slate-200 bg-white p-8 sm:p-12 text-center shadow-xs dark:border-slate-800 dark:bg-slate-900">
          <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-[#EAF0FB] text-[#0E2F73] dark:bg-blue-950/40 dark:text-blue-300">
            <Search className="h-8 w-8" />
          </div>
          <h3 className="font-heading mt-4 text-xl font-extrabold text-[#0E2F73] dark:text-white">
            {allRooms.length === 0 ? "No rooms listed yet" : "0 rooms match your search"}
          </h3>
          <p className="mx-auto mt-2 max-w-lg text-sm text-slate-600 dark:text-slate-400">
            {allRooms.length === 0
              ? "There are currently no rooms listed on the platform. When hosts add properties and rooms, they will appear here with full verification details."
              : `We couldn't find any rooms matching "${activeLocationQuery || "your filters"}". Try adjusting your filters or location search.`}
          </p>

          <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
            {allRooms.length > 0 ? (
              <button
                type="button"
                onClick={resetFilters}
                className="rounded-xl bg-[#0E2F73] px-5 py-2.5 text-xs font-bold text-white shadow-xs hover:bg-[#0B2560] transition-colors"
              >
                Clear all filters
              </button>
            ) : (
              <Link
                href="/account/host/listings"
                className="rounded-xl bg-[#0E2F73] px-5 py-2.5 text-xs font-bold text-white shadow-xs hover:bg-[#0B2560] transition-colors"
              >
                List a room as host
              </Link>
            )}

            <button
              type="button"
              onClick={() => {
                setSearchSaved(true);
                showToast("Search saved for alerts.");
              }}
              className="flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-xs font-bold text-[#0E2F73] hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-blue-300"
            >
              <Bookmark className="h-3.5 w-3.5" />
              <span>Save search for alerts</span>
            </button>
          </div>
        </section>
      ) : (
        /* 2-Column Grid of Horizontal Room Cards Matching Figma Exactly */
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {filteredRooms.map((room) => {
            const isSaved = Boolean(savedRooms[room.id]);
            const isApplied = Boolean(appliedRooms[room.id]);

            // Availability text & status styling
            const isNow = room.availableDate <= todayIsoDate();
            const dateFormatted = new Date(`${room.availableDate}T12:00:00`).toLocaleDateString("en-US", {
              month: "short",
              day: "numeric",
            });
            const baseAvail = room.roomState === "offer" ? "Under Offer" : isNow ? "Available now" : `Available ${dateFormatted}`;
            const appsText = room.applicationsState === "open" ? (room.roomState === "offer" ? "Applications still open" : "Applications open") : "Applications closed";

            // Match notes
            const diffDays = Math.round((new Date(room.availableDate).getTime() - new Date(moveInDate).getTime()) / 864e5);
            let mismatchNote: string | null = null;
            if (diffDays > 3) {
              const weeks = Math.max(1, Math.round(diffDays / 7));
              mismatchNote = `Not an exact match — available ${weeks} ${weeks === 1 ? "week" : "weeks"} after your move-in`;
            } else if (stayMonths && room.minStayMonths > stayMonths) {
              mismatchNote = `Minimum stay ${room.minStayMonths} months is longer than your ${stayMonths}-month stay`;
            }

            return (
              <article
                key={room.id}
                onClick={() => handleOpenRoomDetails(room)}
                className="group cursor-pointer grid grid-cols-1 sm:grid-cols-[210px_1fr] rounded-3xl border border-slate-200 bg-white overflow-hidden shadow-xs hover:border-slate-300 hover:shadow-md transition-all dark:border-slate-800 dark:bg-slate-900"
              >
                {/* Photo Section */}
                <div className="relative min-h-[200px] sm:min-h-[220px] bg-slate-100 dark:bg-slate-800 overflow-hidden">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={room.photos[0] || DEFAULT_ROOM_PHOTOS[0]}
                    alt={room.title}
                    className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-105"
                  />
                  <div className="absolute bottom-2.5 left-2.5 rounded-lg bg-black/60 px-2 py-0.5 text-[11px] font-bold text-white backdrop-blur-xs">
                    1 / {room.photos.length}
                  </div>
                </div>

                {/* Content Section */}
                <div className="p-4 flex flex-col justify-between gap-2.5">
                  <div>
                    {/* Header: Title, Area, and Save Button */}
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <h3 className="font-heading text-base font-extrabold text-[#0B1B3F] group-hover:text-[#D80B0B] transition-colors dark:text-white">
                          {room.title}
                        </h3>
                        <p className="mt-0.5 flex items-center gap-1 text-xs text-slate-500 dark:text-slate-400">
                          <MapPin className="h-3 w-3 text-slate-400" />
                          <span>Approx. location: {room.area}</span>
                        </p>
                      </div>

                      {/* Save Heart Button */}
                      <button
                        type="button"
                        onClick={(e) => handleToggleSave(room, e)}
                        className={`flex items-center gap-1.5 rounded-xl border px-2.5 py-1.5 text-xs font-bold transition-colors ${
                          isSaved
                            ? "border-red-200 bg-red-50 text-red-600 dark:border-red-900/40 dark:bg-red-950/40 dark:text-red-400"
                            : "border-slate-200 bg-white text-[#0E2F73] hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-blue-300"
                        }`}
                      >
                        <Heart className={`h-3.5 w-3.5 ${isSaved ? "fill-red-600 text-red-600" : ""}`} />
                        <span>{isSaved ? "Saved" : "Save"}</span>
                      </button>
                    </div>

                    {/* Trust Badges */}
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <span className="inline-flex items-center gap-1 rounded-full bg-[#E7F8EF] px-2 py-0.5 text-[11px] font-bold text-[#05603A] dark:bg-emerald-950/40 dark:text-emerald-300">
                        <ShieldCheck className="h-3 w-3" />
                        <span>Property Verified</span>
                      </span>
                      <span className="inline-flex items-center gap-1 rounded-full bg-[#E7F8EF] px-2 py-0.5 text-[11px] font-bold text-[#05603A] dark:bg-emerald-950/40 dark:text-emerald-300">
                        <Check className="h-3 w-3" />
                        <span>Listing Authority Verified</span>
                      </span>
                    </div>

                    {/* Availability Line */}
                    <div className="mt-2 flex items-center gap-1.5 text-xs font-bold">
                      {room.roomState === "offer" ? (
                        <div className="flex items-center gap-1.5 text-[#93370D] dark:text-amber-400">
                          <Clock className="h-3.5 w-3.5 shrink-0" />
                          <span>{baseAvail} · {appsText}</span>
                        </div>
                      ) : room.applicationsState === "closed" ? (
                        <div className="flex items-center gap-1.5 text-slate-500">
                          <Lock className="h-3.5 w-3.5 shrink-0" />
                          <span>{baseAvail} · {appsText}</span>
                        </div>
                      ) : (
                        <div className="flex items-center gap-1.5 text-[#05603A] dark:text-emerald-400">
                          <Calendar className="h-3.5 w-3.5 shrink-0" />
                          <span>{baseAvail} · {appsText}</span>
                        </div>
                      )}
                    </div>

                    {/* Move-in mismatch note */}
                    {mismatchNote && (
                      <p className="mt-1 flex items-start gap-1 text-[11px] font-semibold text-[#1D3F8F] dark:text-blue-300">
                        <Info className="h-3 w-3 shrink-0 mt-0.5" />
                        <span>{mismatchNote}</span>
                      </p>
                    )}

                    {/* Renter rating */}
                    {(room.reviewCount ?? 0) > 0 && (
                      <p className="mt-1.5 flex items-center gap-1 text-xs font-semibold text-slate-700 dark:text-slate-300">
                        <Star className="h-3.5 w-3.5 fill-amber-400 text-amber-400" />
                        <span>{(room.rating ?? 0).toFixed(1)}</span>
                        <span className="font-normal text-slate-500 dark:text-slate-400">
                          ({room.reviewCount} review{room.reviewCount === 1 ? "" : "s"})
                        </span>
                      </p>
                    )}

                    {/* Specs summary */}
                    <p className="mt-1.5 text-xs text-slate-600 dark:text-slate-400 line-clamp-1">
                      {[room.roomType, ...room.amenities].join(" · ")}
                    </p>
                  </div>

                  {/* Footer: Price & View Room Button */}
                  <div className="flex items-center justify-between border-t border-slate-100 pt-2.5 dark:border-slate-800">
                    <div>
                      <p className="font-heading text-lg font-extrabold text-[#0E2F73] dark:text-white">
                        {formatCurrency(room.monthlyRent, room.currency)}
                      </p>
                      <p className="text-[11px] text-slate-500 dark:text-slate-400">
                        / month · Minimum {room.minStayMonths} months
                      </p>
                    </div>

                    <div className="flex items-center gap-2">
                      {isApplied && (
                        <span className="inline-flex items-center gap-1 rounded-full bg-[#EEF3FF] px-2.5 py-1 text-xs font-bold text-[#1D3F8F] dark:bg-blue-950/40 dark:text-blue-300">
                          <Check className="h-3.5 w-3.5" />
                          <span>Applied</span>
                        </span>
                      )}
                      <button
                        type="button"
                        onClick={() => handleOpenRoomDetails(room)}
                        className="rounded-xl bg-[#0E2F73] px-4 py-2 text-xs font-bold text-white transition-colors hover:bg-[#0B2560]"
                      >
                        View Room
                      </button>
                    </div>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {/* 5. Bottom Footnote */}
      <p className="text-xs text-slate-500 dark:text-slate-400">
        Prices are shown in each listing’s currency. Locations are approximate until later stages of your application.
      </p>

      {/* 6. Slide-Over Room Detail Drawer */}
      {mounted &&
        drawerRoom &&
        createPortal(
          <div className="fixed inset-0 z-50 flex justify-end">
            {/* Backdrop */}
            <div
              className="fixed inset-0 bg-slate-900/50 backdrop-blur-xs transition-opacity"
              onClick={() => setDrawerRoom(null)}
            />

            {/* Drawer Body */}
            <aside className="relative z-10 flex h-full w-full max-w-xl flex-col bg-white shadow-2xl dark:bg-slate-900 animate-in slide-in-from-right duration-200">
              {/* Drawer Header (Fixed at top) */}
              <div className="flex items-start justify-between gap-4 border-b border-slate-100 p-6 pb-4 dark:border-slate-800 shrink-0">
                <div>
                  <span className="text-[11px] font-bold uppercase tracking-wider text-[#D80B0B]">
                    {drawerRoom.roomNumber} — {drawerRoom.roomType}
                  </span>
                  <h3 className="font-heading text-2xl font-extrabold text-[#0E2F73] dark:text-white mt-0.5">
                    {drawerRoom.title}
                  </h3>
                  <p className="flex items-center gap-1 text-xs text-slate-500 dark:text-slate-400 mt-1">
                    <MapPin className="h-3.5 w-3.5" />
                    <span>{drawerRoom.area} · {drawerRoom.city}</span>
                  </p>
                </div>

                <button
                  type="button"
                  onClick={() => setDrawerRoom(null)}
                  className="rounded-full p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 transition-colors"
                >
                  <X className="h-5 w-5" />
                </button>
              </div>

              {/* Drawer Scrollable Content */}
              <div ref={drawerBodyRef} className="flex-1 overflow-y-auto p-6 space-y-5">
                {/* Photo Gallery Grid */}
                <div className="grid grid-cols-3 gap-2 h-48 rounded-2xl overflow-hidden bg-slate-100 dark:bg-slate-800">
                  <div className="col-span-2 relative h-full">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={drawerRoom.photos[0] || DEFAULT_ROOM_PHOTOS[0]}
                      alt="Room main"
                      className="h-full w-full object-cover"
                    />
                  </div>
                  <div className="grid grid-rows-2 gap-2 h-full">
                    <div className="relative h-full overflow-hidden">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={drawerRoom.photos[1] || drawerRoom.photos[0] || DEFAULT_ROOM_PHOTOS[1]}
                        alt="Room angle"
                        className="h-full w-full object-cover"
                      />
                    </div>
                    <div className="relative h-full overflow-hidden">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={drawerRoom.photos[2] || drawerRoom.photos[0] || DEFAULT_ROOM_PHOTOS[2]}
                        alt="Room detail"
                        className="h-full w-full object-cover"
                      />
                    </div>
                  </div>
                </div>

                {/* Badges & Key Metrics */}
                <div className="flex flex-wrap items-center justify-between gap-3 border-y border-slate-100 py-3 dark:border-slate-800">
                  <div>
                    <p className="text-sm font-semibold text-slate-500 dark:text-slate-400">
                      Monthly Rent:{" "}
                      <strong className="text-2xl font-extrabold text-[#0E2F73] dark:text-white">
                        {formatCurrency(drawerRoom.monthlyRent, drawerRoom.currency)}
                      </strong>
                      <span className="text-xs font-normal"> / month</span>
                    </p>
                    <p className="text-xs text-slate-500">
                      Min stay: {drawerRoom.minStayMonths} months · Available: {drawerRoom.availableDate}
                    </p>
                  </div>

                  <div className="flex flex-col gap-1.5">
                    <span className="inline-flex items-center gap-1 rounded-full bg-[#E7F8EF] px-2.5 py-1 text-xs font-bold text-[#05603A] dark:bg-emerald-950/40 dark:text-emerald-300">
                      <ShieldCheck className="h-3.5 w-3.5" />
                      <span>Property Verified</span>
                    </span>
                    <span className="inline-flex items-center gap-1 rounded-full bg-[#E7F8EF] px-2.5 py-1 text-xs font-bold text-[#05603A] dark:bg-emerald-950/40 dark:text-emerald-300">
                      <Check className="h-3.5 w-3.5" />
                      <span>Listing Authority Verified</span>
                    </span>
                  </div>
                </div>

                {/* Navigation Tabs */}
                <div className="flex border-b border-slate-200 text-xs font-bold dark:border-slate-800">
                  {[
                    { id: "overview", label: "Overview" },
                    { id: "amenities", label: "Amenities" },
                    { id: "availability", label: "Availability" },
                    { id: "rules", label: "House Rules" },
                    { id: "reviews", label: `Reviews${drawerRoom.reviewCount ? ` (${drawerRoom.reviewCount})` : ""}` },
                    { id: "about", label: "About the Property" },
                  ].map((tab) => (
                    <button
                      key={tab.id}
                      type="button"
                      onClick={() => setDrawerTab(tab.id as "overview" | "amenities" | "availability" | "rules" | "reviews" | "about")}
                      className={`pb-2.5 px-3 border-b-2 transition-colors ${
                        drawerTab === tab.id
                          ? "border-[#0E2F73] text-[#0E2F73] dark:border-blue-400 dark:text-blue-400"
                          : "border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400"
                      }`}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>

                {/* Tab Contents */}
                <div className="space-y-4 text-xs leading-relaxed text-slate-700 dark:text-slate-300">
                  {drawerTab === "overview" && (
                    <div className="space-y-4">
                      <p className="text-sm text-slate-600 dark:text-slate-300">
                        {drawerRoom.description || "The host hasn't added a description yet."}
                      </p>
                      <div className="grid grid-cols-2 gap-3 pt-2">
                        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-800/40">
                          <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500">Bathroom</span>
                          <p className="mt-0.5 text-sm font-bold text-[#0E2F73] dark:text-white">
                            {drawerRoom.bathroomType} Bathroom
                          </p>
                        </div>
                        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-800/40">
                          <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500">Furnishing</span>
                          <p className="mt-0.5 text-sm font-bold text-[#0E2F73] dark:text-white capitalize">
                            {drawerRoom.furnishedState === "yes" ? "Fully Furnished" : drawerRoom.furnishedState === "part" ? "Part Furnished" : drawerRoom.furnishedState === "no" ? "Unfurnished" : "Not stated"}
                          </p>
                        </div>
                      </div>
                    </div>
                  )}

                  {drawerTab === "amenities" && (
                    <div className="space-y-3">
                      <h4 className="font-bold text-[#0E2F73] dark:text-white">Amenities</h4>
                      <p className="text-[11px] text-slate-500">As listed by the host.</p>
                      {drawerRoom.amenities.length === 0 && (
                        <p className="text-slate-500">The host hasn&apos;t listed any amenities.</p>
                      )}
                      <ul className="grid grid-cols-2 gap-2">
                        {drawerRoom.amenities.map((am) => (
                          <li key={am} className="flex items-center gap-2 rounded-lg bg-slate-50 p-2.5 dark:bg-slate-800/40">
                            <Check className="h-4 w-4 text-[#05603A]" />
                            <span className="font-semibold text-slate-800 dark:text-slate-200">{am}</span>
                          </li>
                        ))}
                      </ul>
                      {drawerRoom.accessibility.length > 0 && (
                        <div className="mt-4 pt-3 border-t border-slate-100 dark:border-slate-800">
                          <h4 className="font-bold text-[#0E2F73] dark:text-white mb-2">Accessibility Features</h4>
                          <ul className="space-y-1">
                            {drawerRoom.accessibility.map((acc) => (
                              <li key={acc} className="flex items-center gap-2">
                                <Check className="h-4 w-4 text-[#05603A]" />
                                <span>{acc}</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  )}

                  {drawerTab === "availability" && (
                    <div className="space-y-3">
                      <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-800/40 space-y-2">
                        <div className="flex justify-between">
                          <span className="font-semibold text-slate-500">Available from:</span>
                          <span className="font-bold text-[#0E2F73] dark:text-white">{drawerRoom.availableDate}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="font-semibold text-slate-500">Minimum stay:</span>
                          <span className="font-bold text-[#0E2F73] dark:text-white">{drawerRoom.minStayMonths} months</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="font-semibold text-slate-500">Application status:</span>
                          <span className="font-bold text-[#05603A] capitalize">{drawerRoom.applicationsState}</span>
                        </div>
                      </div>
                      <p className="text-slate-500 italic">
                        All leases on Zoiko Rooms are formalized with signed Residential Occupancy Agreements and protected security deposits.
                      </p>
                    </div>
                  )}

                  {drawerTab === "rules" && (
                    <div className="space-y-2">
                      <h4 className="font-bold text-[#0E2F73] dark:text-white">Host House Rules</h4>
                      {(drawerRoom.houseRules || []).length === 0 && (
                        <p className="text-slate-500">The host hasn&apos;t listed any house rules.</p>
                      )}
                      <ul className="space-y-2">
                        {(drawerRoom.houseRules || []).map((rule, idx) => (
                          <li key={idx} className="flex items-start gap-2">
                            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[#EAF0FB] font-bold text-[#0E2F73] text-[10px]">
                              {idx + 1}
                            </span>
                            <span className="mt-0.5">{rule}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {drawerTab === "reviews" && <ListingReviews listingId={drawerRoom.listingId} />}

                  {drawerTab === "about" && (
                    <div className="space-y-3">
                      <div className="rounded-2xl bg-[#EEF3FF] p-4 text-[#1D3F8F] dark:bg-blue-950/40 dark:text-blue-300 space-y-2">
                        <p className="font-bold flex items-center gap-1.5">
                          <ShieldCheck className="h-4 w-4" />
                          <span>Property Location & Authority Verified</span>
                        </p>
                        <p className="leading-relaxed">
                          The host of this property has completed identity verification, property title verification, and declared formal authority to lease private rooms at this location.
                        </p>
                      </div>
                      <p className="text-slate-500">
                        Exact building and apartment address are shared upon application acceptance and agreement execution.
                      </p>
                    </div>
                  )}
                </div>
              </div>

              {/* Drawer Fixed Footer Actions */}
              <div className="border-t border-slate-200 bg-white p-4 sm:px-6 flex items-center justify-between gap-3 dark:border-slate-800 dark:bg-slate-900 shrink-0">
                <p className="flex max-w-[55%] items-center gap-2 text-[11px] text-slate-500 dark:text-slate-400">
                  <MessageSquare className="h-4 w-4 shrink-0" />
                  <span>Questions? Add them to your application message -- the host replies in your application&apos;s chat.</span>
                </p>

                {appliedRooms[drawerRoom.id] ? (
                  <span className="flex items-center gap-2 rounded-xl bg-emerald-50 px-6 py-2.5 text-xs font-bold text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">
                    <Check className="h-4 w-4" />
                    <span>Applied</span>
                  </span>
                ) : user && identityVerified ? (
                  <button
                    type="button"
                    onClick={(e) => handleOpenApply(drawerRoom, e)}
                    className="flex items-center gap-2 rounded-xl bg-[#D80B0B] px-6 py-2.5 text-xs font-bold text-white shadow-xs hover:bg-[#B80909]"
                  >
                    <Check className="h-4 w-4" />
                    <span>Apply for Room</span>
                  </button>
                ) : sessionLoading ? null : (
                  <Link
                    href={user ? "/account/identity" : "/account/login"}
                    className="flex items-center gap-2 rounded-xl border border-[#D80B0B] px-4 py-2.5 text-xs font-bold text-[#D80B0B] hover:bg-red-50 dark:hover:bg-red-950/30"
                  >
                    <ShieldCheck className="h-4 w-4" />
                    <span>{user ? "Verify identity to apply" : "Sign in to apply"}</span>
                  </Link>
                )}
              </div>
            </aside>
          </div>,
          document.body
        )}

      {/* 7. Apply Modal */}
      {mounted &&
        applyModalRoom &&
        createPortal(
          <div className="fixed inset-0 z-[70] flex items-center justify-center p-4">
            <div className="fixed inset-0 bg-slate-900/60 backdrop-blur-xs" onClick={() => setApplyModalRoom(null)} />
            <div className="relative z-10 w-full max-w-lg rounded-3xl bg-white p-6 shadow-2xl dark:bg-slate-900 max-h-[90vh] overflow-y-auto animate-in fade-in zoom-in-95 duration-150">
              <div className="flex items-start justify-between">
                <div>
                  <span className="text-[11px] font-bold uppercase tracking-wider text-[#D80B0B]">
                    Rental Application
                  </span>
                  <h3 className="font-heading text-xl font-extrabold text-[#0E2F73] dark:text-white mt-0.5">
                    Apply for {applyModalRoom.title}
                  </h3>
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    {applyModalRoom.area} · {formatCurrency(applyModalRoom.monthlyRent, applyModalRoom.currency)} / month
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setApplyModalRoom(null)}
                  className="rounded-full p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800"
                >
                  <X className="h-5 w-5" />
                </button>
              </div>

              <form onSubmit={handleConfirmApply} className="mt-4 space-y-4">
                <div>
                  <label className="text-xs font-bold text-slate-700 dark:text-slate-300">
                    Desired Move-in Date
                  </label>
                  <input
                    type="date"
                    required
                    value={applyDesiredMoveIn}
                    onChange={(e) => setApplyDesiredMoveIn(e.target.value)}
                    className="mt-1 w-full rounded-xl border border-slate-300 p-2.5 text-xs font-semibold focus:border-[#0E2F73] focus:outline-none dark:border-slate-700 dark:bg-slate-800"
                  />
                </div>

                <div>
                  <label className="text-xs font-bold text-slate-700 dark:text-slate-300">
                    Message to Lister
                  </label>
                  <textarea
                    rows={4}
                    value={applyMessage}
                    onChange={(e) => setApplyMessage(e.target.value)}
                    placeholder="Introduce yourself, mention your occupation or studies, and confirm your preferred duration..."
                    className="mt-1 w-full rounded-xl border border-slate-300 p-2.5 text-xs focus:border-[#0E2F73] focus:outline-none dark:border-slate-700 dark:bg-slate-800"
                  />
                </div>

                <div className="rounded-xl bg-[#EEF3FF] p-3 text-xs text-[#1D3F8F] dark:bg-blue-950/40 dark:text-blue-300">
                  <p className="font-semibold">Safe Application Process</p>
                  <p className="mt-0.5 text-[11px] leading-relaxed">
                    Your identity and background remain secure. Once the host reviews your application, you will receive an offer in your portal.
                  </p>
                </div>

                {applyError && (
                  <p className="rounded-xl bg-red-50 p-2.5 text-xs font-semibold text-red-600 dark:bg-red-950/40 dark:text-red-400">
                    {applyError}
                  </p>
                )}

                <div className="flex justify-end gap-2 pt-2">
                  <button
                    type="button"
                    onClick={() => setApplyModalRoom(null)}
                    className="rounded-xl border border-slate-300 px-4 py-2.5 text-xs font-bold text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={applySubmitting}
                    className="rounded-xl bg-[#0E2F73] px-6 py-2.5 text-xs font-bold text-white shadow-xs hover:bg-[#0B2560] disabled:opacity-50"
                  >
                    {applySubmitting ? "Submitting..." : "Submit Application"}
                  </button>
                </div>
              </form>
            </div>
          </div>,
          document.body
        )}

      {/* 9. Toast Notification Portal */}
      {toastMsg &&
        typeof document !== "undefined" &&
        createPortal(
          <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[70] flex items-center gap-2 rounded-2xl bg-[#0B1B3F] px-5 py-3 text-xs font-bold text-white shadow-2xl animate-in fade-in slide-in-from-bottom-2 duration-150">
            <Info className="h-4 w-4 text-blue-300 shrink-0" />
            <span>{toastMsg}</span>
          </div>,
          document.body
        )}
    </div>
  );
}
