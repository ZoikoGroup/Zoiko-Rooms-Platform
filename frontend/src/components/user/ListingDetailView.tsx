"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowLeft } from "lucide-react";
import { Loader } from "@/components/ui/Loader";
import { PublicListing } from "@/lib/types";
import { errorMessage, getPublicListing, listRentalApplications } from "@/lib/user-api";
import { ApplyForRoomModal } from "@/components/user/ApplyForRoomModal";
import { ListingDetailContent } from "@/components/user/ListingDetailContent";
import { Card, EmptyState, Toast, useToast } from "@/components/user/ui";

/** Standalone page for a direct link to a listing (e.g. from a notification or
 *  a shared URL). The normal Find a Room card-click flow instead opens
 *  ListingDetailModal over the browse page -- both share ListingDetailContent. */
export function ListingDetailView({ listingId }: { listingId: string }) {
  const { toast, showToast } = useToast();

  const [listing, setListing] = useState<PublicListing | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [applied, setApplied] = useState(false);
  const [applying, setApplying] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [found, applications] = await Promise.all([
        getPublicListing(listingId),
        listRentalApplications().catch(() => []),
      ]);
      setListing(found);
      setApplied(applications.some((a) => a.listingId === listingId));
    } catch (err) {
      setLoadError(errorMessage(err, "This listing could not be found. It may have been withdrawn or unpublished."));
    } finally {
      setLoading(false);
    }
  }, [listingId]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) return <Loader label="Loading listing" />;

  if (loadError || !listing) {
    return (
      <div className="space-y-4">
        <Link href="/account/rent" className="inline-flex items-center gap-1.5 text-sm font-semibold text-primary-700 dark:text-primary-300">
          <ArrowLeft className="h-4 w-4" /> Back to Find a Room
        </Link>
        <Card>
          <div className="flex flex-col items-center gap-3 py-8 text-center">
            <AlertTriangle className="h-6 w-6 text-accent-600" />
            <EmptyState message={loadError || "This listing is no longer available."} />
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <Link href="/account/rent" className="inline-flex items-center gap-1.5 text-sm font-semibold text-primary-700 dark:text-primary-300">
        <ArrowLeft className="h-4 w-4" /> Back to Find a Room
      </Link>

      <ListingDetailContent listing={listing} applied={applied} onApplyClick={() => setApplying(true)} />

      <ApplyForRoomModal
        listing={applying ? listing : null}
        onClose={() => setApplying(false)}
        onApplied={() => {
          setApplied(true);
          setApplying(false);
          showToast("Application submitted. Track it under My Applications.");
        }}
      />

      <Toast toast={toast} />
    </div>
  );
}
