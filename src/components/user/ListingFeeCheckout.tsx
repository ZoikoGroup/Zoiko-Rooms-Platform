"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, Download, ShieldCheck, XCircle } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Card, Field, Toast, inputClass, useToast } from "@/components/user/ui";
import { ListingFeeQuote, PublishEligibility } from "@/lib/types";
import { formatMoney } from "@/lib/utils";
import {
  createListingFeeCheckoutSession,
  createListingFeeQuote,
  downloadListingFeeReceipt,
  errorMessage,
  getHostedListingPublishEligibility,
  getListingFeePayment,
  resolveListingFeeCheckoutSession,
} from "@/lib/user-api";

/** ZR-PAY-002 Section 8.3: the checklist a Listing Fee payment is ONE line
 *  of, never the whole of -- paying it must never be shown as if it alone
 *  satisfies identity/property/authority/compliance. Every other reason
 *  string this listing's own publish-eligibility check produces is shown
 *  alongside it, verbatim from the backend -- never re-derived here. */
function EligibilityChecklist({ eligibility }: { eligibility: PublishEligibility }) {
  const feePaid = !eligibility.reasons.some((r) => r.toLowerCase().includes("listing fee"));
  const otherReasons = eligibility.reasons.filter((r) => !r.toLowerCase().includes("listing fee"));
  return (
    <div className="space-y-2 rounded-xl bg-slate-50 p-4 text-sm dark:bg-slate-800">
      <ChecklistRow label="Listing fee" ok={feePaid} />
      {otherReasons.length === 0 ? (
        <ChecklistRow label="Every other publication requirement" ok />
      ) : (
        otherReasons.map((reason) => <ChecklistRow key={reason} label={reason} ok={false} />)
      )}
      <div className="mt-2 border-t border-slate-200 pt-2 dark:border-white/10">
        <ChecklistRow label="Eligible for publication" ok={eligibility.eligible} strong />
      </div>
    </div>
  );
}

function ChecklistRow({ label, ok, strong }: { label: string; ok: boolean; strong?: boolean }) {
  return (
    <div className={`flex items-center gap-2 ${strong ? "font-semibold" : ""}`}>
      {ok ? (
        <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" aria-hidden="true" />
      ) : (
        <XCircle className="h-4 w-4 shrink-0 text-amber-600" aria-hidden="true" />
      )}
      <span className={ok ? "text-slate-700 dark:text-slate-200" : "text-amber-700 dark:text-amber-300"}>{label}</span>
      <span className="sr-only">{ok ? "complete" : "not yet complete"}</span>
    </div>
  );
}

function SuccessScreen({ listingId, paymentId }: { listingId: string; paymentId: number }) {
  const { toast, showToast } = useToast();
  const [eligibility, setEligibility] = useState<PublishEligibility | null>(null);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    getHostedListingPublishEligibility(listingId).then(setEligibility).catch(() => setEligibility(null));
  }, [listingId]);

  async function handleDownloadReceipt() {
    setDownloading(true);
    try {
      const blob = await downloadListingFeeReceipt(paymentId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `listing-fee-receipt-${paymentId}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      showToast(errorMessage(err, "Could not download the receipt."), "error");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <Card className="!bg-emerald-50 !ring-emerald-200 dark:!bg-emerald-500/10 dark:!ring-emerald-500/20">
      <div className="flex items-center gap-2 text-emerald-800 dark:text-emerald-300">
        <CheckCircle2 className="h-5 w-5" aria-hidden="true" />
        <p className="font-heading text-base font-bold">Listing fee paid</p>
      </div>
      <p className="mt-2 text-sm text-emerald-800 dark:text-emerald-300">
        Your listing can proceed once every other publication requirement below is complete.
      </p>
      {eligibility ? (
        <div className="mt-4">
          <EligibilityChecklist eligibility={eligibility} />
        </div>
      ) : (
        <Loader label="Checking publication eligibility" />
      )}
      <div className="mt-4">
        <Button size="sm" variant="outline" loading={downloading} onClick={handleDownloadReceipt}>
          <Download className="h-3.5 w-3.5" aria-hidden="true" /> View receipt
        </Button>
      </div>
      <Toast toast={toast} />
    </Card>
  );
}

/** Shown once the customer is back from Stripe's own hosted page but the
 *  webhook (the actual source of truth -- see crud/listing_fee.py's
 *  _complete_payment_success) hasn't landed yet. Bounded to ~2 minutes:
 *  real webhook delivery is near-instant in production, so anything past
 *  that is worth surfacing rather than polling forever. */
function ConfirmingPayment({
  paymentId,
  onSucceeded,
  onFailed,
}: {
  paymentId: number;
  onSucceeded: () => void;
  onFailed: (message: string) => void;
}) {
  const [message, setMessage] = useState("Confirming your payment with Stripe -- this only takes a few seconds...");

  useEffect(() => {
    let cancelled = false;
    let attempt = 0;

    async function poll() {
      if (cancelled) return;
      attempt += 1;
      try {
        const payment = await getListingFeePayment(paymentId);
        if (payment.status === "SUCCEEDED") {
          onSucceeded();
          return;
        }
        if (payment.status === "FAILED") {
          onFailed(payment.failureMessage || "Your payment did not go through.");
          return;
        }
      } catch {
        // Transient read failure -- keep polling within the window.
      }
      if (cancelled) return;
      if (attempt >= 24) {
        setMessage("Your payment is taking longer than usual to confirm. Refresh this page in a moment to check its status.");
        return;
      }
      setTimeout(poll, 5000);
    }

    poll();
    return () => {
      cancelled = true;
    };
  }, [paymentId, onSucceeded, onFailed]);

  return (
    <Card>
      <Loader label={message} />
    </Card>
  );
}

export function ListingFeeCheckout({
  listingId,
  returningCheckoutSessionId,
}: {
  listingId: string;
  /** Set once the customer is redirected back from Stripe's own hosted
   *  checkout page (see HostingListingsManager.tsx reading the
   *  `checkoutSessionId` query param Stripe substitutes into
   *  success_url/cancel_url). Skips straight to resolving that payment's
   *  status instead of starting a fresh quote/checkout flow. */
  returningCheckoutSessionId?: string;
}) {
  const { toast, showToast } = useToast();
  const [loading, setLoading] = useState(true);
  const [alreadyPaid, setAlreadyPaid] = useState(false);
  const [quote, setQuote] = useState<ListingFeeQuote | null>(null);
  const [paymentId, setPaymentId] = useState<number | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [billingCountry, setBillingCountry] = useState("GB");
  const [agreed, setAgreed] = useState(false);
  const [startingCheckout, setStartingCheckout] = useState(false);
  const [failure, setFailure] = useState("");
  const [unavailableMessage, setUnavailableMessage] = useState("");
  /** Set when a re-quote came back with a different total -- the host must
   *  explicitly re-confirm before paying (ZR-PAY-CFG-001 8.2 / PAY-CFG-10). */
  const [previousTotal, setPreviousTotal] = useState<ListingFeeQuote | null>(null);

  async function fetchQuote(): Promise<ListingFeeQuote | null> {
    try {
      const fresh = await createListingFeeQuote(listingId);
      setUnavailableMessage("");
      return fresh;
    } catch (err) {
      // No approved price for this market (fails closed server-side), or
      // the fee was already paid through a different path.
      setUnavailableMessage(errorMessage(err, "Listing fee currently unavailable in this market."));
      return null;
    }
  }

  async function loadQuoteForRetry() {
    setQuote(await fetchQuote());
  }

  /** Quotes are valid for 30 minutes and expiry is enforced by the server;
   *  this only avoids a round trip that would certainly be rejected. */
  function isExpired(q: ListingFeeQuote) {
    return Date.now() >= new Date(q.expiresAt).getTime();
  }

  /** Gets a fresh quote. Returns it when the total is unchanged (safe to
   *  continue), or null after flagging a changed total for re-confirmation. */
  async function requote(current: ListingFeeQuote): Promise<ListingFeeQuote | null> {
    const fresh = await fetchQuote();
    setQuote(fresh);
    if (!fresh) return null;
    const changed =
      fresh.currency !== current.currency ||
      (fresh.totalAmountMinor ?? Math.round(fresh.totalAmount * 100)) !==
        (current.totalAmountMinor ?? Math.round(current.totalAmount * 100));
    if (changed) {
      setPreviousTotal(current);
      setAgreed(false);
      return null;
    }
    return fresh;
  }

  useEffect(() => {
    if (returningCheckoutSessionId) {
      resolveListingFeeCheckoutSession(returningCheckoutSessionId)
        .then(async (payment) => {
          setPaymentId(payment.id);
          if (payment.status === "SUCCEEDED") {
            setAlreadyPaid(true);
          } else if (payment.status === "FAILED") {
            setFailure(payment.failureMessage || "Your payment did not go through.");
            await loadQuoteForRetry();
          } else {
            setConfirming(true);
          }
        })
        .catch((err) => showToast(errorMessage(err, "Could not confirm your payment."), "error"))
        .finally(() => setLoading(false));
      return;
    }

    getHostedListingPublishEligibility(listingId)
      .then((eligibility) => {
        const feeAlreadyPaid = !eligibility.reasons.some((r) => r.toLowerCase().includes("listing fee"));
        setAlreadyPaid(feeAlreadyPaid);
        if (!feeAlreadyPaid) return fetchQuote().then(setQuote);
      })
      .catch((err) => showToast(errorMessage(err, "Could not load the Listing Fee for this listing."), "error"))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listingId, returningCheckoutSessionId]);

  async function handleStartCheckout() {
    if (!quote) return;
    setStartingCheckout(true);
    setFailure("");
    setPreviousTotal(null);
    try {
      let payable: ListingFeeQuote | null = quote;
      if (isExpired(quote)) {
        payable = await requote(quote);
        if (!payable) return;
      }
      let created;
      try {
        created = await createListingFeeCheckoutSession({
          quoteId: payable.id,
          idempotencyKey: crypto.randomUUID(),
          billingCountry,
        });
      } catch (err) {
        // Expired between render and click -- the server is the authority.
        if (!errorMessage(err, "").toLowerCase().includes("expired")) throw err;
        payable = await requote(payable);
        if (!payable) return;
        created = await createListingFeeCheckoutSession({
          quoteId: payable.id,
          idempotencyKey: crypto.randomUUID(),
          billingCountry,
        });
      }
      if (created.status === "SUCCEEDED") {
        // Stripe not configured server-side -- completed synchronously,
        // nothing to redirect to.
        setAlreadyPaid(true);
        return;
      }
      if (created.checkoutUrl) {
        // A real browser navigation to Stripe's own hosted payment page --
        // this app never renders its own card form (ZR-PAY-002 Section
        // 8.2's PCI boundary).
        window.location.href = created.checkoutUrl;
        return;
      }
      showToast("Could not start the Listing Fee checkout.", "error");
    } catch (err) {
      showToast(errorMessage(err, "Could not start the Listing Fee checkout."), "error");
    } finally {
      setStartingCheckout(false);
    }
  }

  if (loading) return <Loader label="Loading Listing Fee" />;

  if (alreadyPaid) {
    return paymentId ? (
      <SuccessScreen listingId={listingId} paymentId={paymentId} />
    ) : (
      <Card className="!bg-emerald-50 !ring-emerald-200 dark:!bg-emerald-500/10 dark:!ring-emerald-500/20">
        <p className="flex items-center gap-2 text-sm font-semibold text-emerald-800 dark:text-emerald-300">
          <CheckCircle2 className="h-4 w-4" aria-hidden="true" /> The Listing Fee for this listing has already been paid.
        </p>
      </Card>
    );
  }

  if (confirming && paymentId) {
    return (
      <ConfirmingPayment
        paymentId={paymentId}
        onSucceeded={() => {
          setConfirming(false);
          setAlreadyPaid(true);
        }}
        onFailed={async (message) => {
          setConfirming(false);
          setFailure(message);
          await loadQuoteForRetry();
        }}
      />
    );
  }

  if (!quote) {
    return (
      <Card>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          {unavailableMessage || "Listing fee currently unavailable in this market."}
        </p>
        <p className="mt-1 text-xs text-slate-400">
          Your listing can&apos;t be published until Zoiko Rooms sets a Listing Fee for this market.
        </p>
        <Toast toast={toast} />
      </Card>
    );
  }

  return (
    <Card>
      <p className="font-heading text-base font-bold text-primary-900 dark:text-white">Publish your listing</p>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        This fee is payable to Zoiko Rooms for publishing the listing. It is separate from rent, deposits and other
        rental payments.
      </p>

      <dl className="mt-4 space-y-1.5 text-sm">
        <div className="flex justify-between">
          <dt className="text-slate-500 dark:text-slate-400">Listing fee</dt>
          <dd className="font-semibold text-slate-700 dark:text-slate-200">{formatMoney(quote.amount, quote.currency)}</dd>
        </div>
        <div className="flex justify-between">
          <dt className="text-slate-500 dark:text-slate-400">
            {quote.taxBehavior === "INCLUSIVE" ? "Includes tax" : "Applicable tax"}
            {quote.taxRate != null && ` (${(quote.taxRate * 100).toFixed(2).replace(/\.00$/, "")}%)`}
          </dt>
          <dd className="font-semibold text-slate-700 dark:text-slate-200">{formatMoney(quote.taxAmount, quote.currency)}</dd>
        </div>
        <div className="flex justify-between border-t border-slate-100 pt-1.5 font-semibold dark:border-white/10">
          <dt className="text-primary-900 dark:text-white">Total</dt>
          <dd className="text-primary-900 dark:text-white">{formatMoney(quote.totalAmount, quote.currency)}</dd>
        </div>
      </dl>

      {quote.billingEntityName && (
        <p className="mt-3 text-xs text-slate-400">Billed by {quote.billingEntityName}.</p>
      )}
      {quote.disclosureText && (
        <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">{quote.disclosureText}</p>
      )}
      <p className="mt-2 text-xs text-slate-400">
        This price is held until {new Date(quote.expiresAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}.
      </p>

      {previousTotal && (
        <p role="alert" className="mt-4 rounded-xl bg-amber-50 px-4 py-2.5 text-sm text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
          The Listing Fee changed from {formatMoney(previousTotal.totalAmount, previousTotal.currency)} to{" "}
          {formatMoney(quote.totalAmount, quote.currency)}. Please review the new amount and confirm again to continue.
        </p>
      )}

      {failure && (
        <p role="alert" className="mt-4 rounded-xl bg-rose-50 px-4 py-2.5 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
          {failure}. You can retry with the same or a different payment method.
        </p>
      )}

      <div className="mt-4 space-y-4">
        <Field label="Billing country/region *">
          <input
            value={billingCountry}
            onChange={(e) => setBillingCountry(e.target.value.toUpperCase().slice(0, 2))}
            placeholder="GB"
            maxLength={2}
            className={inputClass}
          />
        </Field>
        <label className="flex items-start gap-2 text-sm text-slate-600 dark:text-slate-300">
          <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} className="mt-0.5 h-4 w-4" />
          I agree to the applicable Listing Fee Terms.
        </label>
        <Button
          fullWidth
          disabled={!agreed || billingCountry.length !== 2}
          loading={startingCheckout}
          onClick={handleStartCheckout}
        >
          Pay {formatMoney(quote.totalAmount, quote.currency)} on Stripe
        </Button>
        <p className="flex items-center gap-1.5 text-xs text-slate-400">
          <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" /> You&apos;ll be redirected to Stripe&apos;s own secure
          payment page -- Zoiko Rooms never sees or stores your card number.
        </p>
      </div>
      <Toast toast={toast} />
    </Card>
  );
}
