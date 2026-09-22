"use client";

import { FormEvent, useEffect, useState } from "react";
import { Elements, PaymentElement, useElements, useStripe } from "@stripe/react-stripe-js";
import { CheckCircle2, Download, ShieldCheck, XCircle } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Card, Field, Toast, inputClass, useToast } from "@/components/user/ui";
import { ListingFeeCheckoutSession, ListingFeeQuote, PublishEligibility } from "@/lib/types";
import { formatMoney } from "@/lib/utils";
import {
  createListingFeeCheckoutSession,
  createListingFeeQuote,
  downloadListingFeeReceipt,
  errorMessage,
  getHostedListingPublishEligibility,
  getListingFeePayment,
} from "@/lib/user-api";
import { getStripe } from "@/lib/stripe";

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

function StripePaymentForm({
  paymentId,
  totalAmount,
  currency,
  onSucceeded,
  onFailed,
}: {
  paymentId: number;
  totalAmount: number;
  currency: string;
  onSucceeded: () => void;
  onFailed: (message: string) => void;
}) {
  const stripe = useStripe();
  const elements = useElements();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!stripe || !elements) return;
    setSubmitting(true);
    setError("");
    // ZR-PAY-002 Section 8.2 PCI boundary: this application never sees card
    // PAN/CVV -- Stripe's own PaymentElement collects it directly, and
    // confirmPayment talks to Stripe, not our backend.
    const { error: confirmError, paymentIntent } = await stripe.confirmPayment({
      elements,
      redirect: "if_required",
    });

    if (confirmError) {
      // Section 8.4: 'Show a neutral failure message; do not expose gateway
      // diagnostics.' confirmError.message is Stripe's own user-safe decline
      // reason (e.g. "Your card was declined."), never a raw stack trace.
      setError(confirmError.message ?? "Your payment could not be completed.");
      setSubmitting(false);
      return;
    }

    if (paymentIntent?.status === "succeeded") {
      onSucceeded();
      return;
    }

    // Any other status (processing, requires_action already redirected,
    // etc.) -- poll our own record once rather than guessing; the webhook
    // is the actual source of truth for SUCCEEDED/FAILED.
    try {
      const payment = await getListingFeePayment(paymentId);
      if (payment.status === "SUCCEEDED") onSucceeded();
      else if (payment.status === "FAILED") onFailed(payment.failureMessage || "Your payment did not go through.");
      else setError("Your payment is still processing. Refresh in a moment to check its status.");
    } catch {
      setError("Your payment is still processing. Refresh in a moment to check its status.");
    }
    setSubmitting(false);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && (
        <p role="alert" className="rounded-xl bg-rose-50 px-4 py-2.5 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </p>
      )}
      <PaymentElement />
      <Button type="submit" fullWidth loading={submitting} disabled={!stripe}>
        Pay {formatMoney(totalAmount, currency)}
      </Button>
    </form>
  );
}

export function ListingFeeCheckout({ listingId }: { listingId: string }) {
  const { toast, showToast } = useToast();
  const [loading, setLoading] = useState(true);
  const [alreadyPaid, setAlreadyPaid] = useState(false);
  const [quote, setQuote] = useState<ListingFeeQuote | null>(null);
  const [session, setSession] = useState<ListingFeeCheckoutSession | null>(null);
  const [billingCountry, setBillingCountry] = useState("GB");
  const [agreed, setAgreed] = useState(false);
  const [creatingSession, setCreatingSession] = useState(false);
  const [failure, setFailure] = useState("");

  useEffect(() => {
    getHostedListingPublishEligibility(listingId)
      .then((eligibility) => {
        const feeAlreadyPaid = !eligibility.reasons.some((r) => r.toLowerCase().includes("listing fee"));
        setAlreadyPaid(feeAlreadyPaid);
        if (!feeAlreadyPaid) return createListingFeeQuote(listingId).then(setQuote);
      })
      .catch((err) => showToast(errorMessage(err, "Could not load the Listing Fee for this listing."), "error"))
      .finally(() => setLoading(false));
  }, [listingId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleStartCheckout() {
    if (!quote) return;
    setCreatingSession(true);
    setFailure("");
    try {
      const created = await createListingFeeCheckoutSession({
        quoteId: quote.id,
        idempotencyKey: crypto.randomUUID(),
        billingCountry,
      });
      setSession(created);
      if (created.status === "SUCCEEDED") {
        // Stripe not configured server-side -- completed synchronously,
        // nothing to confirm client-side.
        setAlreadyPaid(true);
      }
    } catch (err) {
      showToast(errorMessage(err, "Could not start the Listing Fee checkout."), "error");
    } finally {
      setCreatingSession(false);
    }
  }

  if (loading) return <Loader label="Loading Listing Fee" />;

  if (alreadyPaid) {
    const paymentId = session?.id;
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

  if (!quote) {
    return (
      <Card>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          No Listing Fee policy is configured for this listing&apos;s jurisdiction yet.
        </p>
        <Toast toast={toast} />
      </Card>
    );
  }

  if (session?.status === "SUCCEEDED") {
    return <SuccessScreen listingId={listingId} paymentId={session.id} />;
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
          <dt className="text-slate-500 dark:text-slate-400">Applicable tax</dt>
          <dd className="font-semibold text-slate-700 dark:text-slate-200">{formatMoney(quote.taxAmount, quote.currency)}</dd>
        </div>
        <div className="flex justify-between border-t border-slate-100 pt-1.5 font-semibold dark:border-white/10">
          <dt className="text-primary-900 dark:text-white">Total</dt>
          <dd className="text-primary-900 dark:text-white">{formatMoney(quote.totalAmount, quote.currency)}</dd>
        </div>
      </dl>

      {failure && (
        <p role="alert" className="mt-4 rounded-xl bg-rose-50 px-4 py-2.5 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
          {failure}. You can retry with the same or a different payment method.
        </p>
      )}

      {!session?.clientSecret ? (
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
            loading={creatingSession}
            onClick={handleStartCheckout}
          >
            Pay {formatMoney(quote.totalAmount, quote.currency)}
          </Button>
          <p className="flex items-center gap-1.5 text-xs text-slate-400">
            <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" /> Card details are handled directly by our payment provider -- Zoiko
            Rooms never stores your card number.
          </p>
        </div>
      ) : (
        <div className="mt-4">
          <Elements stripe={getStripe()} options={{ clientSecret: session.clientSecret }}>
            <StripePaymentForm
              paymentId={session.id}
              totalAmount={quote.totalAmount}
              currency={quote.currency}
              onSucceeded={() => setAlreadyPaid(true)}
              onFailed={(message) => {
                setFailure(message);
                setSession(null);
              }}
            />
          </Elements>
        </div>
      )}
      <Toast toast={toast} />
    </Card>
  );
}
