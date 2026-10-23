"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, ArrowRightLeft, CalendarClock, FileWarning, History } from "lucide-react";
import { Occupancy, RefundEntitlement, TerminationCase } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { TenancyRecordModal } from "@/components/admin/TenancyRecordModal";
import { apiClientFetch } from "@/lib/api-client";
import {
  occupancyStatusTone,
  refundEntitlementLineItemLabel,
  terminationCauseCodeLabel,
  terminationCaseStatusTone,
} from "@/lib/status";
import { formatCurrency, formatDate } from "@/lib/utils";

/** ZR-45: the super admin's read-only view of tenancies. Rent, move-out,
 *  ending a tenancy and refunds all happen between the host and the renter
 *  (rent is created by the hourly scheduler), so this page only shows them. */
export function OccupancyManager() {
  const [occupancies, setOccupancies] = useState<Occupancy[]>([]);
  const [rentDue, setRentDue] = useState<Occupancy[]>([]);
  const [holdover, setHoldover] = useState<Occupancy[]>([]);
  const [loadError, setLoadError] = useState("");

  const [terminationFor, setTerminationFor] = useState<Occupancy | null>(null);
  const [terminationCases, setTerminationCases] = useState<TerminationCase[]>([]);
  const [terminationEntitlements, setTerminationEntitlements] = useState<Record<number, RefundEntitlement>>({});
  const [terminationLoading, setTerminationLoading] = useState(false);
  const [recordFor, setRecordFor] = useState<number | null>(null);

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
      setLoadError("");
    } catch {
      setLoadError("Failed to load occupancies");
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  async function openTermination(occupancy: Occupancy) {
    setTerminationFor(occupancy);
    setTerminationCases([]);
    setTerminationEntitlements({});
    setTerminationLoading(true);
    try {
      const cases = await apiClientFetch<TerminationCase[]>(`/api/occupancy/${occupancy.id}/termination-cases`);
      setTerminationCases(cases);
      const entitlements: Record<number, RefundEntitlement> = {};
      await Promise.all(
        cases.map(async (c) => {
          try {
            entitlements[c.id] = await apiClientFetch<RefundEntitlement>(`/api/occupancy/termination-cases/${c.id}/calculation`);
          } catch {
            // No refund worked out yet for this case.
          }
        }),
      );
      setTerminationEntitlements(entitlements);
    } catch {
      setTerminationCases([]);
    } finally {
      setTerminationLoading(false);
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
              The hourly scheduled job creates the next rent automatically.
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
              The host and renter decide whether to extend or end these tenancies.
            </p>
          </div>
        </div>
      )}

      {loadError && <p className="text-sm text-accent-600">{loadError}</p>}

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
            <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{occupancy.listingName || occupancy.listingId}</p>
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
              {occupancy.moveOutDate && (
                <p className="flex items-center gap-1.5">
                  <CalendarClock className="h-3.5 w-3.5" /> Moved out {formatDate(occupancy.moveOutDate)}
                </p>
              )}
            </div>
            <div className="mt-3 space-y-2">
              <Button size="sm" variant="outline" className="w-full" onClick={() => setRecordFor(occupancy.id)}>
                <History className="h-3.5 w-3.5" /> View tenancy record
              </Button>
              <Button size="sm" variant="outline" className="w-full" onClick={() => openTermination(occupancy)}>
                <FileWarning className="h-3.5 w-3.5" /> View termination &amp; refund
              </Button>
            </div>
          </div>
        ))}
      </div>

      {occupancies.length === 0 && !loadError && (
        <p className="py-10 text-center text-sm text-slate-400 dark:text-slate-400">No tenancies yet.</p>
      )}

      <TenancyRecordModal occupancyId={recordFor} onClose={() => setRecordFor(null)} />

      <Modal open={Boolean(terminationFor)} onClose={() => setTerminationFor(null)} title="Termination & Refund">
        <div className="space-y-4">
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Read-only. Ending a tenancy and any refund are settled between the host and the renter.
          </p>
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
                  {(termCase.status === "AWAITING_RESPONSE" || termCase.status === "CONTESTED") && (
                    <p className="border-t border-slate-200 pt-2 text-xs text-slate-500 dark:border-slate-700">
                      {termCase.status === "CONTESTED"
                        ? "Contested between the parties — settled when either records a court/tribunal order."
                        : `Waiting on ${(termCase.pendingResponders ?? []).length} part${(termCase.pendingResponders ?? []).length === 1 ? "y" : "ies"} to answer.`}
                    </p>
                  )}
                  {entitlement ? (
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
                    </div>
                  ) : (
                    (termCase.status === "EFFECTIVE_DATE_SET" || termCase.status === "TERMINATED") && (
                      <p className="border-t border-slate-200 pt-2 text-xs text-slate-400 dark:border-slate-700">
                        No refund has been worked out for this case yet.
                      </p>
                    )
                  )}
                </div>
              );
            })
          )}
        </div>
      </Modal>
    </div>
  );
}
