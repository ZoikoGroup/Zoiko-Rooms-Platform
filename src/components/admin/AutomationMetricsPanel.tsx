"use client";

import { useEffect, useState } from "react";
import { Activity } from "lucide-react";
import { apiClientFetch } from "@/lib/api-client";

type Counts = Record<string, number>;

interface PaymentMetrics {
  totalListingFeePayments: number;
  listingFeePaymentFailureRate: number | null;
  avgListingFeeTimeToSuccessSeconds: number | null;
  totalListingFeeRefunds: number;
  totalRentalPaymentObligations: number;
  obligationsByStatus: Counts;
  totalRentalPaymentRecords: number;
  recordsByStatus: Counts;
  rentalPaymentDiscrepancyRate: number | null;
}

interface DisputeMetrics {
  totalCases: number;
  casesByStatus: Counts;
  avgResolutionTimeDays: number | null;
  reopenRate: number | null;
  settlementRate: number | null;
  externalReferralRate: number | null;
}

interface LeasingMetrics {
  approvalTurnaroundSecondsAvg: number | null;
  publicationFailureRate: number | null;
  holdConversionRate: number | null;
  holdExpiryRate: number | null;
  staleHoldCount: number;
  duplicateConfirmationIncidents: number;
  manualOverrideFrequency: number;
}

interface Tile {
  label: string;
  value: string;
}

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);
const num = (v: number | null | undefined) => (v === null || v === undefined ? "—" : String(v));
const secs = (v: number | null | undefined) => {
  if (v === null || v === undefined) return "—";
  return v < 60 ? `${Math.round(v)} s` : v < 3600 ? `${Math.round(v / 60)} min` : `${(v / 3600).toFixed(1)} hrs`;
};
const days = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v.toFixed(1)} days`);
const breakdown = (c: Counts | undefined) => {
  const entries = Object.entries(c ?? {});
  return entries.length === 0 ? "—" : entries.map(([k, n]) => `${n} ${k.replace(/_/g, " ").toLowerCase()}`).join(" · ");
};

function Group({ title, tiles }: { title: string; tiles: Tile[] }) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">{title}</p>
      <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {tiles.map((t) => (
          <div key={t.label} className="rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10">
            <p className="text-xs text-slate-500 dark:text-slate-400">{t.label}</p>
            <p className="mt-1 break-words text-sm font-bold text-primary-900 dark:text-white">{t.value}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Read-only automation health for the super admin. Decisions are made by the
 *  platform's automation and by hosts and renters, so this only reports what
 *  happened -- there is nothing to approve or review here. */
export function AutomationMetricsPanel({ source }: { source: "payments" | "leasing" }) {
  const [groups, setGroups] = useState<Array<{ title: string; tiles: Tile[] }> | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    const run = async () => {
      if (source === "payments") {
        const [p, d] = await Promise.all([
          apiClientFetch<PaymentMetrics>("/api/analytics/payment-operational-metrics"),
          apiClientFetch<DisputeMetrics>("/api/analytics/dispute-operational-metrics"),
        ]);
        return [
          {
            title: "Listing fees",
            tiles: [
              { label: "Payments", value: num(p.totalListingFeePayments) },
              { label: "Failure rate", value: pct(p.listingFeePaymentFailureRate) },
              { label: "Avg time to pay", value: secs(p.avgListingFeeTimeToSuccessSeconds) },
              { label: "Refunds", value: num(p.totalListingFeeRefunds) },
            ],
          },
          {
            title: "Rent & deposit payments",
            tiles: [
              { label: "Obligations", value: `${p.totalRentalPaymentObligations} (${breakdown(p.obligationsByStatus)})` },
              { label: "Payment records", value: `${p.totalRentalPaymentRecords} (${breakdown(p.recordsByStatus)})` },
              { label: "Discrepancy rate", value: pct(p.rentalPaymentDiscrepancyRate) },
            ],
          },
          {
            title: "Payment disputes",
            tiles: [
              { label: "Cases", value: `${d.totalCases} (${breakdown(d.casesByStatus)})` },
              { label: "Avg resolution", value: days(d.avgResolutionTimeDays) },
              { label: "Reopen rate", value: pct(d.reopenRate) },
              { label: "Settled / referred out", value: `${pct(d.settlementRate)} / ${pct(d.externalReferralRate)}` },
            ],
          },
        ];
      }
      const l = await apiClientFetch<LeasingMetrics>("/api/analytics/section1-operational-metrics");
      return [
        {
          title: "Bookings & publishing",
          tiles: [
            { label: "Avg approval time", value: secs(l.approvalTurnaroundSecondsAvg) },
            { label: "Publication failures", value: pct(l.publicationFailureRate) },
            { label: "Holds that became bookings", value: pct(l.holdConversionRate) },
            { label: "Holds that expired", value: pct(l.holdExpiryRate) },
            { label: "Stale holds", value: num(l.staleHoldCount) },
            { label: "Duplicate confirmations", value: num(l.duplicateConfirmationIncidents) },
            { label: "Manual overrides", value: num(l.manualOverrideFrequency) },
          ],
        },
      ];
    };
    run()
      .then((g) => live && setGroups(g))
      .catch(() => live && setError("Could not load automation metrics."));
    return () => {
      live = false;
    };
  }, [source]);

  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex items-center gap-2">
        <Activity className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
        <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Automation health</h2>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Live figures for what the platform handled on its own. Read-only — nothing here needs a manual review.
      </p>
      <div className="mt-4 space-y-4">
        {error ? (
          <p className="text-sm text-accent-600">{error}</p>
        ) : groups === null ? (
          <p className="text-sm text-slate-400">Loading…</p>
        ) : (
          groups.map((g) => <Group key={g.title} title={g.title} tiles={g.tiles} />)
        )}
      </div>
    </section>
  );
}
