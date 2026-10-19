"use client";

import { useEffect, useState } from "react";
import { CreditCard } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Loader } from "@/components/ui/Loader";
import { SimulatedPayment } from "@/lib/types";
import { simulatedPaymentStatusTone } from "@/lib/status";
import { formatCurrency, formatDate } from "@/lib/utils";
import { errorMessage, listUserPayments } from "@/lib/user-api";
import { Card, EmptyState, Toast, useToast } from "@/components/user/ui";

export function PaymentsHistory() {
  const { toast, showToast } = useToast();
  const [payments, setPayments] = useState<SimulatedPayment[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listUserPayments()
      .then(setPayments)
      .catch((err) => showToast(errorMessage(err, "Could not load your payment history."), "error"))
      .finally(() => setLoading(false));
  }, [showToast]);

  if (loading) return <Loader label="Loading your payments" />;

  if (payments.length === 0) {
    return (
      <Card>
        <div className="flex flex-col items-center gap-4 py-10 text-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
            <CreditCard className="h-6 w-6" />
          </span>
          <EmptyState message="No payments have been recorded on your account yet." />
        </div>
      </Card>
    );
  }

  return (
    <Card className="!p-0 overflow-hidden">
      {/* Desktop Table View (sm+) */}
      <div className="hidden sm:block overflow-x-auto">
        <table className="w-full min-w-[560px] text-left text-sm">
          <thead>
            <tr className="border-b border-slate-100 text-xs font-bold uppercase tracking-wide text-slate-400 dark:border-white/10">
              <th className="px-5 py-3">Payment</th>
              <th className="px-5 py-3">Date</th>
              <th className="px-5 py-3">Amount</th>
              <th className="px-5 py-3">Allocated to</th>
              <th className="px-5 py-3">Status</th>
            </tr>
          </thead>
          <tbody>
            {payments.map((payment) => (
              <tr
                key={payment.id}
                className="border-b border-slate-50 last:border-0 dark:border-white/5 hover:bg-slate-50/50 dark:hover:bg-slate-800/40 transition-colors"
              >
                <td className="px-5 py-3 font-semibold text-slate-700 dark:text-slate-200">#{payment.id}</td>
                <td className="px-5 py-3 text-slate-500 dark:text-slate-400">{formatDate(payment.createdAt)}</td>
                <td className="px-5 py-3 font-semibold text-primary-900 dark:text-white">
                  {formatCurrency(payment.amount, payment.currency)}
                </td>
                <td className="px-5 py-3 text-xs text-slate-500 dark:text-slate-400">
                  {payment.allocations.length === 0
                    ? "Unallocated"
                    : payment.allocations
                        .map((a) => `Obligation #${a.obligationId} (${formatCurrency(a.amountAllocated, payment.currency)})`)
                        .join(", ")}
                </td>
                <td className="px-5 py-3">
                  <Badge tone={simulatedPaymentStatusTone[payment.status] ?? "neutral"}>{payment.status}</Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Mobile Card View (sm:hidden) */}
      <div className="sm:hidden divide-y divide-slate-100 dark:divide-slate-800">
        {payments.map((payment) => (
          <div key={payment.id} className="p-4 space-y-2.5">
            <div className="flex items-start justify-between gap-2">
              <div>
                <span className="text-xs font-bold font-mono text-slate-500 dark:text-slate-400">#{payment.id}</span>
                <p className="text-xs text-slate-400">{formatDate(payment.createdAt)}</p>
              </div>
              <span className="text-base font-extrabold text-primary-900 dark:text-white">
                {formatCurrency(payment.amount, payment.currency)}
              </span>
            </div>
            <div className="flex items-center justify-between gap-2 pt-1 border-t border-slate-50 dark:border-slate-800/60 text-xs">
              <span className="text-slate-500 dark:text-slate-400 truncate max-w-[200px]">
                {payment.allocations.length === 0
                  ? "Unallocated"
                  : payment.allocations.map((a) => `Obligation #${a.obligationId}`).join(", ")}
              </span>
              <Badge tone={simulatedPaymentStatusTone[payment.status] ?? "neutral"}>{payment.status}</Badge>
            </div>
          </div>
        ))}
      </div>

      <Toast toast={toast} />
    </Card>
  );
}
