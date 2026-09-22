"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Download, FileWarning, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { Card, EmptyState, Field, SectionHeading, Toast, inputClass, useToast } from "@/components/user/ui";
import { RentalPaymentEvidenceList } from "@/components/user/RentalPaymentEvidenceList";
import {
  ListingFeePayment,
  ListingFeeRefund,
  RentalPaymentDiscrepancyReason,
  RentalPaymentInstruction,
  RentalPaymentMethodCategory,
  RentalPaymentObligation,
  RentalPaymentObligationType,
  RentalPaymentProviderAccount,
  RentalPaymentRecord,
} from "@/lib/types";
import {
  listingFeePaymentStatusTone,
  listingFeeRefundStatusTone,
  rentalPaymentInstructionStatusTone,
  rentalPaymentStatusLabel,
  rentalPaymentStatusTone,
} from "@/lib/status";
import { formatDate, formatDateTime, formatMoney } from "@/lib/utils";
import {
  confirmRentalPaymentInstruction,
  confirmRentalPaymentProviderAccountChange,
  confirmRentalPaymentReceipt,
  connectRentalPaymentProviderAccount,
  downloadListingFeeReceipt,
  errorMessage,
  getRentalPaymentProviderAccount,
  listListingFeeRefundsForPayment,
  listMyListingFeePayments,
  listMyRentalPaymentInstructions,
  listRecipientRentalPaymentObligations,
  refreshRentalPaymentProviderAccount,
  reportRentalPaymentDiscrepancyAsRecipient,
  requestRentalPaymentProviderAccountChange,
  resendRentalPaymentInstructionCode,
  resendRentalPaymentProviderAccountChangeCode,
  simulateRentalPaymentProviderAccountOnboardingComplete,
  submitRentalPaymentInstruction,
} from "@/lib/user-api";
import { ApiError } from "@/lib/api-client";

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

type Tab = "overview" | "amounts-due" | "records" | "instructions" | "listing-fees";
const OPEN_STATUSES = new Set(["UPCOMING", "DUE", "OVERDUE", "RECIPIENT_CONFIRMATION_PENDING", "PAYER_RECORDED", "DISPUTED"]);

export function RecipientRentalPaymentsManager() {
  const { toast, showToast } = useToast();
  const [obligations, setObligations] = useState<RentalPaymentObligation[]>([]);
  const [instructions, setInstructions] = useState<RentalPaymentInstruction[]>([]);
  const [listingFeePayments, setListingFeePayments] = useState<ListingFeePayment[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Tab>("overview");
  const [typeFilter, setTypeFilter] = useState<RentalPaymentObligationType | "ALL">("ALL");
  const [reviewing, setReviewing] = useState<{ record: RentalPaymentRecord; obligation: RentalPaymentObligation } | null>(null);

  function load() {
    Promise.all([listRecipientRentalPaymentObligations(), listMyRentalPaymentInstructions(), listMyListingFeePayments()])
      .then(([o, i, lf]) => {
        setObligations(o);
        setInstructions(i);
        setListingFeePayments(lf);
      })
      .catch((err) => showToast(errorMessage(err, "Could not load your payments."), "error"))
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
  const awaitingConfirmationCount = obligations.filter((o) => o.status === "RECIPIENT_CONFIRMATION_PENDING").length;
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
        {(["overview", "amounts-due", "records", "instructions", "listing-fees"] as Tab[]).map((t) => (
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
                        {formatMoney(o.amount, o.currency)} &middot; Due {formatDate(o.dueDate)}
                      </p>
                    </div>
                    {(o.status === "RECIPIENT_CONFIRMATION_PENDING" || o.status === "PAYER_RECORDED" || o.status === "DISPUTED") && (
                      <Button size="sm" onClick={() => openReview(o)}>
                        Review
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
        </div>
      )}

      {tab === "instructions" && (
        <div className="space-y-6">
          <ProviderAccountManager />
          <InstructionsManager instructions={instructions} onChanged={load} />
        </div>
      )}

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
            <div key={r.id} className="flex items-center justify-between text-xs">
              <span className="text-slate-500 dark:text-slate-400">
                Refund {formatMoney(r.amount, r.currency)}
                {r.status === "FAILED" ? " -- retained internally, contact support" : ""}
              </span>
              <Badge tone={listingFeeRefundStatusTone[r.status] ?? "neutral"}>{r.status.replace(/_/g, " ")}</Badge>
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

  useEffect(() => {
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
          <Row label="Payment method" value={record.paymentMethodCategory.replace(/_/g, " ").toLowerCase()} />
          {record.externalReference && <Row label="External reference" value={record.externalReference} />}
          <div>
            <span className="mb-1 block text-xs text-slate-400">Evidence</span>
            <RentalPaymentEvidenceList recordId={record.id} />
          </div>

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

/** ZR-PAY-LINK-003 Section 6/Wireframe C: the online-payment rail's own
 *  destination, alongside InstructionsManager's direct-instruction one
 *  below -- "Manual bank transfer and provider-hosted digital payment are
 *  two rails over the same relationship" (Section 1.1). */
function ProviderAccountManager() {
  const { toast, showToast } = useToast();
  const [loading, setLoading] = useState(true);
  const [account, setAccount] = useState<RentalPaymentProviderAccount | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [simulating, setSimulating] = useState(false);
  const [email, setEmail] = useState("");
  const [country, setCountry] = useState("GB");
  const [changingAccount, setChangingAccount] = useState(false);

  function load() {
    setLoading(true);
    getRentalPaymentProviderAccount()
      .then(setAccount)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 404) {
          setAccount(null);
        } else {
          showToast(errorMessage(err, "Could not load your payment account."), "error");
        }
      })
      .finally(() => setLoading(false));
  }

  useEffect(load, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleConnect() {
    if (!email.trim()) {
      showToast("Enter the email Stripe should use for this account.", "error");
      return;
    }
    setConnecting(true);
    try {
      const result = await connectRentalPaymentProviderAccount({ country, email: email.trim() });
      setAccount(result.account);
      if (result.onboardingUrl) {
        window.location.href = result.onboardingUrl;
      }
    } catch (err) {
      showToast(errorMessage(err, "Could not connect a payment account."), "error");
    } finally {
      setConnecting(false);
    }
  }

  async function handleRefresh() {
    setRefreshing(true);
    try {
      setAccount(await refreshRentalPaymentProviderAccount());
    } catch (err) {
      showToast(errorMessage(err, "Could not refresh your account status."), "error");
    } finally {
      setRefreshing(false);
    }
  }

  async function handleSimulate() {
    setSimulating(true);
    try {
      setAccount(await simulateRentalPaymentProviderAccountOnboardingComplete());
      showToast("Onboarding marked complete (dev/test only).");
    } catch (err) {
      showToast(errorMessage(err, "Could not simulate onboarding completion."), "error");
    } finally {
      setSimulating(false);
    }
  }

  if (loading) return <Loader label="Loading payment account" />;

  return (
    <Card>
      <SectionHeading title="Secure online payment" subtitle="Connect a Stripe account so tenants can pay you directly online." />
      {!account ? (
        <div className="mt-3 space-y-3">
          <Field label="Country">
            <input value={country} onChange={(e) => setCountry(e.target.value.toUpperCase())} className={inputClass} maxLength={2} />
          </Field>
          <Field label="Email for this Stripe account">
            <input value={email} onChange={(e) => setEmail(e.target.value)} className={inputClass} type="email" />
          </Field>
          <Button size="sm" loading={connecting} onClick={handleConnect}>
            Connect payment account
          </Button>
        </div>
      ) : (
        <div className="mt-3 space-y-3">
          <div className="flex items-center gap-2">
            <Badge tone={account.status === "COMPLETE" ? "success" : "warning"}>
              {account.status === "COMPLETE" ? "Connected" : "Onboarding incomplete"}
            </Badge>
            {account.chargesEnabled && <span className="text-xs text-slate-500 dark:text-slate-400">Ready to accept payments</span>}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" loading={refreshing} onClick={handleRefresh}>
              Refresh status
            </Button>
            {account.status !== "COMPLETE" && (
              <Button size="sm" variant="ghost" loading={simulating} onClick={handleSimulate}>
                Simulate onboarding complete (dev)
              </Button>
            )}
            {account.status === "COMPLETE" && !changingAccount && (
              <Button size="sm" variant="ghost" onClick={() => setChangingAccount(true)}>
                Change account
              </Button>
            )}
          </div>
          {changingAccount && (
            <ProviderAccountChangeForm
              onCancel={() => setChangingAccount(false)}
              onChanged={(updated) => {
                setChangingAccount(false);
                setAccount(updated);
                showToast("Payment account changed -- complete onboarding for the new account.");
              }}
            />
          )}
        </div>
      )}
      <Toast toast={toast} />
    </Card>
  );
}

/** ZR-PAY-LINK-003 Section 14.1: the step-up confirmation for a payment
 *  account CHANGE -- same shape as PaymentRecipientSetup.tsx's own
 *  ConfirmChangeForm, for the sibling destination-change governance flow. */
function ProviderAccountChangeForm({
  onCancel,
  onChanged,
}: {
  onCancel: () => void;
  onChanged: (account: RentalPaymentProviderAccount) => void;
}) {
  const [requested, setRequested] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [code, setCode] = useState("");
  const [newCountry, setNewCountry] = useState("GB");
  const [newEmail, setNewEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [resending, setResending] = useState(false);
  const [resent, setResent] = useState(false);
  const [error, setError] = useState("");

  async function handleRequest() {
    setRequesting(true);
    setError("");
    try {
      await requestRentalPaymentProviderAccountChange();
      setRequested(true);
    } catch (err) {
      setError(errorMessage(err, "Could not request this change."));
    } finally {
      setRequesting(false);
    }
  }

  async function handleResend() {
    setResending(true);
    try {
      await resendRentalPaymentProviderAccountChangeCode();
      setResent(true);
    } catch (err) {
      setError(errorMessage(err, "Could not resend the code."));
    } finally {
      setResending(false);
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (!newEmail.trim()) {
      setError("Enter the email the new Stripe account should use.");
      return;
    }
    setSubmitting(true);
    try {
      const result = await confirmRentalPaymentProviderAccountChange({
        code: code.trim(), country: newCountry, email: newEmail.trim(),
      });
      if (result.onboardingUrl) {
        window.location.href = result.onboardingUrl;
        return;
      }
      onChanged(result.account);
    } catch (err) {
      setError(errorMessage(err, "Could not confirm this change."));
    } finally {
      setSubmitting(false);
    }
  }

  if (!requested) {
    return (
      <Card className="!bg-amber-50 !ring-amber-200 dark:!bg-amber-500/10 dark:!ring-amber-500/20">
        <p className="text-xs text-slate-600 dark:text-slate-300">
          We&apos;ll email a confirmation code to your account before you can connect a new payment account.
        </p>
        {error && <p className="mt-2 text-xs text-rose-600 dark:text-rose-300">{error}</p>}
        <div className="mt-3 flex gap-2">
          <Button size="sm" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button size="sm" loading={requesting} onClick={handleRequest}>
            Send confirmation code
          </Button>
        </div>
      </Card>
    );
  }

  return (
    <Card className="!bg-amber-50 !ring-amber-200 dark:!bg-amber-500/10 dark:!ring-amber-500/20">
      <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">Confirm this change</p>
      <form onSubmit={handleSubmit} className="mt-3 space-y-3">
        {error && (
          <p role="alert" className="rounded-xl bg-rose-50 px-4 py-2.5 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
            {error}
          </p>
        )}
        <Field label="Confirmation code *">
          <input
            required value={code} onChange={(e) => setCode(e.target.value)} className={inputClass}
            inputMode="numeric" maxLength={6} autoComplete="one-time-code"
          />
        </Field>
        <Field label="Country for the new account">
          <input value={newCountry} onChange={(e) => setNewCountry(e.target.value.toUpperCase())} className={inputClass} maxLength={2} />
        </Field>
        <Field label="Email for the new account">
          <input required value={newEmail} onChange={(e) => setNewEmail(e.target.value)} className={inputClass} type="email" />
        </Field>
        <div className="flex items-center justify-between gap-2">
          <Button type="button" variant="ghost" size="sm" loading={resending} onClick={handleResend}>
            {resent ? "Code resent" : "Resend code"}
          </Button>
          <div className="flex gap-2">
            <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
              Cancel
            </Button>
            <Button type="submit" size="sm" loading={submitting}>
              Confirm change
            </Button>
          </div>
        </div>
      </form>
    </Card>
  );
}

function InstructionsManager({ instructions, onChanged }: { instructions: RentalPaymentInstruction[]; onChanged: () => void }) {
  const { toast, showToast } = useToast();
  const [formOpen, setFormOpen] = useState(false);
  const [method, setMethod] = useState<RentalPaymentMethodCategory>("BANK_TRANSFER");
  const [recipientName, setRecipientName] = useState("");
  const [accountIdentifier, setAccountIdentifier] = useState("");
  const [referenceFormat, setReferenceFormat] = useState("");
  const [additionalInstructions, setAdditionalInstructions] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const [confirmingId, setConfirmingId] = useState<number | null>(null);
  const [code, setCode] = useState("");
  const [confirmSubmitting, setConfirmSubmitting] = useState(false);
  const [confirmError, setConfirmError] = useState("");

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const created = await submitRentalPaymentInstruction({
        method, recipientName, accountIdentifier, referenceFormat, additionalInstructions,
      });
      setFormOpen(false);
      setRecipientName("");
      setAccountIdentifier("");
      setReferenceFormat("");
      setAdditionalInstructions("");
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
      if (confirmed.status === "PENDING_REVIEW") {
        showToast("Code confirmed. This change was flagged for review and needs admin approval before it becomes active.");
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
                  {i.method.replace(/_/g, " ").toLowerCase()} &middot; {i.accountIdentifierMasked}
                </p>
                {i.status === "PENDING_REVIEW" && (
                  <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
                    Flagged for admin review{i.highRiskReason ? ` (${i.highRiskReason})` : ""} -- the previous instructions remain active until this is approved.
                  </p>
                )}
                {i.status === "REJECTED" && (
                  <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">
                    This change was rejected on review{i.reviewReason ? `: ${i.reviewReason}` : "."}
                  </p>
                )}
              </div>
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
            <select value={method} onChange={(e) => setMethod(e.target.value as RentalPaymentMethodCategory)} className={inputClass}>
              {METHOD_OPTIONS.map((m) => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
          </Field>
          <Field label="Recipient name *">
            <input required value={recipientName} onChange={(e) => setRecipientName(e.target.value)} className={inputClass} />
          </Field>
          <Field label="Account / payment ID *" hint="Only the last 4 characters are ever stored or shown to tenants.">
            <input required minLength={4} value={accountIdentifier} onChange={(e) => setAccountIdentifier(e.target.value)} className={inputClass} />
          </Field>
          <Field label="Reference format" hint="Optional -- what reference tenants should use.">
            <input value={referenceFormat} onChange={(e) => setReferenceFormat(e.target.value)} className={inputClass} />
          </Field>
          <Field label="Additional instructions" hint="Optional">
            <textarea value={additionalInstructions} onChange={(e) => setAdditionalInstructions(e.target.value)} rows={2} className={inputClass} />
          </Field>
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
