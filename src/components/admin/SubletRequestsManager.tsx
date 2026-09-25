"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Repeat, ThumbsDown, ThumbsUp } from "lucide-react";
import { SubletRequest } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { apiClientFetch } from "@/lib/api-client";
import { getCurrentAdmin } from "@/lib/auth";
import {
  REPLACING_ARRANGEMENT_TYPES,
  subletArrangementTypeAdminLabel,
  subletDeclineReasonCodeLabel,
  subletRequestStatusLabel,
  subletRequestStatusTone,
} from "@/lib/status";
import { formatDate } from "@/lib/utils";

export function SubletRequestsManager() {
  const [isSuperAdmin, setIsSuperAdmin] = useState(false);
  const [checkingRole, setCheckingRole] = useState(true);
  const [requests, setRequests] = useState<SubletRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [rejectTarget, setRejectTarget] = useState<SubletRequest | null>(null);
  const [rejectNotes, setRejectNotes] = useState("");
  const [rejectReasonCode, setRejectReasonCode] = useState("");
  const [approveTarget, setApproveTarget] = useState<SubletRequest | null>(null);
  const [approveStepUpPassword, setApproveStepUpPassword] = useState("");
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

  function requiresStepUp(request: SubletRequest): boolean {
    return (REPLACING_ARRANGEMENT_TYPES as readonly string[]).includes(request.arrangementType);
  }

  async function approve(request: SubletRequest, stepUpPassword = "") {
    setBusyId(request.id);
    try {
      await apiClientFetch<SubletRequest>(`/api/occupancy/sublet-requests/${request.id}/approve`, {
        method: "POST",
        body: JSON.stringify({ notes: "", stepUpPassword }),
      });
      showToast("Sublet request approved — occupancy transferred");
      setApproveTarget(null);
      setApproveStepUpPassword("");
      await load();
    } catch {
      showToast("Failed to approve this sublet request");
      setApproveStepUpPassword("");
    } finally {
      setBusyId(null);
    }
  }

  function handleApproveClick(request: SubletRequest) {
    if (requiresStepUp(request)) {
      setApproveTarget(request);
      setApproveStepUpPassword("");
      return;
    }
    approve(request);
  }

  function openReject(request: SubletRequest) {
    setRejectTarget(request);
    setRejectNotes("");
    setRejectReasonCode("");
  }

  async function submitReject() {
    if (!rejectTarget || !rejectReasonCode) return;
    setBusyId(rejectTarget.id);
    try {
      await apiClientFetch<SubletRequest>(`/api/occupancy/sublet-requests/${rejectTarget.id}/reject`, {
        method: "POST",
        body: JSON.stringify({ notes: rejectNotes.trim(), declineReasonCode: rejectReasonCode }),
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
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={subletRequestStatusTone[request.status] ?? "neutral"}>
                  {subletRequestStatusLabel[request.status] ?? request.status}
                </Badge>
                <Button size="sm" variant="primary" loading={busyId === request.id} onClick={() => handleApproveClick(request)}>
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
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Reason
            </label>
            <select
              value={rejectReasonCode}
              onChange={(e) => setRejectReasonCode(e.target.value)}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            >
              <option value="">Select a reason…</option>
              {Object.entries(subletDeclineReasonCodeLabel).map(([code, label]) => (
                <option key={code} value={code}>
                  {label}
                </option>
              ))}
            </select>
          </div>
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
            <Button
              variant="accent"
              loading={busyId === rejectTarget?.id}
              disabled={!rejectReasonCode || (rejectReasonCode === "OTHER" && !rejectNotes.trim())}
              onClick={submitReject}
            >
              Reject request
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={Boolean(approveTarget)} onClose={() => setApproveTarget(null)} title="Confirm approval">
        <div className="space-y-3.5">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            This hands the tenancy over to a new occupant and can&apos;t be undone — re-enter your admin password to
            confirm (ZR-SUB-003 Section 10 step-up authentication).
          </p>
          <input
            type="password"
            autoComplete="off"
            data-1p-ignore
            data-lpignore="true"
            value={approveStepUpPassword}
            onChange={(e) => setApproveStepUpPassword(e.target.value)}
            placeholder="Your admin password"
            className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
          />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setApproveTarget(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={busyId === approveTarget?.id}
              disabled={!approveStepUpPassword}
              onClick={() => approveTarget && approve(approveTarget, approveStepUpPassword)}
            >
              Approve request
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
