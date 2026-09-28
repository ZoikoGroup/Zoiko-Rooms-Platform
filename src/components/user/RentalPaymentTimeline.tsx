"use client";

import { useEffect, useState } from "react";
import { History } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { RentalTransactionTimelineEntry } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";
import { getRentalPaymentRecordTimeline } from "@/lib/user-api";

const PAGE_SIZE = 10;

// Plain-words names for the events a payment record collects.
const EVENT_LABELS: Record<string, string> = {
  "rental_payment.marked_paid": "Renter recorded the payment",
  "rental_payment.receipt_confirmed": "Payment confirmed",
  "rental_payment.receipt_confirmed_by_admin": "Confirmed by Zoiko support",
  "rental_payment.receipt_recorded_by_recipient": "Host marked it as received",
  "rental_payment.disputed": "A problem was reported",
  "rental_payment.dispute_updated": "The problem report was updated",
  "rental_payment.dispute_resolved": "Support resolved the problem",
  "rental_payment.corrected": "Payment details corrected",
  "rental_payment.evidence_uploaded": "Proof of payment added",
  "rental_payment.evidence_accessed": "Proof of payment viewed",
  "rental_payment.evidence_hold_placed": "Proof placed on legal hold",
  "rental_payment.evidence_hold_released": "Legal hold released",
  "rental_payment.reversed": "Payment reversed",
  "rental_payment.chargeback_opened": "Card dispute opened",
  "rental_payment.chargeback_closed": "Card dispute closed",
  "rental_payment.card_refund_recorded": "Card payment refunded",
  "rental_payment.card_refund_failed": "Card refund failed",
  "rental_payment.card_refunded": "Card payment refunded",
};

function label(entry: RentalTransactionTimelineEntry): string {
  if (EVENT_LABELS[entry.eventType]) {
    if (entry.eventType === "rental_payment.receipt_confirmed" && entry.detail?.recordedByRecipient) {
      return "Host marked it as received";
    }
    return EVENT_LABELS[entry.eventType];
  }
  const tail = entry.eventType.split(".").pop() ?? entry.eventType;
  return tail.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

/** The history of one payment record -- recorded, corrected, disputed,
 *  confirmed -- shown the same way to the renter and the host. */
export function RentalPaymentTimeline({ recordId }: { recordId: number }) {
  const [items, setItems] = useState<RentalTransactionTimelineEntry[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setFailed(false);
    getRentalPaymentRecordTimeline(recordId, { limit: PAGE_SIZE, offset: 0 })
      .then((page) => {
        if (cancelled) return;
        setItems(page.items);
        setHasMore(page.hasMore);
      })
      .catch(() => !cancelled && setFailed(true))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [recordId]);

  function loadMore() {
    getRentalPaymentRecordTimeline(recordId, { limit: PAGE_SIZE, offset: items.length })
      .then((page) => {
        setItems((prev) => [...prev, ...page.items]);
        setHasMore(page.hasMore);
      })
      .catch(() => setFailed(true));
  }

  return (
    <div>
      <span className="mb-1 flex items-center gap-1 text-xs text-slate-400">
        <History className="h-3.5 w-3.5" aria-hidden="true" /> History
      </span>
      {loading ? (
        <p className="text-xs text-slate-400">Loading...</p>
      ) : failed ? (
        <p className="text-xs text-slate-400">History isn&apos;t available right now.</p>
      ) : items.length === 0 ? (
        <p className="text-xs text-slate-400">Nothing recorded yet.</p>
      ) : (
        <ol className="space-y-1.5 border-l border-slate-200 pl-3 dark:border-white/10">
          {items.map((entry, i) => (
            <li key={`${entry.timestamp}-${i}`} className="text-xs">
              <span className="font-semibold text-slate-700 dark:text-slate-200">{label(entry)}</span>
              <span className="block text-slate-400">{formatDateTime(entry.timestamp)}</span>
            </li>
          ))}
        </ol>
      )}
      {hasMore && (
        <Button size="sm" variant="ghost" onClick={loadMore}>Show more</Button>
      )}
    </div>
  );
}
