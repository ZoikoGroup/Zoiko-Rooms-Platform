"use client";

import { FormEvent, useEffect, useState } from "react";
import { RotateCcw } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Card, EmptyState, Field, SectionHeading, Toast, inputClass, useToast } from "@/components/user/ui";
import { PaymentMethodCategory, RentalPaymentReturn, RentalPaymentReturnCandidate } from "@/lib/types";
import {
  DuplicateWarning,
  EMPTY_PAYMENT_METHOD,
  ExternalPaymentNotice,
  PaymentMethodErrors,
  PaymentMethodFields,
  PaymentMethodValue,
  handleSubmitWarning,
  paymentMethodDisplay,
  usePaymentMethodCatalog,
  validatePaymentMethod,
} from "@/components/user/payment-methods/PaymentMethodFields";
import { formatDate, formatMoney } from "@/lib/utils";
import {
  confirmRentalPaymentReturn,
  disputeRentalPaymentReturn,
  errorMessage,
  listMyRentalPaymentReturns,
  listRecipientRentalPaymentReturnCandidates,
  listRecipientRentalPaymentReturns,
  recordRentalPaymentReturn,
} from "@/lib/user-api";

/** Deposit returns and cancellation returns are paid host -> renter
 *  directly -- the host records what they sent back, the renter confirms it
 *  arrived. Zoiko never holds or sends back this money. */

const KIND_LABELS: Record<string, string> = {
  DEPOSIT_RETURN: "Deposit return",
  CANCELLATION_RETURN: "Cancelled booking",
};

const STATUS: Record<string, { label: string; tone: "warning" | "success" | "danger" }> = {
  RECORDED: { label: "Awaiting renter", tone: "warning" },
  CONFIRMED: { label: "Received", tone: "success" },
  DISPUTED: { label: "Problem reported", tone: "danger" },
};

function ReturnSummary({ item }: { item: RentalPaymentReturn }) {
  const status = STATUS[item.status] ?? { label: item.status, tone: "warning" as const };
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-semibold text-primary-900 dark:text-white">
          {KIND_LABELS[item.kind] ?? item.kind} · {formatMoney(item.amount, item.currency)}
        </p>
        <Badge tone={status.tone}>{status.label}</Badge>
      </div>
      <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
        {paymentMethodDisplay(item)} on {formatDate(item.returnedDate)}
        {item.externalReference ? ` · ref ${item.externalReference}` : ""}
      </p>
      {item.deductionsAmount > 0 && (
        <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
          Kept back {formatMoney(item.deductionsAmount, item.currency)}: {item.deductionsReason}
        </p>
      )}
      {item.note && <p className="mt-0.5 text-xs text-slate-400">{item.note}</p>}
      {item.status === "DISPUTED" && item.tenantDisputeDetails && (
        <p className="mt-1 text-xs text-rose-600 dark:text-rose-300">Renter: {item.tenantDisputeDetails}</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- host

export function HostReturnsManager() {
  const { toast, showToast } = useToast();
  const [candidates, setCandidates] = useState<RentalPaymentReturnCandidate[]>([]);
  const [returns, setReturns] = useState<RentalPaymentReturn[]>([]);
  const [recording, setRecording] = useState<RentalPaymentReturnCandidate | null>(null);

  function load() {
    Promise.all([listRecipientRentalPaymentReturnCandidates(), listRecipientRentalPaymentReturns()])
      .then(([c, r]) => {
        setCandidates(c);
        setReturns(r);
      })
      .catch((err) => showToast(errorMessage(err, "Could not load returns."), "error"));
  }

  useEffect(load, []); // eslint-disable-line react-hooks/exhaustive-deps

  const open = candidates.filter((c) => c.remaining > 0);

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <SectionHeading
          title="Money to send back"
          subtitle="Deposits after a tenancy ends, and payments on bookings cancelled before move-in. Send it to the renter directly, then record it here."
        />
        {open.length === 0 ? (
          <Card>
            <EmptyState message="Nothing to send back right now." />
          </Card>
        ) : (
          open.map((c) => (
            <Card key={`${c.occupancyId}-${c.kind}`} className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-primary-900 dark:text-white">
                  {c.listingName || `Booking #${c.occupancyId}`} · {KIND_LABELS[c.kind]}
                </p>
                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                  {c.kind === "DEPOSIT_RETURN" ? "Deposit received" : "Paid on this booking"}: {formatMoney(c.paid, c.currency)}
                  {c.settled > 0 && ` · already settled ${formatMoney(c.settled, c.currency)}`} · left to settle{" "}
                  {formatMoney(c.remaining, c.currency)}
                  {c.endedOn && ` · ended ${formatDate(c.endedOn)}`}
                </p>
              </div>
              <Button size="sm" onClick={() => setRecording(c)}>
                <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" /> Record money sent back
              </Button>
            </Card>
          ))
        )}
      </div>

      <div className="space-y-3">
        <SectionHeading title="Recorded" />
        {returns.length === 0 ? (
          <Card>
            <EmptyState message="You haven't recorded sending anything back yet." />
          </Card>
        ) : (
          returns.map((r) => (
            <Card key={r.id}>
              <ReturnSummary item={r} />
            </Card>
          ))
        )}
      </div>

      <RecordReturnModal
        candidate={recording}
        onClose={() => setRecording(null)}
        onRecorded={() => {
          setRecording(null);
          showToast("Recorded. The renter has been asked to confirm it arrived.");
          load();
        }}
      />
      <Toast toast={toast} />
    </div>
  );
}

function RecordReturnModal({
  candidate,
  onClose,
  onRecorded,
}: {
  candidate: RentalPaymentReturnCandidate | null;
  onClose: () => void;
  onRecorded: () => void;
}) {
  const [amount, setAmount] = useState("");
  const [deductions, setDeductions] = useState("0");
  const [deductionsReason, setDeductionsReason] = useState("");
  const [method, setMethod] = useState<PaymentMethodValue>(EMPTY_PAYMENT_METHOD);
  const [returnedDate, setReturnedDate] = useState("");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [methodErrors, setMethodErrors] = useState<PaymentMethodErrors>({});
  const [duplicate, setDuplicate] = useState("");
  const { catalog, error: catalogError } = usePaymentMethodCatalog(candidate?.countryCode, "return_record", Boolean(candidate));

  useEffect(() => {
    if (!candidate) return;
    setAmount(String(candidate.remaining));
    setDeductions("0");
    setDeductionsReason("");
    setMethod(EMPTY_PAYMENT_METHOD);
    setReturnedDate(new Date().toISOString().slice(0, 10));
    setNote("");
    setError("");
    setMethodErrors({});
    setDuplicate("");
  }, [candidate]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    void submit();
  }

  async function submit(confirmNotDuplicate = false) {
    if (!candidate) return;
    const errors = validatePaymentMethod(catalog, method);
    setMethodErrors(errors);
    if (Object.keys(errors).length > 0) {
      setError("Please fix the highlighted fields.");
      return;
    }
    const sent = Number(amount);
    const kept = Number(deductions || 0);
    if (!Number.isFinite(sent) || !Number.isFinite(kept) || sent < 0 || kept < 0 || sent + kept <= 0) {
      setError("Enter what you sent back.");
      return;
    }
    if (sent + kept > candidate.remaining + 0.001) {
      setError(`Sent back plus kept can't be more than ${formatMoney(candidate.remaining, candidate.currency)}.`);
      return;
    }
    if (kept > 0 && !deductionsReason.trim()) {
      setError("Explain what you kept back -- the renter sees this.");
      return;
    }
    setSubmitting(true);
    setError("");
    setDuplicate("");
    try {
      await recordRentalPaymentReturn(candidate.occupancyId, {
        kind: candidate.kind, amount: sent, deductionsAmount: kept, deductionsReason: deductionsReason.trim(),
        paymentMethodCategory: method.category as PaymentMethodCategory, paymentMethodSubtype: method.subtype,
        paymentMethodDescription: method.description, externalReference: method.reference,
        returnedDate, note, confirmNotDuplicate,
      });
      onRecorded();
    } catch (err) {
      if (!handleSubmitWarning(err, setDuplicate, setError)) setError(errorMessage(err, "Could not record this."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open={Boolean(candidate)} onClose={onClose} size="lg" title="Record money sent back">
      {candidate && (
        <form onSubmit={handleSubmit} className="space-y-4">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            {KIND_LABELS[candidate.kind]} for {candidate.listingName || `booking #${candidate.occupancyId}`}. Left to
            settle: {formatMoney(candidate.remaining, candidate.currency)}.
          </p>
          {error && <p role="alert" className="rounded-xl bg-rose-50 px-4 py-2.5 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">{error}</p>}
          {duplicate && <DuplicateWarning message={duplicate} submitting={submitting} onConfirm={() => void submit(true)} />}
          <Field label={`Amount sent back (${candidate.currency}) *`}>
            <input required type="number" min="0" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} className={inputClass} />
          </Field>
          <Field
            label={`Amount kept back (${candidate.currency})`}
            hint={candidate.kind === "DEPOSIT_RETURN" ? "e.g. for damage or unpaid rent" : "e.g. a cancellation fee in your agreement"}
          >
            <input type="number" min="0" step="0.01" value={deductions} onChange={(e) => setDeductions(e.target.value)} className={inputClass} />
          </Field>
          {Number(deductions || 0) > 0 && (
            <Field label="Why it was kept back *">
              <textarea value={deductionsReason} onChange={(e) => setDeductionsReason(e.target.value)} rows={2} className={inputClass} />
            </Field>
          )}
          <Field label="Date sent *">
            <input required type="date" value={returnedDate} onChange={(e) => setReturnedDate(e.target.value)} className={inputClass} />
          </Field>
          <PaymentMethodFields
            catalog={catalog}
            catalogError={catalogError}
            value={method}
            onChange={setMethod}
            errors={methodErrors}
            categoryLabel="Sent by"
          />
          <Field label="Note to the renter" hint="Optional">
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} className={inputClass} />
          </Field>
          <ExternalPaymentNotice role="return" />
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
            <Button type="submit" loading={submitting}>Record</Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------- renter

export function TenantReturnsSection() {
  const { toast, showToast } = useToast();
  const [returns, setReturns] = useState<RentalPaymentReturn[]>([]);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [disputing, setDisputing] = useState<RentalPaymentReturn | null>(null);
  const [details, setDetails] = useState("");

  function load() {
    listMyRentalPaymentReturns()
      .then(setReturns)
      .catch(() => setReturns([]));
  }

  useEffect(load, []);

  async function confirm(item: RentalPaymentReturn) {
    setBusyId(item.id);
    try {
      await confirmRentalPaymentReturn(item.id);
      showToast("Thanks -- your host has been told it arrived.");
      load();
    } catch (err) {
      showToast(errorMessage(err, "Could not confirm this."), "error");
    } finally {
      setBusyId(null);
    }
  }

  async function submitDispute(e: FormEvent) {
    e.preventDefault();
    if (!disputing) return;
    if (!details.trim()) {
      showToast("Tell your host what's wrong.", "error");
      return;
    }
    setBusyId(disputing.id);
    try {
      await disputeRentalPaymentReturn(disputing.id, details.trim());
      showToast("Your host has been told there's a problem.");
      setDisputing(null);
      setDetails("");
      load();
    } catch (err) {
      showToast(errorMessage(err, "Could not report this."), "error");
    } finally {
      setBusyId(null);
    }
  }

  if (returns.length === 0) return null;

  return (
    <div className="space-y-3">
      <SectionHeading title="Money returned to you" subtitle="Deposits and cancelled-booking payments your host has sent back to you directly." />
      {returns.map((r) => (
        <Card key={r.id} className="flex flex-wrap items-center justify-between gap-3">
          <ReturnSummary item={r} />
          {r.status === "RECORDED" && (
            <div className="flex items-center gap-2">
              <Button size="sm" variant="ghost" onClick={() => setDisputing(r)}>Report a problem</Button>
              <Button size="sm" loading={busyId === r.id} onClick={() => confirm(r)}>It arrived</Button>
            </div>
          )}
        </Card>
      ))}
      <Modal open={Boolean(disputing)} onClose={() => setDisputing(null)} title="Report a problem">
        <form onSubmit={submitDispute} className="space-y-4">
          <Field label="What's wrong? *" hint="e.g. nothing has arrived, or the amount is different.">
            <textarea value={details} onChange={(e) => setDetails(e.target.value)} rows={3} className={inputClass} />
          </Field>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setDisputing(null)}>Cancel</Button>
            <Button type="submit" loading={busyId === disputing?.id}>Send</Button>
          </div>
        </form>
      </Modal>
      <Toast toast={toast} />
    </div>
  );
}
