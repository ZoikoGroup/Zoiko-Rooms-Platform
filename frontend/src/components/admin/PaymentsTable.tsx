"use client";

import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { Payment, PaymentStatus } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { formatCurrency, formatDate } from "@/lib/utils";
import { paymentStatusTone } from "@/lib/status";

export function PaymentsTable({ payments }: { payments: Payment[] }) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<PaymentStatus | "all">("all");

  const filtered = useMemo(() => {
    return payments.filter((p) => {
      const matchesStatus = status === "all" || p.status === status;
      const q = query.trim().toLowerCase();
      const matchesQuery = !q || p.guestName.toLowerCase().includes(q) || p.bookingId.toLowerCase().includes(q);
      return matchesStatus && matchesQuery;
    });
  }, [payments, query, status]);

  return (
    <div className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 sm:p-6 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400 dark:text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by guest or booking ID"
            className="w-full rounded-full bg-slate-50 py-2.5 pl-10 pr-4 text-sm outline-none ring-1 ring-slate-100 transition-all focus:ring-primary-300 dark:bg-slate-800 dark:ring-slate-700"
          />
        </div>
        <div className="flex flex-wrap gap-2">
          {(["all", "paid", "unpaid", "refunded"] as const).map((s) => (
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
        <table className="w-full min-w-[700px] text-left text-sm">
          <thead>
            <tr className="border-b border-slate-100 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:border-slate-800 dark:text-slate-400">
              <th className="py-3 pr-4">Payment ID</th>
              <th className="py-3 pr-4">Booking</th>
              <th className="py-3 pr-4">Guest</th>
              <th className="py-3 pr-4">Method</th>
              <th className="py-3 pr-4">Amount</th>
              <th className="py-3 pr-4">Date</th>
              <th className="py-3 pr-4">Status</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((p, i) => (
              <tr
                key={p.id}
                className="animate-fade-in border-b border-slate-50 transition-colors hover:bg-primary-50/50 dark:border-slate-800 dark:hover:bg-primary-500/10"
                style={{ animationDelay: `${Math.min(i, 8) * 0.03}s` }}
              >
                <td className="py-3.5 pr-4 font-mono text-xs font-semibold text-primary-700 dark:text-primary-300">{p.id}</td>
                <td className="py-3.5 pr-4 text-slate-500 dark:text-slate-400">{p.bookingId}</td>
                <td className="py-3.5 pr-4 font-medium text-slate-700 dark:text-slate-300">{p.guestName}</td>
                <td className="py-3.5 pr-4 text-slate-500 dark:text-slate-400">{p.method}</td>
                <td className="py-3.5 pr-4 font-semibold text-primary-900 dark:text-white">{formatCurrency(p.amount)}</td>
                <td className="py-3.5 pr-4 text-slate-500 dark:text-slate-400">{formatDate(p.date)}</td>
                <td className="py-3.5 pr-4">
                  <Badge tone={paymentStatusTone[p.status]} className="capitalize">
                    {p.status}
                  </Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {filtered.length === 0 && (
          <p className="py-10 text-center text-sm text-slate-400 dark:text-slate-400">No payments match your search.</p>
        )}
      </div>
    </div>
  );
}
