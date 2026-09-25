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
    <Card className="!p-0">
      <div className="overflow-x-auto">
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
                className="border-b border-slate-50 last:border-0 dark:border-white/5"
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

      <Toast toast={toast} />
    </Card>
  );
}
