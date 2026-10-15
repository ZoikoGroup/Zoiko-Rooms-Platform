"use client";

import { FormEvent, useEffect, useId, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Download, FileWarning, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { Card, EmptyState, Field, SectionHeading, Toast, inputClass, useToast } from "@/components/user/ui";
import { RentalPaymentEvidenceList } from "@/components/user/RentalPaymentEvidenceList";
import { COUNTRY_OPTIONS, resolveBankFieldSchema } from "@/lib/bankFieldSchemas";
import { HostReturnsManager } from "@/components/user/RentalPaymentReturns";
import { RentalPaymentTimeline } from "@/components/user/RentalPaymentTimeline";
import {
  ListingFeePayment,
  ListingFeeRefund,
  RentalPaymentDiscrepancyReason,
  RentalPaymentInstruction,
  PaymentMethodCategory,
  RentalPaymentInstructionMethod,
  RentalPaymentObligation,
  RentalPaymentObligationType,
  RentalPaymentRecord,
} from "@/lib/types";
import {
  listingFeePaymentStatusTone,
  listingFeeRefundStatusTone,
  rentalPaymentInstructionStatusTone,
  rentalPaymentStatusLabel,
  rentalPaymentStatusTone,
} from "@/lib/status";
import { cn, formatDate, formatDateTime, formatMoney } from "@/lib/utils";
import {
  DuplicateWarning,
  EMPTY_PAYMENT_METHOD,
  EvidenceUpload,
  ExternalPaymentNotice,
  FieldError,
  FieldLabel,
  LockedCurrency,
  OverpaymentWarning,
  PaymentMethodErrors,
  PaymentMethodFields,
  PaymentMethodGuidance,
  PaymentMethodValue,
  SubmitAcks,
  currencySymbol,
  handleSubmitWarning,
  paymentMethodDisplay,
  resolveFieldRules,
  usePaymentMethodCatalog,
  validatePaymentMethod,
} from "@/components/user/payment-methods/PaymentMethodFields";
import { OptionCombobox } from "@/components/user/payment-methods/OptionCombobox";
import {
  cancelRentalPaymentInstruction,
  confirmRentalPaymentInstruction,
  confirmRentalPaymentReceipt,
  downloadListingFeeCreditNote,
  downloadListingFeeReceipt,
  errorMessage,
  listListingFeeRefundsForPayment,
  listMyListingFeePayments,
  listMyRentalPaymentInstructions,
  listRecipientRentalPaymentObligations,
  recordRentalPaymentReceiptAsRecipient,
  applyRentalPaymentExcess,
  returnRentalPaymentExcess,
  updateOwnOpenRentalPaymentDispute,
  uploadRentalPaymentEvidenceAsRecipient,
  reportRentalPaymentDiscrepancyAsRecipient,
  resendRentalPaymentInstructionCode,
  submitRentalPaymentInstruction,
} from "@/lib/user-api";

// Where the payee asks to be paid (payment instructions). Recorded payments
// use the global method catalog instead (payment-methods/PaymentMethodFields).
// Canonical categories (ZR-PAY-METHODS-GLOBAL-001); for the instant rail,
// wallet and mobile-money options the specific rail/provider is chosen too.
const METHOD_OPTIONS: { value: RentalPaymentInstructionMethod; label: string }[] = [
  { value: "BANK_TRANSFER", label: "Bank Transfer" },
  { value: "INSTANT_BANK_PAYMENT", label: "Instant Bank Payment / Pay by Bank" },
  { value: "DIGITAL_WALLET_P2P", label: "Digital Wallet / P2P Payment App" },
  { value: "MOBILE_MONEY", label: "Mobile Money" },
  { value: "CASH", label: "Cash" },
];
const METHODS_WITH_RAIL = new Set<string>(["INSTANT_BANK_PAYMENT", "DIGITAL_WALLET_P2P", "MOBILE_MONEY"]);

// Owed in full or in part, with nothing already waiting on the host to review.
const MARK_RECEIVED_STATUSES = new Set(["UPCOMING", "DUE", "OVERDUE", "REVERSED", "PARTIALLY_PAID"]);

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

type Tab = "overview" | "amounts-due" | "records" | "returns" | "instructions" | "listing-fees";
// REVERSED (a refund or lost chargeback sent the money back) and
// PARTIALLY_PAID (a remainder is still owed) are money the host is still due.
const OPEN_STATUSES = new Set([
  "UPCOMING", "DUE", "OVERDUE", "RECIPIENT_CONFIRMATION_PENDING", "PAYER_RECORDED", "DISPUTED", "REVERSED", "PARTIALLY_PAID",
]);

// See RentalPaymentsManager.tsx's own OBLIGATIONS_PAGE_LIMIT docstring --
// same reasoning: the Amounts due/Overview tabs need every open obligation
// up front, so a full page at the ceiling is fetched first; loadMoreObligations
// only ever appends further (older) history for the Records tab on top of it.
const OBLIGATIONS_PAGE_LIMIT = 100;

export function RecipientRentalPaymentsManager() {
  const { toast, showToast } = useToast();
  const [markingReceived, setMarkingReceived] = useState<RentalPaymentObligation | null>(null);
  const [obligations, setObligations] = useState<RentalPaymentObligation[]>([]);
  const [obligationsTotal, setObligationsTotal] = useState(0);
  const [hasMoreObligations, setHasMoreObligations] = useState(false);
  const [loadingMoreObligations, setLoadingMoreObligations] = useState(false);
  const [instructions, setInstructions] = useState<RentalPaymentInstruction[]>([]);
  const [listingFeePayments, setListingFeePayments] = useState<ListingFeePayment[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Tab>("overview");
  const [typeFilter, setTypeFilter] = useState<RentalPaymentObligationType | "ALL">("ALL");
  const [reviewing, setReviewing] = useState<{ record: RentalPaymentRecord; obligation: RentalPaymentObligation } | null>(null);
  const [decidingExcess, setDecidingExcess] = useState<{ record: RentalPaymentRecord; obligation: RentalPaymentObligation } | null>(null);

  function load() {
    Promise.all([
      listRecipientRentalPaymentObligations(undefined, { limit: OBLIGATIONS_PAGE_LIMIT, offset: 0 }),
      listMyRentalPaymentInstructions(),
      listMyListingFeePayments(),
    ])
      .then(([obligationsPage, i, lf]) => {
        setObligations(obligationsPage.items);
        setObligationsTotal(obligationsPage.total);
        setHasMoreObligations(obligationsPage.hasMore);
        setInstructions(i);
        setListingFeePayments(lf);
      })
      .catch((err) => showToast(errorMessage(err, "Could not load your payments."), "error"))
      .finally(() => setLoading(false));
  }

  function loadMoreObligations() {
    setLoadingMoreObligations(true);
    listRecipientRentalPaymentObligations(undefined, { limit: OBLIGATIONS_PAGE_LIMIT, offset: obligations.length })
      .then((page) => {
        setObligations((prev) => [...prev, ...page.items]);
        setHasMoreObligations(page.hasMore);
      })
      .catch((err) => showToast(errorMessage(err, "Could not load more payment records."), "error"))
      .finally(() => setLoadingMoreObligations(false));
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
  const awaitingConfirmationCount = obligations.filter((o) => o.status === "RECIPIENT_CONFIRMATION_PENDING").length;
  // ZR-PAY-METHODS-GLOBAL-001 Section 10: money received above what was
  // owed, waiting for the host to apply it elsewhere or send it back.
  const excessToDecide = useMemo(
    () =>
      obligations.flatMap((o) =>
        o.records.filter((r) => r.excessStatus === "REVIEW_REQUIRED").map((r) => ({ record: r, obligation: o }))
      ),
    [obligations]
  );
  const upcomingCount = obligations.filter((o) => o.status === "UPCOMING" || o.status === "DUE").length;

  const allRecords = useMemo(
    () =>
      filteredObligations
        .flatMap((o) => o.records.map((r) => ({ record: r, obligation: o })))
        .sort((a, b) => b.record.createdAt.localeCompare(a.record.createdAt)),
    [filteredObligations]
  );

  function openReview(obligation: RentalPaymentObligation) {
    const record = obligation.records[obligation.records.length - 1];
    if (record) setReviewing({ record, obligation });
  }

  if (loading) return <Loader label="Loading your payments" />;

  return (
    <div className="space-y-5">
      <div role="tablist" aria-label="Payments" className="flex flex-wrap gap-2 border-b border-slate-100 pb-3 dark:border-white/10">
        {(["overview", "amounts-due", "records", "returns", "instructions", "listing-fees"] as Tab[]).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`rounded-full px-4 py-2 text-sm font-semibold transition-colors ${
              tab === t
                ? "bg-primary-700 text-white"
                : "text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-white/5"
            }`}
          >
            {{
              overview: "Overview",
              "amounts-due": "Amounts due",
              records: "Payment records",
              returns: "Returns",
              instructions: "Instructions",
              "listing-fees": "Listing fees",
            }[t]}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <div className="grid gap-4 sm:grid-cols-3">
          <Card>
            <p className="text-xs text-slate-400">Awaiting confirmation</p>
            <p className="mt-1 font-heading text-2xl font-extrabold text-primary-900 dark:text-white">{awaitingConfirmationCount}</p>
          </Card>
          <Card>
            <p className="text-xs text-slate-400">Upcoming obligations</p>
            <p className="mt-1 font-heading text-2xl font-extrabold text-primary-900 dark:text-white">{upcomingCount}</p>
          </Card>
          <Card>
            <p className="text-xs text-slate-400">Listing fee receipts</p>
            <button onClick={() => setTab("listing-fees")} className="mt-1 text-sm font-semibold text-primary-700 hover:underline dark:text-primary-300">
              View
            </button>
          </Card>
          <div className="sm:col-span-3">
            <Button size="sm" onClick={() => setTab("amounts-due")}>
              Review payments awaiting confirmation
            </Button>
          </div>
        </div>
      )}

      {(tab === "overview" || tab === "amounts-due") && excessToDecide.length > 0 && (
        <Card className="ring-amber-200 dark:ring-amber-500/30">
          <SectionHeading
            title="Overpayments to decide"
            subtitle="You received more than was owed. Nothing is applied automatically -- apply the extra to another payment from the same renter, or record sending it back."
          />
          <ul className="mt-3 space-y-2">
            {excessToDecide.map(({ record, obligation }) => (
              <li key={record.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span>
                  <span className="font-semibold capitalize">{obligation.displayLabel}</span> payment #{record.id} ·{" "}
                  {formatMoney(record.excessRemaining, record.declaredCurrency)} extra to decide
                </span>
                <Button size="sm" onClick={() => setDecidingExcess({ record, obligation })}>Decide</Button>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {tab === "amounts-due" && (
        <div className="space-y-4">
          <SectionHeading title="Amounts due" subtitle="Rent and deposit obligations owed to you across your properties." />
          {openObligations.length === 0 ? (
            <Card>
              <EmptyState message="Nothing outstanding right now." />
            </Card>
          ) : (
            <div className="space-y-3">
              {openObligations.map((o) => (
                <Card key={o.id}>
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <p className="font-heading text-sm font-bold capitalize text-primary-900 dark:text-white">{o.displayLabel}</p>
                        <Badge tone={rentalPaymentStatusTone[o.status] ?? "neutral"}>{rentalPaymentStatusLabel[o.status] ?? o.status}</Badge>
                      </div>
                      <p className="mt-1 text-xs text-slate-400">
                        {formatMoney(o.amount, o.currency)}
                        {o.outstandingAmount > 0 && o.outstandingAmount < o.amount && (
                          <> ({formatMoney(o.outstandingAmount, o.currency)} remaining)</>
                        )}{" "}
                        &middot; Due {formatDate(o.dueDate)}
                      </p>
                    </div>
                    {(o.status === "RECIPIENT_CONFIRMATION_PENDING" || o.status === "PAYER_RECORDED" || o.status === "DISPUTED") && (
                      <Button size="sm" onClick={() => openReview(o)}>
                        Review
                      </Button>
                    )}
                    {MARK_RECEIVED_STATUSES.has(o.status) && o.payerAllocations.length === 0 && (
                      <Button size="sm" onClick={() => setMarkingReceived(o)}>
                        Record Payment
                      </Button>
                    )}
                  </div>
                </Card>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === "records" && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <SectionHeading title="Payment records" />
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
                          {record.excessStatus === "REVIEW_REQUIRED" && (
                            <Badge tone="warning" className="ml-1.5">Overpayment -- review required</Badge>
                          )}
                        </td>
                        <td className="px-5 py-3 text-right">
                          <button onClick={() => setReviewing({ record, obligation })} className="text-xs font-semibold text-primary-700 hover:underline dark:text-primary-300">
                            Open record
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
          {hasMoreObligations && (
            <div className="flex justify-center">
              <Button size="sm" variant="outline" loading={loadingMoreObligations} onClick={loadMoreObligations}>
                Load older records ({obligations.length} of {obligationsTotal})
              </Button>
            </div>
          )}
        </div>
      )}

      {tab === "returns" && <HostReturnsManager />}

      {tab === "instructions" && (
        <div className="space-y-6">
          <InstructionsManager instructions={instructions} onChanged={load} />
        </div>
      )}

      <ExcessDecisionModal
        entry={decidingExcess}
        obligations={obligations}
        onClose={() => setDecidingExcess(null)}
        onDone={(message) => {
          setDecidingExcess(null);
          showToast(message);
          load();
        }}
      />

      <MarkReceivedModal
        obligation={markingReceived}
        onClose={() => setMarkingReceived(null)}
        onRecorded={(updated, warning) => {
          setMarkingReceived(null);
          if (warning) showToast(warning, "error");
          else showToast(
            updated.status === "CONFIRMED"
              ? "Marked as received. The renter has been notified."
              : `Part payment recorded -- ${formatMoney(updated.outstandingAmount, updated.currency)} is still due.`,
          );
          load();
        }}
      />

      {tab === "listing-fees" && (
        <div className="space-y-4">
          <SectionHeading title="Listing fee receipts" subtitle="The only payment Zoiko Rooms collects for itself." />
          {listingFeePayments.length === 0 ? (
            <Card>
              <EmptyState message="No Listing Fee payments yet." />
            </Card>
          ) : (
            <div className="space-y-3">
              {listingFeePayments.map((p) => (
                <ListingFeePaymentRow key={p.id} payment={p} />
              ))}
            </div>
          )}
        </div>
      )}

      <ReviewModal
        entry={reviewing}
        onClose={() => setReviewing(null)}
        onDone={(message) => {
          setReviewing(null);
          showToast(message);
          load();
        }}
      />

      <Toast toast={toast} />
    </div>
  );
}

function ListingFeePaymentRow({ payment }: { payment: ListingFeePayment }) {
  const { toast, showToast } = useToast();
  const [downloading, setDownloading] = useState(false);
  const [refunds, setRefunds] = useState<ListingFeeRefund[]>([]);

  useEffect(() => {
    if (payment.status !== "SUCCEEDED") return;
    listListingFeeRefundsForPayment(payment.id)
      .then(setRefunds)
      .catch(() => setRefunds([]));
  }, [payment.id, payment.status]);

  async function handleDownload() {
    setDownloading(true);
    try {
      const blob = await downloadListingFeeReceipt(payment.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `listing-fee-receipt-${payment.id}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      showToast(errorMessage(err, "Could not download the receipt."), "error");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-primary-900 dark:text-white">Listing {payment.listingId}</p>
          <p className="mt-0.5 text-xs text-slate-400">{formatDate(payment.createdAt)} &middot; {formatMoney(payment.amount, payment.currency)}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={listingFeePaymentStatusTone[payment.status] ?? "neutral"}>{payment.status}</Badge>
          {payment.status === "SUCCEEDED" && payment.refundEligible && (
            <Badge tone="accent">Refund eligible</Badge>
          )}
          {payment.status === "SUCCEEDED" && (
            <Button size="sm" variant="outline" loading={downloading} onClick={handleDownload}>
              <Download className="h-3.5 w-3.5" aria-hidden="true" /> Receipt
            </Button>
          )}
        </div>
      </div>
      {refunds.length > 0 && (
        <div className="mt-3 space-y-1.5 border-t border-slate-100 pt-3 dark:border-white/10">
          {refunds.map((r) => (
            <div key={r.id} className="flex items-center justify-between gap-2 text-xs">
              <span className="text-slate-500 dark:text-slate-400">
                Refund {formatMoney(r.amount, r.currency)}
                {r.status === "FAILED" ? " -- didn't go through; Zoiko will retry it" : ""}
              </span>
              <span className="flex items-center gap-2">
                {(r.status === "REFUNDED" || r.status === "PARTIALLY_REFUNDED") && (
                  <button
                    type="button"
                    onClick={async () => {
                      try {
                        const url = URL.createObjectURL(await downloadListingFeeCreditNote(r.id));
                        const a = document.createElement("a");
                        a.href = url;
                        a.download = `${r.creditNoteNumber ?? `credit-note-${r.id}`}.pdf`;
                        a.click();
                        URL.revokeObjectURL(url);
                      } catch (err) {
                        showToast(errorMessage(err, "Could not download the credit note."), "error");
                      }
                    }}
                    className="font-semibold text-primary-700 hover:underline dark:text-primary-300"
                  >
                    Credit note
                  </button>
                )}
                <Badge tone={listingFeeRefundStatusTone[r.status] ?? "neutral"}>{r.status.replace(/_/g, " ")}</Badge>
              </span>
            </div>
          ))}
        </div>
      )}
      <Toast toast={toast} />
    </Card>
  );
}

function ReviewModal({
  entry,
  onClose,
  onDone,
}: {
  entry: { record: RentalPaymentRecord; obligation: RentalPaymentObligation } | null;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [disputing, setDisputing] = useState(false);
  const [partialAmount, setPartialAmount] = useState(false);
  const [confirmAmount, setConfirmAmount] = useState("");
  const [reason, setReason] = useState<RentalPaymentDiscrepancyReason>("NOT_ARRIVED");
  const [details, setDetails] = useState("");
  const [error, setError] = useState("");
  // Proof the host attaches to this record, and editing the host's own open report.
  const [evidenceKey, setEvidenceKey] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [editingDispute, setEditingDispute] = useState(false);

  useEffect(() => {
    setEditingDispute(false);
    setDisputing(false);
    setPartialAmount(false);
    setConfirmAmount(entry ? String(entry.record.declaredAmount) : "");
    setReason("NOT_ARRIVED");
    setDetails("");
    setError("");
  }, [entry]);

  if (!entry) return null;
  const { record, obligation } = entry;
  const canAct = record.status === "PAYER_RECORDED" || record.status === "DISPUTED";
  // The host's own still-open report on this payment -- editable until support resolves it.
  const ownOpenDispute = record.disputes.find((d) => d.status === "OPEN" && d.reportedByPartyId !== null) ?? null;

  async function handleUploadProof(file: File | undefined) {
    if (!file) return;
    setUploading(true);
    setError("");
    try {
      await uploadRentalPaymentEvidenceAsRecipient(record.id, file);
      setEvidenceKey((k) => k + 1);
    } catch (err) {
      setError(errorMessage(err, "Could not upload this file."));
    } finally {
      setUploading(false);
    }
  }

  async function handleUpdateDispute(e: FormEvent) {
    e.preventDefault();
    if (!ownOpenDispute) return;
    setConfirming(true);
    setError("");
    try {
      await updateOwnOpenRentalPaymentDispute(ownOpenDispute.id, { reasonCode: reason, details });
      onDone("Your report was updated.");
    } catch (err) {
      setError(errorMessage(err, "Could not update your report."));
    } finally {
      setConfirming(false);
    }
  }

  async function handleConfirm() {
    setConfirming(true);
    setError("");
    try {
      const amount = partialAmount ? Number(confirmAmount) : undefined;
      const updated = await confirmRentalPaymentReceipt(record.id, amount);
      const updatedRecord = updated.records[updated.records.length - 1];
      onDone(
        updatedRecord?.status === "PARTIALLY_PAID"
          ? `Confirmed receipt of ${formatMoney(Number(confirmAmount), record.declaredCurrency)} -- less than the amount declared.`
          : "Payment confirmed by the recipient."
      );
    } catch (err) {
      setError(errorMessage(err, "Could not confirm this payment."));
    } finally {
      setConfirming(false);
    }
  }

  async function handleReportDiscrepancy(e: FormEvent) {
    e.preventDefault();
    setConfirming(true);
    setError("");
    try {
      await reportRentalPaymentDiscrepancyAsRecipient(record.id, { reasonCode: reason, details });
      onDone("The record is now marked as disputed.");
    } catch (err) {
      setError(errorMessage(err, "Could not report this discrepancy."));
    } finally {
      setConfirming(false);
    }
  }

  return (
    <Modal open={Boolean(entry)} onClose={onClose} title={disputing ? "Report payment discrepancy" : "Payment awaiting confirmation"}>
      {error && <p role="alert" className="mb-3 rounded-xl bg-rose-50 px-4 py-2.5 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">{error}</p>}

      {!disputing ? (
        <div className="space-y-3 text-sm">
          <Row label="Purpose" value={obligation.displayLabel} />
          <Row label="Tenant reference" value={record.declaredByGuestId} />
          <Row label="Amount declared" value={formatMoney(record.declaredAmount, record.declaredCurrency)} />
          <Row label="Tenant marked paid" value={formatDateTime(record.createdAt)} />
          <Row label="Payment method" value={paymentMethodDisplay(record)} />
          {record.excessAmount > 0 && (
            <Row
              label="Paid above what was owed"
              value={`${formatMoney(record.excessAmount, record.declaredCurrency)} -- after you confirm, you decide whether it goes towards another payment or is sent back`}
            />
          )}
          {record.externalReference && <Row label="Transaction reference" value={record.externalReference} />}
          <RentalPaymentTimeline recordId={record.id} />
          <div>
            <span className="mb-1 block text-xs text-slate-400">Evidence</span>
            <RentalPaymentEvidenceList key={evidenceKey} recordId={record.id} />
            <label className="mt-2 inline-flex cursor-pointer items-center gap-1.5 text-xs font-semibold text-primary-700 hover:underline dark:text-primary-300">
              <input
                type="file" className="sr-only" disabled={uploading}
                accept="image/*,application/pdf"
                onChange={(e) => {
                  handleUploadProof(e.target.files?.[0]);
                  e.target.value = "";
                }}
              />
              {uploading ? "Uploading..." : "Add proof (e.g. bank statement screenshot)"}
            </label>
          </div>

          {ownOpenDispute && (
            <div className="rounded-xl bg-amber-50 px-4 py-3 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
              <p className="font-semibold">Your report is open with support</p>
              <p className="mt-0.5">
                {DISCREPANCY_OPTIONS.find((o) => o.value === ownOpenDispute.reasonCode)?.label ?? ownOpenDispute.reasonCode}
                {ownOpenDispute.details ? ` -- ${ownOpenDispute.details}` : ""}
              </p>
              {!editingDispute ? (
                <button
                  type="button"
                  onClick={() => {
                    setReason(ownOpenDispute.reasonCode);
                    setDetails(ownOpenDispute.details);
                    setEditingDispute(true);
                  }}
                  className="mt-1 font-semibold text-primary-700 hover:underline dark:text-primary-300"
                >
                  Edit report
                </button>
              ) : (
                <form onSubmit={handleUpdateDispute} className="mt-2 space-y-2">
                  <select
                    value={reason}
                    onChange={(e) => setReason(e.target.value as RentalPaymentDiscrepancyReason)}
                    className={inputClass}
                  >
                    {DISCREPANCY_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                  <textarea value={details} onChange={(e) => setDetails(e.target.value)} rows={2} className={inputClass} />
                  <div className="flex justify-end gap-2">
                    <Button type="button" size="sm" variant="ghost" onClick={() => setEditingDispute(false)}>Cancel</Button>
                    <Button type="submit" size="sm" loading={confirming}>Save</Button>
                  </div>
                </form>
              )}
            </div>
          )}

          <p className="flex items-start gap-1.5 pt-1 text-xs text-slate-400">
            <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" /> Confirmation means only that you confirm receipt of the
            stated payment. It does not certify legal sufficiency, satisfy a disputed obligation automatically, or make
            Zoiko Rooms the payment processor.
          </p>

          {canAct && (
            <>
              <label className="flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
                <input type="checkbox" checked={partialAmount} onChange={(e) => setPartialAmount(e.target.checked)} className="h-4 w-4" />
                I received a different amount
              </label>
              {partialAmount && (
                <Field label="Amount received *">
                  <input
                    type="number" step="0.01" min="0.01" max={record.declaredAmount}
                    value={confirmAmount} onChange={(e) => setConfirmAmount(e.target.value)} className={inputClass}
                  />
                </Field>
              )}
              <div className="flex justify-end gap-2 pt-2">
                <Button variant="ghost" onClick={() => setDisputing(true)}>
                  <FileWarning className="h-4 w-4" aria-hidden="true" /> Payment not received
                </Button>
                <Button loading={confirming} onClick={handleConfirm}>
                  <CheckCircle2 className="h-4 w-4" aria-hidden="true" /> Confirm receipt
                </Button>
              </div>
            </>
          )}
        </div>
      ) : (
        <form onSubmit={handleReportDiscrepancy} className="space-y-4">
          <div className="space-y-2">
            {DISCREPANCY_OPTIONS.map((opt) => (
              <label key={opt.value} className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
                <input type="radio" name="recipient-reason" checked={reason === opt.value} onChange={() => setReason(opt.value)} className="h-4 w-4" />
                {opt.label}
              </label>
            ))}
          </div>
          <Field label="Details" hint="Optional">
            <textarea value={details} onChange={(e) => setDetails(e.target.value)} rows={3} className={inputClass} />
          </Field>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={() => setDisputing(false)}>Back</Button>
            <Button type="submit" variant="accent" loading={confirming}>Report discrepancy</Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-slate-50 pb-2 dark:border-white/5">
      <span className="text-xs text-slate-400">{label}</span>
      <span className="text-right font-semibold text-slate-700 dark:text-slate-200">{value}</span>
    </div>
  );
}

/** ZR-PAY-METHODS-GLOBAL-001 Section 10: the host decides what happens to
 *  money received above what was owed -- applied to another open payment
 *  from the same renter, or sent back to them. Never automatic. */
function ExcessDecisionModal({
  entry,
  obligations,
  onClose,
  onDone,
}: {
  entry: { record: RentalPaymentRecord; obligation: RentalPaymentObligation } | null;
  obligations: RentalPaymentObligation[];
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const ids = useId();
  const [action, setAction] = useState<"apply" | "return">("apply");
  const [targetId, setTargetId] = useState("");
  const [amount, setAmount] = useState("");
  const [returnedDate, setReturnedDate] = useState("");
  const [method, setMethod] = useState<PaymentMethodValue>(EMPTY_PAYMENT_METHOD);
  const [methodErrors, setMethodErrors] = useState<PaymentMethodErrors>({});
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const record = entry?.record;
  const { catalog, error: catalogError } = usePaymentMethodCatalog(
    entry?.obligation.countryCode, "return_record", Boolean(entry) && action === "return"
  );

  const targets = useMemo(
    () =>
      entry
        ? obligations.filter(
            (o) =>
              o.id !== entry.obligation.id &&
              o.tenantGuestId === entry.obligation.tenantGuestId &&
              o.currency === entry.record.declaredCurrency &&
              MARK_RECEIVED_STATUSES.has(o.status) &&
              o.payerAllocations.length === 0 &&
              o.outstandingAmount > 0
          )
        : [],
    [entry, obligations]
  );

  useEffect(() => {
    if (!entry) return;
    setAction(targets.length > 0 ? "apply" : "return");
    setTargetId(targets[0] ? String(targets[0].id) : "");
    setAmount(String(entry.record.excessRemaining));
    setReturnedDate(new Date().toISOString().slice(0, 10));
    setMethod(EMPTY_PAYMENT_METHOD);
    setMethodErrors({});
    setNote("");
    setError("");
  }, [entry]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!record) return;
    const value = Number(amount);
    if (!Number.isFinite(value) || value <= 0 || value > record.excessRemaining) {
      setError(`Enter an amount up to ${formatMoney(record.excessRemaining, record.declaredCurrency)}.`);
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      if (action === "apply") {
        const target = targets.find((t) => String(t.id) === targetId);
        if (!target) {
          setError("Choose the payment to apply the extra to.");
          return;
        }
        if (value > target.outstandingAmount) {
          setError(`Only ${formatMoney(target.outstandingAmount, target.currency)} is owed on that payment.`);
          return;
        }
        await applyRentalPaymentExcess(record.id, { targetObligationId: target.id, amount: value, note });
        onDone(`Applied ${formatMoney(value, record.declaredCurrency)} to the ${target.displayLabel} due ${formatDate(target.dueDate)}.`);
      } else {
        const errors = validatePaymentMethod(catalog, method);
        setMethodErrors(errors);
        if (Object.keys(errors).length > 0) {
          setError("Please fix the highlighted fields.");
          return;
        }
        await returnRentalPaymentExcess(record.id, {
          amount: value,
          returnedDate,
          paymentMethodCategory: method.category as PaymentMethodCategory,
          paymentMethodSubtype: method.subtype,
          paymentMethodDescription: method.description,
          externalReference: method.reference,
          note,
        });
        onDone(`Recorded sending back ${formatMoney(value, record.declaredCurrency)}. The renter has been notified.`);
      }
    } catch (err) {
      setError(errorMessage(err, "Could not save this decision."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open={Boolean(entry)} onClose={onClose} size="lg" title="Decide what happens to the overpayment">
      {entry && record && (
        <form onSubmit={handleSubmit} noValidate className="space-y-4 text-sm">
          <p className="text-slate-600 dark:text-slate-300">
            Payment #{record.id} for the <span className="font-semibold">{entry.obligation.displayLabel}</span> was{" "}
            {formatMoney(record.excessAmount, record.declaredCurrency)} more than was owed.{" "}
            {formatMoney(record.excessRemaining, record.declaredCurrency)} is still to be decided.
          </p>
          {record.excessAllocations.length > 0 && (
            <ul className="list-disc space-y-0.5 pl-5 text-xs text-slate-500 dark:text-slate-400">
              {record.excessAllocations.map((a) => (
                <li key={a.id}>
                  {formatMoney(a.amount, a.currency)}{" "}
                  {a.action === "APPLY_TO_OBLIGATION"
                    ? `applied to payment #${a.targetObligationId}`
                    : `sent back${a.paymentMethodLabel ? ` (${a.paymentMethodLabel})` : ""}`}{" "}
                  on {formatDate(a.createdAt)}
                </li>
              ))}
            </ul>
          )}
          {error && <p role="alert" className="rounded-xl bg-rose-50 px-4 py-2.5 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">{error}</p>}

          <fieldset className="space-y-2">
            <legend className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">What should happen?</legend>
            <label className="flex items-start gap-2">
              <input type="radio" name={`${ids}-action`} checked={action === "apply"} disabled={targets.length === 0} onChange={() => setAction("apply")} className="mt-1" />
              <span>
                Apply it to another payment from this renter
                {targets.length === 0 && <span className="block text-xs text-slate-400">Nothing else is owed by this renter right now.</span>}
              </span>
            </label>
            <label className="flex items-start gap-2">
              <input type="radio" name={`${ids}-action`} checked={action === "return"} onChange={() => setAction("return")} className="mt-1" />
              <span>I sent it back to the renter</span>
            </label>
          </fieldset>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <FieldLabel htmlFor={`${ids}-amount`} text={`Amount (${record.declaredCurrency})`} required />
              <input id={`${ids}-amount`} type="number" min="0.01" step="0.01" max={record.excessRemaining} value={amount} onChange={(e) => setAmount(e.target.value)} className={inputClass} />
            </div>
            {action === "apply" ? (
              <div>
                <FieldLabel htmlFor={`${ids}-target`} text="Apply to" required />
                <select id={`${ids}-target`} value={targetId} onChange={(e) => setTargetId(e.target.value)} className={inputClass}>
                  {targets.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.displayLabel} due {formatDate(t.dueDate)} -- {formatMoney(t.outstandingAmount, t.currency)} owed
                    </option>
                  ))}
                </select>
              </div>
            ) : (
              <div>
                <FieldLabel htmlFor={`${ids}-date`} text="Date sent back" required />
                <input id={`${ids}-date`} type="date" value={returnedDate} max={new Date().toISOString().slice(0, 10)} onChange={(e) => setReturnedDate(e.target.value)} className={inputClass} />
              </div>
            )}
          </div>

          {action === "return" && (
            <PaymentMethodFields catalog={catalog} catalogError={catalogError} value={method} onChange={setMethod} errors={methodErrors} categoryLabel="Sent by" />
          )}

          <div>
            <FieldLabel htmlFor={`${ids}-note`} text="Note to the renter" optional />
            <textarea id={`${ids}-note`} value={note} maxLength={500} rows={2} onChange={(e) => setNote(e.target.value)} className={cn(inputClass, "resize-none")} />
          </div>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Zoiko Rooms did not process or hold this payment. This records your decision; the original payment is kept unchanged.
          </p>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
            <Button type="submit" loading={submitting}>Save decision</Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

/** ZR-PAY-METHODS-GLOBAL-001 host flow ("Record External Payment"): money
 *  the renter paid the host directly, recorded by the host as recipient. */
function MarkReceivedModal({
  obligation,
  onClose,
  onRecorded,
}: {
  obligation: RentalPaymentObligation | null;
  onClose: () => void;
  onRecorded: (updated: RentalPaymentObligation, warning?: string) => void;
}) {
  const ids = useId();
  const [amount, setAmount] = useState("");
  const [receivedDate, setReceivedDate] = useState("");
  const [method, setMethod] = useState<PaymentMethodValue>(EMPTY_PAYMENT_METHOD);
  const [note, setNote] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<PaymentMethodErrors & { amount?: string; date?: string }>({});
  const [duplicate, setDuplicate] = useState("");
  const [overpayment, setOverpayment] = useState("");
  const [acks, setAcks] = useState<SubmitAcks>({});
  const context = obligation?.obligationType === "DEPOSIT" ? "deposit_record" : "rent_record";
  const { catalog, error: catalogError } = usePaymentMethodCatalog(obligation?.countryCode, context, Boolean(obligation));
  const { rules } = resolveFieldRules(catalog, method);

  useEffect(() => {
    if (!obligation) return;
    setAmount(String(obligation.outstandingAmount));
    setReceivedDate(new Date().toISOString().slice(0, 10));
    setMethod(EMPTY_PAYMENT_METHOD);
    setNote("");
    setFile(null);
    setError("");
    setFieldErrors({});
    setDuplicate("");
    setOverpayment("");
    setAcks({});
  }, [obligation]);

  async function submit(next: SubmitAcks = {}) {
    if (!obligation) return;
    const flags = { ...acks, ...next };
    setAcks(flags);
    const errors: PaymentMethodErrors & { amount?: string; date?: string } = validatePaymentMethod(catalog, method);
    const value = Number(amount);
    if (!Number.isFinite(value) || value <= 0) errors.amount = "Enter the amount you received.";
    if (!receivedDate) errors.date = "Enter the date you received it.";
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) {
      setError("Please fix the highlighted fields.");
      return;
    }
    setSubmitting(true);
    setError("");
    setDuplicate("");
    setOverpayment("");
    try {
      const updated = await recordRentalPaymentReceiptAsRecipient(obligation.id, {
        amount: value,
        receivedDate,
        paymentMethodCategory: method.category as PaymentMethodCategory,
        paymentMethodSubtype: method.subtype,
        paymentMethodDescription: method.description,
        externalReference: method.reference,
        note,
        ...flags,
      });
      const newRecord = updated.records[updated.records.length - 1];
      let warning: string | undefined;
      if (file && newRecord) {
        try {
          await uploadRentalPaymentEvidenceAsRecipient(newRecord.id, file);
        } catch (err) {
          warning = `Payment recorded, but the evidence didn't upload: ${errorMessage(err, "please try again.")}`;
        }
      }
      onRecorded(updated, warning);
    } catch (err) {
      if (!handleSubmitWarning(err, setDuplicate, setOverpayment)) setError(errorMessage(err, "Could not record this payment."));
    } finally {
      setSubmitting(false);
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    void submit();
  }

  const errorList = Object.values(fieldErrors).filter(Boolean);

  return (
    <Modal open={Boolean(obligation)} onClose={onClose} size="2xl" title={`Record External Payment · ${obligation?.displayLabel ?? ""}`}>
      {obligation && (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
          <form onSubmit={handleSubmit} noValidate className="space-y-5">
            <p className="text-sm text-slate-600 dark:text-slate-300">
              Record the <span className="font-semibold">{obligation.displayLabel}</span> the renter paid you directly. Still owed:{" "}
              {formatMoney(obligation.outstandingAmount, obligation.currency)}.
            </p>
            {(error || errorList.length > 0) && (
              <div role="alert" className="rounded-xl bg-rose-50 px-4 py-2.5 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
                <p className="font-semibold">{error || "Please fix the highlighted fields."}</p>
                {errorList.length > 0 && (
                  <ul className="mt-1 list-disc pl-5 text-xs">
                    {errorList.map((m) => (
                      <li key={m}>{m}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
            {duplicate && <DuplicateWarning message={duplicate} submitting={submitting} onConfirm={() => void submit({ confirmNotDuplicate: true })} />}
            {overpayment && <OverpaymentWarning message={overpayment} submitting={submitting} onConfirm={() => void submit({ acknowledgeOverpayment: true })} />}

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <FieldLabel htmlFor={`${ids}-amount`} text="Amount received" required />
                <div className={cn("flex items-center rounded-xl bg-slate-50 ring-1 dark:bg-slate-800", fieldErrors.amount ? "ring-rose-400" : "ring-slate-200 dark:ring-slate-700")}>
                  <span className="pl-4 pr-1 text-slate-500" aria-hidden="true">{currencySymbol(obligation.currency)}</span>
                  <input
                    id={`${ids}-amount`}
                    type="number"
                    inputMode="decimal"
                    min="0.01"
                    step="0.01"
                    required
                    value={amount}
                    onChange={(e) => setAmount(e.target.value)}
                    aria-invalid={Boolean(fieldErrors.amount) || undefined}
                    aria-describedby={`${ids}-amount-help${fieldErrors.amount ? ` ${ids}-amount-err` : ""}`}
                    className="w-full rounded-xl bg-transparent px-3 py-2.5 text-sm text-slate-800 outline-none focus:ring-2 focus:ring-primary-400 dark:text-slate-100"
                  />
                </div>
                <p id={`${ids}-amount-help`} className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                  Less than what&apos;s owed records a part payment. Anything above it is held for you to apply or send back.
                </p>
                <FieldError id={`${ids}-amount-err`} message={fieldErrors.amount} />
              </div>
              <LockedCurrency currency={obligation.currency} />
              <div>
                <FieldLabel htmlFor={`${ids}-date`} text="Date received" required />
                <input
                  id={`${ids}-date`}
                  required
                  type="date"
                  value={receivedDate}
                  max={new Date().toISOString().slice(0, 10)}
                  onChange={(e) => setReceivedDate(e.target.value)}
                  aria-invalid={Boolean(fieldErrors.date) || undefined}
                  className={cn(inputClass, fieldErrors.date && "ring-rose-400")}
                />
                <FieldError id={`${ids}-date-err`} message={fieldErrors.date} />
              </div>
            </div>

            <PaymentMethodFields catalog={catalog} catalogError={catalogError} value={method} onChange={setMethod} errors={fieldErrors} />

            <div className="grid gap-4 sm:grid-cols-2">
              <EvidenceUpload file={file} onChange={setFile} helper={rules?.evidenceHelper || undefined} />
              <div>
                <FieldLabel htmlFor={`${ids}-note`} text="Notes" optional />
                <textarea
                  id={`${ids}-note`}
                  value={note}
                  maxLength={500}
                  rows={4}
                  onChange={(e) => setNote(e.target.value)}
                  aria-describedby={`${ids}-note-count`}
                  className={cn(inputClass, "resize-none")}
                />
                <p id={`${ids}-note-count`} className="mt-1 text-right text-xs text-slate-400">{note.length}/500</p>
              </div>
            </div>

            <ExternalPaymentNotice role="host" />

            <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-end">
              <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
              <Button type="submit" loading={submitting}>Record Payment</Button>
            </div>
          </form>
          <PaymentMethodGuidance catalog={catalog} className="self-start" />
        </div>
      )}
    </Modal>
  );
}

/** Add, verify and review the payment details tenants pay to -- also used on the host Payments page. */
export function InstructionsManager({ instructions, onChanged }: { instructions: RentalPaymentInstruction[]; onChanged: () => void }) {
  const { toast, showToast } = useToast();
  const [formOpen, setFormOpen] = useState(false);
  const [method, setMethod] = useState<RentalPaymentInstructionMethod>("BANK_TRANSFER");
  const [methodSubtype, setMethodSubtype] = useState("");
  const [recipientName, setRecipientName] = useState("");
  const [countryCode, setCountryCode] = useState("GB");
  const [bankDetails, setBankDetails] = useState<Record<string, string>>({});
  const [referenceFormat, setReferenceFormat] = useState("");
  const [additionalInstructions, setAdditionalInstructions] = useState("");
  const [authorizedRecipientConfirmed, setAuthorizedRecipientConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const bankFieldSchema = resolveBankFieldSchema(countryCode, method, methodSubtype);
  const needsCountry = method !== "CASH";
  const needsRail = METHODS_WITH_RAIL.has(method);
  const railLabelId = useId();
  const { catalog: railCatalog } = usePaymentMethodCatalog(countryCode === "OTHER" ? "" : countryCode, "rent_record", formOpen && needsRail);
  const railCategory = railCatalog?.categories.find((c) => c.code === method);
  const railOptions = (railCategory?.subtypes ?? []).map((s) => ({ value: s.code, label: s.label, hint: s.region || undefined }));

  const [confirmingId, setConfirmingId] = useState<number | null>(null);
  const [code, setCode] = useState("");
  const [confirmSubmitting, setConfirmSubmitting] = useState(false);
  const [confirmError, setConfirmError] = useState("");

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!authorizedRecipientConfirmed) {
      setError("Confirm that these instructions belong to the authorized recipient before saving.");
      return;
    }
    if (needsRail && !methodSubtype) {
      setError(`Choose the ${railCategory?.subtypeLabel.toLowerCase() ?? "payment network"}.`);
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const created = await submitRentalPaymentInstruction({
        method, methodSubtype: needsRail ? methodSubtype : "", recipientName, countryCode, bankDetails,
        authorizedRecipientConfirmed, referenceFormat, additionalInstructions,
      });
      setFormOpen(false);
      setRecipientName("");
      setBankDetails({});
      setReferenceFormat("");
      setAdditionalInstructions("");
      setAuthorizedRecipientConfirmed(false);
      showToast("We emailed you a verification code. Confirm it below to activate these details.");
      onChanged();
      setConfirmingId(created.id);
    } catch (err) {
      setError(errorMessage(err, "Could not save these payment instructions."));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleConfirmCode(e: FormEvent) {
    e.preventDefault();
    if (!confirmingId) return;
    setConfirmSubmitting(true);
    setConfirmError("");
    try {
      const confirmed = await confirmRentalPaymentInstruction(confirmingId, code);
      setConfirmingId(null);
      setCode("");
      if (confirmed.status === "COOLING_OFF") {
        showToast(
          `Code confirmed. For your security this change takes effect on ${
            confirmed.activatesAt ? new Date(confirmed.activatesAt).toLocaleString() : "in 72 hours"
          } — your current details stay active until then.`,
        );
      } else {
        showToast("Payment instructions are now active. Affected tenants have been notified.");
      }
      onChanged();
    } catch (err) {
      setConfirmError(errorMessage(err, "Incorrect or expired code."));
    } finally {
      setConfirmSubmitting(false);
    }
  }

  async function handleResend(instructionId: number) {
    try {
      await resendRentalPaymentInstructionCode(instructionId);
      showToast("A new code has been sent.");
    } catch (err) {
      showToast(errorMessage(err, "Could not resend the code."), "error");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SectionHeading
          title="Payment instructions"
          subtitle="Step-up verification required -- a new instruction only becomes active once you confirm the emailed code."
        />
        <Button size="sm" onClick={() => setFormOpen(true)}>Save changes</Button>
      </div>

      {instructions.length === 0 ? (
        <Card>
          <EmptyState message="No payment instructions set up yet." />
        </Card>
      ) : (
        <div className="space-y-3">
          {instructions.map((i) => (
            <Card key={i.id} className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">{i.recipientName}</p>
                  <Badge tone={rentalPaymentInstructionStatusTone[i.status] ?? "neutral"}>{i.status.replace(/_/g, " ")}</Badge>
                </div>
                <p className="mt-0.5 text-xs text-slate-400">
                  {[i.methodLabel || i.method, i.countryCode, i.accountIdentifierMasked].filter(Boolean).join(" · ")}
                </p>
                {(i.status === "COOLING_OFF" || i.status === "PENDING_REVIEW") && (
                  <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
                    Takes effect {i.activatesAt ? `on ${new Date(i.activatesAt).toLocaleString()}` : "after a short security wait"}
                    {i.highRiskReason ? ` (${i.highRiskReason})` : ""}. Your current details stay active until then. If you
                    didn&apos;t make this change, cancel it and change your password.
                  </p>
                )}
                {i.status === "CANCELLED" && (
                  <p className="mt-1 text-xs text-slate-500">You cancelled this change.</p>
                )}
                {i.status === "REJECTED" && (
                  <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">
                    This change didn&apos;t take effect{i.reviewReason ? `: ${i.reviewReason}` : "."}
                  </p>
                )}
              </div>
              {i.status === "COOLING_OFF" && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={async () => {
                    try {
                      await cancelRentalPaymentInstruction(i.id);
                      showToast("Change cancelled. Your current payment details stay active.");
                      onChanged();
                    } catch (err) {
                      showToast(errorMessage(err, "Could not cancel this change."), "error");
                    }
                  }}
                >
                  Cancel change
                </Button>
              )}
              {i.status === "PENDING_VERIFICATION" && (
                <div className="flex items-center gap-2">
                  <Button size="sm" variant="ghost" onClick={() => handleResend(i.id)}>Resend code</Button>
                  <Button size="sm" onClick={() => setConfirmingId(i.id)}>Enter code</Button>
                </div>
              )}
            </Card>
          ))}
        </div>
      )}

      <Modal open={formOpen} onClose={() => setFormOpen(false)} title="Manage payment instructions">
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && <p role="alert" className="rounded-xl bg-rose-50 px-4 py-2.5 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">{error}</p>}
          <Field label="Method *">
            <select
              value={method}
              onChange={(e) => {
                setMethod(e.target.value as RentalPaymentInstructionMethod);
                setMethodSubtype("");
                setBankDetails({});
              }}
              className={inputClass}
            >
              {METHOD_OPTIONS.map((m) => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
          </Field>
          <Field label="Recipient name *">
            <input required value={recipientName} onChange={(e) => setRecipientName(e.target.value)} className={inputClass} />
          </Field>
          {needsCountry && (
            <Field label="Country / region *">
              <select
                value={countryCode}
                onChange={(e) => {
                  setCountryCode(e.target.value);
                  setMethodSubtype("");
                  setBankDetails({});
                }}
                className={inputClass}
              >
                {COUNTRY_OPTIONS.map((c) => (
                  <option key={c.code} value={c.code}>{c.label}</option>
                ))}
              </select>
            </Field>
          )}
          {needsRail && (
            <div>
              <FieldLabel id={railLabelId} text={railCategory?.subtypeLabel ?? "Payment network"} required />
              {railOptions.length > 0 ? (
                <OptionCombobox
                  labelId={railLabelId}
                  options={railOptions}
                  value={methodSubtype}
                  onChange={(code) => {
                    setMethodSubtype(code);
                    setBankDetails({});
                  }}
                  placeholder="Select..."
                  required
                />
              ) : (
                <p className="text-xs text-slate-500">
                  {railCatalog ? "None is configured for this country -- choose another method." : "Loading..."}
                </p>
              )}
            </div>
          )}
          {bankFieldSchema.fields.map((field) => (
            <Field key={field.key} label={`${field.label} *`} hint={field.hint}>
              <input
                required
                value={bankDetails[field.key] ?? ""}
                onChange={(e) => setBankDetails((prev) => ({ ...prev, [field.key]: e.target.value }))}
                className={inputClass}
              />
            </Field>
          ))}
          <p className="text-xs text-slate-400">
            The tenant sees these details in full to pay you -- only the last 4 characters of the primary account
            field ever appear anywhere else (your own list above, admin review).
          </p>
          <Field label="Reference format" hint="Optional -- what reference tenants should use.">
            <input value={referenceFormat} onChange={(e) => setReferenceFormat(e.target.value)} className={inputClass} />
          </Field>
          <Field label="Additional instructions" hint="Optional -- anything else the tenant should know (e.g. payment timing, account name notes).">
            <textarea
              value={additionalInstructions}
              onChange={(e) => setAdditionalInstructions(e.target.value)}
              rows={3}
              className={inputClass}
            />
          </Field>
          <label className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-200">
            <input
              type="checkbox"
              required
              checked={authorizedRecipientConfirmed}
              onChange={(e) => setAuthorizedRecipientConfirmed(e.target.checked)}
              className="mt-0.5"
            />
            I confirm these instructions belong to the authorized recipient.
          </label>
          <p className="flex items-start gap-1.5 text-xs text-slate-400">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> Affected tenants will be notified that payment
            instructions changed and asked to review the new details before their next payment.
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={() => setFormOpen(false)}>Cancel</Button>
            <Button type="submit" loading={submitting}>Save changes</Button>
          </div>
        </form>
      </Modal>

      <Modal open={Boolean(confirmingId)} onClose={() => setConfirmingId(null)} title="Confirm payment instructions">
        <form onSubmit={handleConfirmCode} className="space-y-4">
          {confirmError && <p role="alert" className="rounded-xl bg-rose-50 px-4 py-2.5 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">{confirmError}</p>}
          <p className="text-sm text-slate-500 dark:text-slate-400">Enter the code we emailed you to activate these payment details.</p>
          <Field label="Verification code *">
            <input required value={code} onChange={(e) => setCode(e.target.value)} maxLength={6} className={inputClass} />
          </Field>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={() => setConfirmingId(null)}>Cancel</Button>
            <Button type="submit" loading={confirmSubmitting}>Confirm</Button>
          </div>
        </form>
      </Modal>

      <Toast toast={toast} />
    </div>
  );
}
