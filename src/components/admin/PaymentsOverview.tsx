"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Clock, Home, RefreshCcw, Search, Wallet } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { ListingFeePaymentsAdmin } from "@/components/admin/ListingFeePaymentsAdmin";
import { apiClientFetch } from "@/lib/api-client";
import { CurrencyTotal, ListingFeeRevenue, RentRecordBucket, RentRecords } from "@/lib/types";
import { rentalPaymentStatusLabel, rentalPaymentStatusTone } from "@/lib/status";
import { formatCurrency, formatDate } from "@/lib/utils";

const BUCKETS: { value: RentRecordBucket; label: string; icon: typeof Wallet; hint: string }[] = [
  { value: "confirmed", label: "Confirmed received", icon: CheckCircle2, hint: "Hosts confirmed they received it" },
  { value: "awaiting_host", label: "Awaiting host confirmation", icon: Clock, hint: "Renters recorded paying; host hasn't confirmed" },
  { value: "overdue", label: "Overdue", icon: AlertTriangle, hint: "Past due and not recorded as paid" },
  { value: "disputed", label: "Disputed", icon: AlertTriangle, hint: "Tenant and host disagree" },
  { value: "due", label: "Due / upcoming", icon: Home, hint: "Owed now or coming up" },
];

/** Amounts in different currencies are never added together -- one line each. */
function Totals({ totals, empty = "0" }: { totals: CurrencyTotal[]; empty?: string }) {
  if (totals.length === 0) return <>{empty}</>;
  return (
    <>
      {totals.map((t, i) => (
        <span key={t.currency}>
          {i > 0 && " · "}
          {formatCurrency(t.amount, t.currency)}
        </span>
      ))}
    </>
  );
}

function Tile({ label, value, sub, icon: Icon }: { label: string; value: React.ReactNode; sub?: string; icon: typeof Wallet }) {
  return (
    <div className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
        <Icon className="h-5 w-5" aria-hidden="true" />
      </span>
      <p className="mt-3 font-heading text-xl font-extrabold text-primary-900 dark:text-white">{value}</p>
      <p className="text-sm text-slate-500 dark:text-slate-400">{label}</p>
      {sub && <p className="mt-0.5 text-xs text-slate-400">{sub}</p>}
    </div>
  );
}

/** Admin Payments page, in the two halves the payment model keeps apart:
 *  Zoiko's own Listing Fee revenue (the only money Zoiko receives), and
 *  rent between hosts and renters -- records only, paid to hosts directly. */
export function PaymentsOverview() {
  const [toast, setToast] = useState("");
  const [revenue, setRevenue] = useState<ListingFeeRevenue | null>(null);
  const [rent, setRent] = useState<RentRecords | null>(null);
  const [bucket, setBucket] = useState<RentRecordBucket | "all">("all");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");

  const showToast = useCallback((message: string) => {
    setToast(message);
    setTimeout(() => setToast(""), 3600);
  }, []);

  useEffect(() => {
    apiClientFetch<ListingFeeRevenue>("/api/finance/payments-overview/listing-fees")
      .then(setRevenue)
      .catch(() => showToast("Failed to load Listing Fee revenue"));
  }, [showToast]);

  useEffect(() => {
    const params = new URLSearchParams();
    if (bucket !== "all") params.set("bucket", bucket);
    if (query) params.set("q", query);
    const qs = params.toString();
    apiClientFetch<RentRecords>(`/api/finance/payments-overview/rent${qs ? `?${qs}` : ""}`)
      .then(setRent)
      .catch(() => showToast("Failed to load rent records"));
  }, [bucket, query, showToast]);

  return (
    <div className="space-y-8">
      {/* ---------------- Zoiko revenue ---------------- */}
      <section className="space-y-4">
        <div>
          <h2 className="font-heading text-lg font-bold text-primary-900 dark:text-white">Zoiko revenue -- Listing Fees</h2>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            The fee hosts pay Zoiko through Stripe to publish a listing. This is the only money Zoiko receives.
          </p>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Tile label="Collected" icon={Wallet} value={revenue ? <Totals totals={revenue.collected} /> : "…"}
            sub={revenue ? `${revenue.paidCount} paid` : undefined} />
          <Tile label="Refunded" icon={RefreshCcw} value={revenue ? <Totals totals={revenue.refunded} /> : "…"} />
          <Tile label="Not completed" icon={Clock} value={revenue ? String(revenue.pendingCount + revenue.failedCount) : "…"}
            sub={revenue ? `${revenue.pendingCount} pending · ${revenue.failedCount} failed` : undefined} />
        </div>
        <ListingFeePaymentsAdmin showToast={showToast} />
      </section>

      {/* ---------------- Rent records ---------------- */}
      <section className="space-y-4">
        <div>
          <h2 className="font-heading text-lg font-bold text-primary-900 dark:text-white">Rent between hosts and renters -- records only</h2>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Paid directly to hosts -- Zoiko does not hold this money. These are what hosts and renters have recorded.
          </p>
        </div>
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
          {BUCKETS.map((b) => (
            <button
              key={b.value}
              type="button"
              onClick={() => setBucket(bucket === b.value ? "all" : b.value)}
              className={`text-left transition ${bucket === b.value ? "ring-2 ring-primary-500 rounded-2xl" : ""}`}
              title={b.hint}
            >
              <Tile
                label={b.label} icon={b.icon}
                value={rent ? <Totals totals={rent.summary[b.value]?.totals ?? []} /> : "…"}
                sub={rent ? `${rent.summary[b.value]?.count ?? 0} obligation(s)` : undefined}
              />
            </button>
          ))}
        </div>

        <div className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
          <div className="flex flex-wrap items-center gap-3">
            <form
              className="flex min-w-[240px] flex-1 items-center gap-2 rounded-xl bg-slate-50 px-3 py-2 ring-1 ring-slate-200 dark:bg-slate-800 dark:ring-slate-700"
              onSubmit={(e) => {
                e.preventDefault();
                setQuery(search.trim());
              }}
            >
              <Search className="h-4 w-4 text-slate-400" aria-hidden="true" />
              <input
                value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search renter, host, listing or obligation ID"
                className="w-full bg-transparent text-sm outline-none dark:text-slate-100"
              />
            </form>
            <div className="flex flex-wrap gap-2">
              {(["all", ...BUCKETS.map((b) => b.value)] as (RentRecordBucket | "all")[]).map((b) => (
                <button
                  key={b}
                  type="button"
                  onClick={() => setBucket(b)}
                  className={`rounded-full px-3 py-1.5 text-xs font-semibold ${
                    bucket === b ? "bg-primary-700 text-white" : "text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-white/5"
                  }`}
                >
                  {b === "all" ? "All" : BUCKETS.find((x) => x.value === b)?.label}
                </button>
              ))}
            </div>
          </div>

          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead>
                <tr className="border-b border-slate-100 text-xs font-bold uppercase tracking-wide text-slate-400 dark:border-white/10">
                  <th className="px-3 py-2">Obligation</th>
                  <th className="px-3 py-2">Listing</th>
                  <th className="px-3 py-2">Renter</th>
                  <th className="px-3 py-2">Host</th>
                  <th className="px-3 py-2">Amount</th>
                  <th className="px-3 py-2">Still owed</th>
                  <th className="px-3 py-2">Due</th>
                  <th className="px-3 py-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {(rent?.rows ?? []).map((row) => (
                  <tr key={row.obligationId} className="border-b border-slate-50 last:border-0 dark:border-white/5">
                    <td className="px-3 py-2 capitalize text-slate-700 dark:text-slate-200">#{row.obligationId} {row.obligationLabel}</td>
                    <td className="px-3 py-2 text-slate-600 dark:text-slate-300">{row.listingName || "--"}</td>
                    <td className="px-3 py-2 text-slate-600 dark:text-slate-300">{row.renterName}</td>
                    <td className="px-3 py-2 text-slate-600 dark:text-slate-300">{row.hostName}</td>
                    <td className="px-3 py-2 font-semibold text-primary-900 dark:text-white">{formatCurrency(row.amount, row.currency)}</td>
                    <td className="px-3 py-2 text-slate-600 dark:text-slate-300">{formatCurrency(row.outstandingAmount, row.currency)}</td>
                    <td className="px-3 py-2 text-slate-500 dark:text-slate-400">{formatDate(row.dueDate)}</td>
                    <td className="px-3 py-2">
                      <Badge tone={rentalPaymentStatusTone[row.status as keyof typeof rentalPaymentStatusTone] ?? "neutral"}>
                        {rentalPaymentStatusLabel[row.status as keyof typeof rentalPaymentStatusLabel] ?? row.status}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rent && rent.rows.length === 0 && (
              <p className="py-8 text-center text-sm text-slate-400">No rent records match.</p>
            )}
          </div>
        </div>
      </section>

      {toast && (
        <div
          role="status" aria-live="polite"
          className="animate-fade-up fixed bottom-6 right-6 z-[300] flex max-w-sm items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl"
        >
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" aria-hidden="true" /> {toast}
        </div>
      )}
    </div>
  );
}
