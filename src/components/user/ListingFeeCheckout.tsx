"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, Download, Info, ShieldCheck, XCircle } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Card, Toast, inputClass, useToast } from "@/components/user/ui";
import { ListingFeeListingStatus, ListingFeePayment, ListingFeeQuote } from "@/lib/types";
import { countryOptions } from "@/lib/countries";
import { formatDate, formatMoney } from "@/lib/utils";
import {
  createListingFeeCheckoutSession,
  createListingFeeQuote,
  downloadListingFeeCreditNote,
  downloadListingFeeReceipt,
  errorMessage,
  getListingFeePayment,
  getListingFeeStatus,
  reportListingFeeCheckoutCancelled,
  resolveListingFeeCheckoutSession,
} from "@/lib/user-api";

/** ZR-LF-001 screens, in order:
 *  gate (A)       -- the publish checklist; the fee is the last step.
 *  review (B)     -- listing, amounts, one-time wording, then Stripe.
 *  finalizing     -- back from Stripe, waiting for its confirmation.
 *  success (D)    -- payment received, amounts, receipt number.
 *  cancelled / failed / refunded / disputed / unavailable -- the exits. */
type View =
  | "loading"
  | "gate"
  | "review"
  | "finalizing"
  | "success"
  | "cancelled"
  | "failed"
  | "refunded"
  | "disputed"
  | "unavailable";

/** How the host came back from Stripe (Stripe's success_url / cancel_url). */
export type StripeReturn = "success" | "cancel";

const FINALIZE_POLL_MS = 3000;
const FINALIZE_MAX_ATTEMPTS = 40; // ~2 minutes

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Moves focus to a screen's heading when the screen changes, so keyboard
 *  and screen-reader users land on the new content (WCAG 2.4.3). */
function ScreenHeading({ children }: { children: React.ReactNode }) {
  const ref = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  return (
    <h3 ref={ref} tabIndex={-1} className="font-heading text-base font-bold text-primary-900 outline-none dark:text-white">
      {children}
    </h3>
  );
}

function ChecklistRow({ label, ok }: { label: string; ok: boolean }) {
  return (
    <li className="flex items-start gap-2">
      {ok ? (
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-700 dark:text-emerald-400" aria-hidden="true" />
      ) : (
        <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-700 dark:text-amber-400" aria-hidden="true" />
      )}
      <span className={ok ? "text-slate-700 dark:text-slate-200" : "text-amber-800 dark:text-amber-300"}>
        {label}
        <span className="sr-only">{ok ? " -- done" : " -- not done yet"}</span>
      </span>
    </li>
  );
}

function AmountRows({
  fee,
  tax,
  total,
  currency,
  taxLabel = "Tax",
}: {
  fee: number | null;
  tax: number | null;
  total: number;
  currency: string;
  taxLabel?: string;
}) {
  return (
    <>
      {fee != null && (
        <div className="flex justify-between gap-4">
          <dt className="text-slate-600 dark:text-slate-400">Listing fee</dt>
          <dd className="font-semibold text-slate-700 dark:text-slate-200">{formatMoney(fee, currency)}</dd>
        </div>
      )}
      {tax != null && (
        <div className="flex justify-between gap-4">
          <dt className="text-slate-600 dark:text-slate-400">{taxLabel}</dt>
          <dd className="font-semibold text-slate-700 dark:text-slate-200">{formatMoney(tax, currency)}</dd>
        </div>
      )}
      <div className="flex justify-between gap-4 border-t border-slate-200 pt-1.5 font-semibold dark:border-white/10">
        <dt className="text-primary-900 dark:text-white">Total</dt>
        <dd className="text-primary-900 dark:text-white">{formatMoney(total, currency)}</dd>
      </div>
    </>
  );
}

function taxLabelFor(quote: ListingFeeQuote) {
  const rate = quote.taxRate != null ? ` (${(quote.taxRate * 100).toFixed(2).replace(/\.00$/, "")}%)` : "";
  return `${quote.taxBehavior === "INCLUSIVE" ? "Includes tax" : "Tax"}${rate}`;
}

export function ListingFeeCheckout({
  listingId,
  returningCheckoutSessionId,
  stripeReturn,
  onDone,
}: {
  listingId: string;
  /** Set once the host is back from Stripe's hosted checkout (see
   *  HostingListingsManager.tsx reading `checkoutSessionId`). */
  returningCheckoutSessionId?: string;
  /** Which Stripe URL brought them back -- a cancel is never "confirming". */
  stripeReturn?: StripeReturn;
  /** "Continue to listing" -- closes the fee flow. */
  onDone?: () => void;
}) {
  const { toast, showToast } = useToast();
  const [view, setView] = useState<View>("loading");
  const [status, setStatus] = useState<ListingFeeListingStatus | null>(null);
  const [payment, setPayment] = useState<ListingFeePayment | null>(null);
  const [quote, setQuote] = useState<ListingFeeQuote | null>(null);
  const [previousTotal, setPreviousTotal] = useState<ListingFeeQuote | null>(null);
  const [unavailableMessage, setUnavailableMessage] = useState("");
  const [failure, setFailure] = useState("");
  const [billingCountry, setBillingCountry] = useState("GB");
  const [agreed, setAgreed] = useState(false);
  const [startingCheckout, setStartingCheckout] = useState(false);
  const [finalizeSlow, setFinalizeSlow] = useState(false);
  const [pollRound, setPollRound] = useState(0);
  const countries = useMemo(() => countryOptions(), []);
  const [downloading, setDownloading] = useState(false);

  const fetchQuote = useCallback(async (): Promise<ListingFeeQuote | null> => {
    try {
      const fresh = await createListingFeeQuote(listingId);
      setUnavailableMessage("");
      setQuote(fresh);
      return fresh;
    } catch (err) {
      // No approved price for this market (fails closed server-side).
      setUnavailableMessage(errorMessage(err, "Listing fee currently unavailable in this market."));
      setQuote(null);
      return null;
    }
  }, [listingId]);

  /** Loads the listing's fee status and picks the screen for it. */
  const showCurrentState = useCallback(async () => {
    const current = await getListingFeeStatus(listingId);
    setStatus(current);
    const latest = current.latestPayment;
    setPayment(latest);
    if (current.feePaid && latest) {
      setView("success");
      return;
    }
    if (latest?.status === "SUCCEEDED" && latest.fullyRefunded) {
      setView("refunded");
      return;
    }
    if (latest?.status === "SUCCEEDED" && latest.disputeStatus && latest.disputeStatus !== "WON") {
      setView("disputed");
      return;
    }
    const q = await fetchQuote();
    setView(q ? "gate" : "unavailable");
  }, [listingId, fetchQuote]);

  useEffect(() => {
    let cancelled = false;
    async function start() {
      try {
        if (!returningCheckoutSessionId) {
          await showCurrentState();
          return;
        }
        const returned = await resolveListingFeeCheckoutSession(returningCheckoutSessionId);
        if (cancelled) return;
        setPayment(returned);
        getListingFeeStatus(listingId).then((s) => !cancelled && setStatus(s)).catch(() => undefined);
        if (returned.status === "SUCCEEDED") {
          await showCurrentState();
        } else if (returned.status === "FAILED") {
          setFailure(returned.failureMessage || "Your payment did not go through.");
          await fetchQuote();
          setView("failed");
        } else if (stripeReturn === "cancel") {
          // Nothing was charged -- Stripe only sends the host to cancel_url
          // when they left without paying.
          reportListingFeeCheckoutCancelled(returningCheckoutSessionId).catch(() => undefined);
          await fetchQuote();
          setView("cancelled");
        } else {
          setView("finalizing");
        }
      } catch (err) {
        if (cancelled) return;
        showToast(errorMessage(err, "Could not load the Listing Fee for this listing."), "error");
        setView("unavailable");
      }
    }
    start();
    return () => {
      cancelled = true;
    };
  }, [listingId, returningCheckoutSessionId, stripeReturn, showCurrentState, fetchQuote, showToast]);

  // Back from Stripe's success_url, waiting for its webhook to land.
  useEffect(() => {
    if (view !== "finalizing" || !payment) return;
    let cancelled = false;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      attempt += 1;
      try {
        const latest = await getListingFeePayment(payment!.id);
        if (cancelled) return;
        if (latest.status === "SUCCEEDED") {
          await showCurrentState();
          return;
        }
        if (latest.status === "FAILED") {
          setFailure(latest.failureMessage || "Your payment did not go through.");
          await fetchQuote();
          setView("failed");
          return;
        }
      } catch {
        // Transient read failure -- keep polling within the window.
      }
      if (cancelled) return;
      if (attempt >= FINALIZE_MAX_ATTEMPTS) {
        setFinalizeSlow(true);
        return;
      }
      timer = setTimeout(poll, FINALIZE_POLL_MS);
    }
    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [view, payment, pollRound, showCurrentState, fetchQuote]);

  /** Gets a fresh quote. Returns it when the total is unchanged (safe to
   *  continue), or null after flagging a changed total for re-confirmation
   *  (ZR-PAY-CFG-001 8.2 / PAY-CFG-10). */
  async function requote(current: ListingFeeQuote): Promise<ListingFeeQuote | null> {
    const fresh = await fetchQuote();
    if (!fresh) {
      setView("unavailable");
      return null;
    }
    const minor = (q: ListingFeeQuote) => q.totalAmountMinor ?? Math.round(q.totalAmount * 100);
    if (fresh.currency !== current.currency || minor(fresh) !== minor(current)) {
      setPreviousTotal(current);
      setAgreed(false);
      return null;
    }
    return fresh;
  }

  async function goToReview() {
    setFailure("");
    if (!quote || Date.now() >= new Date(quote.expiresAt).getTime()) {
      const fresh = await fetchQuote();
      if (!fresh) {
        setView("unavailable");
        return;
      }
    }
    setView("review");
  }

  async function payAgain() {
    const q = quote ?? (await fetchQuote());
    setView(q ? "gate" : "unavailable");
  }

  async function handleStartCheckout() {
    if (!quote) return;
    setStartingCheckout(true);
    setFailure("");
    setPreviousTotal(null);
    try {
      let payable: ListingFeeQuote | null = quote;
      if (Date.now() >= new Date(quote.expiresAt).getTime()) {
        payable = await requote(quote);
        if (!payable) return;
      }
      const open = (q: ListingFeeQuote) =>
        createListingFeeCheckoutSession({ quoteId: q.id, idempotencyKey: crypto.randomUUID(), billingCountry });
      let created;
      try {
        created = await open(payable);
      } catch (err) {
        // Expired between render and click -- the server is the authority.
        if (!errorMessage(err, "").toLowerCase().includes("expired")) throw err;
        payable = await requote(payable);
        if (!payable) return;
        created = await open(payable);
      }
      if (created.status === "SUCCEEDED") {
        // No Stripe keys server-side -- completed synchronously.
        await showCurrentState();
        return;
      }
      if (created.checkoutUrl) {
        // A real navigation to Stripe's hosted page; this app never renders
        // a card form (ZR-PAY-002 Section 8.2's PCI boundary).
        window.location.href = created.checkoutUrl;
        return;
      }
      setFailure("Could not start the Listing Fee checkout.");
    } catch (err) {
      setFailure(errorMessage(err, "Could not start the Listing Fee checkout."));
    } finally {
      setStartingCheckout(false);
    }
  }

  function checkAgain() {
    setFinalizeSlow(false);
    setPollRound((n) => n + 1);
  }

  async function handleDownloadReceipt() {
    if (!payment) return;
    setDownloading(true);
    try {
      const blob = await downloadListingFeeReceipt(payment.id);
      saveBlob(blob, `${payment.receiptNumber ?? `listing-fee-receipt-${payment.id}`}.pdf`);
    } catch (err) {
      showToast(errorMessage(err, "Could not download the receipt."), "error");
    } finally {
      setDownloading(false);
    }
  }

  async function handleDownloadCreditNote() {
    if (!payment?.refundId) return;
    setDownloading(true);
    try {
      const blob = await downloadListingFeeCreditNote(payment.refundId);
      saveBlob(blob, `${payment.creditNoteNumber ?? `listing-fee-credit-note-${payment.refundId}`}.pdf`);
    } catch (err) {
      showToast(errorMessage(err, "Could not download the credit note."), "error");
    } finally {
      setDownloading(false);
    }
  }

  const listingName = status?.listingName ?? "your listing";

  if (view === "loading") {
    return (
      <div role="status" aria-live="polite">
        <Loader label="Loading Listing Fee" />
      </div>
    );
  }

  if (view === "finalizing") {
    return (
      <Card>
        <div role="status" aria-live="polite">
          {finalizeSlow ? (
            <div className="space-y-3 py-6 text-center">
              <p className="text-sm text-slate-700 dark:text-slate-200">
                Your payment is taking longer than usual to confirm. You won&apos;t be charged twice -- check back in a
                moment.
              </p>
              <Button size="sm" variant="outline" onClick={checkAgain}>
                Check again
              </Button>
            </div>
          ) : (
            <Loader label="Finalizing payment" />
          )}
        </div>
      </Card>
    );
  }

  if (view === "success" && payment) {
    return (
      <Card className="!bg-emerald-50 !ring-emerald-200 dark:!bg-emerald-500/10 dark:!ring-emerald-500/20">
        <div className="flex items-center gap-2 text-emerald-800 dark:text-emerald-300">
          <CheckCircle2 className="h-5 w-5 shrink-0" aria-hidden="true" />
          <ScreenHeading>Payment received for {listingName}</ScreenHeading>
        </div>
        <dl className="mt-4 space-y-1.5 text-sm">
          <AmountRows fee={payment.feeAmount} tax={payment.taxAmount} total={payment.amount} currency={payment.currency} />
          <div className="flex justify-between gap-4 pt-1.5">
            <dt className="text-slate-600 dark:text-slate-400">Receipt number</dt>
            <dd className="font-mono font-semibold text-slate-700 dark:text-slate-200">
              {payment.receiptNumber ?? "Being issued"}
            </dd>
          </div>
          {payment.paidAt && (
            <div className="flex justify-between gap-4">
              <dt className="text-slate-600 dark:text-slate-400">Paid on</dt>
              <dd className="text-slate-700 dark:text-slate-200">{formatDate(payment.paidAt)}</dd>
            </div>
          )}
        </dl>
        {payment.refundedAmount > 0 && (
          <p className="mt-3 text-sm text-slate-700 dark:text-slate-200">
            {formatMoney(payment.refundedAmount, payment.currency)} of this fee has been refunded.
          </p>
        )}
        <p className="mt-4 text-sm font-medium text-emerald-900 dark:text-emerald-200">
          There is no recurring charge for this listing.
        </p>
        <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
          Your listing is now live -- renters can find it in Find a Room.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button size="sm" variant="outline" loading={downloading} onClick={handleDownloadReceipt}>
            <Download className="h-3.5 w-3.5" aria-hidden="true" /> View receipt
          </Button>
          {onDone && (
            <Button size="sm" onClick={onDone}>
              Continue to listing
            </Button>
          )}
        </div>
        <Toast toast={toast} />
      </Card>
    );
  }

  if (view === "refunded" && payment) {
    return (
      <Card>
        <div className="flex items-center gap-2">
          <Info className="h-5 w-5 shrink-0 text-primary-700 dark:text-primary-300" aria-hidden="true" />
          <ScreenHeading>Listing fee refunded</ScreenHeading>
        </div>
        <p className="mt-2 text-sm text-slate-700 dark:text-slate-200">
          The {formatMoney(payment.refundedAmount || payment.amount, payment.currency)} Listing Fee for {listingName} was
          refunded{payment.refundedAt ? ` on ${formatDate(payment.refundedAt)}` : ""}.
        </p>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
          Because the fee was returned, this listing can&apos;t be published until the fee is paid again. You only pay
          again if you choose to.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          {payment.creditNoteNumber && (
            <Button size="sm" variant="outline" loading={downloading} onClick={handleDownloadCreditNote}>
              <Download className="h-3.5 w-3.5" aria-hidden="true" /> Credit note {payment.creditNoteNumber}
            </Button>
          )}
          <Button size="sm" variant="outline" onClick={payAgain}>
            Pay listing fee again
          </Button>
        </div>
        <Toast toast={toast} />
      </Card>
    );
  }

  if (view === "disputed" && payment) {
    const lost = payment.disputeStatus === "LOST";
    return (
      <Card>
        <ScreenHeading>{lost ? "Listing fee payment reversed" : "Listing fee payment under dispute"}</ScreenHeading>
        <p className="mt-2 text-sm text-slate-700 dark:text-slate-200">
          {lost
            ? "Your bank reversed the Listing Fee payment for this listing, so it can't be published until the fee is paid again."
            : "Your bank has opened a dispute on the Listing Fee payment for this listing. The listing can't be published while it is open. Contact Zoiko support if you didn't mean to dispute it."}
        </p>
        {lost && (
          <div className="mt-4">
            <Button size="sm" variant="outline" onClick={payAgain}>
              Pay listing fee again
            </Button>
          </div>
        )}
      </Card>
    );
  }

  if (view === "unavailable" || !quote) {
    return (
      <Card>
        <p className="text-sm text-slate-700 dark:text-slate-200">
          {unavailableMessage || "Listing fee currently unavailable in this market."}
        </p>
        <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
          Your listing can&apos;t be published until Zoiko Rooms sets a Listing Fee for this market.
        </p>
        <Toast toast={toast} />
      </Card>
    );
  }

  if (view === "cancelled" || view === "failed") {
    const cancelled = view === "cancelled";
    return (
      <Card>
        <div role="alert">
          <div className="flex items-center gap-2">
            <XCircle className="h-5 w-5 shrink-0 text-amber-700 dark:text-amber-400" aria-hidden="true" />
            <ScreenHeading>Payment not completed</ScreenHeading>
          </div>
          <p className="mt-2 text-sm text-slate-700 dark:text-slate-200">
            {cancelled ? "Your listing has not been charged." : `${failure.replace(/\.$/, "")}. Your listing has not been charged.`}
          </p>
        </div>
        <div className="mt-4">
          <Button size="sm" onClick={goToReview}>
            Try again
          </Button>
        </div>
        <Toast toast={toast} />
      </Card>
    );
  }

  const blockers = status?.checkoutBlockers ?? [];

  if (view === "gate") {
    return (
      <Card>
        <ScreenHeading>Publish {listingName}</ScreenHeading>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Complete every step below. The listing fee is paid last, once the listing is ready to publish.
        </p>
        <ul className="mt-4 space-y-2 rounded-xl bg-slate-50 p-4 text-sm dark:bg-slate-800" aria-label="Publishing checklist">
          {blockers.length === 0 ? (
            <ChecklistRow label="Identity, property, authority and compliance checks" ok />
          ) : (
            blockers.map((reason) => <ChecklistRow key={reason} label={reason} ok={false} />)
          )}
          <ChecklistRow label="Listing fee" ok={false} />
        </ul>

        <div className="mt-4 rounded-xl border border-slate-200 p-4 dark:border-white/10">
          <p className="text-sm font-semibold text-primary-900 dark:text-white">One-time fee for this listing</p>
          <p className="mt-1 text-lg font-bold text-primary-900 dark:text-white">
            {formatMoney(quote.totalAmount, quote.currency)}
            {quote.taxBehavior === "INCLUSIVE" && <span className="ml-1 text-xs font-normal text-slate-600 dark:text-slate-400">incl. tax</span>}
          </p>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">No subscription. No monthly or annual charge.</p>
          <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
            Paid to Zoiko Rooms for publishing the listing -- separate from rent, deposits and other rental payments.
          </p>
        </div>

        <div className="mt-4">
          <Button
            fullWidth
            disabled={blockers.length > 0}
            aria-describedby={blockers.length > 0 ? "listing-fee-gate-hint" : undefined}
            onClick={goToReview}
          >
            Review and pay listing fee
          </Button>
          {blockers.length > 0 && (
            <p id="listing-fee-gate-hint" className="mt-2 text-xs text-slate-600 dark:text-slate-400">
              Finish the steps above first -- the fee can only be paid once the listing is otherwise ready to publish.
            </p>
          )}
        </div>
        <Toast toast={toast} />
      </Card>
    );
  }

  // view === "review" (Screen B)
  const canPay = agreed && billingCountry.length === 2 && blockers.length === 0;
  return (
    <Card>
      <ScreenHeading>Review listing fee</ScreenHeading>

      <dl className="mt-4 space-y-1.5 text-sm">
        <div className="flex justify-between gap-4">
          <dt className="text-slate-600 dark:text-slate-400">Listing</dt>
          <dd className="text-right font-semibold text-slate-700 dark:text-slate-200">{listingName}</dd>
        </div>
        {status?.listingAddress && (
          <div className="flex justify-between gap-4">
            <dt className="text-slate-600 dark:text-slate-400">Address</dt>
            <dd className="text-right text-slate-700 dark:text-slate-200">{status.listingAddress}</dd>
          </div>
        )}
        <div className="flex justify-between gap-4 border-b border-slate-200 pb-1.5 dark:border-white/10">
          <dt className="text-slate-600 dark:text-slate-400">Listing ID</dt>
          <dd className="font-mono text-slate-700 dark:text-slate-200">{listingId}</dd>
        </div>
        <AmountRows fee={quote.amount} tax={quote.taxAmount} total={quote.totalAmount} currency={quote.currency} taxLabel={taxLabelFor(quote)} />
      </dl>

      <p className="mt-4 rounded-xl bg-slate-50 px-4 py-2.5 text-sm text-slate-700 dark:bg-slate-800 dark:text-slate-200">
        This is a one-time payment for this listing. There is no subscription and no automatic renewal.
      </p>
      {quote.billingEntityName && <p className="mt-3 text-xs text-slate-600 dark:text-slate-400">Billed by {quote.billingEntityName}.</p>}
      {quote.disclosureText && <p className="mt-2 text-xs text-slate-600 dark:text-slate-400">{quote.disclosureText}</p>}
      <p className="mt-2 text-xs text-slate-600 dark:text-slate-400">
        This price is held until {new Date(quote.expiresAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}.
      </p>

      {previousTotal && (
        <p role="alert" className="mt-4 rounded-xl bg-amber-50 px-4 py-2.5 text-sm text-amber-900 dark:bg-amber-500/10 dark:text-amber-300">
          The Listing Fee changed from {formatMoney(previousTotal.totalAmount, previousTotal.currency)} to{" "}
          {formatMoney(quote.totalAmount, quote.currency)}. Please review the new amount and confirm again to continue.
        </p>
      )}
      {failure && (
        <p role="alert" className="mt-4 rounded-xl bg-rose-50 px-4 py-2.5 text-sm text-rose-800 dark:bg-rose-500/10 dark:text-rose-300">
          {failure}
        </p>
      )}

      <div className="mt-4 space-y-4">
        <div>
          <label htmlFor="listing-fee-billing-country" className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-400">
            Billing country/region
          </label>
          <select
            id="listing-fee-billing-country"
            value={billingCountry}
            onChange={(e) => setBillingCountry(e.target.value)}
            required
            autoComplete="country"
            className={inputClass}
          >
            {countries.map(([code, name]) => (
              <option key={code} value={code}>
                {name}
              </option>
            ))}
          </select>
        </div>
        <label className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-300">
          <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} className="mt-0.5 h-4 w-4" />
          I agree to the applicable Listing Fee Terms.
        </label>
        <Button fullWidth disabled={!canPay} loading={startingCheckout} onClick={handleStartCheckout}>
          Continue securely to Stripe
        </Button>
        <p className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-400">
          <ShieldCheck className="h-3.5 w-3.5 shrink-0" aria-hidden="true" /> You&apos;ll pay on Stripe&apos;s secure page --
          Zoiko Rooms never sees or stores your card number.
        </p>
        <Button fullWidth variant="ghost" onClick={() => setView("gate")}>
          Back
        </Button>
      </div>
      <Toast toast={toast} />
    </Card>
  );
}
