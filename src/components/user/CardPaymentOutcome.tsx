"use client";

import { AlertTriangle, RotateCcw } from "lucide-react";
import type { RentalPaymentRecord } from "@/lib/types";
import { formatMoney } from "@/lib/utils";

type Viewer = "tenant" | "host";

/** Stripe's own dispute statuses, in plain words. */
const DISPUTE_STATUS_TEXT: Record<string, string> = {
  warning_needs_response: "Early warning from the bank -- a response is needed",
  warning_under_review: "Early warning under review by the bank",
  warning_closed: "Early warning closed",
  needs_response: "A response is needed",
  under_review: "Under review by the bank",
  won: "Closed -- the payment stands",
  lost: "Closed -- the money was returned to the tenant",
  charge_refunded: "Closed -- the payment was refunded",
  prevented: "Closed -- prevented",
};

/** Stripe treats these as final -- mirrors the backend's
 *  stripe_client.FINAL_DISPUTE_STATUSES. */
const CLOSED_DISPUTE_STATUSES = new Set(["won", "lost", "warning_closed", "charge_refunded", "prevented"]);

function disputeIsClosed(status: string): boolean {
  return CLOSED_DISPUTE_STATUSES.has(status);
}

function disputeStatusText(status: string): string {
  return DISPUTE_STATUS_TEXT[status] ?? status.replace(/_/g, " ");
}

/** A short inline line for a payment-history row: refunded and/or disputed
 *  (a payment can be both -- e.g. partly refunded, then disputed). */
export function CardPaymentOutcomeTag({ record }: { record: RentalPaymentRecord }) {
  const dispute = record.providerDisputeId
    ? disputeIsClosed(record.providerDisputeStatus) ? "Card dispute closed" : "Card dispute open"
    : null;
  const refunded = record.refundedAmount ? `${formatMoney(record.refundedAmount, record.declaredCurrency)} refunded` : null;
  if (!dispute && !refunded) return null;
  return (
    <>
      {refunded && <span className="mt-0.5 block text-xs font-normal text-slate-500 dark:text-slate-400">{refunded}</span>}
      {dispute && <span className="mt-0.5 block text-xs font-normal text-amber-700 dark:text-amber-300">{dispute}</span>}
    </>
  );
}

/** The detail-view explanation of a card refund or chargeback. Card rent
 *  goes straight to the host's own Stripe account, so a refund comes back
 *  from there and a chargeback is the host's to answer in their own Stripe
 *  Dashboard -- Zoiko Rooms is never in the middle of the money. */
export function CardPaymentOutcome({ record, viewer }: { record: RentalPaymentRecord; viewer: Viewer }) {
  const refunded = record.refundedAmount ? formatMoney(record.refundedAmount, record.declaredCurrency) : null;
  if (!refunded && !record.providerDisputeId) return null;

  return (
    <div className="space-y-2">
      {refunded && (
        <div className="flex items-start gap-2 rounded-xl bg-slate-50 px-3 py-2.5 text-xs text-slate-600 dark:bg-white/5 dark:text-slate-300">
          <RotateCcw className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <p>
            {viewer === "tenant"
              ? `${refunded} was refunded to the card you paid with. Card refunds usually take 5–10 business days to appear.`
              : `${refunded} was refunded to the tenant's card from your Stripe account.`}
          </p>
        </div>
      )}
      {record.providerDisputeId && (
        <div className="flex items-start gap-2 rounded-xl bg-amber-50 px-3 py-2.5 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-semibold">Card dispute: {disputeStatusText(record.providerDisputeStatus)}</p>
            <p className="mt-0.5">
              {disputeIsClosed(record.providerDisputeStatus)
                ? record.providerDisputeStatus === "lost"
                  ? viewer === "tenant"
                    ? "Your bank returned this payment to you, so this amount is owed again."
                    : "The tenant's bank returned this payment to them, so this amount is owed again."
                  : "This dispute is closed and the bank's decision is final."
                : viewer === "tenant"
                  ? "Your bank is handling a dispute on this card payment. Its decision is final and is shown here once made."
                  : "The tenant's bank has disputed this card payment. Respond with evidence in your Stripe Dashboard -- the outcome is shown here once the bank decides."}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
