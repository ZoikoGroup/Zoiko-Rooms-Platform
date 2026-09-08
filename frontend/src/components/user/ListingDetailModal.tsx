"use client";

import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { PublicListing } from "@/lib/types";
import { errorMessage, getPublicListing, listRentalApplications } from "@/lib/user-api";
import { ApplyForRoomModal } from "@/components/user/ApplyForRoomModal";
import { ListingDetailContent } from "@/components/user/ListingDetailContent";
import { EmptyState } from "@/components/user/ui";

/** The Find a Room card-click flow: full listing/property details and image
 *  gallery in a large floating dialog over the browse page, so a renter never
 *  leaves Find a Room just to look at a room. Reuses the same detail content and
 *  apply flow as the standalone /account/rent/[id] page. */
export function ListingDetailModal({
  listingId,
  onClose,
  onApplied,
}: {
  listingId: string | null;
  onClose: () => void;
  onApplied: (listingId: string) => void;
}) {
  const [listing, setListing] = useState<PublicListing | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [applied, setApplied] = useState(false);
  const [applying, setApplying] = useState(false);

  useEffect(() => {
    if (!listingId) return;
    setListing(null);
    setLoadError("");
    setApplying(false);
    setLoading(true);
    Promise.all([getPublicListing(listingId), listRentalApplications().catch(() => [])])
      .then(([found, applications]) => {
        setListing(found);
        setApplied(applications.some((a) => a.listingId === listingId));
      })
      .catch((err) => setLoadError(errorMessage(err, "This listing could not be found. It may have been withdrawn or unpublished.")))
      .finally(() => setLoading(false));
  }, [listingId]);

  return (
    <>
      <Modal open={Boolean(listingId)} onClose={onClose} title={listing?.name ?? "Listing details"} size="xl">
        {loading ? (
          <Loader label="Loading listing" />
        ) : loadError || !listing ? (
          <div className="flex flex-col items-center gap-3 py-8 text-center">
            <AlertTriangle className="h-6 w-6 text-accent-600" />
            <EmptyState message={loadError || "This listing is no longer available."} />
          </div>
        ) : (
          <ListingDetailContent listing={listing} applied={applied} onApplyClick={() => setApplying(true)} />
        )}
      </Modal>

      <ApplyForRoomModal
        listing={applying ? listing : null}
        onClose={() => setApplying(false)}
        onApplied={(id) => {
          setApplied(true);
          setApplying(false);
          onApplied(id);
        }}
      />
    </>
  );
}
