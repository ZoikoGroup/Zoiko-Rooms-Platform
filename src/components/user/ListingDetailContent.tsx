"use client";

import { Bath, BedDouble, MapPin, Ruler, Users } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { StarRating } from "@/components/ui/StarRating";
import { PublicListing } from "@/lib/types";
import { formatCurrency } from "@/lib/utils";
import { ListingImageGallery } from "@/components/user/ListingImageGallery";
import { Card } from "@/components/user/ui";

/** The full listing/property detail body -- gallery, name/location/price, stats,
 *  description, amenities, host, and the Apply CTA. Shared by the standalone
 *  /account/rent/[id] page and the floating listing detail modal so the detail
 *  UI only exists in one place. */
export function ListingDetailContent({
  listing,
  applied,
  onApplyClick,
}: {
  listing: PublicListing;
  applied: boolean;
  onApplyClick: () => void;
}) {
  return (
    <div className="space-y-5">
      <ListingImageGallery images={listing.images} alt={listing.name} />

      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="font-heading text-xl font-extrabold text-primary-900 dark:text-white">{listing.name}</h1>
            <Badge tone="primary">{listing.roomType}</Badge>
          </div>
          <p className="mt-1 flex items-center gap-1.5 text-sm text-slate-500 dark:text-slate-400">
            <MapPin className="h-4 w-4" /> {listing.location}, {listing.city}
          </p>
          {listing.reviewCount > 0 && (
            <div className="mt-1.5 flex items-center gap-1.5">
              <StarRating rating={listing.rating} size={14} />
              <span className="text-xs text-slate-400">
                {listing.rating.toFixed(1)} ({listing.reviewCount} review{listing.reviewCount === 1 ? "" : "s"})
              </span>
            </div>
          )}
        </div>

        <div className="shrink-0 text-right">
          <p className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">
            {formatCurrency(listing.pricePerNight, listing.currency)}
            <span className="text-sm font-medium text-slate-400"> / night</span>
          </p>
          <p className="text-xs text-slate-400">Minimum stay {listing.minStayNights} nights</p>
        </div>
      </div>

      <Card>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <div className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
            <Users className="h-4 w-4 text-primary-600" /> {listing.guests} guest{listing.guests === 1 ? "" : "s"}
          </div>
          <div className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
            <BedDouble className="h-4 w-4 text-primary-600" /> {listing.bedrooms} bedroom{listing.bedrooms === 1 ? "" : "s"}
          </div>
          <div className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
            <Bath className="h-4 w-4 text-primary-600" /> {listing.bathrooms} bathroom{listing.bathrooms === 1 ? "" : "s"}
          </div>
          {listing.size > 0 && (
            <div className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
              <Ruler className="h-4 w-4 text-primary-600" /> {listing.size} sq ft
            </div>
          )}
        </div>
      </Card>

      {listing.description && (
        <Card>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Description</p>
          <p className="whitespace-pre-line text-sm text-slate-600 dark:text-slate-300">{listing.description}</p>
        </Card>
      )}

      {listing.amenities.length > 0 && (
        <Card>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Amenities</p>
          <div className="flex flex-wrap gap-2">
            {listing.amenities.map((amenity) => (
              <Badge key={amenity} tone="neutral">
                {amenity}
              </Badge>
            ))}
          </div>
        </Card>
      )}

      <Card>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Host</p>
        <p className="text-sm text-slate-600 dark:text-slate-300">{listing.ownerName || "Zoiko host"}</p>
      </Card>

      <Card className="!bg-emerald-50 !ring-emerald-200 dark:!bg-emerald-500/10 dark:!ring-emerald-500/20">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-emerald-800 dark:text-emerald-300">Ready to apply?</p>
            {/* ZR-ENG-CLR-012 Section 5/AC-03: progressive verification -- no
               global requirement to verify identity before applying. Full
               identity verification is only required later, before your
               agreement can be prepared (see VerificationStatusSummary on
               the dashboard for that check). */}
            <p className="text-xs text-emerald-700 dark:text-emerald-400">
              You can apply without verifying your identity first — you&apos;ll need a verified identity later, before your agreement can be prepared.
            </p>
          </div>
          <Button disabled={applied} onClick={onApplyClick}>
            {applied ? "Applied" : "Apply for this room"}
          </Button>
        </div>
      </Card>
    </div>
  );
}
