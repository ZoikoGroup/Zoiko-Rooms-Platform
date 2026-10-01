"use client";

import { useCallback, useEffect, useState } from "react";
import { Scale } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { ApiError, apiClientFetch } from "@/lib/api-client";
import { RentalPaymentDisputeAdmin } from "@/lib/types";
import { rentalPaymentStatusLabel, rentalPaymentStatusTone } from "@/lib/status";
import { formatCurrency, formatDate } from "@/lib/utils";

const REASON_LABELS: Record<string, string> = {
  NOT_ARRIVED: "Payment has not arrived",
  AMOUNT_DIFFERENT: "Amount received is different",
  REFERENCE_MISMATCH: "Reference cannot be matched",
  RETURNED_OR_REVERSED: "Payment was returned or reversed",
  OTHER: "Other",
};

const METHOD_LABELS: Record<string, string> = {
  BANK_TRANSFER: "Bank transfer", UPI: "UPI", CASH: "Cash", CARD: "Card", OTHER: "Other",
};

/** Rent is paid to the host directly, and a problem with a payment is sorted
 *  out between the tenant and the host on their Disputes pages (the host
 *  confirms receiving it, the tenant confirms it wasn't paid, or the reporter
 *  withdraws). This is a read-only view of the ones still open. */
export function RentalPaymentDisputesManager({ showToast }: { showToast: (message: string) => void }) {
  const [disputes, setDisputes] = useState<RentalPaymentDisputeAdmin[]>([]);
  const [allowed, setAllowed] = useState(true);

  const load = useCallback(async () => {
    try {
      setDisputes(await apiClientFetch<RentalPaymentDisputeAdmin[]>("/api/finance/rental-payments/disputes"));
    } catch (err) {
      // Only payment staff and super admins can see this queue.
      if (err instanceof ApiError && err.status === 403) setAllowed(false);
      else showToast("Failed to load rent payment disputes");
    }
  }, [showToast]);

  useEffect(() => {
    load();
  }, [load]);

  if (!allowed) return null;

  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex items-center gap-2">
        <Scale className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
        <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Rent payment disputes</h2>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Open problems reported on rent and deposit payments. The tenant and host resolve these between themselves on
        their Disputes pages -- nothing to decide here.
      </p>
      <div className="mt-4 space-y-2">
        {disputes.map((d) => (
          <div key={d.id} className="rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-sm font-semibold capitalize text-primary-900 dark:text-white">
                {d.obligationLabel} · {formatCurrency(d.declaredAmount, d.declaredCurrency)}
              </p>
              <Badge tone={rentalPaymentStatusTone[d.recordStatus as keyof typeof rentalPaymentStatusTone] ?? "neutral"}>
                {rentalPaymentStatusLabel[d.recordStatus as keyof typeof rentalPaymentStatusLabel] ?? d.recordStatus}
              </Badge>
              {d.disputeCaseId != null && <span className="text-xs text-slate-400">Dispute case #{d.disputeCaseId}</span>}
            </div>
            <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
              Reported by the {d.reportedBy} on {formatDate(d.reportedAt)}: {REASON_LABELS[d.reasonCode] ?? d.reasonCode}
              {d.details ? ` -- "${d.details}"` : ""}
            </p>
            <p className="mt-0.5 text-xs text-slate-400">
              {METHOD_LABELS[d.paymentMethodCategory] ?? d.paymentMethodCategory} on {formatDate(d.declaredDate)}
              {d.externalReference ? ` · ref ${d.externalReference}` : ""} · obligation #{d.obligationId}
            </p>
          </div>
        ))}
        {disputes.length === 0 && <p className="text-sm text-slate-400">No open rent payment disputes.</p>}
      </div>
    </section>
  );
}
