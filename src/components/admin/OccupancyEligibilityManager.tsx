"use client";

import { useCallback, useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { OccupancyEligibilityCheck } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { apiClientFetch } from "@/lib/api-client";
import { occupancyEligibilityMethodLabel, occupancyEligibilityStatusTone } from "@/lib/status";
import { formatDate } from "@/lib/utils";

/** ZR-ENG-CLR-012 Section 9/23: Occupancy Eligibility (e.g. England's right
 * to rent) is completed automatically once the renter's identity is
 * verified. Read-only here: open checks are older records from before this
 * was automated. Kept separate from identity verification (Section 12). */
export function OccupancyEligibilityManager() {
  const [checks, setChecks] = useState<OccupancyEligibilityCheck[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setChecks(await apiClientFetch<OccupancyEligibilityCheck[]>("/api/verification/occupancy-eligibility-checks"));
      setError("");
    } catch {
      setError("Failed to load occupancy eligibility checks");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex items-center gap-2">
        <ShieldCheck className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
        <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Occupancy Eligibility</h2>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Jurisdiction-specific eligibility (e.g. England&apos;s right to rent), completed automatically once the
        renter&apos;s identity is verified — never decided here.
      </p>

      <div className="mt-4 space-y-2">
        {loading ? (
          <p className="text-sm text-slate-400">Loading...</p>
        ) : error ? (
          <p className="text-sm text-accent-700" role="alert">{error}</p>
        ) : checks.length === 0 ? (
          <p className="text-sm text-slate-400">No open occupancy eligibility checks.</p>
        ) : (
          checks.map((check) => (
            <div
              key={check.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10"
            >
              <div className="min-w-0">
                <p className="text-sm font-semibold text-primary-900 dark:text-white">
                  Party #{check.partyId} · {check.jurisdictionCode}
                </p>
                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                  {occupancyEligibilityMethodLabel[check.method] ?? check.method}
                  {check.shareCode && ` — code: ${check.shareCode}`}
                </p>
                {check.reasonNote && <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{check.reasonNote}</p>}
                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">Submitted {formatDate(check.createdAt)}</p>
              </div>
              <Badge tone={occupancyEligibilityStatusTone[check.status] ?? "neutral"}>{check.status.replace(/_/g, " ")}</Badge>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
