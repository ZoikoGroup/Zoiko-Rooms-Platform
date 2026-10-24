"use client";

import { useEffect, useState } from "react";
import { StarRating } from "@/components/ui/StarRating";
import { listPublicListingReviews, PublicReview } from "@/lib/user-api";
import { formatDate } from "@/lib/utils";

/** What earlier renters said about a room. Public: works for visitors who are
 *  not signed in. Reviewer names arrive already shortened ("Priya S."). */
export function ListingReviews({ listingId }: { listingId: string }) {
  // `null` while loading.
  const [reviews, setReviews] = useState<PublicReview[] | null>(null);

  useEffect(() => {
    let live = true;
    setReviews(null);
    listPublicListingReviews(listingId)
      .then((r) => live && setReviews(r))
      .catch(() => live && setReviews([]));
    return () => {
      live = false;
    };
  }, [listingId]);

  if (reviews === null) return <p className="text-sm text-slate-400">Loading reviews…</p>;

  if (reviews.length === 0) {
    return (
      <p className="text-sm text-slate-500 dark:text-slate-400">
        No reviews yet. Renters can review a room once they have rented it.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {reviews.map((r) => (
        <div key={r.id} className="flex gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary-50 text-sm font-bold text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
            {r.guestName.charAt(0).toUpperCase()}
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-primary-900 dark:text-white">
              {r.guestName}
              <span className="ml-2 text-xs font-normal text-slate-400">{formatDate(r.date)}</span>
            </p>
            <StarRating rating={r.rating} size={13} />
            {r.comment && <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">{r.comment}</p>}
          </div>
        </div>
      ))}
    </div>
  );
}
