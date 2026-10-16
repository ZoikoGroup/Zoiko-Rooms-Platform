"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertOctagon, BadgeCheck, CheckCircle2, Landmark, ReceiptText, RotateCcw } from "lucide-react";
import {
  AdminRole,
  DisputeCase,
  PaymentCapabilities,
  PayoutRecord,
  ReconciliationRun,
  RefundRequest,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { ListingFeePriceBookManager } from "@/components/admin/ListingFeePriceBookManager";
import { RentalPaymentDisputesManager } from "@/components/admin/RentalPaymentDisputesManager";
import { RentalPaymentSupportManager } from "@/components/admin/RentalPaymentSupportManager";
import { ListingFeePaymentsAdmin } from "@/components/admin/ListingFeePaymentsAdmin";
import { apiClientFetch } from "@/lib/api-client";
import { getCurrentAdmin } from "@/lib/auth";
import {
  disputeStatusLabel,
  disputeStatusTone,
  payoutStatusLabel,
  payoutStatusTone,
  reconciliationStatusLabel,
  reconciliationStatusTone,
  refundStatusLabel,
  refundStatusTone,
} from "@/lib/status";
import { formatCurrency, formatDate } from "@/lib/utils";

const emptyPayoutForm = { partyId: "", periodKey: "" };
export function FinanceOpsManager() {
  const [payouts, setPayouts] = useState<PayoutRecord[]>([]);
  const [refunds, setRefunds] = useState<RefundRequest[]>([]);
  const [disputes, setDisputes] = useState<DisputeCase[]>([]);
  const [runs, setRuns] = useState<ReconciliationRun[]>([]);
  const [capabilities, setCapabilities] = useState<PaymentCapabilities | null>(null);
  const [toast, setToast] = useState("");
  const [role, setRole] = useState<AdminRole | null>(null);

  const [payoutModalOpen, setPayoutModalOpen] = useState(false);
  const [payoutForm, setPayoutForm] = useState(emptyPayoutForm);

  // Stable identity -- ListingFeePriceBookManager reloads when it changes.
  const showToast = useCallback((message: string) => {
    setToast(message);
    setTimeout(() => setToast(""), 3600);
  }, []);

  const loadAll = useCallback(async () => {
    try {
      const admin = await getCurrentAdmin();
      setRole(admin?.role ?? null);
      setCapabilities(await apiClientFetch<PaymentCapabilities>("/api/finance/capabilities"));

      const [payoutsData, refundsData, disputesData] = await Promise.all([
        apiClientFetch<PayoutRecord[]>("/api/finance/payouts"),
        apiClientFetch<RefundRequest[]>("/api/finance/refunds"),
        apiClientFetch<DisputeCase[]>("/api/finance/disputes"),
      ]);
      setPayouts(payoutsData);
      setRefunds(refundsData);
      setDisputes(disputesData);

      // Reconciliation is a platform-wide metric, super_admin only -- fetched
      // separately so a 403 here never blocks the rest of the page for a provider.
      if (admin?.role === "super_admin") {
        setRuns(await apiClientFetch<ReconciliationRun[]>("/api/finance/reconciliation"));
      }
    } catch {
      showToast("Failed to load finance operations data");
    }
  }, [showToast]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  async function runPayout(e: React.FormEvent) {
    e.preventDefault();
    try {
      const payout = await apiClientFetch<PayoutRecord>("/api/finance/payouts/run", {
        method: "POST",
        body: JSON.stringify({ partyId: Number(payoutForm.partyId), periodKey: payoutForm.periodKey }),
      });
      showToast(payout.status === "HELD" ? `Payout held: ${payout.holdReason}` : `Payout of ${formatCurrency(payout.amount, payout.currency)} paid`);
      setPayoutModalOpen(false);
      setPayoutForm(emptyPayoutForm);
      loadAll();
    } catch {
      showToast("Failed to run payout — it may already exist for this provider and period");
    }
  }

  async function runReconciliation() {
    try {
      const run = await apiClientFetch<ReconciliationRun>("/api/finance/reconciliation/run", { method: "POST" });
      showToast(run.status === "CLEAN" ? "Reconciliation clean" : "Discrepancies found — check the run details");
      loadAll();
    } catch {
      showToast("Failed to run reconciliation (super admin only)");
    }
  }

  return (
    <div className="space-y-6">
      <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Landmark className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
            <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Provider Payouts</h2>
          </div>
          {capabilities?.host_payouts_enabled && (
            <Button size="sm" variant="accent" onClick={() => setPayoutModalOpen(true)}>
              Run Payout
            </Button>
          )}
        </div>
        {capabilities && !capabilities.host_payouts_enabled && (
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            Zoiko Rooms doesn&apos;t collect or pay out rental money — renters pay hosts directly using their verified
            payment methods. Past payouts are shown for the record only.
          </p>
        )}
        <div className="mt-4 space-y-2">
          {payouts.map((payout) => (
            <div key={payout.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
              <div>
                <p className="text-sm font-semibold text-primary-900 dark:text-white">
                  Party {payout.partyId} · {formatCurrency(payout.amount, payout.currency)} · {payout.periodKey}
                </p>
                {payout.holdReason && <p className="text-xs text-accent-600">{payout.holdReason}</p>}
              </div>
              <Badge tone={payoutStatusTone[payout.status]}>{payoutStatusLabel[payout.status] ?? payout.status}</Badge>
            </div>
          ))}
          {payouts.length === 0 && <p className="text-sm text-slate-400">No payouts yet.</p>}
        </div>
      </section>

      <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <RotateCcw className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
            <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Refunds</h2>
          </div>
        </div>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          Read-only. Refunds are calculated and paid by the rules when a tenancy ends, or settled between renter and
          host — Zoiko Rooms staff don&apos;t approve them.
        </p>
        {capabilities && !capabilities.rent_collection_enabled && (
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            Rental refunds happen directly between renter and recipient. Listing Fee refunds are handled from the Listing
            Fee payment itself.
          </p>
        )}
        <div className="mt-4 space-y-2">
          {refunds.map((refund) => (
            <div key={refund.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
              <div>
                <p className="text-sm font-semibold text-primary-900 dark:text-white">
                  {formatCurrency(refund.amount, refund.currency)} · payment #{refund.paymentId} · obligation #{refund.obligationId}
                </p>
                <p className="text-xs text-slate-500 dark:text-slate-400">{refund.reason || "No reason given"}</p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={refundStatusTone[refund.status]}>{refundStatusLabel[refund.status] ?? refund.status}</Badge>
              </div>
            </div>
          ))}
          {refunds.length === 0 && <p className="text-sm text-slate-400">No refund requests yet.</p>}
        </div>
      </section>

      <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertOctagon className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
            <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Disputes</h2>
          </div>
        </div>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          Read-only. Disputes are settled between renter and host, or by the outside body that handles them.
        </p>
        <div className="mt-4 space-y-2">
          {disputes.map((dispute) => (
            <div key={dispute.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
              <div>
                <p className="text-sm font-semibold text-primary-900 dark:text-white">{dispute.category}</p>
                <p className="text-xs text-slate-500 dark:text-slate-400">{dispute.description || "No description"}</p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={disputeStatusTone[dispute.status]}>{disputeStatusLabel[dispute.status] ?? dispute.status}</Badge>
              </div>
            </div>
          ))}
          {disputes.length === 0 && <p className="text-sm text-slate-400">No disputes yet.</p>}
        </div>
      </section>


      <RentalPaymentDisputesManager showToast={showToast} />

      <RentalPaymentSupportManager showToast={showToast} />

      {role === "super_admin" && <ListingFeePriceBookManager showToast={showToast} />}

      {role === "super_admin" && <ListingFeePaymentsAdmin showToast={showToast} />}

      {role === "super_admin" && (
        <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <ReceiptText className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
              <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Reconciliation</h2>
            </div>
            <Button size="sm" variant="accent" onClick={runReconciliation}>
              <BadgeCheck className="h-3.5 w-3.5" /> Run Reconciliation
            </Button>
          </div>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            A platform-wide view across every provider — super admin only.
          </p>
          <div className="mt-4 space-y-2">
            {runs.map((run) => (
              <div key={run.id} className="rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-semibold text-primary-900 dark:text-white">{formatDate(run.runAt)}</p>
                  <Badge tone={reconciliationStatusTone[run.status]}>{reconciliationStatusLabel[run.status] ?? run.status}</Badge>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-500 dark:text-slate-400 sm:grid-cols-3">
                  {Object.entries(run.totals).map(([key, value]) => (
                    <p key={key}>
                      {key}: <span className="font-medium text-slate-700 dark:text-slate-200">{formatCurrency(value)}</span>
                    </p>
                  ))}
                </div>
                {run.mismatches.length > 0 && (
                  <ul className="mt-2 list-disc pl-4 text-xs text-accent-600">
                    {run.mismatches.map((m, i) => (
                      <li key={i}>{m}</li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
            {runs.length === 0 && <p className="text-sm text-slate-400">No reconciliation runs yet.</p>}
          </div>
        </section>
      )}

      <Modal open={payoutModalOpen} onClose={() => setPayoutModalOpen(false)} title="Run Provider Payout">
        <form onSubmit={runPayout} className="space-y-3.5">
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Party ID</label>
            <input
              type="number"
              value={payoutForm.partyId}
              onChange={(e) => setPayoutForm((f) => ({ ...f, partyId: e.target.value }))}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              required
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Period Key <span className="font-normal normal-case text-slate-400">(e.g. 2026-08 — unique per provider)</span>
            </label>
            <input
              value={payoutForm.periodKey}
              onChange={(e) => setPayoutForm((f) => ({ ...f, periodKey: e.target.value }))}
              placeholder="2026-08"
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              required
            />
          </div>
          <Button type="submit" variant="primary" fullWidth>
            Run Payout
          </Button>
        </form>
      </Modal>


      {toast && (
        <div
          role="status"
          aria-live="polite"
          aria-atomic="true"
          className="animate-fade-up fixed bottom-6 right-6 z-[300] flex max-w-sm items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl"
        >
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" aria-hidden="true" /> {toast}
        </div>
      )}
    </div>
  );
}
