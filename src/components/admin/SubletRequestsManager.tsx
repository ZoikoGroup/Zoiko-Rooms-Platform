"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Repeat, ThumbsDown, ThumbsUp } from "lucide-react";
import { SubletRequest } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { apiClientFetch } from "@/lib/api-client";
import { getCurrentAdmin } from "@/lib/auth";
import { subletRequestStatusLabel, subletRequestStatusTone } from "@/lib/status";
import { formatDate } from "@/lib/utils";

export function SubletRequestsManager() {
  const [isSuperAdmin, setIsSuperAdmin] = useState(false);
  const [checkingRole, setCheckingRole] = useState(true);
  const [requests, setRequests] = useState<SubletRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [rejectTarget, setRejectTarget] = useState<SubletRequest | null>(null);
  const [rejectNotes, setRejectNotes] = useState("");
  const [toast, setToast] = useState("");

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3200);
  }

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiClientFetch<SubletRequest[]>("/api/occupancy/sublet-requests");
      setRequests(data);
    } catch {
      showToast("Failed to load sublet requests");
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

  async function approve(request: SubletRequest) {
    setBusyId(request.id);
    try {
      await apiClientFetch<SubletRequest>(`/api/occupancy/sublet-requests/${request.id}/approve`, {
        method: "POST",
        body: JSON.stringify({ notes: "" }),
      });
      showToast("Sublet request approved — occupancy transferred");
      await load();
    } catch {
      showToast("Failed to approve this sublet request");
    } finally {
      setBusyId(null);
    }
  }

  function openReject(request: SubletRequest) {
    setRejectTarget(request);
    setRejectNotes("");
  }

  async function submitReject() {
    if (!rejectTarget) return;
    setBusyId(rejectTarget.id);
    try {
      await apiClientFetch<SubletRequest>(`/api/occupancy/sublet-requests/${rejectTarget.id}/reject`, {
        method: "POST",
        body: JSON.stringify({ notes: rejectNotes.trim() }),
      });
      showToast("Sublet request rejected");
      setRejectTarget(null);
      await load();
    } catch {
      showToast("Failed to reject this sublet request");
    } finally {
      setBusyId(null);
    }
  }

  // Only super_admin can see/act on this per the backend (require_super_admin on
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
        A current renter is asking to hand their occupancy over to another verified renter. Approving transfers the
        occupancy immediately; the 30-night minimum still applies.
      </p>

      <div className="mt-4 space-y-2">
        {loading ? (
          <p className="text-sm text-slate-400">Loading...</p>
        ) : requests.length === 0 ? (
          <p className="text-sm text-slate-400 dark:text-slate-400">No sublet requests are pending review.</p>
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
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={subletRequestStatusTone[request.status] ?? "neutral"}>
                  {subletRequestStatusLabel[request.status] ?? request.status}
                </Badge>
                <Button size="sm" variant="primary" loading={busyId === request.id} onClick={() => approve(request)}>
                  <ThumbsUp className="h-3.5 w-3.5" /> Approve
                </Button>
                <Button size="sm" variant="outline" onClick={() => openReject(request)}>
                  <ThumbsDown className="h-3.5 w-3.5" /> Reject
                </Button>
              </div>
            </div>
          ))
        )}
      </div>

      <Modal open={Boolean(rejectTarget)} onClose={() => setRejectTarget(null)} title="Reject sublet request">
        <div className="space-y-3.5">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Optionally tell the requester why this sublet was rejected — they will see this note.
          </p>
          <textarea
            value={rejectNotes}
            onChange={(e) => setRejectNotes(e.target.value)}
            rows={4}
            placeholder="e.g. The proposed renter's identity verification could not be confirmed."
            className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
          />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setRejectTarget(null)}>
              Cancel
            </Button>
            <Button variant="accent" loading={busyId === rejectTarget?.id} onClick={submitReject}>
              Reject request
            </Button>
          </div>
        </div>
      </Modal>

      {toast && (
        <div className="animate-fade-up fixed bottom-6 right-6 z-[300] flex max-w-sm items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" /> {toast}
        </div>
      )}
    </section>
  );
}
