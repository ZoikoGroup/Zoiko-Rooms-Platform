"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, CreditCard, PiggyBank, Wallet } from "lucide-react";
import { DepositRecord, Obligation } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { apiClientFetch } from "@/lib/api-client";
import { depositStatusLabel, depositStatusTone, obligationStatusLabel, obligationStatusTone } from "@/lib/status";
import { formatCurrency, formatDate } from "@/lib/utils";

export function FinanceLedgerManager() {
  const [obligations, setObligations] = useState<Obligation[]>([]);
  const [deposits, setDeposits] = useState<DepositRecord[]>([]);
  const [toast, setToast] = useState("");
  const [payingId, setPayingId] = useState<number | null>(null);

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3200);
  }

  const loadAll = useCallback(async () => {
    try {
      const [obligationsData, depositsData] = await Promise.all([
        apiClientFetch<Obligation[]>("/api/finance/obligations"),
        apiClientFetch<DepositRecord[]>("/api/finance/deposits"),
      ]);
      setObligations(obligationsData);
      setDeposits(depositsData);
    } catch {
      showToast("Failed to load finance data");
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  async function payInFull(obligation: Obligation) {
    setPayingId(obligation.id);
    try {
      const idempotencyKey = `PAY-${obligation.id}-${crypto.randomUUID()}`;
      const payment = await apiClientFetch<{ id: number }>("/api/finance/payments", {
        method: "POST",
        body: JSON.stringify({
          guestId: obligation.guestId,
          amount: obligation.amount,
          currency: obligation.currency,
          idempotencyKey,
        }),
      });
      await apiClientFetch(`/api/finance/payments/${payment.id}/confirm`, {
        method: "POST",
        body: JSON.stringify({ allocations: [{ obligationId: obligation.id, amount: obligation.amount }] }),
      });
      showToast(`Recorded ${obligation.obligationType.toLowerCase()} obligation as paid in full`);
      loadAll();
    } catch {
      showToast("Could not record this payment");
    } finally {
      setPayingId(null);
    }
  }

  async function releaseDeposit(deposit: DepositRecord) {
    const remaining = deposit.heldAmount - deposit.releasedAmount;
    try {
      await apiClientFetch(`/api/finance/deposits/${deposit.id}/release`, {
        method: "POST",
        body: JSON.stringify({ amount: remaining, notes: "Released in full" }),
      });
      showToast("Deposit released");
      loadAll();
    } catch {
      showToast("Failed to release deposit");
    }
  }

  async function forfeitDeposit(deposit: DepositRecord) {
    try {
      await apiClientFetch(`/api/finance/deposits/${deposit.id}/forfeit`, { method: "POST" });
      showToast("Deposit forfeited");
      loadAll();
    } catch {
      showToast("Failed to forfeit deposit");
    }
  }

  return (
    <div className="space-y-6">
      <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        <div className="flex items-center gap-2">
          <Wallet className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
          <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Obligations</h2>
        </div>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          What each tenant owes the platform — rent, deposits, fees and tax. There is no live payment gateway
          connected yet, so &quot;Record Payment in Full&quot; marks an obligation paid in Zoiko&apos;s own records; it
          does not charge the tenant or move real money.
        </p>
        <div className="mt-4 space-y-2">
          {obligations.map((obligation) => (
            <div
              key={obligation.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10"
            >
              <div>
                <p className="text-sm font-semibold text-primary-900 dark:text-white">
                  {obligation.obligationType} · {formatCurrency(obligation.amount)}
                </p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Owed by guest {obligation.guestId} · due {formatDate(obligation.dueDate)} · {obligation.moneyPlane.toLowerCase()} plane
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={obligationStatusTone[obligation.status]}>{obligationStatusLabel[obligation.status] ?? obligation.status}</Badge>
                {(obligation.status === "PENDING" || obligation.status === "PARTIALLY_PAID") && (
                  <Button size="sm" variant="primary" disabled={payingId === obligation.id} onClick={() => payInFull(obligation)}>
                    <CreditCard className="h-3.5 w-3.5" /> {payingId === obligation.id ? "Recording…" : "Record Payment in Full"}
                  </Button>
                )}
              </div>
            </div>
          ))}
          {obligations.length === 0 && <p className="text-sm text-slate-400 dark:text-slate-400">No obligations yet.</p>}
        </div>
      </section>

      <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        <div className="flex items-center gap-2">
          <PiggyBank className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
          <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Deposits</h2>
        </div>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          Held in the safeguarded money plane, separate from rent and Zoiko&apos;s own revenue, until released or forfeited.
        </p>
        <div className="mt-4 space-y-2">
          {deposits.map((deposit) => (
            <div
              key={deposit.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10"
            >
              <div>
                <p className="text-sm font-semibold text-primary-900 dark:text-white">{formatCurrency(deposit.heldAmount)} held</p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {formatCurrency(deposit.releasedAmount)} released{deposit.notes ? ` · ${deposit.notes}` : ""}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={depositStatusTone[deposit.status]}>{depositStatusLabel[deposit.status] ?? deposit.status}</Badge>
                {(deposit.status === "HELD" || deposit.status === "PARTIALLY_RELEASED") && (
                  <>
                    <Button size="sm" variant="primary" onClick={() => releaseDeposit(deposit)}>
                      Release
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => forfeitDeposit(deposit)}>
                      Forfeit
                    </Button>
                  </>
                )}
              </div>
            </div>
          ))}
          {deposits.length === 0 && <p className="text-sm text-slate-400 dark:text-slate-400">No deposits yet.</p>}
        </div>
      </section>

      {toast && (
        <div className="animate-fade-up fixed bottom-6 right-6 z-[300] flex max-w-sm items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" /> {toast}
        </div>
      )}
    </div>
  );
}
