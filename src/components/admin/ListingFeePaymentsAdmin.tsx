"use client";

import { useCallback, useEffect, useState } from "react";
import { ReceiptText } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { ApiError, apiClientFetch } from "@/lib/api-client";
import { ListingFeePayment, ListingFeeRefund } from "@/lib/types";
import { listingFeePaymentStatusTone, listingFeeRefundStatusTone } from "@/lib/status";
import { formatDate, formatMoney } from "@/lib/utils";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** One payment's refund history -- loaded when the admin opens it. */
function RefundHistory({ paymentId, showToast }: { paymentId: number; showToast: (message: string) => void }) {
  const [refunds, setRefunds] = useState<ListingFeeRefund[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    apiClientFetch<ListingFeeRefund[]>(`/api/finance/listing-fees/payments/${paymentId}/refunds`)
      .then(setRefunds)
      .catch(() => setLoadFailed(true));
  }, [paymentId]);

  async function openCreditNote(refund: ListingFeeRefund) {
    try {
      const res = await fetch(`${API_URL}/api/finance/listing-fees/refunds/${refund.id}/credit-note`, { credentials: "include" });
      if (!res.ok) throw new Error();
      const url = URL.createObjectURL(await res.blob());
      window.open(url, "_blank", "noopener");
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch {
      showToast("Could not open the credit note");
    }
  }

  if (loadFailed) return <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">Could not load the refunds for this payment.</p>;
  if (refunds === null) return <p className="mt-2 text-xs text-slate-400">Loading refunds...</p>;
  if (refunds.length === 0) return <p className="mt-2 text-xs text-slate-400">No refunds on this payment.</p>;
  return (
    <div className="mt-2 space-y-1 border-t border-slate-200 pt-2 dark:border-white/10">
      {refunds.map((r) => (
        <div key={r.id} className="flex flex-wrap items-center justify-between gap-2 text-xs">
          <span className="text-slate-600 dark:text-slate-300">
            {formatMoney(r.amount, r.currency)} · {formatDate(r.createdAt)}
            {r.reason ? ` · ${r.reason}` : ""}
            {r.status === "FAILED" && r.failureMessage ? ` · failed: ${r.failureMessage}` : ""}
          </span>
          <span className="flex items-center gap-2">
            {r.creditNoteNumber && (
              <button type="button" onClick={() => openCreditNote(r)} className="font-semibold text-primary-700 hover:underline dark:text-primary-300">
                {r.creditNoteNumber}
              </button>
            )}
            {!r.creditNoteNumber && (r.status === "REFUNDED" || r.status === "PARTIALLY_REFUNDED") && (
              <button type="button" onClick={() => openCreditNote(r)} className="font-semibold text-primary-700 hover:underline dark:text-primary-300">
                Credit note
              </button>
            )}
            <Badge tone={listingFeeRefundStatusTone[r.status] ?? "neutral"}>{r.status.replace(/_/g, " ").toLowerCase()}</Badge>
          </span>
        </div>
      ))}
    </div>
  );
}
/** What's still refundable -- the fee less refunds already confirmed (the
 *  backend also subtracts any still in flight and has the final say). */
function refundable(payment: ListingFeePayment) {
  return Math.max(0, Math.round((payment.amount - (payment.refundedAmount || 0)) * 100) / 100);
}

/** Listing Fees are the one payment Zoiko itself collects (through
 *  Stripe) -- so they're the one payment support can refund from here.
 *  Super admin only; the refund itself goes back through Stripe. */
export function ListingFeePaymentsAdmin({ showToast }: { showToast: (message: string) => void }) {
  const [payments, setPayments] = useState<ListingFeePayment[]>([]);
  const [listingFilter, setListingFilter] = useState("");
  const [refunding, setRefunding] = useState<ListingFeePayment | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async (listingId?: string) => {
    try {
      const query = listingId ? `?listing_id=${encodeURIComponent(listingId)}` : "";
      setPayments(await apiClientFetch<ListingFeePayment[]>(`/api/finance/listing-fees/payments${query}`));
    } catch {
      showToast("Failed to load Listing Fee payments");
    }
  }, [showToast]);

  useEffect(() => {
    load();
  }, [load]);

  function open(payment: ListingFeePayment) {
    setRefunding(payment);
    setAmount(String(refundable(payment)));
    setReason("");
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!refunding) return;
    const value = Number(amount);
    const max = refundable(refunding);
    if (!Number.isFinite(value) || value <= 0 || value > max) {
      showToast(`Enter an amount between 0 and ${formatMoney(max, refunding.currency)}`);
      return;
    }
    if (!reason.trim()) {
      showToast("A reason is required");
      return;
    }
    setSubmitting(true);
    try {
      await apiClientFetch(`/api/finance/listing-fees/payments/${refunding.id}/refunds`, {
        method: "POST",
        body: JSON.stringify({
          amount: value, reason: reason.trim(),
          // One key per submission -- a double-click can't refund twice.
          idempotencyKey: `admin-refund-${refunding.id}-${crypto.randomUUID()}`,
        }),
      });
      showToast("Refund requested -- it's sent back through Stripe");
      setRefunding(null);
      load(listingFilter.trim() || undefined);
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : "Failed to request the refund");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex items-center gap-2">
        <ReceiptText className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
        <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Listing Fee payments & refunds</h2>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        The fee hosts pay Zoiko to publish a listing -- the only payment Zoiko collects, and so the only one refunded from here.
      </p>
      <form
        className="mt-3 flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          load(listingFilter.trim() || undefined);
        }}
      >
        <input
          value={listingFilter} onChange={(e) => setListingFilter(e.target.value)} placeholder="Listing ID (optional)"
          className="w-56 rounded-xl bg-slate-50 px-3 py-2 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
        />
        <Button type="submit" size="sm" variant="outline">Filter</Button>
      </form>
      <div className="mt-4 space-y-2">
        {payments.map((p) => (
          <div key={p.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
            <div>
              <div className="flex items-center gap-2">
                <p className="text-sm font-semibold text-primary-900 dark:text-white">
                  #{p.id} · {formatMoney(p.amount, p.currency)} · {p.listingId}
                </p>
                <Badge tone={listingFeePaymentStatusTone[p.status] ?? "neutral"}>{p.status.replace(/_/g, " ").toLowerCase()}</Badge>
                {p.disputeStatus && (
                  <Badge tone={p.disputeStatus === "WON" ? "neutral" : "danger"}>chargeback {p.disputeStatus.toLowerCase()}</Badge>
                )}
                {p.refundStatus && (
                  <Badge tone={listingFeeRefundStatusTone[p.refundStatus as keyof typeof listingFeeRefundStatusTone] ?? "neutral"}>
                    refund {p.refundStatus.replace(/_/g, " ").toLowerCase()}
                  </Badge>
                )}
              </div>
              <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                {p.paidAt ? `Paid ${formatDate(p.paidAt)}` : `Started ${formatDate(p.createdAt)}`}
              </p>
            </div>
            <div className="flex items-center gap-2">
              {p.status === "SUCCEEDED" && (
                <Button size="sm" variant="ghost" onClick={() => setExpanded(expanded === p.id ? null : p.id)}>
                  {expanded === p.id ? "Hide refunds" : "Refunds"}
                </Button>
              )}
              {p.refundEligible && p.disputeStatus !== "OPEN" && p.disputeStatus !== "LOST" && (
                <Button size="sm" variant="outline" onClick={() => open(p)}>Refund</Button>
              )}
            </div>
            {expanded === p.id && (
              <div className="w-full">
                <RefundHistory paymentId={p.id} showToast={showToast} />
              </div>
            )}
          </div>
        ))}
        {payments.length === 0 && <p className="text-sm text-slate-400">No Listing Fee payments.</p>}
      </div>

      <Modal open={Boolean(refunding)} onClose={() => setRefunding(null)} title="Refund Listing Fee">
        {refunding && (
          <form onSubmit={submit} className="space-y-3.5">
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Payment #{refunding.id} for listing {refunding.listingId} -- {formatMoney(refunding.amount, refunding.currency)}
              {refunding.refundedAmount > 0 && `, ${formatMoney(refunding.refundedAmount, refunding.currency)} already refunded`}.
            </p>
            <label htmlFor="lf-refund-amount" className="block text-xs font-semibold text-slate-600 dark:text-slate-300">
              Amount to refund (up to {formatMoney(refundable(refunding), refunding.currency)})
            </label>
            <input
              id="lf-refund-amount" type="number" step="0.01" min="0.01" max={refundable(refunding)} value={amount}
              onChange={(e) => setAmount(e.target.value)}
              className="w-full rounded-xl bg-slate-50 px-3 py-2 text-sm ring-1 ring-slate-200 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
            <label htmlFor="lf-refund-reason" className="block text-xs font-semibold text-slate-600 dark:text-slate-300">Reason</label>
            <textarea
              id="lf-refund-reason" value={reason} onChange={(e) => setReason(e.target.value)} rows={3} required placeholder="Reason"
              className="w-full resize-none rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setRefunding(null)}>Cancel</Button>
              <Button type="submit" variant="primary" loading={submitting}>Refund</Button>
            </div>
          </form>
        )}
      </Modal>
    </section>
  );
}
