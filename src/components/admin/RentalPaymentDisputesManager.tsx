"use client";

import { useCallback, useEffect, useState } from "react";
import { Scale } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { ApiError, apiClientFetch } from "@/lib/api-client";
import { RentalPaymentDisputeAdmin, RentalPaymentDisputeOutcome } from "@/lib/types";
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

const OUTCOMES: { value: RentalPaymentDisputeOutcome; label: string; hint: string }[] = [
  { value: "PAYMENT_STANDS", label: "Payment stands", hint: "The host did receive it -- the payment is confirmed." },
  { value: "PAYMENT_NOT_RECEIVED", label: "Not received", hint: "The money isn't with the host -- this amount is owed again." },
  { value: "CLOSE_ONLY", label: "Close only", hint: "Close the dispute and leave the payment as it is." },
];

/** Rent is paid to the host directly, so when tenant and host disagree about
 *  a payment, support decides it here: the queue of open disputes with the
 *  payment each one is about, and a decision that goes with closing it. */
export function RentalPaymentDisputesManager({ showToast }: { showToast: (message: string) => void }) {
  const [disputes, setDisputes] = useState<RentalPaymentDisputeAdmin[]>([]);
  const [allowed, setAllowed] = useState(true);
  const [resolving, setResolving] = useState<RentalPaymentDisputeAdmin | null>(null);
  const [outcome, setOutcome] = useState<RentalPaymentDisputeOutcome>("PAYMENT_STANDS");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);

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

  function open(dispute: RentalPaymentDisputeAdmin) {
    setResolving(dispute);
    setOutcome("PAYMENT_STANDS");
    setNotes("");
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!resolving) return;
    if (outcome !== "CLOSE_ONLY" && !notes.trim()) {
      showToast("Explain the decision in the notes -- both sides will see them");
      return;
    }
    setSubmitting(true);
    try {
      await apiClientFetch(`/api/finance/rental-payments/disputes/${resolving.id}/resolve`, {
        method: "POST",
        body: JSON.stringify({ resolutionNotes: notes.trim(), outcome }),
      });
      showToast("Dispute resolved -- tenant and host have been notified");
      setResolving(null);
      load();
    } catch {
      showToast("Failed to resolve this dispute");
    } finally {
      setSubmitting(false);
    }
  }

  if (!allowed) return null;

  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex items-center gap-2">
        <Scale className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
        <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Rent payment disputes</h2>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Rent is paid to the host directly -- when tenant and host disagree about a payment, decide it here.
      </p>
      <div className="mt-4 space-y-2">
        {disputes.map((d) => (
          <div key={d.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-semibold capitalize text-primary-900 dark:text-white">
                  {d.obligationLabel} · {formatCurrency(d.declaredAmount, d.declaredCurrency)}
                </p>
                <Badge tone={rentalPaymentStatusTone[d.recordStatus as keyof typeof rentalPaymentStatusTone] ?? "neutral"}>
                  {rentalPaymentStatusLabel[d.recordStatus as keyof typeof rentalPaymentStatusLabel] ?? d.recordStatus}
                </Badge>
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
            <Button size="sm" variant="primary" onClick={() => open(d)}>
              Resolve
            </Button>
          </div>
        ))}
        {disputes.length === 0 && <p className="text-sm text-slate-400">No open rent payment disputes.</p>}
      </div>

      <Modal open={Boolean(resolving)} onClose={() => setResolving(null)} title="Resolve payment dispute">
        {resolving && (
          <form onSubmit={submit} className="space-y-4">
            <p className="text-sm text-slate-600 dark:text-slate-300">
              <span className="capitalize">{resolving.obligationLabel}</span> payment of{" "}
              {formatCurrency(resolving.declaredAmount, resolving.declaredCurrency)} --{" "}
              {REASON_LABELS[resolving.reasonCode] ?? resolving.reasonCode}.
            </p>
            <fieldset className="space-y-2">
              {OUTCOMES.map((o) => (
                <label key={o.value} className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-200">
                  <input
                    type="radio" name="outcome" value={o.value} checked={outcome === o.value}
                    onChange={() => setOutcome(o.value)} className="mt-1"
                  />
                  <span>
                    <span className="font-semibold">{o.label}</span>
                    <span className="block text-xs text-slate-500 dark:text-slate-400">{o.hint}</span>
                  </span>
                </label>
              ))}
            </fieldset>
            <label className="block text-sm text-slate-700 dark:text-slate-200">
              <span className="font-semibold">Notes{outcome !== "CLOSE_ONLY" ? " *" : ""}</span>
              <textarea
                value={notes} onChange={(e) => setNotes(e.target.value)} rows={3}
                className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm dark:border-white/10 dark:bg-slate-800"
                placeholder="What you checked and why -- the tenant and host both see this."
              />
            </label>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setResolving(null)}>Cancel</Button>
              <Button type="submit" loading={submitting}>Resolve</Button>
            </div>
          </form>
        )}
      </Modal>
    </section>
  );
}
