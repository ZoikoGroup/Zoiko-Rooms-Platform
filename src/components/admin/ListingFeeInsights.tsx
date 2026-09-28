"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { apiClientFetch } from "@/lib/api-client";
import { ListingFeeDuplicateFlag, ListingFeeFunnel } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

const STAGE_LABELS: Record<string, string> = {
  fee_viewed: "Saw the fee",
  checkout_started: "Went to Stripe",
  checkout_completed: "Paid",
  checkout_cancelled: "Cancelled on Stripe",
  checkout_failed: "Payment failed",
};

const WINDOWS = [7, 30, 90] as const;

/** ZR-LF-001 funnel (listings reaching each step) and the listings flagged
 *  as possible duplicates of a room whose fee is already paid. */
export function ListingFeeInsights() {
  const [days, setDays] = useState<(typeof WINDOWS)[number]>(30);
  const [funnel, setFunnel] = useState<ListingFeeFunnel | null>(null);
  const [flags, setFlags] = useState<ListingFeeDuplicateFlag[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiClientFetch<ListingFeeFunnel>(`/api/finance/listing-fees/funnel?days=${days}`)
      .then((f) => !cancelled && setFunnel(f))
      .catch(() => !cancelled && setFunnel(null));
    return () => {
      cancelled = true;
    };
  }, [days]);

  useEffect(() => {
    apiClientFetch<ListingFeeDuplicateFlag[]>("/api/finance/listing-fees/duplicate-flags")
      .then(setFlags)
      .catch(() => setFlags([]));
  }, []);

  const viewed = funnel?.stages.find((s) => s.name === "fee_viewed")?.listings ?? 0;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <div className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-heading text-sm font-bold text-primary-900 dark:text-white">Listing Fee funnel</h3>
          <div role="group" aria-label="Time window" className="flex gap-1">
            {WINDOWS.map((w) => (
              <button
                key={w}
                type="button"
                aria-pressed={days === w}
                onClick={() => setDays(w)}
                className={`rounded-lg px-2 py-1 text-xs font-semibold ${
                  days === w
                    ? "bg-primary-700 text-white"
                    : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
                }`}
              >
                {w} days
              </button>
            ))}
          </div>
        </div>
        {!funnel ? (
          <p className="mt-3 text-sm text-slate-600 dark:text-slate-400">Loading...</p>
        ) : (
          <>
            <ul className="mt-3 space-y-2">
              {funnel.stages.map((stage) => {
                const share = viewed ? Math.round((stage.listings / viewed) * 100) : 0;
                return (
                  <li key={stage.name} className="text-sm">
                    <div className="flex justify-between gap-2">
                      <span className="text-slate-700 dark:text-slate-200">{STAGE_LABELS[stage.name] ?? stage.name}</span>
                      <span className="font-semibold text-slate-900 dark:text-white">
                        {stage.listings}
                        {viewed > 0 && stage.name !== "fee_viewed" && (
                          <span className="ml-1 text-xs font-normal text-slate-600 dark:text-slate-400">({share}%)</span>
                        )}
                      </span>
                    </div>
                    <div className="mt-1 h-1.5 rounded-full bg-slate-100 dark:bg-slate-800" aria-hidden="true">
                      <div className="h-1.5 rounded-full bg-primary-600" style={{ width: `${viewed ? share : 0}%` }} />
                    </div>
                  </li>
                );
              })}
            </ul>
            <p className="mt-3 text-xs text-slate-600 dark:text-slate-400">
              Listings, not attempts -- a host who opens the fee screen several times counts once.
              {funnel.conversionRate != null && ` Saw the fee → paid: ${Math.round(funnel.conversionRate * 100)}%.`}
            </p>
          </>
        )}
      </div>

      <div className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        <h3 className="font-heading text-sm font-bold text-primary-900 dark:text-white">Possible duplicate listings</h3>
        <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
          A second listing for a room whose other listing already paid the fee. Check it isn&apos;t a copy made to avoid or
          reset the fee. Nothing is blocked automatically.
        </p>
        {flags === null ? (
          <p className="mt-3 text-sm text-slate-600 dark:text-slate-400">Loading...</p>
        ) : flags.length === 0 ? (
          <p className="mt-3 text-sm text-slate-600 dark:text-slate-400">No listings flagged.</p>
        ) : (
          <ul className="mt-3 divide-y divide-slate-100 dark:divide-white/10">
            {flags.map((f) => (
              <li key={`${f.listingId}-${f.flaggedAt}`} className="py-2 text-sm">
                <p className="text-slate-800 dark:text-slate-100">
                  <span className="font-semibold">{f.listingName || f.listingId}</span>{" "}
                  <span className="font-mono text-xs text-slate-600 dark:text-slate-400">{f.listingId}</span>{" "}
                  <Badge tone="neutral">{f.listingState.toLowerCase()}</Badge>
                </p>
                <p className="text-xs text-slate-600 dark:text-slate-400">
                  Same room{f.roomId != null ? ` (#${f.roomId})` : ""} as {f.paidListingName || f.paidListingId}{" "}
                  <span className="font-mono">{f.paidListingId}</span> ({f.paidListingState.toLowerCase()}, fee paid) ·
                  flagged {formatDateTime(f.flaggedAt)}
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
