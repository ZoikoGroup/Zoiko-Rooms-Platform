"use client";

import { useCallback, useEffect, useState } from "react";
import { CreditCard, IndianRupee } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Obligation, SimulatedPayment } from "@/lib/types";
import { obligationStatusLabel, obligationStatusTone, simulatedPaymentStatusTone } from "@/lib/status";
import { formatCurrency, formatDate } from "@/lib/utils";
import { errorMessage, listUserObligations, listUserPayments, payUserObligation } from "@/lib/user-api";
import { Card, EmptyState, Toast, useToast } from "@/components/user/ui";

function DueCharges({
  obligations,
  payingId,
  onPay,
}: {
  obligations: Obligation[];
  payingId: number | null;
  onPay: (id: number) => void;
}) {
  const due = obligations.filter((o) => o.status === "PENDING" || o.status === "PARTIALLY_PAID");
  if (due.length === 0) return null;

  return (
    <Card>
      <div className="flex items-center gap-2">
        <IndianRupee className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
        <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Rent &amp; Charges Due</h2>
      </div>
      <div className="mt-3 space-y-2">
        {due.map((obligation) => (
          <div
            key={obligation.id}
            className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10"
          >
            <div>
              <p className="text-sm font-semibold text-primary-900 dark:text-white">
                {obligation.obligationType === "RENT" ? "Rent" : obligation.obligationType === "DEPOSIT" ? "Security deposit" : obligation.obligationType}
                {" — "}
                {formatCurrency(obligation.amount)} {obligation.currency}
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400">Due {formatDate(obligation.dueDate)}</p>
            </div>
            <div className="flex items-center gap-2">
              <Badge tone={obligationStatusTone[obligation.status]}>{obligationStatusLabel[obligation.status]}</Badge>
              <Button size="sm" loading={payingId === obligation.id} onClick={() => onPay(obligation.id)}>
                Pay Now
              </Button>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

export function PaymentsHistory() {
  const { toast, showToast } = useToast();
  const [payments, setPayments] = useState<SimulatedPayment[]>([]);
  const [obligations, setObligations] = useState<Obligation[]>([]);
  const [loading, setLoading] = useState(true);
  const [payingId, setPayingId] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const [paymentsData, obligationsData] = await Promise.all([listUserPayments(), listUserObligations()]);
      setPayments(paymentsData);
      setObligations(obligationsData);
    } catch (err) {
      showToast(errorMessage(err, "Could not load your payments."), "error");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    load();
  }, [load]);

  async function handlePay(obligationId: number) {
    setPayingId(obligationId);
    try {
      await payUserObligation(obligationId);
      showToast("Payment successful.");
      await load();
    } catch (err) {
      showToast(errorMessage(err, "Payment failed. Please try again."), "error");
    } finally {
      setPayingId(null);
    }
  }

  if (loading) return <Loader label="Loading your payments" />;

  if (payments.length === 0 && obligations.length === 0) {
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
    <div className="space-y-4">
      <DueCharges obligations={obligations} payingId={payingId} onPay={handlePay} />
      {payments.length > 0 && (
        <Card className="!p-0">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-left text-sm">
          <thead>
            <tr className="border-b border-slate-100 text-xs font-bold uppercase tracking-wide text-slate-400 dark:border-white/10">
              <th className="px-5 py-3">Payment</th>
              <th className="px-5 py-3">Room</th>
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
                <td className="px-5 py-3 text-xs text-slate-500 dark:text-slate-400">
                  {payment.listingName ? (
                    <>
                      <span className="block font-medium text-slate-700 dark:text-slate-200">{payment.listingName}</span>
                      {payment.propertyAddress && <span>{payment.propertyAddress}</span>}
                    </>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="px-5 py-3 text-slate-500 dark:text-slate-400">{formatDate(payment.createdAt)}</td>
                <td className="px-5 py-3 font-semibold text-primary-900 dark:text-white">
                  {formatCurrency(payment.amount)}
                  <span className="ml-1 text-xs font-normal text-slate-400">{payment.currency}</span>
                </td>
                <td className="px-5 py-3 text-xs text-slate-500 dark:text-slate-400">
                  {payment.allocations.length === 0
                    ? "Unallocated"
                    : payment.allocations
                        .map((a) => `Obligation #${a.obligationId} (${formatCurrency(a.amountAllocated)})`)
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
        </Card>
      )}

      <Toast toast={toast} />
    </div>
  );
}
