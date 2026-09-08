"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CalendarClock, Repeat, Search } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { SubletRequest } from "@/lib/types";
import { subletRequestStatusLabel, subletRequestStatusTone } from "@/lib/status";
import { formatDate } from "@/lib/utils";
import { errorMessage, listSubletRequests } from "@/lib/user-api";
import { Card, EmptyState, Toast, useToast } from "@/components/user/ui";

type StatusFilter = "all" | keyof typeof subletRequestStatusLabel;

const STATUS_FILTERS: StatusFilter[] = ["all", "pending_verification", "pending_admin_review", "approved", "rejected"];

export function SubletRequestsList() {
  const { toast, showToast } = useToast();
  const [requests, setRequests] = useState<SubletRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [query, setQuery] = useState("");

  function load() {
    setLoading(true);
    setLoadError("");
    listSubletRequests()
      .then(setRequests)
      .catch((err) => {
        const message = errorMessage(err, "Could not load your sublet requests.");
        setLoadError(message);
        showToast(message, "error");
      })
      .finally(() => setLoading(false));
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, []);

  const filtered = useMemo(() => {
    const byStatus = statusFilter === "all" ? requests : requests.filter((r) => r.status === statusFilter);
    const q = query.trim().toLowerCase();
    if (!q) return byStatus;
    return byStatus.filter(
      (r) =>
        r.listingName.toLowerCase().includes(q) ||
        r.propertyAddress.toLowerCase().includes(q) ||
        r.proposedRenterName.toLowerCase().includes(q)
    );
  }, [requests, statusFilter, query]);

  if (loading) return <Loader label="Loading your sublet requests" />;

  if (loadError) {
    return (
      <Card>
        <div className="flex flex-col items-center gap-3 py-8 text-center">
          <AlertTriangle className="h-6 w-6 text-accent-600" />
          <p className="text-sm text-slate-500 dark:text-slate-400">{loadError}</p>
          <Button size="sm" variant="outline" onClick={load}>
            Retry
          </Button>
        </div>
      </Card>
    );
  }

  if (requests.length === 0) {
    return (
      <Card>
        <div className="flex flex-col items-center gap-4 py-10 text-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
            <Repeat className="h-6 w-6" />
          </span>
          <EmptyState message="You have not requested to sublet any of your rentals." />
          <Link href="/account/rentals">
            <Button size="sm" variant="outline">
              Go to My Rentals
            </Button>
          </Link>
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-3">
      <div className="relative">
        <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by room, address, or proposed renter"
          className="w-full rounded-full bg-slate-50 py-2.5 pl-10 pr-4 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
        />
      </div>

      <div className="flex flex-wrap gap-2">
        {STATUS_FILTERS.map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`rounded-full px-3 py-1.5 text-xs font-semibold transition-colors ${
              statusFilter === s
                ? "bg-primary-700 text-white"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
            }`}
          >
            {s === "all" ? "All" : subletRequestStatusLabel[s]}
          </button>
        ))}
      </div>

      {filtered.length === 0 && (
        <Card>
          <EmptyState message="No sublet requests match this filter." />
        </Card>
      )}

      {filtered.map((request) => (
        <Card key={request.id} className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="font-heading text-sm font-bold text-primary-900 dark:text-white">
              {request.listingName || `Occupancy #${request.currentOccupancyId}`}
            </p>
            {request.propertyAddress && (
              <p className="text-xs text-slate-400">{request.propertyAddress}</p>
            )}
            <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
              <span className="flex items-center gap-1">
                <CalendarClock className="h-3 w-3" /> Requested {formatDate(request.createdAt)}
              </span>
              <span>
                Proposed renter: {request.proposedRenterName || `Party #${request.proposedRenterPartyId}`}
              </span>
              {request.decidedAt && <span>Decided {formatDate(request.decidedAt)}</span>}
            </p>
            {request.adminNotes && (
              <p className="mt-2 max-w-xl text-xs text-slate-500 dark:text-slate-400">
                Reviewer notes: {request.adminNotes}
              </p>
            )}
          </div>
          <Badge tone={subletRequestStatusTone[request.status] ?? "neutral"}>
            {subletRequestStatusLabel[request.status] ?? request.status}
          </Badge>
        </Card>
      ))}

      <Toast toast={toast} />
    </div>
  );
}
