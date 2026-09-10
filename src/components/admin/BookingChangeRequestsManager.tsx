"use client";

import { useCallback, useEffect, useState } from "react";
import { CalendarClock, CheckCircle2, ThumbsDown, ThumbsUp } from "lucide-react";
import { BookingChangeRequest } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { apiClientFetch } from "@/lib/api-client";
import { getCurrentAdmin } from "@/lib/auth";
import { bookingChangeRequestStatusLabel, bookingChangeRequestStatusTone, bookingChangeTypeLabel } from "@/lib/status";
import { formatCurrency, formatDate } from "@/lib/utils";

/** ZR-ENG-CLR-008 Section 8 MVP admin console -- a renter's move-in date
 * change / extension / shortening request. Approving drives the existing
 * agreement amendment engine (re-signature required); declining just closes
 * the request out, no agreement changes. Sibling to SubletRequestsManager,
 * same list/decide shape, but approve is super_admin-only (matches the
 * backend's own gate on the amendment engine) while decline is any admin. */
export function BookingChangeRequestsManager() {
  const [role, setRole] = useState<string | null>(null);
  const [checkingRole, setCheckingRole] = useState(true);
  const [requests, setRequests] = useState<BookingChangeRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [declineTarget, setDeclineTarget] = useState<BookingChangeRequest | null>(null);
  const [declineNote, setDeclineNote] = useState("");
  const [toast, setToast] = useState("");

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3200);
  }

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiClientFetch<BookingChangeRequest[]>("/api/leasing/booking-change-requests");
      setRequests(data);
    } catch {
      showToast("Failed to load booking change requests");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    getCurrentAdmin().then((admin) => {
      setRole(admin?.role ?? null);
      setCheckingRole(false);
      if (admin) load();
    });
  }, [load]);

  async function approve(request: BookingChangeRequest) {
    setBusyId(request.id);
    try {
      await apiClientFetch<BookingChangeRequest>(`/api/leasing/booking-change-requests/${request.id}/approve`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      showToast("Approved — the renter and host now need to re-sign the updated agreement.");
      await load();
    } catch {
      showToast("Failed to approve this request");
    } finally {
      setBusyId(null);
    }
  }

  function openDecline(request: BookingChangeRequest) {
    setDeclineTarget(request);
    setDeclineNote("");
  }

  async function submitDecline() {
    if (!declineTarget) return;
    setBusyId(declineTarget.id);
    try {
      await apiClientFetch<BookingChangeRequest>(`/api/leasing/booking-change-requests/${declineTarget.id}/decline`, {
        method: "POST",
        body: JSON.stringify({ decisionNote: declineNote.trim() }),
      });
      showToast("Request declined");
      setDeclineTarget(null);
      await load();
    } catch {
      showToast("Failed to decline this request");
    } finally {
      setBusyId(null);
    }
  }

  // Any admin can view/decline (scoped to their own listings on the backend);
  // only super_admin can approve, matching the amendment engine's own gate.
  if (checkingRole || !role) return null;
  const canApprove = role === "super_admin";

  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex items-center gap-2">
        <CalendarClock className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
        <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Booking Change Requests</h2>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        A renter is asking to shift their move-in date, extend, or shorten their stay. Approving generates an
        agreement amendment that both parties must re-sign before it takes effect.
      </p>

      <div className="mt-4 space-y-2">
        {loading ? (
          <p className="text-sm text-slate-400">Loading...</p>
        ) : requests.length === 0 ? (
          <p className="text-sm text-slate-400 dark:text-slate-400">No booking change requests are pending review.</p>
        ) : (
          requests.map((request) => (
            <div
              key={request.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10"
            >
              <div className="min-w-0">
                <p className="text-sm font-semibold text-primary-900 dark:text-white">
                  {request.listingName || `Agreement #${request.agreementId}`}
                  {" · "}
                  {bookingChangeTypeLabel[request.changeType] ?? request.changeType}
                </p>
                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                  {request.guestName || "Renter"}{" "}
                  {request.changeType === "PREMISES_CHANGE" ? (
                    <>→ {request.targetListingName || request.targetListingId}</>
                  ) : request.changeType === "FINANCIAL_CHANGE" ? (
                    <>
                      → {formatCurrency(request.originalMonthlyRent ?? 0)} → {formatCurrency(request.proposedMonthlyRent ?? 0)}/month
                      {request.originalMonthlyRent != null && request.proposedMonthlyRent != null && (
                        <span className={request.proposedMonthlyRent > request.originalMonthlyRent ? "text-accent-700 dark:text-accent-400" : "text-emerald-600 dark:text-emerald-400"}>
                          {" "}({request.proposedMonthlyRent > request.originalMonthlyRent ? "+" : ""}
                          {formatCurrency(request.proposedMonthlyRent - request.originalMonthlyRent)}/mo)
                        </span>
                      )}
                    </>
                  ) : (
                    <>
                      · {formatDate(request.originalStartDate)} → {formatDate(request.proposedStartDate)}
                      {request.additionalTermMonths != null &&
                        ` (${request.additionalTermMonths > 0 ? "+" : ""}${request.additionalTermMonths} month${
                          Math.abs(request.additionalTermMonths) === 1 ? "" : "s"
                        })`}
                    </>
                  )}
                </p>
                {request.reason && (
                  <p className="mt-0.5 text-xs text-slate-400">“{request.reason}”</p>
                )}
                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                  Requested {formatDate(request.createdAt)} · expires {formatDate(request.expiresAt)}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={bookingChangeRequestStatusTone[request.status] ?? "neutral"}>
                  {bookingChangeRequestStatusLabel[request.status] ?? request.status}
                </Badge>
                {request.status === "PENDING" && (
                  <>
                    {canApprove && (
                      <Button
                        size="sm"
                        variant="primary"
                        loading={busyId === request.id}
                        onClick={() => approve(request)}
                      >
                        <ThumbsUp className="h-3.5 w-3.5" /> Approve
                      </Button>
                    )}
                    <Button size="sm" variant="outline" onClick={() => openDecline(request)}>
                      <ThumbsDown className="h-3.5 w-3.5" /> Decline
                    </Button>
                  </>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      <Modal open={Boolean(declineTarget)} onClose={() => setDeclineTarget(null)} title="Decline booking change request">
        <div className="space-y-3.5">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Optionally tell the renter why this request was declined — they will see this note.
          </p>
          <textarea
            value={declineNote}
            onChange={(e) => setDeclineNote(e.target.value)}
            rows={4}
            placeholder="e.g. Those dates conflict with another confirmed booking."
            className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
          />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setDeclineTarget(null)}>
              Cancel
            </Button>
            <Button variant="accent" loading={busyId === declineTarget?.id} onClick={submitDecline}>
              Decline request
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
