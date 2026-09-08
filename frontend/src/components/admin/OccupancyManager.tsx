"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CalendarClock, CheckCircle2, DoorOpen, Repeat, RefreshCw, ThumbsDown, ThumbsUp } from "lucide-react";
import { Obligation, Occupancy, SubletRequest } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { apiClientFetch } from "@/lib/api-client";
import { occupancyStatusTone, subletRequestStatusLabel, subletRequestStatusTone } from "@/lib/status";
import { formatDate } from "@/lib/utils";

export function OccupancyManager() {
  const [occupancies, setOccupancies] = useState<Occupancy[]>([]);
  const [rentDue, setRentDue] = useState<Occupancy[]>([]);
  const [subletRequests, setSubletRequests] = useState<SubletRequest[]>([]);
  const [decidingId, setDecidingId] = useState<number | null>(null);
  const [toast, setToast] = useState("");

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3200);
  }

  const loadAll = useCallback(async () => {
    try {
      const [occ, due] = await Promise.all([
        apiClientFetch<Occupancy[]>("/api/occupancy"),
        apiClientFetch<Occupancy[]>("/api/occupancy/rent-due-check"),
      ]);
      setOccupancies(occ);
      setRentDue(due);
    } catch {
      showToast("Failed to load occupancies");
    }
    // Super-admin-only endpoint -- a plain admin correctly gets a 403 here, so
    // this fetch is isolated from the one above and fails silently for them.
    try {
      setSubletRequests(await apiClientFetch<SubletRequest[]>("/api/occupancy/sublet-requests"));
    } catch {
      setSubletRequests([]);
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  async function decideSublet(id: number, action: "approve" | "reject") {
    setDecidingId(id);
    try {
      await apiClientFetch(`/api/occupancy/sublet-requests/${id}/${action}`, { method: "POST" });
      showToast(`Sublet request ${action === "approve" ? "approved" : "rejected"}`);
      loadAll();
    } catch {
      showToast(`Failed to ${action} sublet request`);
    } finally {
      setDecidingId(null);
    }
  }

  async function generateRent(id: number) {
    try {
      const obligation = await apiClientFetch<Obligation | null>(`/api/occupancy/${id}/generate-rent`, { method: "POST" });
      showToast(obligation ? `Rent obligation generated for ${formatDate(obligation.dueDate)}` : "No new rent obligation needed yet");
      loadAll();
    } catch {
      showToast("Failed to generate rent obligation");
    }
  }

  async function endOccupancy(id: number) {
    try {
      await apiClientFetch(`/api/occupancy/${id}/end`, { method: "POST" });
      showToast("Occupancy ended");
      loadAll();
    } catch {
      showToast("Failed to end occupancy");
    }
  }

  const pendingSublets = subletRequests.filter((r) => r.status === "pending_admin_review");

  return (
    <div className="space-y-4">
      {pendingSublets.length > 0 && (
        <div className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
          <div className="flex items-center gap-2">
            <Repeat className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
            <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">
              Sublet Requests Awaiting Review ({pendingSublets.length})
            </h2>
          </div>
          <div className="mt-3 space-y-2">
            {pendingSublets.map((request) => (
              <div
                key={request.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10"
              >
                <div>
                  <p className="text-sm font-semibold text-primary-900 dark:text-white">
                    {request.listingName || `Occupancy #${request.currentOccupancyId}`}
                  </p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    {request.currentRenterName || "Current renter"} → {request.proposedRenterName || `Party #${request.proposedRenterPartyId}`}
                    {request.propertyAddress && ` · ${request.propertyAddress}`}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge tone={subletRequestStatusTone[request.status] ?? "neutral"}>
                    {subletRequestStatusLabel[request.status] ?? request.status}
                  </Badge>
                  <Button
                    size="sm"
                    variant="primary"
                    disabled={decidingId === request.id}
                    onClick={() => decideSublet(request.id, "approve")}
                  >
                    <ThumbsUp className="h-3.5 w-3.5" /> Approve
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={decidingId === request.id}
                    onClick={() => decideSublet(request.id, "reject")}
                  >
                    <ThumbsDown className="h-3.5 w-3.5" /> Reject
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {rentDue.length > 0 && (
        <div className="flex items-start gap-3 rounded-2xl bg-amber-50 p-4 ring-1 ring-amber-200 dark:bg-amber-500/10 dark:ring-amber-500/20">
          <AlertTriangle className="h-5 w-5 shrink-0 text-amber-600" />
          <div>
            <p className="text-sm font-semibold text-amber-800 dark:text-amber-300">
              {rentDue.length} occupanc{rentDue.length === 1 ? "y has" : "ies have"} no upcoming rent obligation scheduled
            </p>
            <p className="mt-0.5 text-xs text-amber-700 dark:text-amber-400">
              No billing scheduler runs automatically — generate the next rent obligation manually below.
            </p>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {occupancies.map((occupancy) => (
          <div
            key={occupancy.id}
            className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10"
          >
            <div className="flex items-center justify-between">
              <p className="font-heading text-sm font-bold text-primary-900 dark:text-white">{occupancy.guestName}</p>
              <Badge tone={occupancyStatusTone[occupancy.status]}>{occupancy.status}</Badge>
            </div>
            <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{occupancy.listingId}</p>
            <div className="mt-2 space-y-1 text-xs text-slate-500 dark:text-slate-400">
              <p className="flex items-center gap-1.5">
                <CalendarClock className="h-3.5 w-3.5" /> Moved in {occupancy.moveInDate ? formatDate(occupancy.moveInDate) : "—"}
              </p>
              <p className="flex items-center gap-1.5">
                <CalendarClock className="h-3.5 w-3.5" /> Lease ends {occupancy.expectedEndDate ? formatDate(occupancy.expectedEndDate) : "—"}
              </p>
            </div>
            {occupancy.requestedMoveOutDate && (
              <p className="mt-2 flex items-center gap-1.5 rounded-lg bg-amber-50 px-2.5 py-1.5 text-xs font-semibold text-amber-700 dark:bg-amber-500/10 dark:text-amber-400">
                <DoorOpen className="h-3.5 w-3.5" /> Move-out requested for {formatDate(occupancy.requestedMoveOutDate)}
              </p>
            )}
            {occupancy.status === "ACTIVE" && (
              <div className="mt-3 flex gap-2">
                <Button size="sm" variant="outline" className="flex-1" onClick={() => generateRent(occupancy.id)}>
                  <RefreshCw className="h-3.5 w-3.5" /> Generate Rent
                </Button>
                <Button size="sm" variant="outline" className="flex-1" onClick={() => endOccupancy(occupancy.id)}>
                  <DoorOpen className="h-3.5 w-3.5" /> End
                </Button>
              </div>
            )}
          </div>
        ))}
      </div>

      {occupancies.length === 0 && (
        <p className="py-10 text-center text-sm text-slate-400 dark:text-slate-400">
          No active occupancies yet — confirm move-in from a signed agreement on the Leasing page.
        </p>
      )}

      {toast && (
        <div className="animate-fade-up fixed bottom-6 right-6 z-[300] flex max-w-sm items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" /> {toast}
        </div>
      )}
    </div>
  );
}
