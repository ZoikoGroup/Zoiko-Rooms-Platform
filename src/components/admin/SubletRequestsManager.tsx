"use client";

import { useCallback, useEffect, useState } from "react";
import { Repeat } from "lucide-react";
import { SubletRequest } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { apiClientFetch } from "@/lib/api-client";
import { getCurrentAdmin } from "@/lib/auth";
import { subletArrangementTypeAdminLabel, subletRequestStatusLabel, subletRequestStatusTone } from "@/lib/status";
import { formatDate } from "@/lib/utils";

/** Read-only oversight: approval, decline and requests for more information
 * stay between the host and the renter (the host's own Sublet requests page).
 * Admins only see what is waiting on a host. */
export function SubletRequestsManager() {
  const [isSuperAdmin, setIsSuperAdmin] = useState(false);
  const [checkingRole, setCheckingRole] = useState(true);
  const [requests, setRequests] = useState<SubletRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRequests(await apiClientFetch<SubletRequest[]>("/api/occupancy/sublet-requests"));
      setLoadError(false);
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    getCurrentAdmin().then((admin) => {
      const superAdmin = admin?.role === "super_admin";
      setIsSuperAdmin(superAdmin);
      setCheckingRole(false);
      if (superAdmin) load();
    });
  }, [load]);

  // Only super_admin can see this per the backend (require_super_admin on
  // every /api/occupancy/sublet-requests* route) -- a regular admin gets nothing
  // rather than an empty/erroring card.
  if (checkingRole || !isSuperAdmin) return null;

  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex items-center gap-2">
        <Repeat className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
        <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Sublet Requests</h2>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Requests waiting on the host. Hosts approve, decline or ask the renter for more information themselves —
        this view is read-only.
      </p>

      <div className="mt-4 space-y-2">
        {loading ? (
          <p className="text-sm text-slate-400">Loading...</p>
        ) : loadError ? (
          <p className="text-sm text-red-600 dark:text-red-400">Failed to load sublet requests.</p>
        ) : requests.length === 0 ? (
          <p className="text-sm text-slate-400 dark:text-slate-400">No sublet requests are waiting on a host.</p>
        ) : (
          requests.map((request) => (
            <div
              key={request.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10"
            >
              <div className="min-w-0">
                <p className="text-sm font-semibold text-primary-900 dark:text-white">
                  {request.listingName || `Occupancy #${request.currentOccupancyId}`}
                  {request.listingCity && `, ${request.listingCity}`}
                </p>
                <p className="mt-0.5 text-xs font-medium text-primary-700 dark:text-primary-300">
                  {subletArrangementTypeAdminLabel[request.arrangementType] ?? request.arrangementType}
                </p>
                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                  {request.currentTenantName || "Current tenant"} → {request.proposedRenterName || "proposed renter"}
                  {(request.bedrooms || request.bathrooms || request.guests) && (
                    <>
                      {" "}· {request.bedrooms} bd · {request.bathrooms} ba · up to {request.guests} guests
                    </>
                  )}
                </p>
                <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
                  <span>Requested {formatDate(request.createdAt)}</span>
                  {request.authorityEvidenceRef && <span>Evidence ref: {request.authorityEvidenceRef}</span>}
                </p>
                {request.occupantRiskTier !== "NONE" && (
                  <p
                    className={`mt-1 text-xs font-medium ${
                      request.occupantRiskTier === "BLOCK" ? "text-red-600 dark:text-red-400" : "text-amber-700 dark:text-amber-300"
                    }`}
                  >
                    Occupant overlap {request.occupantRiskTier}: {request.occupantRiskReason}
                  </p>
                )}
              </div>
              <Badge tone={subletRequestStatusTone[request.status] ?? "neutral"}>
                {subletRequestStatusLabel[request.status] ?? request.status}
              </Badge>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
