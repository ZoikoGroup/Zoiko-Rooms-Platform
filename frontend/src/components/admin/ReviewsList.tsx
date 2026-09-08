"use client";

import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { Review } from "@/lib/types";
import { StarRating } from "@/components/ui/StarRating";
import { formatDate } from "@/lib/utils";

export function ReviewsList({ reviews }: { reviews: Review[] }) {
  const [query, setQuery] = useState("");
  const [minRating, setMinRating] = useState(0);

  const filtered = useMemo(() => {
    return reviews.filter((r) => {
      const matchesRating = r.rating >= minRating;
      const q = query.trim().toLowerCase();
      const matchesQuery =
        !q || r.guestName.toLowerCase().includes(q) || r.listingName.toLowerCase().includes(q);
      return matchesRating && matchesQuery;
    });
  }, [reviews, query, minRating]);

  return (
    <div className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 sm:p-6 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400 dark:text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by guest or property"
            className="w-full rounded-full bg-slate-50 py-2.5 pl-10 pr-4 text-sm outline-none ring-1 ring-slate-100 transition-all focus:ring-primary-300 dark:bg-slate-800 dark:ring-slate-700"
          />
        </div>
        <select
          value={minRating}
          onChange={(e) => setMinRating(Number(e.target.value))}
          className="rounded-full bg-slate-50 px-4 py-2.5 text-sm font-medium text-slate-600 outline-none ring-1 ring-slate-100 focus:ring-primary-300 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700"
        >
          <option value={0}>All ratings</option>
          <option value={5}>5 stars</option>
          <option value={4}>4 stars &amp; up</option>
          <option value={3}>3 stars &amp; up</option>
        </select>
      </div>

      <div className="mt-5 space-y-4">
        {filtered.map((r, i) => (
          <div
            key={r.id}
            className="animate-fade-up flex gap-3 rounded-2xl p-4 ring-1 ring-slate-100 transition-all duration-300 hover:-translate-y-0.5 hover:shadow-md dark:ring-white/10"
            style={{ animationDelay: `${Math.min(i, 8) * 0.04}s` }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={r.guestAvatar} alt={r.guestName} className="h-11 w-11 shrink-0 rounded-full bg-primary-50 dark:bg-primary-500/10" />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <span className="font-bold text-primary-900 dark:text-white">{r.guestName}</span>
                  <span className="ml-2 text-xs text-slate-400 dark:text-slate-400">{formatDate(r.date)}</span>
                </div>
              </div>
              <p className="mt-0.5 text-xs font-medium text-accent-600">{r.listingName}</p>
              <div className="mt-1.5 flex items-center gap-2">
                <StarRating rating={r.rating} size={13} />
              </div>
              <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">{r.comment}</p>
            </div>
          </div>
        ))}

        {filtered.length === 0 && (
          <p className="py-10 text-center text-sm text-slate-400 dark:text-slate-400">No reviews match your filters.</p>
        )}
      </div>
    </div>
  );
}
