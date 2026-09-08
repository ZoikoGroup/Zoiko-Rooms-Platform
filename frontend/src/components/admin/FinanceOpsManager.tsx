"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertOctagon, BadgeCheck, CheckCircle2, Landmark, ReceiptText, RotateCcw } from "lucide-react";
import { AdminRole, DisputeCase, Obligation, PayoutRecord, ReconciliationRun, RefundRequest, SimulatedPayment } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
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
const emptyRefundForm = { paymentId: "", obligationId: "", amount: "", reason: "" };
const emptyDisputeForm = { paymentId: "", category: "OTHER", description: "" };

export function FinanceOpsManager() {
  const [payments, setPayments] = useState<SimulatedPayment[]>([]);
  const [obligations, setObligations] = useState<Obligation[]>([]);
  const [payouts, setPayouts] = useState<PayoutRecord[]>([]);
  const [refunds, setRefunds] = useState<RefundRequest[]>([]);
  const [disputes, setDisputes] = useState<DisputeCase[]>([]);
  const [runs, setRuns] = useState<ReconciliationRun[]>([]);
  const [toast, setToast] = useState("");
  const [role, setRole] = useState<AdminRole | null>(null);

  const [payoutModalOpen, setPayoutModalOpen] = useState(false);
  const [payoutForm, setPayoutForm] = useState(emptyPayoutForm);
  const [refundModalOpen, setRefundModalOpen] = useState(false);
  const [refundForm, setRefundForm] = useState(emptyRefundForm);
  const [disputeModalOpen, setDisputeModalOpen] = useState(false);
  const [disputeForm, setDisputeForm] = useState(emptyDisputeForm);

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3600);
  }

  const loadAll = useCallback(async () => {
    try {
      const admin = await getCurrentAdmin();
      setRole(admin?.role ?? null);

      const [paymentsData, obligationsData, payoutsData, refundsData, disputesData] = await Promise.all([
        apiClientFetch<SimulatedPayment[]>("/api/finance/payments"),
        apiClientFetch<Obligation[]>("/api/finance/obligations"),
        apiClientFetch<PayoutRecord[]>("/api/finance/payouts"),
        apiClientFetch<RefundRequest[]>("/api/finance/refunds"),
        apiClientFetch<DisputeCase[]>("/api/finance/disputes"),
      ]);
      setPayments(paymentsData);
      setObligations(obligationsData);
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
  }, []);

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
      showToast(payout.status === "HELD" ? `Payout held: ${payout.holdReason}` : `Payout of ${formatCurrency(payout.amount)} paid`);
      setPayoutModalOpen(false);
      setPayoutForm(emptyPayoutForm);
      loadAll();
    } catch {
      showToast("Failed to run payout — it may already exist for this provider and period");
    }
  }

  async function submitRefund(e: React.FormEvent) {
    e.preventDefault();
    try {
      await apiClientFetch("/api/finance/refunds", {
        method: "POST",
        body: JSON.stringify({
          paymentId: Number(refundForm.paymentId),
          obligationId: Number(refundForm.obligationId),
          amount: Number(refundForm.amount),
          reason: refundForm.reason,
        }),
      });
      showToast("Refund requested");
      setRefundModalOpen(false);
      setRefundForm(emptyRefundForm);
      loadAll();
    } catch {
      showToast("Failed to request refund");
    }
  }

  async function decideRefund(id: number, approve: boolean) {
    try {
      await apiClientFetch(`/api/finance/refunds/${id}/decide`, { method: "POST", body: JSON.stringify({ approve }) });
      showToast(approve ? "Refund completed" : "Refund rejected");
      loadAll();
    } catch {
      showToast("Failed to decide refund");
    }
  }

  async function submitDispute(e: React.FormEvent) {
    e.preventDefault();
    try {
      await apiClientFetch("/api/finance/disputes", {
        method: "POST",
        body: JSON.stringify({
          paymentId: disputeForm.paymentId ? Number(disputeForm.paymentId) : undefined,
          category: disputeForm.category,
          description: disputeForm.description,
        }),
      });
      showToast("Dispute opened");
      setDisputeModalOpen(false);
      setDisputeForm(emptyDisputeForm);
      loadAll();
    } catch {
      showToast("Failed to open dispute");
    }
  }

  async function resolveDispute(id: number, resolved: boolean) {
    try {
      await apiClientFetch(`/api/finance/disputes/${id}/resolve`, {
        method: "POST",
        body: JSON.stringify({ status: resolved ? "RESOLVED" : "REJECTED", resolutionNotes: "" }),
      });
      showToast(resolved ? "Dispute resolved" : "Dispute rejected");
      loadAll();
    } catch {
      showToast("Failed to update dispute");
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
          <Button size="sm" variant="accent" onClick={() => setPayoutModalOpen(true)}>
            Run Payout
          </Button>
        </div>
        <div className="mt-4 space-y-2">
          {payouts.map((payout) => (
            <div key={payout.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
              <div>
                <p className="text-sm font-semibold text-primary-900 dark:text-white">
                  Party {payout.partyId} · {formatCurrency(payout.amount)} · {payout.periodKey}
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
          <Button size="sm" variant="accent" onClick={() => setRefundModalOpen(true)}>
            Request Refund
          </Button>
        </div>
        <div className="mt-4 space-y-2">
          {refunds.map((refund) => (
            <div key={refund.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
              <div>
                <p className="text-sm font-semibold text-primary-900 dark:text-white">
                  {formatCurrency(refund.amount)} · payment #{refund.paymentId} · obligation #{refund.obligationId}
                </p>
                <p className="text-xs text-slate-500 dark:text-slate-400">{refund.reason || "No reason given"}</p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={refundStatusTone[refund.status]}>{refundStatusLabel[refund.status] ?? refund.status}</Badge>
                {refund.status === "REQUESTED" && (
                  <>
                    <Button size="sm" variant="primary" onClick={() => decideRefund(refund.id, true)}>
                      Approve
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => decideRefund(refund.id, false)}>
                      Reject
                    </Button>
                  </>
                )}
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
          <Button size="sm" variant="accent" onClick={() => setDisputeModalOpen(true)}>
            Open Dispute
          </Button>
        </div>
        <div className="mt-4 space-y-2">
          {disputes.map((dispute) => (
            <div key={dispute.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
              <div>
                <p className="text-sm font-semibold text-primary-900 dark:text-white">{dispute.category}</p>
                <p className="text-xs text-slate-500 dark:text-slate-400">{dispute.description || "No description"}</p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={disputeStatusTone[dispute.status]}>{disputeStatusLabel[dispute.status] ?? dispute.status}</Badge>
                {dispute.status === "OPEN" && (
                  <>
                    <Button size="sm" variant="primary" onClick={() => resolveDispute(dispute.id, true)}>
                      Resolve
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => resolveDispute(dispute.id, false)}>
                      Reject
                    </Button>
                  </>
                )}
              </div>
            </div>
          ))}
          {disputes.length === 0 && <p className="text-sm text-slate-400">No disputes yet.</p>}
        </div>
      </section>

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

      <Modal open={refundModalOpen} onClose={() => setRefundModalOpen(false)} title="Request Refund">
        <form onSubmit={submitRefund} className="space-y-3.5">
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Payment</label>
            <select
              value={refundForm.paymentId}
              onChange={(e) => setRefundForm((f) => ({ ...f, paymentId: e.target.value }))}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              required
            >
              <option value="">Select a payment…</option>
              {payments.map((p) => (
                <option key={p.id} value={p.id}>
                  #{p.id} · {p.guestName || "Unknown guest"}
                  {p.listingName ? ` · ${p.listingName}` : ""} · {formatCurrency(p.amount)} · {p.status}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Obligation</label>
            <select
              value={refundForm.obligationId}
              onChange={(e) => setRefundForm((f) => ({ ...f, obligationId: e.target.value }))}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              required
            >
              <option value="">Select an obligation…</option>
              {obligations.map((o) => (
                <option key={o.id} value={o.id}>
                  #{o.id} · {o.obligationType} · {formatCurrency(o.amount)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Amount (₹)</label>
            <input
              type="number"
              min={0}
              value={refundForm.amount}
              onChange={(e) => setRefundForm((f) => ({ ...f, amount: e.target.value }))}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              required
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Reason</label>
            <input
              value={refundForm.reason}
              onChange={(e) => setRefundForm((f) => ({ ...f, reason: e.target.value }))}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <Button type="submit" variant="primary" fullWidth>
            Request Refund
          </Button>
        </form>
      </Modal>

      <Modal open={disputeModalOpen} onClose={() => setDisputeModalOpen(false)} title="Open Dispute">
        <form onSubmit={submitDispute} className="space-y-3.5">
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Payment (optional)</label>
            <select
              value={disputeForm.paymentId}
              onChange={(e) => setDisputeForm((f) => ({ ...f, paymentId: e.target.value }))}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            >
              <option value="">None</option>
              {payments.map((p) => (
                <option key={p.id} value={p.id}>
                  #{p.id} · {p.guestName || "Unknown guest"}
                  {p.listingName ? ` · ${p.listingName}` : ""} · {formatCurrency(p.amount)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Category</label>
            <select
              value={disputeForm.category}
              onChange={(e) => setDisputeForm((f) => ({ ...f, category: e.target.value }))}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            >
              <option value="CHARGEBACK">Chargeback</option>
              <option value="COMPENSATION">Compensation</option>
              <option value="OTHER">Other</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Description</label>
            <textarea
              value={disputeForm.description}
              onChange={(e) => setDisputeForm((f) => ({ ...f, description: e.target.value }))}
              rows={3}
              className="w-full resize-none rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <Button type="submit" variant="primary" fullWidth>
            Open Dispute
          </Button>
        </form>
      </Modal>

      {toast && (
        <div className="animate-fade-up fixed bottom-6 right-6 z-[300] flex max-w-sm items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" /> {toast}
        </div>
      )}
    </div>
  );
}
