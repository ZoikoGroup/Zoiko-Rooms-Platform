"use client";

import { useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Mail, Phone, Search } from "lucide-react";
import { Guest } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { formatCurrency, formatDate } from "@/lib/utils";

export function GuestsTable({ guests }: { guests: Guest[] }) {
  const searchParams = useSearchParams();
  const [query, setQuery] = useState(() => searchParams.get("q") ?? "");
  const [status, setStatus] = useState<"all" | "active" | "inactive">("all");

  const filtered = useMemo(() => {
    return guests.filter((g) => {
      const matchesStatus = status === "all" || g.status === status;
      const q = query.trim().toLowerCase();
      const matchesQuery = !q || g.name.toLowerCase().includes(q) || g.email.toLowerCase().includes(q);
      return matchesStatus && matchesQuery;
    });
  }, [guests, query, status]);

  return (
    <div className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 sm:p-6 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400 dark:text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search guests by name or email"
            className="w-full rounded-full bg-slate-50 py-2.5 pl-10 pr-4 text-sm outline-none ring-1 ring-slate-100 transition-all focus:ring-primary-300 dark:bg-slate-800 dark:ring-slate-700"
          />
        </div>
        <div className="flex gap-2">
          {(["all", "active", "inactive"] as const).map((s) => (
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

      <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {filtered.map((g, i) => (
          <div
            key={g.id}
            className="animate-fade-up rounded-2xl p-4 ring-1 ring-slate-100 transition-all duration-300 hover:-translate-y-1 hover:shadow-lg dark:ring-white/10"
            style={{ animationDelay: `${Math.min(i, 8) * 0.04}s` }}
          >
            <div className="flex items-center gap-3">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={g.avatar} alt={g.name} className="h-12 w-12 rounded-full bg-primary-50 dark:bg-primary-500/10" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-bold text-primary-900 dark:text-white">{g.name}</p>
                <p className="truncate text-xs text-slate-400 dark:text-slate-400">{g.location}</p>
              </div>
              <Badge tone={g.status === "active" ? "success" : "neutral"} className="capitalize">
                {g.status}
              </Badge>
            </div>

            <div className="mt-3 space-y-1.5 text-xs text-slate-500 dark:text-slate-400">
              <p className="flex items-center gap-1.5 truncate">
                <Mail className="h-3.5 w-3.5 shrink-0" /> {g.email}
              </p>
              <p className="flex items-center gap-1.5">
                <Phone className="h-3.5 w-3.5 shrink-0" /> {g.phone}
              </p>
            </div>

            <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-3 text-xs dark:border-slate-800">
              <div>
                <p className="font-bold text-primary-900 dark:text-white">{g.totalBookings}</p>
                <p className="text-slate-400 dark:text-slate-400">Bookings</p>
              </div>
              <div>
                <p className="font-bold text-primary-900 dark:text-white">{formatCurrency(g.totalSpent)}</p>
                <p className="text-slate-400 dark:text-slate-400">Total Spent</p>
              </div>
              <div>
                <p className="font-bold text-primary-900 dark:text-white">{formatDate(g.joinedAt)}</p>
                <p className="text-slate-400 dark:text-slate-400">Joined</p>
              </div>
            </div>
          </div>
        ))}
      </div>

      {filtered.length === 0 && (
        <p className="mt-10 text-center text-sm text-slate-400 dark:text-slate-400">No guests match your search.</p>
      )}
    </div>
  );
}
