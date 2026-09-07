"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertTriangle, Bath, BedDouble, ChevronLeft, ChevronRight, MapPin, Ruler, Search, Users } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { StarRating } from "@/components/ui/StarRating";
import { PublicListing } from "@/lib/types";
import { formatCurrency, resolveImageUrl } from "@/lib/utils";
import { errorMessage, listPublicListings, listRentalApplications } from "@/lib/user-api";
import { ApplyForRoomModal } from "@/components/user/ApplyForRoomModal";
import { ListingDetailModal } from "@/components/user/ListingDetailModal";
import { IdentityGate } from "@/components/user/IdentityGate";
import { useUserSession } from "@/components/user/UserSessionContext";
import { Card, EmptyState, Field, Toast, inputClass, useToast } from "@/components/user/ui";

const PAGE_SIZE = 12;
const FILTER_DEBOUNCE_MS = 400;

interface FilterState {
  city: string;
  roomType: string;
  minPrice: string;
  maxPrice: string;
}

const emptyFilters: FilterState = { city: "", roomType: "", minPrice: "", maxPrice: "" };

export function RentBrowser() {
  const { user, identityVerified } = useUserSession();
  const { toast, showToast } = useToast();
  const router = useRouter();
  const searchParams = useSearchParams();

  const [filters, setFilters] = useState<FilterState>(() => ({
    ...emptyFilters,
    city: searchParams.get("city") ?? "",
    maxPrice: searchParams.get("maxPrice") ?? "",
  }));
  const [offset, setOffset] = useState(0);

  const [listings, setListings] = useState<PublicListing[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [appliedListingIds, setAppliedListingIds] = useState<Set<string>>(new Set());

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  const [selected, setSelected] = useState<PublicListing | null>(null);
  const [detailListingId, setDetailListingId] = useState<string | null>(null);

  // Applications only need to be read once -- they don't depend on the filters/page.
  useEffect(() => {
    listRentalApplications()
      .then((applications) =>
        setAppliedListingIds(new Set(applications.filter((a) => a.status !== "WITHDRAWN").map((a) => a.listingId)))
      )
      .catch(() => undefined);
  }, []);

  const load = useCallback(
    (currentFilters: FilterState, currentOffset: number) => {
      setLoading(true);
      setLoadError("");
      const minPrice = currentFilters.minPrice.trim() ? Number(currentFilters.minPrice) : undefined;
      const maxPrice = currentFilters.maxPrice.trim() ? Number(currentFilters.maxPrice) : undefined;
      listPublicListings({
        city: currentFilters.city.trim() || undefined,
        roomType: currentFilters.roomType.trim() || undefined,
        minPrice: minPrice !== undefined && !Number.isNaN(minPrice) ? minPrice : undefined,
        maxPrice: maxPrice !== undefined && !Number.isNaN(maxPrice) ? maxPrice : undefined,
        limit: PAGE_SIZE,
        offset: currentOffset,
      })
        .then((page) => {
          setListings(page.items);
          setTotal(page.total);
          setHasMore(page.hasMore);
        })
        .catch((err) => setLoadError(errorMessage(err, "Could not load available rooms right now.")))
        .finally(() => setLoading(false));
    },
    []
  );

  // Debounce filter changes (reset to page 1 on every filter edit) -- except the
  // very first load, which should happen immediately rather than wait out the
  // debounce with nothing on screen. Page changes (Prev/Next) fetch immediately
  // too, since there's no typing to debounce there.
  const isFirstRun = useRef(true);
  useEffect(() => {
    if (isFirstRun.current) {
      isFirstRun.current = false;
      load(filters, 0);
      return;
    }
    const timeout = setTimeout(() => load(filters, 0), FILTER_DEBOUNCE_MS);
    return () => clearTimeout(timeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters]);

  useEffect(() => {
    if (offset === 0) return; // already covered by the filter effect above
    load(filters, offset);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offset]);

  function updateFilter<K extends keyof FilterState>(key: K, value: FilterState[K]) {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setOffset(0);
  }

  function goToPage(direction: "prev" | "next") {
    setOffset((prev) => Math.max(0, prev + (direction === "next" ? PAGE_SIZE : -PAGE_SIZE)));
  }

  function viewDetails(listingId: string) {
    if (!user) {
      router.push("/account/login");
      return;
    }
    setDetailListingId(listingId);
  }

  function openApply(listing: PublicListing) {
    if (!user) {
      router.push("/account/login");
      return;
    }
    setSelected(listing);
  }

  function handleApplied(listingId: string) {
    setAppliedListingIds((prev) => new Set(prev).add(listingId));
    setSelected(null);
    showToast("Application submitted. Track it under My Applications.");
  }

  const hasActiveFilters = Boolean(filters.city || filters.roomType || filters.minPrice || filters.maxPrice);
  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + listings.length, total);

  return (
    <div className="space-y-5">
      {user && (
        <IdentityGate action="apply for a room">
          <Card className="!bg-emerald-50 !ring-emerald-200 dark:!bg-emerald-500/10 dark:!ring-emerald-500/20">
            <p className="text-sm font-semibold text-emerald-800 dark:text-emerald-300">
              Your identity is verified — you can apply to any room below.
            </p>
          </Card>
        </IdentityGate>
      )}

      <Card>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="City">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <input
                value={filters.city}
                onChange={(e) => updateFilter("city", e.target.value)}
                placeholder="e.g. Mumbai"
                className={`${inputClass} pl-10`}
              />
            </div>
          </Field>
          <Field label="Room type">
            <input
              value={filters.roomType}
              onChange={(e) => updateFilter("roomType", e.target.value)}
              placeholder="e.g. Private room"
              className={inputClass}
            />
          </Field>
          <Field label="Min price / night">
            <input
              inputMode="decimal"
              value={filters.minPrice}
              onChange={(e) => updateFilter("minPrice", e.target.value)}
              placeholder="0"
              className={inputClass}
            />
          </Field>
          <Field label="Max price / night">
            <input
              inputMode="decimal"
              value={filters.maxPrice}
              onChange={(e) => updateFilter("maxPrice", e.target.value)}
              placeholder="Any"
              className={inputClass}
            />
          </Field>
        </div>
      </Card>

      {loading ? (
        <Loader label="Loading available rooms" />
      ) : loadError ? (
        <Card>
          <div className="flex flex-col items-center gap-3 py-8 text-center">
            <AlertTriangle className="h-6 w-6 text-accent-600" />
            <p className="text-sm text-slate-500 dark:text-slate-400">{loadError}</p>
            <Button size="sm" variant="outline" onClick={() => load(filters, offset)}>
              Retry
            </Button>
          </div>
        </Card>
      ) : listings.length === 0 ? (
        <Card>
          <EmptyState
            message={hasActiveFilters ? "No rooms match those filters." : "No rooms are published yet. Check back once hosts publish their listings."}
          />
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {listings.map((listing) => {
              const applied = appliedListingIds.has(listing.id);
              return (
                <div
                  key={listing.id}
                  className="flex flex-col overflow-hidden rounded-2xl bg-white shadow-sm ring-1 ring-slate-100 transition-all duration-300 hover:-translate-y-1 hover:shadow-lg dark:bg-slate-900 dark:ring-white/10"
                >
                  <button
                    type="button"
                    onClick={() => viewDetails(listing.id)}
                    className="block w-full text-left"
                    aria-label={`View details for ${listing.name}`}
                  >
                    {listing.images[0] ? (
                      /* eslint-disable-next-line @next/next/no-img-element */
                      <img src={resolveImageUrl(listing.images[0])} alt={listing.name} className="h-44 w-full object-cover" />
                    ) : (
                      <div className="flex h-44 w-full items-center justify-center bg-primary-50 dark:bg-primary-500/10">
                        <BedDouble className="h-8 w-8 text-primary-300" />
                      </div>
                    )}
                  </button>

                  <div className="flex flex-1 flex-col p-4">
                    <button
                      type="button"
                      onClick={() => viewDetails(listing.id)}
                      className="flex items-start justify-between gap-2 text-left"
                    >
                      <p className="font-heading text-sm font-bold text-primary-900 dark:text-white hover:underline">{listing.name}</p>
                      <Badge tone="primary">{listing.roomType}</Badge>
                    </button>

                    <p className="mt-1 flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
                      <MapPin className="h-3.5 w-3.5" /> {listing.location}, {listing.city}
                    </p>

                    <div className="mt-1.5 flex items-center gap-1.5">
                      {listing.reviewCount > 0 ? (
                        <>
                          <StarRating rating={listing.rating} size={12} />
                          <span className="text-xs text-slate-400">
                            {listing.rating.toFixed(1)} ({listing.reviewCount} review{listing.reviewCount === 1 ? "" : "s"})
                          </span>
                        </>
                      ) : (
                        <span className="text-xs font-medium text-slate-400">New · no reviews yet</span>
                      )}
                    </div>

                    <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
                      <span className="flex items-center gap-1">
                        <Users className="h-3.5 w-3.5" /> {listing.guests} guest{listing.guests === 1 ? "" : "s"}
                      </span>
                      <span className="flex items-center gap-1">
                        <BedDouble className="h-3.5 w-3.5" /> {listing.bedrooms} bed{listing.bedrooms === 1 ? "" : "s"}
                      </span>
                      <span className="flex items-center gap-1">
                        <Bath className="h-3.5 w-3.5" /> {listing.bathrooms}
                      </span>
                      {listing.size > 0 && (
                        <span className="flex items-center gap-1">
                          <Ruler className="h-3.5 w-3.5" /> {listing.size} sq ft
                        </span>
                      )}
                    </div>

                    {listing.amenities.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {listing.amenities.slice(0, 3).map((amenity) => (
                          <Badge key={amenity} tone="neutral" className="!text-[10px]">
                            {amenity}
                          </Badge>
                        ))}
                        {listing.amenities.length > 3 && (
                          <Badge tone="neutral" className="!text-[10px]">
                            +{listing.amenities.length - 3} more
                          </Badge>
                        )}
                      </div>
                    )}

                    {listing.description && (
                      <p className="mt-3 line-clamp-2 text-xs text-slate-500 dark:text-slate-400">
                        {listing.description}
                      </p>
                    )}

                    <div className="mt-auto flex items-end justify-between gap-2 pt-4">
                      <div>
                        <p className="font-heading text-lg font-extrabold text-primary-900 dark:text-white">
                          {formatCurrency(listing.pricePerNight, listing.currency)}
                          <span className="text-xs font-medium text-slate-400"> / night</span>
                        </p>
                        <p className="text-xs text-slate-400">Min. {listing.minStayNights} nights</p>
                      </div>
                      <Button
                        size="sm"
                        variant={applied ? "outline" : "primary"}
                        disabled={applied || (Boolean(user) && !identityVerified)}
                        onClick={() => openApply(listing)}
                      >
                        {applied ? "Applied" : "Apply"}
                      </Button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
            <p className="text-xs text-slate-400">
              Showing {rangeStart}–{rangeEnd} of {total} room{total === 1 ? "" : "s"}
            </p>
            <div className="flex items-center gap-2">
              <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => goToPage("prev")}>
                <ChevronLeft className="h-3.5 w-3.5" /> Previous
              </Button>
              <Button size="sm" variant="outline" disabled={!hasMore} onClick={() => goToPage("next")}>
                Next <ChevronRight className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        </>
      )}

      <ApplyForRoomModal listing={selected} onClose={() => setSelected(null)} onApplied={handleApplied} />
      <ListingDetailModal listingId={detailListingId} onClose={() => setDetailListingId(null)} onApplied={handleApplied} />

      <p className="text-center text-xs text-slate-400">
        Already applied somewhere?{" "}
        <Link href="/account/applications" className="font-semibold text-primary-700 dark:text-primary-300">
          Track your applications
        </Link>
      </p>

      <Toast toast={toast} />
    </div>
  );
}
