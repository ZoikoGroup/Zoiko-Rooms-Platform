"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, ArrowRightLeft, CalendarClock, CheckCircle2, DoorOpen, FileWarning, RefreshCw, XCircle } from "lucide-react";
import {
  Obligation,
  Occupancy,
  PreMoveInCancellationResult,
  RefundEntitlement,
  TerminationCase,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { apiClientFetch } from "@/lib/api-client";
import {
  occupancyStatusTone,
  refundEntitlementLineItemLabel,
  terminationCauseCodeLabel,
  terminationCaseStatusTone,
} from "@/lib/status";
import { formatCurrency, formatDate } from "@/lib/utils";

export function OccupancyManager() {
  const [occupancies, setOccupancies] = useState<Occupancy[]>([]);
  const [rentDue, setRentDue] = useState<Occupancy[]>([]);
  const [holdover, setHoldover] = useState<Occupancy[]>([]);
  const [toast, setToast] = useState("");
  const [cancelFor, setCancelFor] = useState<Occupancy | null>(null);
  const [cancelReason, setCancelReason] = useState("");
  const [cancelSubmitting, setCancelSubmitting] = useState(false);

  // Section 6 gap: the whole termination/refund-entitlement flow previously
  // had zero admin-facing UI despite a complete backend.
  const [terminationFor, setTerminationFor] = useState<Occupancy | null>(null);
  const [terminationCases, setTerminationCases] = useState<TerminationCase[]>([]);
  const [terminationEntitlements, setTerminationEntitlements] = useState<Record<number, RefundEntitlement>>({});
  const [terminationLoading, setTerminationLoading] = useState(false);
  const [terminationBusy, setTerminationBusy] = useState<number | null>(null);
  const [decisionNote, setDecisionNote] = useState("");

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3200);
  }

  const loadAll = useCallback(async () => {
    try {
      const [occ, due, held] = await Promise.all([
        apiClientFetch<Occupancy[]>("/api/occupancy"),
        apiClientFetch<Occupancy[]>("/api/occupancy/rent-due-check"),
        apiClientFetch<Occupancy[]>("/api/occupancy/holdover-check"),
      ]);
      setOccupancies(occ);
      setRentDue(due);
      setHoldover(held);
    } catch {
      showToast("Failed to load occupancies");
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

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

  async function confirmMoveOut(id: number) {
    try {
      await apiClientFetch(`/api/occupancy/${id}/move-out/confirm`, { method: "POST", body: JSON.stringify({}) });
      showToast("Move-out confirmed — you can now end the tenancy");
    } catch {
      showToast("Failed to confirm move-out");
    }
  }

  function openCancel(occupancy: Occupancy) {
    setCancelFor(occupancy);
    setCancelReason("");
  }

  async function cancelBeforeMoveIn() {
    if (!cancelFor) return;
    setCancelSubmitting(true);
    try {
      const result = await apiClientFetch<PreMoveInCancellationResult>(
        `/api/occupancy/${cancelFor.id}/cancel-before-move-in`,
        { method: "POST", body: JSON.stringify({ reason: cancelReason.trim() }) },
      );
      setCancelFor(null);
      showToast(
        result.feeAmount > 0
          ? `Cancelled. ${result.refundedAmount} refunded (${result.feeAmount} fee applied).`
          : `Cancelled. ${result.refundedAmount} refunded in full.`,
      );
      loadAll();
    } catch {
      showToast("Failed to cancel this booking");
    } finally {
      setCancelSubmitting(false);
    }
  }

  async function openTermination(occupancy: Occupancy) {
    setTerminationFor(occupancy);
    setDecisionNote("");
    setTerminationLoading(true);
    try {
      const cases = await apiClientFetch<TerminationCase[]>(`/api/occupancy/${occupancy.id}/termination-cases`);
      setTerminationCases(cases);
      const entitlements: Record<number, RefundEntitlement> = {};
      await Promise.all(
        cases.map(async (c) => {
          try {
            const entitlement = await apiClientFetch<RefundEntitlement>(`/api/occupancy/termination-cases/${c.id}/calculation`);
            entitlements[c.id] = entitlement;
          } catch {
            // No entitlement calculated yet for this case.
          }
        }),
      );
      setTerminationEntitlements(entitlements);
    } catch {
      showToast("Failed to load termination cases");
    } finally {
      setTerminationLoading(false);
    }
  }

  async function refreshTerminationCases() {
    if (!terminationFor) return;
    await openTermination(terminationFor);
  }

  async function decideCase(caseId: number, approve: boolean) {
    setTerminationBusy(caseId);
    try {
      await apiClientFetch(`/api/occupancy/termination-cases/${caseId}/decision`, {
        method: "POST",
        body: JSON.stringify({ approve, reason: decisionNote.trim() || (approve ? "Approved" : "Rejected") }),
      });
      showToast(approve ? "Case approved" : "Case rejected");
      await refreshTerminationCases();
    } catch {
      showToast("Failed to decide this case");
    } finally {
      setTerminationBusy(null);
    }
  }

  async function calculateRefund(caseId: number) {
    setTerminationBusy(caseId);
    try {
      await apiClientFetch(`/api/occupancy/termination-cases/${caseId}/calculate-refund`, { method: "POST" });
      showToast("Refund calculated");
      await refreshTerminationCases();
    } catch {
      showToast("Failed to calculate refund");
    } finally {
      setTerminationBusy(null);
    }
  }

  async function approveRefund(entitlementId: number, caseId: number) {
    setTerminationBusy(caseId);
    try {
      await apiClientFetch(`/api/occupancy/refund-entitlements/${entitlementId}/approve`, { method: "POST" });
      showToast("Refund approved");
      await refreshTerminationCases();
    } catch {
      showToast("Failed to approve refund");
    } finally {
      setTerminationBusy(null);
    }
  }

  async function executeRefund(entitlementId: number, caseId: number) {
    setTerminationBusy(caseId);
    try {
      await apiClientFetch(`/api/occupancy/refund-entitlements/${entitlementId}/execute`, { method: "POST" });
      showToast("Refund executed");
      await refreshTerminationCases();
    } catch {
      showToast("Failed to execute refund");
    } finally {
      setTerminationBusy(null);
    }
  }

  return (
    <div className="space-y-4">
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

      {holdover.length > 0 && (
        <div className="flex items-start gap-3 rounded-2xl bg-rose-50 p-4 ring-1 ring-rose-200 dark:bg-rose-500/10 dark:ring-rose-500/20">
          <AlertTriangle className="h-5 w-5 shrink-0 text-rose-600" />
          <div>
            <p className="text-sm font-semibold text-rose-800 dark:text-rose-300">
              {holdover.length} occupanc{holdover.length === 1 ? "y is" : "ies are"} past their lease end date and still active
            </p>
            <p className="mt-0.5 text-xs text-rose-700 dark:text-rose-400">
              These tenants are holding over — review whether to extend, start a termination, or end the tenancy.
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
            {occupancy.reassignedViaSubletRequestId && (
              <p className="mt-1 flex items-center gap-1 text-xs font-medium text-amber-600 dark:text-amber-400">
                <ArrowRightLeft className="h-3.5 w-3.5" /> Reassigned via sublet request #{occupancy.reassignedViaSubletRequestId} — cannot be sublet again
              </p>
            )}
            <div className="mt-2 space-y-1 text-xs text-slate-500 dark:text-slate-400">
              <p className="flex items-center gap-1.5">
                <CalendarClock className="h-3.5 w-3.5" /> Moved in {occupancy.moveInDate ? formatDate(occupancy.moveInDate) : "—"}
              </p>
              <p className="flex items-center gap-1.5">
                <CalendarClock className="h-3.5 w-3.5" /> Lease ends {occupancy.expectedEndDate ? formatDate(occupancy.expectedEndDate) : "—"}
              </p>
            </div>
            {occupancy.status === "ACTIVE" && (
              <div className="mt-3 flex flex-wrap gap-2">
                <Button size="sm" variant="outline" className="flex-1" onClick={() => generateRent(occupancy.id)}>
                  <RefreshCw className="h-3.5 w-3.5" /> Generate Rent
                </Button>
                <Button size="sm" variant="outline" className="flex-1" onClick={() => endOccupancy(occupancy.id)}>
                  <DoorOpen className="h-3.5 w-3.5" /> End
                </Button>
                <Button size="sm" variant="outline" className="flex-1" onClick={() => confirmMoveOut(occupancy.id)}>
                  <CheckCircle2 className="h-3.5 w-3.5" /> Confirm Move-Out
                </Button>
                <Button size="sm" variant="outline" className="w-full" onClick={() => openTermination(occupancy)}>
                  <FileWarning className="h-3.5 w-3.5" /> Termination &amp; Refund
                </Button>
              </div>
            )}
            {occupancy.status === "PENDING_MOVE_IN" && (
              <div className="mt-3">
                <Button size="sm" variant="outline" className="w-full" onClick={() => openCancel(occupancy)}>
                  <XCircle className="h-3.5 w-3.5" /> Cancel Before Move-In
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

      <Modal open={Boolean(cancelFor)} onClose={() => setCancelFor(null)} title="Cancel Before Move-In">
        <div className="space-y-3.5">
          <p className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800 dark:text-slate-400">
            This cancels the booking and issues a real refund for what&apos;s already been paid, minus any
            cancellation fee if outside the free-cancellation window. This cannot be undone.
          </p>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Reason (optional)
            </label>
            <textarea
              value={cancelReason}
              onChange={(e) => setCancelReason(e.target.value)}
              rows={3}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <Button variant="primary" fullWidth loading={cancelSubmitting} onClick={cancelBeforeMoveIn}>
            Cancel Booking
          </Button>
        </div>
      </Modal>

      <Modal open={Boolean(terminationFor)} onClose={() => setTerminationFor(null)} title="Termination & Refund">
        <div className="space-y-4">
          {terminationLoading ? (
            <p className="text-sm text-slate-400">Loading…</p>
          ) : terminationCases.length === 0 ? (
            <p className="text-sm text-slate-400">No termination case has been opened for this occupancy.</p>
          ) : (
            terminationCases.map((termCase) => {
              const entitlement = terminationEntitlements[termCase.id];
              return (
                <div
                  key={termCase.id}
                  className="space-y-2 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10"
                >
                  <div className="flex items-center justify-between gap-2">
                    <Badge tone={terminationCaseStatusTone[termCase.status] ?? "neutral"}>
                      {termCase.status.replace(/_/g, " ")}
                    </Badge>
                    <span className="text-xs text-slate-400">
                      {terminationCauseCodeLabel[termCase.causeCode] ?? termCase.causeCode}
                    </span>
                  </div>
                  {termCase.notes && <p className="text-xs text-slate-500 dark:text-slate-400">{termCase.notes}</p>}
                  {termCase.effectiveTerminationDate && (
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      Effective {formatDate(termCase.effectiveTerminationDate)}
                    </p>
                  )}

                  {termCase.status === "PENDING_REVIEW" && (
                    <div className="space-y-2 border-t border-slate-200 pt-2 dark:border-slate-700">
                      <textarea
                        value={decisionNote}
                        onChange={(e) => setDecisionNote(e.target.value)}
                        placeholder="Decision reason"
                        rows={2}
                        className="w-full rounded-xl bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-900 dark:text-slate-100 dark:ring-slate-700"
                      />
                      <div className="flex gap-2">
                        <Button
                          size="sm" variant="primary" className="flex-1"
                          loading={terminationBusy === termCase.id}
                          onClick={() => decideCase(termCase.id, true)}
                        >
                          Approve
                        </Button>
                        <Button
                          size="sm" variant="outline" className="flex-1"
                          loading={terminationBusy === termCase.id}
                          onClick={() => decideCase(termCase.id, false)}
                        >
                          Reject
                        </Button>
                      </div>
                    </div>
                  )}

                  {(termCase.status === "EFFECTIVE_DATE_SET" || termCase.status === "TERMINATED") && !entitlement && (
                    <Button
                      size="sm" variant="outline" className="w-full"
                      loading={terminationBusy === termCase.id}
                      onClick={() => calculateRefund(termCase.id)}
                    >
                      Calculate Refund
                    </Button>
                  )}

                  {entitlement && (
                    <div className="space-y-1 border-t border-slate-200 pt-2 dark:border-slate-700">
                      {entitlement.lineItems.filter((li) => li.amount !== 0).map((li) => (
                        <p key={li.id} className="flex items-center justify-between text-xs text-slate-600 dark:text-slate-300">
                          <span>{refundEntitlementLineItemLabel[li.type] ?? li.type}</span>
                          <span>{formatCurrency(li.amount, entitlement.currency)}</span>
                        </p>
                      ))}
                      <p className="flex items-center justify-between text-sm font-semibold text-primary-900 dark:text-white">
                        <span>Net refund</span>
                        <span>{formatCurrency(entitlement.netRefund, entitlement.currency)}</span>
                      </p>
                      <Badge tone={entitlement.status === "EXECUTED" ? "success" : "warning"}>{entitlement.status}</Badge>
                      {entitlement.status === "CALCULATED" && (
                        <Button
                          size="sm" variant="primary" className="w-full"
                          loading={terminationBusy === termCase.id}
                          onClick={() => approveRefund(entitlement.id, termCase.id)}
                        >
                          Approve Refund
                        </Button>
                      )}
                      {entitlement.status === "APPROVED" && (
                        <Button
                          size="sm" variant="primary" className="w-full"
                          loading={terminationBusy === termCase.id}
                          onClick={() => executeRefund(entitlement.id, termCase.id)}
                        >
                          Execute Refund
                        </Button>
                      )}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </Modal>

      {toast && (
        <div className="animate-fade-up fixed bottom-6 right-6 z-[300] flex max-w-sm items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" /> {toast}
        </div>
      )}
    </div>
  );
}
