"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { AlertTriangle, CalendarClock, FileWarning, Home, Upload } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { Card, EmptyState, Field, SectionHeading, Toast, inputClass, useToast } from "@/components/user/ui";
import { RentalPaymentEvidenceList } from "@/components/user/RentalPaymentEvidenceList";
import {
  RentalPaymentDiscrepancyReason,
  RentalPaymentInstruction,
  RentalPaymentMethodCategory,
  RentalPaymentObligation,
  RentalPaymentObligationType,
  RentalPaymentRecord,
} from "@/lib/types";
import { rentalPaymentStatusLabel, rentalPaymentStatusTone } from "@/lib/status";
import { formatDate, formatDateTime, formatMoney } from "@/lib/utils";
import {
  errorMessage,
  getMyRentalPaymentInstructions,
  listMyRentalPaymentObligations,
  markRentalPaymentPaid,
  reportRentalPaymentDiscrepancyAsTenant,
  uploadRentalPaymentEvidence,
} from "@/lib/user-api";

const METHOD_OPTIONS: { value: RentalPaymentMethodCategory; label: string }[] = [
  { value: "BANK_TRANSFER", label: "Bank transfer" },
  { value: "CASH", label: "Cash" },
  { value: "CARD", label: "Card" },
  { value: "OTHER", label: "Other" },
];

const DISCREPANCY_OPTIONS: { value: RentalPaymentDiscrepancyReason; label: string }[] = [
  { value: "NOT_ARRIVED", label: "Payment has not arrived" },
  { value: "AMOUNT_DIFFERENT", label: "Amount received is different" },
  { value: "REFERENCE_MISMATCH", label: "Reference cannot be matched" },
  { value: "RETURNED_OR_REVERSED", label: "Payment was returned or reversed" },
  { value: "OTHER", label: "Other" },
];

const TYPE_FILTERS: { value: RentalPaymentObligationType | "ALL"; label: string }[] = [
  { value: "ALL", label: "All" },
  { value: "RENT", label: "Rent" },
  { value: "DEPOSIT", label: "Deposit" },
  { value: "OTHER", label: "Other" },
];

type Tab = "overview" | "upcoming" | "records" | "instructions";

const OPEN_STATUSES = new Set(["UPCOMING", "DUE", "OVERDUE"]);

export function RentalPaymentsManager() {
  const { toast, showToast } = useToast();
  const [obligations, setObligations] = useState<RentalPaymentObligation[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Tab>("overview");
  const [typeFilter, setTypeFilter] = useState<RentalPaymentObligationType | "ALL">("ALL");

  const [payingObligation, setPayingObligation] = useState<RentalPaymentObligation | null>(null);
  const [instructionsObligation, setInstructionsObligation] = useState<RentalPaymentObligation | null>(null);
  const [disputingRecord, setDisputingRecord] = useState<RentalPaymentRecord | null>(null);
  const [viewingRecord, setViewingRecord] = useState<RentalPaymentRecord | null>(null);

  function load() {
    listMyRentalPaymentObligations()
      .then(setObligations)
      .catch((err) => showToast(errorMessage(err, "Could not load your rental payments."), "error"))
      .finally(() => setLoading(false));
  }

  useEffect(load, []); // eslint-disable-line react-hooks/exhaustive-deps

  const filteredObligations = useMemo(
    () => (typeFilter === "ALL" ? obligations : obligations.filter((o) => o.obligationType === typeFilter)),
    [obligations, typeFilter]
  );

  const openObligations = useMemo(
    () => [...obligations].filter((o) => OPEN_STATUSES.has(o.status)).sort((a, b) => a.dueDate.localeCompare(b.dueDate)),
    [obligations]
  );
  const nextObligation = openObligations[0] ?? null;

  const allRecords = useMemo(
    () =>
      filteredObligations
        .flatMap((o) => o.records.map((r) => ({ record: r, obligation: o })))
        .sort((a, b) => b.record.createdAt.localeCompare(a.record.createdAt)),
    [filteredObligations]
  );

  if (loading) return <Loader label="Loading your rental payments" />;

  return (
    <div className="space-y-5">
      <Card className="!bg-primary-50 !ring-primary-200 dark:!bg-primary-500/10 dark:!ring-primary-500/20">
        <p className="text-sm text-primary-800 dark:text-primary-200">
          Rental payments are made directly to your landlord, agent or other authorized recipient. Zoiko Rooms does
          not receive or hold these funds.
        </p>
      </Card>

      <div role="tablist" aria-label="Payments" className="flex flex-wrap gap-2 border-b border-slate-100 pb-3 dark:border-white/10">
        {(["overview", "upcoming", "records", "instructions"] as Tab[]).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`rounded-full px-4 py-2 text-sm font-semibold capitalize transition-colors ${
              tab === t
                ? "bg-primary-700 text-white"
                : "text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-white/5"
            }`}
          >
            {t === "records" ? "Payment records" : t === "instructions" ? "Payment instructions" : t}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <div className="space-y-4">
          <SectionHeading title="Next obligation" />
          {nextObligation ? (
            <ObligationCard
              obligation={nextObligation}
              onPay={() => setPayingObligation(nextObligation)}
              onViewInstructions={() => setInstructionsObligation(nextObligation)}
            />
          ) : (
            <Card>
              <EmptyState message="You have no upcoming rental payments." />
            </Card>
          )}
        </div>
      )}

      {tab === "upcoming" && (
        <div className="space-y-4">
          <SectionHeading title="Upcoming obligations" subtitle="Payments due now or coming up." />
          {openObligations.length === 0 ? (
            <Card>
              <EmptyState message="Nothing due right now." />
            </Card>
          ) : (
            <div className="space-y-3">
              {openObligations.map((o) => (
                <ObligationCard
                  key={o.id}
                  obligation={o}
                  onPay={() => setPayingObligation(o)}
                  onViewInstructions={() => setInstructionsObligation(o)}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {tab === "records" && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <SectionHeading title="Payment records" subtitle="Every declaration and confirmation on your account." />
            <div role="group" aria-label="Filter by type" className="flex gap-1.5">
              {TYPE_FILTERS.map((f) => (
                <button
                  key={f.value}
                  aria-pressed={typeFilter === f.value}
                  onClick={() => setTypeFilter(f.value)}
                  className={`rounded-full px-3 py-1.5 text-xs font-semibold transition-colors ${
                    typeFilter === f.value
                      ? "bg-primary-700 text-white"
                      : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-white/5 dark:text-slate-300"
                  }`}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>

          {allRecords.length === 0 ? (
            <Card>
              <EmptyState message="No payment records yet." />
            </Card>
          ) : (
            <Card className="!p-0">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] text-left text-sm">
                  <thead>
                    <tr className="border-b border-slate-100 text-xs font-bold uppercase tracking-wide text-slate-400 dark:border-white/10">
                      <th className="px-5 py-3">Date</th>
                      <th className="px-5 py-3">Type</th>
                      <th className="px-5 py-3">Amount</th>
                      <th className="px-5 py-3">Status</th>
                      <th className="px-5 py-3">Source</th>
                      <th className="px-5 py-3"><span className="sr-only">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {allRecords.map(({ record, obligation }) => (
                      <tr key={record.id} className="border-b border-slate-50 last:border-0 dark:border-white/5">
                        <td className="px-5 py-3 text-slate-500 dark:text-slate-400">{formatDate(record.declaredDate)}</td>
                        <td className="px-5 py-3 capitalize text-slate-600 dark:text-slate-300">{obligation.displayLabel}</td>
                        <td className="px-5 py-3 font-semibold text-primary-900 dark:text-white">
                          {formatMoney(record.declaredAmount, record.declaredCurrency)}
                        </td>
                        <td className="px-5 py-3">
                          <Badge tone={rentalPaymentStatusTone[record.status] ?? "neutral"}>
                            {rentalPaymentStatusLabel[record.status] ?? record.status}
                          </Badge>
                        </td>
                        <td className="px-5 py-3 text-xs text-slate-400">{record.provenance.replace(/_/g, " ").toLowerCase()}</td>
                        <td className="px-5 py-3">
                          <div className="flex items-center justify-end gap-3 text-right">
                            <button
                              onClick={() => setViewingRecord(record)}
                              className="text-xs font-semibold text-primary-700 hover:underline dark:text-primary-300"
                            >
                              Open record
                            </button>
                            {record.status !== "DISPUTED" && (
                              <button
                                onClick={() => setDisputingRecord(record)}
                                className="inline-flex items-center gap-1 text-xs font-semibold text-accent-600 hover:text-accent-700"
                              >
                                <FileWarning className="h-3.5 w-3.5" aria-hidden="true" /> Report a problem
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </div>
      )}

      {tab === "instructions" && (
        <div className="space-y-4">
          <SectionHeading
            title="Payment instructions"
            subtitle="Where to send each rental payment. Confirm the recipient and details before sending funds."
          />
          {obligations.length === 0 ? (
            <Card>
              <EmptyState message="No rental obligations yet." />
            </Card>
          ) : (
            <div className="space-y-3">
              {obligations.map((o) => (
                <Card key={o.id} className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="font-heading text-sm font-bold capitalize text-primary-900 dark:text-white">
                      {o.displayLabel} &middot; {formatMoney(o.amount, o.currency)}
                    </p>
                    <p className="text-xs text-slate-400">Due {formatDate(o.dueDate)}</p>
                  </div>
                  <Button size="sm" variant="outline" onClick={() => setInstructionsObligation(o)}>
                    View instructions
                  </Button>
                </Card>
              ))}
            </div>
          )}
        </div>
      )}

      <PayObligationModal
        obligation={payingObligation}
        onClose={() => setPayingObligation(null)}
        onRecorded={() => {
          setPayingObligation(null);
          showToast("Payment recorded. We recorded that you marked this payment as made. The recipient may still need to confirm receipt.");
          load();
        }}
      />
      <InstructionsModal obligation={instructionsObligation} onClose={() => setInstructionsObligation(null)} />
      <RecordDetailModal record={viewingRecord} onClose={() => setViewingRecord(null)} />
      <ReportDiscrepancyModal
        record={disputingRecord}
        onClose={() => setDisputingRecord(null)}
        onReported={() => {
          setDisputingRecord(null);
          showToast("The record is now marked as disputed.");
          load();
        }}
      />

      <Toast toast={toast} />
    </div>
  );
}

function ObligationCard({
  obligation,
  onPay,
  onViewInstructions,
}: {
  obligation: RentalPaymentObligation;
  onPay: () => void;
  onViewInstructions: () => void;
}) {
  const canMarkPaid = obligation.status === "UPCOMING" || obligation.status === "DUE" || obligation.status === "OVERDUE";
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
              <Home className="h-4 w-4" aria-hidden="true" />
            </span>
            <p className="font-heading text-sm font-bold capitalize text-primary-900 dark:text-white">{obligation.displayLabel}</p>
            <Badge tone={rentalPaymentStatusTone[obligation.status] ?? "neutral"}>
              {rentalPaymentStatusLabel[obligation.status] ?? obligation.status}
            </Badge>
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1.5 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-xs text-slate-400">Amount due</dt>
              <dd className="font-semibold text-primary-900 dark:text-white">{formatMoney(obligation.amount, obligation.currency)}</dd>
            </div>
            <div>
              <dt className="text-xs text-slate-400">Due date</dt>
              <dd className="flex items-center gap-1 text-slate-600 dark:text-slate-300">
                <CalendarClock className="h-3.5 w-3.5" aria-hidden="true" /> {formatDate(obligation.dueDate)}
              </dd>
            </div>
          </dl>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" onClick={onViewInstructions}>
            View payment instructions
          </Button>
          {canMarkPaid && (
            <Button size="sm" onClick={onPay}>
              I have made this payment
            </Button>
          )}
        </div>
      </div>
      <p className="mt-3 text-xs text-slate-400">Zoiko Rooms does not receive or hold this payment.</p>
    </Card>
  );
}

function InstructionsModal({ obligation, onClose }: { obligation: RentalPaymentObligation | null; onClose: () => void }) {
  const { toast, showToast } = useToast();
  const [instruction, setInstruction] = useState<RentalPaymentInstruction | null>(null);
  const [loading, setLoading] = useState(false);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (!obligation) return;
    setLoading(true);
    setNotFound(false);
    getMyRentalPaymentInstructions(obligation.id)
      .then(setInstruction)
      .catch(() => setNotFound(true))
      .finally(() => setLoading(false));
  }, [obligation]);

  async function copyReference() {
    if (!instruction?.referenceFormat) return;
    try {
      await navigator.clipboard.writeText(instruction.referenceFormat);
      showToast("Copied.");
    } catch {
      showToast("Could not copy to clipboard.", "error");
    }
  }

  return (
    <Modal open={Boolean(obligation)} onClose={onClose} title="Payment instructions">
      {loading ? (
        <Loader label="Loading" />
      ) : notFound ? (
        <EmptyState message="The recipient has not set up payment instructions yet." />
      ) : instruction ? (
        <div className="space-y-3 text-sm">
          <Row label="Recipient" value={instruction.recipientName} />
          <Row label="Method" value={instruction.method.replace(/_/g, " ").toLowerCase()} />
          <Row label="Account / payment ID" value={instruction.accountIdentifierMasked} />
          {instruction.referenceFormat && (
            <div className="flex items-center justify-between gap-3 rounded-xl bg-slate-50 px-4 py-2.5 dark:bg-slate-800">
              <div>
                <span className="block text-xs text-slate-400">Payment reference</span>
                <span className="font-semibold text-slate-700 dark:text-slate-200">{instruction.referenceFormat}</span>
              </div>
              <button onClick={copyReference} className="text-xs font-semibold text-primary-700 hover:underline dark:text-primary-300">
                Copy
              </button>
            </div>
          )}
          {instruction.additionalInstructions && (
            <p className="rounded-xl bg-slate-50 px-4 py-2.5 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
              {instruction.additionalInstructions}
            </p>
          )}
          <p className="flex items-center gap-1.5 pt-2 text-xs font-semibold text-primary-700 dark:text-primary-300">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" /> Confirm the recipient and details before sending funds.
          </p>
        </div>
      ) : null}
      <Toast toast={toast} />
    </Modal>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between border-b border-slate-50 pb-2 dark:border-white/5">
      <span className="text-xs text-slate-400">{label}</span>
      <span className="font-semibold capitalize text-slate-700 dark:text-slate-200">{value}</span>
    </div>
  );
}

function PayObligationModal({
  obligation,
  onClose,
  onRecorded,
}: {
  obligation: RentalPaymentObligation | null;
  onClose: () => void;
  onRecorded: () => void;
}) {
  const [amount, setAmount] = useState("");
  const [date, setDate] = useState("");
  const [method, setMethod] = useState<RentalPaymentMethodCategory>("BANK_TRANSFER");
  const [reference, setReference] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (obligation) {
      setAmount(String(obligation.amount));
      setDate(new Date().toISOString().slice(0, 10));
      setMethod("BANK_TRANSFER");
      setReference("");
      setFile(null);
      setError("");
    }
  }, [obligation]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!obligation) return;
    setSubmitting(true);
    setError("");
    try {
      const updated = await markRentalPaymentPaid(obligation.id, {
        amount: Number(amount),
        currency: obligation.currency,
        declaredDate: date,
        paymentMethodCategory: method,
        externalReference: reference,
      });
      const newRecord = updated.records[updated.records.length - 1];
      if (file && newRecord) {
        try {
          await uploadRentalPaymentEvidence(newRecord.id, file);
        } catch {
          // Best-effort -- the declaration itself already succeeded; a failed
          // evidence upload must never undo or block that.
        }
      }
      onRecorded();
    } catch (err) {
      setError(errorMessage(err, "Could not record this payment."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open={Boolean(obligation)} onClose={onClose} title="Record payment">
      {obligation && (
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && <p className="rounded-xl bg-rose-50 px-4 py-2.5 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">{error}</p>}
          <div className="grid grid-cols-2 gap-3">
            <Field label="Amount paid *">
              <input
                type="number" step="0.01" min="0" required value={amount}
                onChange={(e) => setAmount(e.target.value)} className={inputClass}
              />
            </Field>
            <Field label="Currency">
              <input value={obligation.currency} disabled className={inputClass} />
            </Field>
            <Field label="Date paid *">
              <input type="date" required value={date} onChange={(e) => setDate(e.target.value)} className={inputClass} />
            </Field>
            <Field label="Payment method *">
              <select value={method} onChange={(e) => setMethod(e.target.value as RentalPaymentMethodCategory)} className={inputClass}>
                {METHOD_OPTIONS.map((m) => (
                  <option key={m.value} value={m.value}>{m.label}</option>
                ))}
              </select>
            </Field>
          </div>
          <Field label="External reference" hint="Optional">
            <input value={reference} onChange={(e) => setReference(e.target.value)} className={inputClass} />
          </Field>
          <Field label="Proof of payment" hint="Optional -- PDF, JPG or PNG">
            <label className="flex cursor-pointer items-center gap-2 rounded-xl bg-slate-50 px-4 py-2.5 text-sm text-slate-500 ring-1 ring-slate-200 has-focus-visible:ring-2 has-focus-visible:ring-primary-400 dark:bg-slate-800 dark:ring-slate-700">
              <Upload className="h-4 w-4" aria-hidden="true" />
              {file ? file.name : "Choose a file"}
              <input
                type="file" accept=".pdf,.jpg,.jpeg,.png" className="sr-only"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </label>
          </Field>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
            <Button type="submit" loading={submitting}>Record payment</Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

function RecordDetailModal({ record, onClose }: { record: RentalPaymentRecord | null; onClose: () => void }) {
  if (!record) return null;
  return (
    <Modal open={Boolean(record)} onClose={onClose} title="Payment record">
      <div className="space-y-3 text-sm">
        <Row label="Status" value={rentalPaymentStatusLabel[record.status] ?? record.status} />
        <Row label="Confirmation source" value={record.provenance.replace(/_/g, " ").toLowerCase()} />
        <Row label="Amount declared" value={formatMoney(record.declaredAmount, record.declaredCurrency)} />
        <Row label="Date paid" value={formatDate(record.declaredDate)} />
        <Row label="Payment method" value={record.paymentMethodCategory.replace(/_/g, " ").toLowerCase()} />
        {record.externalReference && <Row label="External reference" value={record.externalReference} />}
        <Row label="Marked paid at" value={formatDateTime(record.createdAt)} />
        {record.confirmedAt && <Row label="Confirmed at" value={formatDateTime(record.confirmedAt)} />}
        <div>
          <span className="mb-1 block text-xs text-slate-400">Evidence</span>
          <RentalPaymentEvidenceList recordId={record.id} />
        </div>
      </div>
    </Modal>
  );
}

function ReportDiscrepancyModal({
  record,
  onClose,
  onReported,
}: {
  record: RentalPaymentRecord | null;
  onClose: () => void;
  onReported: () => void;
}) {
  const [reason, setReason] = useState<RentalPaymentDiscrepancyReason>("NOT_ARRIVED");
  const [details, setDetails] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (record) {
      setReason("NOT_ARRIVED");
      setDetails("");
      setError("");
    }
  }, [record]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!record) return;
    setSubmitting(true);
    setError("");
    try {
      await reportRentalPaymentDiscrepancyAsTenant(record.id, { reasonCode: reason, details });
      onReported();
    } catch (err) {
      setError(errorMessage(err, "Could not report this discrepancy."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open={Boolean(record)} onClose={onClose} title="Report payment discrepancy">
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <p className="rounded-xl bg-rose-50 px-4 py-2.5 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">{error}</p>}
        <div className="space-y-2">
          {DISCREPANCY_OPTIONS.map((opt) => (
            <label key={opt.value} className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
              <input
                type="radio" name="reason" checked={reason === opt.value}
                onChange={() => setReason(opt.value)} className="h-4 w-4"
              />
              {opt.label}
            </label>
          ))}
        </div>
        <Field label="Details" hint="Optional">
          <textarea value={details} onChange={(e) => setDetails(e.target.value)} rows={3} className={inputClass} />
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="accent" loading={submitting}>Report discrepancy</Button>
        </div>
      </form>
    </Modal>
  );
}
