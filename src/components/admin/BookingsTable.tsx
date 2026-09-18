"use client";

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Search } from "lucide-react";
import { Booking, BookingStatus } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { formatCurrency, formatDate } from "@/lib/utils";
import { bookingStatusTone, paymentStatusTone } from "@/lib/status";

const statusFilters: Array<BookingStatus | "all"> = ["all", "confirmed", "pending", "completed", "cancelled"];

// ZR-ENG-CLR-001 Section 1's own architecture (published listing ->
// Application -> Offer -> Agreement -> Occupancy, with RoomHold/
// jurisdiction/overlap/identity gates) is the one real booking pipeline.
// This legacy hotel-style Booking model predates that pipeline, so the
// "New Booking" creation form was removed -- creating one here would
// silently bypass every gate the real pipeline enforces. This view stays
// read-only, for visibility into whatever historical rows already exist.
export function BookingsTable({ bookings }: { bookings: Booking[] }) {
  const [items] = useState(bookings);
  const searchParams = useSearchParams();
  const [query, setQuery] = useState(() => searchParams.get("q") ?? "");
  const [status, setStatus] = useState<BookingStatus | "all">("all");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    const t = setTimeout(() => setLoading(false), 400);
    return () => clearTimeout(t);
  }, [query, status]);

  const filtered = useMemo(() => {
    return items.filter((b) => {
      const matchesStatus = status === "all" || b.status === status;
      const q = query.trim().toLowerCase();
      const matchesQuery =
        !q ||
        b.guestName.toLowerCase().includes(q) ||
        b.listingName.toLowerCase().includes(q) ||
        b.id.toLowerCase().includes(q);
      return matchesStatus && matchesQuery;
    });
  }, [items, query, status]);

  return (
    <div className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 sm:p-6 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400 dark:text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by guest, property or booking ID"
            className="w-full rounded-full bg-slate-50 py-2.5 pl-10 pr-4 text-sm outline-none ring-1 ring-slate-100 transition-all focus:ring-primary-300 dark:bg-slate-800 dark:ring-slate-700"
          />
        </div>
        <div className="flex flex-wrap gap-2">
          {statusFilters.map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`rounded-full px-3.5 py-1.5 text-xs font-semibold capitalize transition-all duration-200 ${
                status === s
                  ? "bg-primary-700 text-white shadow-md shadow-primary-900/25"
                  : "bg-slate-50 text-slate-500 hover:bg-primary-50 hover:text-primary-700 dark:bg-slate-800 dark:text-slate-400 dark:hover:bg-primary-500/10 dark:hover:text-primary-300"
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-5 overflow-x-auto">
        <table className="w-full min-w-[880px] text-left text-sm">
          <thead>
            <tr className="border-b border-slate-100 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:border-slate-800 dark:text-slate-400">
              <th className="py-3 pr-4">Booking ID</th>
              <th className="py-3 pr-4">Guest</th>
              <th className="py-3 pr-4">Property</th>
              <th className="py-3 pr-4">Check-in</th>
              <th className="py-3 pr-4">Check-out</th>
              <th className="py-3 pr-4">Amount</th>
              <th className="py-3 pr-4">Payment</th>
              <th className="py-3 pr-4">Status</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 6 }).map((_, i) => (
                  <tr key={i} className="border-b border-slate-50 dark:border-slate-800">
                    {Array.from({ length: 8 }).map((__, j) => (
                      <td key={j} className="py-3.5 pr-4">
                        <Skeleton className="h-4 w-full max-w-[110px]" />
                      </td>
                    ))}
                  </tr>
                ))
              : filtered.map((b, i) => (
                  <tr
                    key={b.id}
                    className="animate-fade-in border-b border-slate-50 transition-colors hover:bg-primary-50/50 dark:border-slate-800 dark:hover:bg-primary-500/10"
                    style={{ animationDelay: `${Math.min(i, 8) * 0.03}s` }}
                  >
                    <td className="py-3.5 pr-4 font-mono text-xs font-semibold text-primary-700 dark:text-primary-300">{b.id}</td>
                    <td className="py-3.5 pr-4">
                      <div className="flex items-center gap-2.5">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={b.guestAvatar} alt={b.guestName} className="h-8 w-8 rounded-full bg-primary-50 dark:bg-primary-500/10" />
                        <span className="font-medium text-slate-700 dark:text-slate-300">{b.guestName}</span>
                      </div>
                    </td>
                    <td className="py-3.5 pr-4 text-slate-600 dark:text-slate-300">
                      <span className="capitalize text-xs font-semibold text-accent-600">{b.propertyType}</span>
                      <br />
                      {b.listingName}
                    </td>
                    <td className="py-3.5 pr-4 text-slate-500 dark:text-slate-400">{formatDate(b.checkIn)}</td>
                    <td className="py-3.5 pr-4 text-slate-500 dark:text-slate-400">{formatDate(b.checkOut)}</td>
                    <td className="py-3.5 pr-4 font-semibold text-primary-900 dark:text-white">{formatCurrency(b.totalAmount)}</td>
                    <td className="py-3.5 pr-4">
                      <Badge tone={paymentStatusTone[b.paymentStatus]} className="capitalize">
                        {b.paymentStatus}
                      </Badge>
                    </td>
                    <td className="py-3.5 pr-4">
                      <Badge tone={bookingStatusTone[b.status]} className="capitalize">
                        {b.status}
                      </Badge>
                    </td>
                  </tr>
                ))}
          </tbody>
        </table>

        {!loading && filtered.length === 0 && (
          <p className="py-10 text-center text-sm text-slate-400 dark:text-slate-400">No bookings match your search.</p>
        )}
      </div>
    </div>
  );
}
