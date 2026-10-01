"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Clock, FileSignature, MessageSquare, Send, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { Agreement, DisclosureRequirement, Offer, RentalPaymentObligation } from "@/lib/types";
import {
  agreementStatusLabel,
  agreementStatusTone,
  offerStatusLabel,
  offerStatusTone,
  rentalPaymentStatusLabel,
  rentalPaymentStatusTone,
} from "@/lib/status";
import { formatCurrency, formatDate } from "@/lib/utils";
import {
  acceptHostedOfferCounter,
  addHostedOfferTerms,
  createHostedAgreement,
  createHostedOffer,
  deliverHostedDisclosure,
  errorMessage,
  getHostedOffer,
  listHostedAgreementDisclosures,
  listRecipientRentalPaymentObligations,
  rejectHostedOfferCounter,
  sendHostedAgreement,
  sendHostedOffer,
  signHostedAgreement,
} from "@/lib/user-api";
import { Field, inputClass, useToast, Toast } from "@/components/user/ui";

interface TermsForm {
  monthlyRent: string;
  depositAmount: string;
  startDate: string;
  termMonths: string;
}

const emptyTermsForm: TermsForm = { monthlyRent: "", depositAmount: "", startDate: "", termMonths: "11" };

export function HostOfferAgreementPanel({
  open,
  onClose,
  applicationId,
  offerId,
  renterName,
  onChanged,
}: {
  open: boolean;
  onClose: () => void;
  applicationId: number;
  /** Null when this application has no offer yet -- the panel starts at "Create Offer". */
  offerId: number | null;
  renterName: string;
  /** Called after any action that changes offer/agreement state, so the caller
   *  (HostApplicationsManager's application list) can refresh in the background. */
  onChanged: () => void;
}) {
  const [offer, setOffer] = useState<Offer | null>(null);
  const [disclosures, setDisclosures] = useState<DisclosureRequirement[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [termsForm, setTermsForm] = useState<TermsForm>(emptyTermsForm);
  // SENT offers: the host can answer a renter's counter proposal, or send
  // revised terms of their own instead.
  const [reviseOpen, setReviseOpen] = useState(false);
  const [counterNote, setCounterNote] = useState("");
  const [rentalPaymentObligations, setRentalPaymentObligations] = useState<RentalPaymentObligation[]>([]);
  const { toast, showToast } = useToast();

  const reload = useCallback(async (currentOfferId: number) => {
    const fresh = await getHostedOffer(currentOfferId);
    setOffer(fresh);
    if (fresh.agreement) {
      const items = await listHostedAgreementDisclosures(fresh.agreement.id);
      setDisclosures(items);
      // ZR-PAY-LINK-003: this agreement's own RENT/DEPOSIT
      // RentalPaymentObligations (crud/leasing.py:create_agreement already
      // creates these alongside the legacy pair) -- so the host sees real
      // payment progress here instead of a static "waiting" banner. The
      // host is the recipient, not the payer -- no payment actions here,
      // their own confirm-receipt UI already exists in
      // RecipientRentalPaymentsManager.tsx.
      listRecipientRentalPaymentObligations(undefined, { agreementId: fresh.agreement.id })
        .then((page) => setRentalPaymentObligations(page.items))
        .catch(() => setRentalPaymentObligations([]));
    } else {
      setDisclosures([]);
      setRentalPaymentObligations([]);
    }
    return fresh;
  }, []);

  useEffect(() => {
    if (!open) return;
    setError("");
    setTermsForm(emptyTermsForm);
    setReviseOpen(false);
    setCounterNote("");
    setLoading(true);
    (async () => {
      try {
        if (offerId) {
          await reload(offerId);
        } else {
          setOffer(null);
          setDisclosures([]);
        }
      } catch (err) {
        showToast(errorMessage(err, "Could not load this offer."), "error");
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, offerId]);

  async function runAction(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
      onChanged();
    } catch (err) {
      setError(errorMessage(err, "That action could not be completed."));
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateOffer() {
    await runAction(async () => {
      const created = await createHostedOffer(applicationId);
      await reload(created.id);
    });
  }

  async function handleSaveTerms() {
    if (!offer) return;
    const monthlyRent = Number(termsForm.monthlyRent);
    const depositAmount = Number(termsForm.depositAmount);
    const termMonths = Number(termsForm.termMonths);
    if (!Number.isFinite(monthlyRent) || monthlyRent <= 0) {
      setError("Enter a monthly rent greater than zero.");
      return;
    }
    if (!Number.isFinite(depositAmount) || depositAmount < 0) {
      setError("Enter a valid deposit amount.");
      return;
    }
    if (!termsForm.startDate) {
      setError("Pick a start date.");
      return;
    }
    if (!Number.isFinite(termMonths) || termMonths <= 0) {
      setError("Enter a term length in months.");
      return;
    }
    await runAction(async () => {
      await addHostedOfferTerms(offer.id, { monthlyRent, depositAmount, startDate: termsForm.startDate, termMonths });
      await reload(offer.id);
      if (offer.status === "SENT") {
        setReviseOpen(false);
        setTermsForm(emptyTermsForm);
        showToast("Revised terms sent to the renter.");
      }
    });
  }

  async function handleAnswerCounter(counterId: number, accept: boolean) {
    if (!offer) return;
    await runAction(async () => {
      const updated = accept
        ? await acceptHostedOfferCounter(offer.id, counterId, counterNote.trim())
        : await rejectHostedOfferCounter(offer.id, counterId, counterNote.trim());
      setOffer(updated);
      setCounterNote("");
      showToast(
        accept
          ? "Proposal accepted. The renter now needs to accept the updated offer."
          : "Proposal declined. Your original terms stand."
      );
    });
  }

  async function handleSendOffer() {
    if (!offer) return;
    await runAction(async () => {
      await sendHostedOffer(offer.id);
      await reload(offer.id);
    });
  }

  async function handleCreateAgreement() {
    if (!offer) return;
    await runAction(async () => {
      await createHostedAgreement(offer.id);
      await reload(offer.id);
    });
  }

  async function handleSendAgreement(agreement: Agreement) {
    if (!offer) return;
    await runAction(async () => {
      await sendHostedAgreement(agreement.id);
      await reload(offer.id);
    });
  }

  async function handleDeliverDisclosure(agreement: Agreement, disclosureId: number) {
    if (!offer) return;
    await runAction(async () => {
      await deliverHostedDisclosure(agreement.id, disclosureId);
      await reload(offer.id);
    });
  }

  async function handleSign(agreement: Agreement) {
    if (!offer) return;
    await runAction(async () => {
      await signHostedAgreement(agreement.id);
      await reload(offer.id);
      showToast("Signed. Your part is done.");
    });
  }

  const agreement = offer?.agreement ?? null;
  const latestTerms = offer && offer.terms.length > 0 ? offer.terms[offer.terms.length - 1] : null;
  const pendingCounter = offer?.counterProposals.find((c) => c.status === "PENDING") ?? null;
  const showTermsForm = offer?.status === "DRAFT" || (offer?.status === "SENT" && reviseOpen && !pendingCounter);
  const allDisclosuresDelivered = disclosures.length > 0 && disclosures.every((d) => d.status !== "REQUIRED_MISSING");

  return (
    <Modal open={open} onClose={onClose} title={`Offer & agreement — ${renterName}`} size="xl">
      {loading ? (
        <Loader label="Loading" />
      ) : (
        <div className="max-h-[65vh] space-y-5 overflow-y-auto pr-1">
          {/* Step 1: create the offer */}
          {!offer && (
            <div className="rounded-xl bg-slate-50 p-5 text-center dark:bg-slate-800/60">
              <p className="text-sm text-slate-600 dark:text-slate-300">
                No offer has been created yet for this applicant.
              </p>
              <Button className="mt-3" loading={busy} onClick={handleCreateOffer}>
                Create offer
              </Button>
            </div>
          )}

          {offer && (
            <div className="flex items-center justify-between rounded-xl bg-slate-50 px-4 py-3 dark:bg-slate-800/60">
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Offer status
              </span>
              <Badge tone={offerStatusTone[offer.status] ?? "neutral"}>
                {offerStatusLabel[offer.status] ?? offer.status}
              </Badge>
            </div>
          )}

          {/* Step 2: terms (also used to send revised terms on a SENT offer) */}
          {offer && showTermsForm && (
            <div className="space-y-3">
              <p className="text-xs font-bold uppercase tracking-wide text-slate-400">
                {offer.status === "SENT" ? "Send revised terms" : latestTerms ? "Update terms" : "Set terms"}
              </p>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="Monthly rent">
                  <input
                    inputMode="decimal"
                    value={termsForm.monthlyRent}
                    onChange={(e) => setTermsForm((f) => ({ ...f, monthlyRent: e.target.value }))}
                    placeholder="1800"
                    className={inputClass}
                  />
                </Field>
                <Field label="Deposit amount">
                  <input
                    inputMode="decimal"
                    value={termsForm.depositAmount}
                    onChange={(e) => setTermsForm((f) => ({ ...f, depositAmount: e.target.value }))}
                    placeholder="1800"
                    className={inputClass}
                  />
                </Field>
                <Field label="Start date">
                  <input
                    type="date"
                    value={termsForm.startDate}
                    onChange={(e) => setTermsForm((f) => ({ ...f, startDate: e.target.value }))}
                    className={inputClass}
                  />
                </Field>
                <Field label="Term (months)">
                  <input
                    inputMode="numeric"
                    value={termsForm.termMonths}
                    onChange={(e) => setTermsForm((f) => ({ ...f, termMonths: e.target.value }))}
                    className={inputClass}
                  />
                </Field>
              </div>
              <div className="flex gap-2">
                <Button loading={busy} onClick={handleSaveTerms}>
                  {offer.status === "SENT" ? "Send revised terms" : "Save terms"}
                </Button>
                {offer.status === "SENT" && (
                  <Button variant="ghost" onClick={() => setReviseOpen(false)}>
                    Cancel
                  </Button>
                )}
              </div>
            </div>
          )}

          {/* Step 3: send offer */}
          {offer && offer.status === "DRAFT" && latestTerms && (
            <Button variant="outline" loading={busy} onClick={handleSendOffer}>
              <Send className="h-3.5 w-3.5" /> Send offer to renter
            </Button>
          )}

          {latestTerms && (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Current terms: {formatCurrency(latestTerms.monthlyRent, latestTerms.currency)}/month ·{" "}
              {formatCurrency(latestTerms.depositAmount, latestTerms.currency)} deposit · {latestTerms.termMonths} months
              from {formatDate(latestTerms.startDate)}
            </p>
          )}

          {offer && offer.status === "SENT" && pendingCounter && latestTerms && (
            <div className="space-y-3 rounded-xl bg-sky-50 p-4 ring-1 ring-sky-200 dark:bg-sky-500/10 dark:ring-sky-500/20">
              <p className="flex items-center gap-1.5 text-sm font-semibold text-sky-800 dark:text-sky-200">
                <MessageSquare className="h-4 w-4" /> The renter proposed different terms
              </p>
              <dl className="grid grid-cols-2 gap-3 text-xs">
                <div>
                  <dt className="text-slate-500 dark:text-slate-400">Monthly rent</dt>
                  <dd className="font-semibold text-primary-900 dark:text-white">
                    {formatCurrency(pendingCounter.monthlyRent, pendingCounter.currency)}{" "}
                    <span className="font-normal text-slate-400">
                      (yours: {formatCurrency(latestTerms.monthlyRent, latestTerms.currency)})
                    </span>
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500 dark:text-slate-400">Deposit</dt>
                  <dd className="font-semibold text-primary-900 dark:text-white">
                    {formatCurrency(pendingCounter.depositAmount, pendingCounter.currency)}{" "}
                    <span className="font-normal text-slate-400">
                      (yours: {formatCurrency(latestTerms.depositAmount, latestTerms.currency)})
                    </span>
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500 dark:text-slate-400">Start date</dt>
                  <dd className="font-semibold text-primary-900 dark:text-white">
                    {formatDate(pendingCounter.startDate ?? latestTerms.startDate)}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500 dark:text-slate-400">Term</dt>
                  <dd className="font-semibold text-primary-900 dark:text-white">
                    {pendingCounter.termMonths ?? latestTerms.termMonths} months
                  </dd>
                </div>
              </dl>
              {pendingCounter.message && (
                <p className="rounded-lg bg-white px-3 py-2 text-xs text-slate-600 dark:bg-slate-900 dark:text-slate-300">
                  &ldquo;{pendingCounter.message}&rdquo;
                </p>
              )}
              <Field label="Note to the renter (optional)">
                <input
                  value={counterNote}
                  onChange={(e) => setCounterNote(e.target.value)}
                  maxLength={1000}
                  className={inputClass}
                />
              </Field>
              <div className="flex flex-wrap gap-2">
                <Button loading={busy} onClick={() => handleAnswerCounter(pendingCounter.id, true)}>
                  Accept proposal
                </Button>
                <Button variant="outline" loading={busy} onClick={() => handleAnswerCounter(pendingCounter.id, false)}>
                  Decline proposal
                </Button>
              </div>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Accepting updates the offer to these terms; the renter then accepts the offer themselves.
              </p>
            </div>
          )}

          {offer && offer.status === "SENT" && !pendingCounter && (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-amber-50 px-4 py-3 text-xs text-amber-700 ring-1 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-500/20">
              <span className="flex items-center gap-2">
                <Clock className="h-3.5 w-3.5 shrink-0" /> Waiting for the renter to accept, decline, or propose
                different terms.
              </span>
              {!reviseOpen && (
                <Button size="sm" variant="outline" onClick={() => setReviseOpen(true)}>
                  Send revised terms
                </Button>
              )}
            </div>
          )}

          {offer && (offer.status === "DECLINED" || offer.status === "EXPIRED" || offer.status === "WITHDRAWN") && (
            <div className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
              This offer is {offerStatusLabel[offer.status]?.toLowerCase()}. Nothing further to do here.
            </div>
          )}

          {/* Step 4: create agreement, once accepted */}
          {offer && offer.status === "ACCEPTED" && !agreement && (
            <div className="rounded-xl bg-emerald-50 p-5 text-center dark:bg-emerald-500/10">
              <p className="text-sm text-emerald-700 dark:text-emerald-300">The renter accepted your offer.</p>
              <Button className="mt-3" loading={busy} onClick={handleCreateAgreement}>
                <FileSignature className="h-3.5 w-3.5" /> Create agreement
              </Button>
            </div>
          )}

          {/* Step 5: agreement lifecycle */}
          {agreement && (
            <div className="space-y-4 border-t border-slate-100 pt-4 dark:border-white/10">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold uppercase tracking-wide text-slate-400">Agreement</span>
                <Badge tone={agreementStatusTone[agreement.status] ?? "neutral"}>
                  {agreementStatusLabel[agreement.status] ?? agreement.status}
                </Badge>
              </div>

              {agreement.status === "DRAFT" && (
                <Button variant="outline" loading={busy} onClick={() => handleSendAgreement(agreement)}>
                  <Send className="h-3.5 w-3.5" /> Send agreement to renter
                </Button>
              )}

              {agreement.status !== "DRAFT" && disclosures.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs font-semibold text-slate-500 dark:text-slate-400">Required disclosures</p>
                  {disclosures.map((d) => (
                    <div
                      key={d.id}
                      className="flex items-center justify-between gap-2 rounded-lg bg-slate-50 px-3 py-2 text-xs dark:bg-slate-800/60"
                    >
                      <span className="text-slate-600 dark:text-slate-300">{d.title || d.disclosureType}</span>
                      {d.status === "REQUIRED_MISSING" ? (
                        <Button size="sm" variant="ghost" loading={busy} onClick={() => handleDeliverDisclosure(agreement, d.id)}>
                          Deliver
                        </Button>
                      ) : (
                        <span className="flex items-center gap-1 font-semibold text-emerald-600">
                          <CheckCircle2 className="h-3.5 w-3.5" /> {d.status === "ACKNOWLEDGED" ? "Acknowledged" : "Delivered"}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {(agreement.status === "SENT" || agreement.status === "PARTIALLY_EXECUTED") && (
                <div className="space-y-2">
                  <p className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
                    Renter signature:{" "}
                    {agreement.signedByRenterAt ? (
                      <span className="flex items-center gap-1 font-semibold text-emerald-600">
                        <CheckCircle2 className="h-3 w-3" /> Signed {formatDate(agreement.signedByRenterAt)}
                      </span>
                    ) : (
                      "Not yet signed"
                    )}
                  </p>
                  {agreement.signedByProviderAt ? (
                    <p className="flex items-center gap-1 text-xs font-semibold text-emerald-600">
                      <CheckCircle2 className="h-3 w-3" /> You signed {formatDate(agreement.signedByProviderAt)}
                    </p>
                  ) : (
                    <Button
                      loading={busy}
                      disabled={!allDisclosuresDelivered}
                      onClick={() => handleSign(agreement)}
                    >
                      <ShieldCheck className="h-3.5 w-3.5" /> Sign as host
                    </Button>
                  )}
                  {!allDisclosuresDelivered && !agreement.signedByProviderAt && (
                    <p className="text-xs text-slate-400">Deliver every disclosure above before signing.</p>
                  )}
                </div>
              )}

              {(agreement.status === "PAYMENT_IN_PROGRESS" || agreement.status === "SIGNED") && (
                <div className="space-y-2 rounded-xl bg-emerald-50 px-4 py-3 dark:bg-emerald-500/10">
                  <p className="text-xs text-emerald-700 dark:text-emerald-300">
                    Both parties have signed.{" "}
                    {agreement.status === "PAYMENT_IN_PROGRESS"
                      ? "Waiting for the renter's initial payment to confirm the booking."
                      : "The agreement is fully executed."}
                  </p>
                  {rentalPaymentObligations.length > 0 && (
                    <div className="space-y-1 border-t border-emerald-200/60 pt-2 dark:border-emerald-500/20">
                      {rentalPaymentObligations.map((o) => (
                        <div key={o.id} className="flex items-center justify-between gap-2 text-xs">
                          <span className="capitalize text-emerald-800 dark:text-emerald-200">
                            {o.displayLabel} — {formatCurrency(o.amount, o.currency)}
                          </span>
                          <Badge tone={rentalPaymentStatusTone[o.status] ?? "neutral"}>
                            {rentalPaymentStatusLabel[o.status] ?? o.status}
                          </Badge>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {error && (
            <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
              {error}
            </p>
          )}
        </div>
      )}
      <Toast toast={toast} />
    </Modal>
  );
}
