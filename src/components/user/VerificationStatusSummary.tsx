"use client";

import { useEffect, useState } from "react";
import { ChevronDown, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { RenterVerificationStatus, RenterVerificationStatusItem } from "@/lib/types";
import { renterVerificationStatusTone } from "@/lib/status";
import { cn, formatDate } from "@/lib/utils";
import { getMyVerificationStatus } from "@/lib/user-api";
import { Card, EmptyState } from "@/components/user/ui";

/** ZR-ENG-CLR-012 Section 19: Renter Verification Center -- "showing only
 * requirements relevant to the current booking/application and explaining
 * why each is needed." A read-only summary; submitting identity evidence
 * itself stays in IdentityVerificationManager below it on this page. */
export function VerificationStatusSummary() {
  const [status, setStatus] = useState<RenterVerificationStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMyVerificationStatus()
      .then((data) => {
        if (!cancelled) setStatus(data);
      })
      .catch(() => {
        if (!cancelled) setStatus(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function row(item: RenterVerificationStatusItem, label: string) {
    const key = `${item.requirementCode}-${item.jurisdictionCode}`;
    const expanded = expandedKey === key;
    const hasDetails = Boolean(item.sharingScope || item.retentionNote || item.alternativeMethodNote);
    return (
      <div key={key} className="py-2.5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-primary-900 dark:text-white">{label}</p>
            {item.explanation && <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{item.explanation}</p>}
            {item.expiresAt && (
              <p className="mt-0.5 text-xs text-slate-400">
                {item.status === "PASS" || item.status === "verified" ? "Valid until" : "Due"} {formatDate(item.expiresAt)}
              </p>
            )}
            {hasDetails && (
              <button
                type="button"
                onClick={() => setExpandedKey(expanded ? null : key)}
                className="mt-1 flex items-center gap-0.5 text-xs font-semibold text-primary-700 hover:text-accent-600 dark:text-primary-300"
              >
                <ChevronDown className={cn("h-3 w-3 transition-transform", expanded && "rotate-180")} />
                {expanded ? "Hide details" : "Sharing, retention & alternatives"}
              </button>
            )}
            {expanded && (
              <div className="mt-1.5 space-y-1 rounded-lg bg-slate-50 p-2.5 text-xs text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                {item.sharingScope && (
                  <p>
                    <span className="font-semibold text-slate-600 dark:text-slate-300">Who sees this: </span>
                    {item.sharingScope}
                  </p>
                )}
                {item.retentionNote && (
                  <p>
                    <span className="font-semibold text-slate-600 dark:text-slate-300">Retention: </span>
                    {item.retentionNote}
                  </p>
                )}
                {item.alternativeMethodNote && (
                  <p>
                    <span className="font-semibold text-slate-600 dark:text-slate-300">Alternatives: </span>
                    {item.alternativeMethodNote}
                  </p>
                )}
              </div>
            )}
          </div>
          <Badge tone={renterVerificationStatusTone[item.status] ?? "neutral"}>{item.status.replace(/_/g, " ")}</Badge>
        </div>
      </div>
    );
  }

  return (
    <Card>
      <div className="flex items-center gap-2">
        <ShieldCheck className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
        <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Your verification status</h2>
      </div>
      <div className="mt-3 divide-y divide-slate-100 dark:divide-white/10">
        {loading ? (
          <p className="py-6 text-sm text-slate-400">Loading...</p>
        ) : !status ? (
          <EmptyState message="Verification status is unavailable right now." />
        ) : (
          <>
            {row(status.identity, "Identity")}
            {status.occupancyEligibility.map((item) =>
              row(item, `Occupancy eligibility — ${item.jurisdictionCode}`)
            )}
          </>
        )}
      </div>
    </Card>
  );
}
