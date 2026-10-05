"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { AlertTriangle, CalendarClock, Copy, Download, FileWarning, Home, Upload } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { Card, EmptyState, Field, SectionHeading, Toast, inputClass, useToast } from "@/components/user/ui";
import { RentalPaymentEvidenceList } from "@/components/user/RentalPaymentEvidenceList";
import { resolveBankFieldSchema } from "@/lib/bankFieldSchemas";
import { DIRECT_RENT_PAYMENT_WORDING } from "@/components/user/DirectPaymentNotice";
import { HowToPay, downloadRentalPaymentReceipt, getHowToPay, payeeTypeLabel } from "@/lib/sublet-payments";
import { TenantReturnsSection } from "@/components/user/RentalPaymentReturns";
import { RentalPaymentTimeline } from "@/components/user/RentalPaymentTimeline";
import {
  RentalPaymentDiscrepancyReason,
  RentalPaymentMethodCategory,
  RentalPaymentObligation,
  RentalPaymentObligationType,
  RentalPaymentRecord,
} from "@/lib/types";
import { rentalPaymentStatusLabel, rentalPaymentStatusTone } from "@/lib/status";
import { formatDate, formatDateTime, formatMoney } from "@/lib/utils";
import {
  correctOwnRentalPaymentRecord,
  errorMessage,
  getMyRentalPaymentInstructions,
  getRentalPaymentObligationConnection,
  listMyRentalPaymentObligations,
  markRentalPaymentPaid,
  reportRentalPaymentDiscrepancyAsTenant,
  uploadRentalPaymentEvidence,
} from "@/lib/user-api";

const METHOD_OPTIONS: { value: RentalPaymentMethodCategory; label: string }[] = [
  { value: "BANK_TRANSFER", label: "Bank transfer" },
  { value: "UPI", label: "UPI" },
  { value: "CASH", label: "Cash" },
  { value: "OTHER", label: "Other" },
];

const METHOD_LABELS: Record<string, string> = {
  BANK_TRANSFER: "Bank transfer", UPI: "UPI", CASH: "Cash", OTHER: "Other",
};

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

// REVERSED: the earlier payment went back, so this is owed again.
// PARTIALLY_PAID: the remainder is still owed.
const OPEN_STATUSES = new Set(["UPCOMING", "DUE", "OVERDUE", "REVERSED", "PARTIALLY_PAID"]);

// GET /obligations is now paginated (ZR-PAY-LINK-003 Section 19/G11 -- an
// unbounded list doesn't scale to a years-long tenancy). This view's
// overview/upcoming tabs need every open obligation to compute "next
// obligation due" correctly, so a full page at the pagination ceiling is
// fetched up front rather than the ordinary page size; loadMoreObligations
// below only ever appends further (older) history for the Records tab, on
// top of that same shared list -- it never replaces it, since other tabs
// depend on everything already loaded staying present.
const OBLIGATIONS_PAGE_LIMIT = 100;

export function RentalPaymentsManager() {
  const { toast, showToast } = useToast();
  const [obligations, setObligations] = useState<RentalPaymentObligation[]>([]);
  const [obligationsTotal, setObligationsTotal] = useState(0);
  const [hasMoreObligations, setHasMoreObligations] = useState(false);
  const [loadingMoreObligations, setLoadingMoreObligations] = useState(false);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Tab>("overview");
  const [typeFilter, setTypeFilter] = useState<RentalPaymentObligationType | "ALL">("ALL");

  const [payingObligation, setPayingObligation] = useState<RentalPaymentObligation | null>(null);
  const [instructionsObligation, setInstructionsObligation] = useState<RentalPaymentObligation | null>(null);
  const [disputingRecord, setDisputingRecord] = useState<RentalPaymentRecord | null>(null);
  const [viewingRecord, setViewingRecord] = useState<RentalPaymentRecord | null>(null);

  function load() {
    listMyRentalPaymentObligations(undefined, { limit: OBLIGATIONS_PAGE_LIMIT, offset: 0 })
      .then((page) => {
        setObligations(page.items);
        setObligationsTotal(page.total);
        setHasMoreObligations(page.hasMore);
      })
      .catch((err) => showToast(errorMessage(err, "Could not load your rental payments."), "error"))
      .finally(() => setLoading(false));
  }

  function loadMoreObligations() {
    setLoadingMoreObligations(true);
    listMyRentalPaymentObligations(undefined, { limit: OBLIGATIONS_PAGE_LIMIT, offset: obligations.length })
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
          <TenantReturnsSection />
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
          {hasMoreObligations && (
            <div className="flex justify-center">
              <Button size="sm" variant="outline" loading={loadingMoreObligations} onClick={loadMoreObligations}>
                Load older records ({obligations.length} of {obligationsTotal})
              </Button>
            </div>
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
      <RecordDetailModal
        record={viewingRecord}
        onClose={() => setViewingRecord(null)}
        onCorrected={(message) => {
          setViewingRecord(null);
          showToast(message);
          load();
        }}
      />
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
  // Owed in full or in part -- the renter can record a direct payment they made.
  const canMarkPaid = ["UPCOMING", "DUE", "OVERDUE", "REVERSED", "PARTIALLY_PAID"].includes(obligation.status);
  const showRemaining = obligation.outstandingAmount > 0 && obligation.outstandingAmount < obligation.amount;

  // ZR-PAY-LINK-003 Section 3.1: self-contained per-card fetch, same shape
  // as InstructionsModal's own per-obligation load below -- lets a tenant
  // see (and never act on) a SUSPENDED connection instead of the card
  // silently offering payment actions that would 409 anyway.
  const [suspended, setSuspended] = useState(false);
  useEffect(() => {
    let cancelled = false;
    getRentalPaymentObligationConnection(obligation.id)
      .then((connection) => {
        if (!cancelled) setSuspended(connection.state === "SUSPENDED");
      })
      .catch(() => {
        // No linked room, or nothing to check -- never block the card on this.
      });
    return () => {
      cancelled = true;
    };
  }, [obligation.id]);

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
            {showRemaining && (
              <div>
                <dt className="text-xs text-slate-400">Remaining</dt>
                <dd className="font-semibold text-primary-900 dark:text-white">
                  {formatMoney(obligation.outstandingAmount, obligation.currency)}
                </dd>
              </div>
            )}
            {obligation.paymentReference && (
              <div>
                <dt className="text-xs text-slate-400">Reference to quote</dt>
                <dd className="font-mono text-xs text-slate-600 dark:text-slate-300">{obligation.paymentReference}</dd>
              </div>
            )}
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
            How to pay
          </Button>
          {canMarkPaid && (
            <Button size="sm" disabled={suspended} onClick={onPay}>
              I have made this payment
            </Button>
          )}
        </div>
      </div>
      {obligation.payerAllocations.length > 0 && <PayerAllocationsView obligation={obligation} />}
      {suspended && (
        <p className="mt-3 flex items-center gap-1.5 text-xs font-semibold text-rose-600 dark:text-rose-300">
          <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" /> Payments for this room are currently suspended --
          contact your landlord or agent before sending money.
        </p>
      )}
      <p className="mt-3 text-xs text-slate-400">
        {obligation.payeeType !== "LANDLORD_AGENT" && <>Paid to: {payeeTypeLabel[obligation.payeeType]}. </>}
        {DIRECT_RENT_PAYMENT_WORDING} Zoiko Rooms platform fee: {formatMoney(0, obligation.currency)}.
      </p>
    </Card>
  );
}

/** ZR-PAY-LINK-003 Section 15/Wireframe PAY-17: joint-tenancy payer
 *  allocation view -- read-only. Per Section 15, a co-tenant sees every
 *  payer's reconciled contribution status but can never alter another
 *  payer's own record, so this view has no actions at all. Each payer's
 *  contribution status is the latest of their own records (matched by
 *  declaredByGuestId), never a separate value stored on the allocation
 *  itself -- see RentalPaymentAllocation's own type docstring. */
function PayerAllocationsView({ obligation }: { obligation: RentalPaymentObligation }) {
  return (
    <div className="mt-3 space-y-1.5 border-t border-slate-100 pt-3 dark:border-white/10">
      <p className="text-xs font-semibold text-slate-500 dark:text-slate-400">Split between {obligation.payerAllocations.length} payers</p>
      {obligation.payerAllocations.map((allocation) => {
        const ownRecords = obligation.records
          .filter((r) => r.declaredByGuestId === allocation.payerGuestId)
          .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
        const latest = ownRecords[0] ?? null;
        return (
          <div key={allocation.id} className="flex items-center justify-between gap-2 text-xs">
            <span className="text-slate-500 dark:text-slate-400">
              {allocation.payerGuestId} — {formatMoney(allocation.allocatedAmount, obligation.currency)}
            </span>
            <Badge tone={latest ? rentalPaymentStatusTone[latest.status] ?? "neutral" : "neutral"}>
              {latest ? rentalPaymentStatusLabel[latest.status] ?? latest.status : "Not yet paid"}
            </Badge>
          </div>
        );
      })}
    </div>
  );
}

/** Self-contained, drop-in reuse of ObligationCard for any screen that has
 *  already found the single ZR-PAY-LINK-003 RentalPaymentObligation it
 *  wants to render payment actions for (the agreement-signing "Your offer"
 *  modal and the "My Rentals" dashboard both matched via
 *  src/lib/rentalPaymentMatch.ts, rather than duplicating
 *  ObligationCard/PayObligationModal/InstructionsModal/the Stripe-session-
 *  start handler a third and fourth time). onChanged is called after any
 *  action that could have changed the obligation's own status, so the
 *  caller can refetch. */
export function SingleObligationPaymentCard({ obligation, onChanged }: { obligation: RentalPaymentObligation; onChanged: () => void }) {
  const { toast, showToast } = useToast();
  const [paying, setPaying] = useState(false);
  const [showInstructions, setShowInstructions] = useState(false);

  return (
    <>
      <ObligationCard
        obligation={obligation}
        onPay={() => setPaying(true)}
        onViewInstructions={() => setShowInstructions(true)}
      />
      <PayObligationModal
        obligation={paying ? obligation : null}
        onClose={() => setPaying(false)}
        onRecorded={() => {
          setPaying(false);
          showToast("Payment recorded. We recorded that you marked this payment as made. The recipient may still need to confirm receipt.");
          onChanged();
        }}
      />
      <InstructionsModal obligation={showInstructions ? obligation : null} onClose={() => setShowInstructions(false)} />
      <Toast toast={toast} />
    </>
  );
}

/** ZR-SUBLET-PAY-003 Screens C/D/E: who receives this payment and why, the
 *  amount (Zoiko fee 0), the reference to quote, and the payee's own bank
 *  account or UPI ID -- the renter pays them directly. */
function InstructionsModal({ obligation, onClose }: { obligation: RentalPaymentObligation | null; onClose: () => void }) {
  const { toast, showToast } = useToast();
  const [view, setView] = useState<HowToPay | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!obligation) return;
    setLoading(true);
    setError("");
    setView(null);
    getHowToPay(obligation.id)
      .then(setView)
      .catch((err) => setError(errorMessage(err, "Payment details aren't available yet.")))
      .finally(() => setLoading(false));
  }, [obligation]);

  async function copy(text: string, what: string) {
    try {
      await navigator.clipboard.writeText(text);
      showToast(`${what} copied.`);
    } catch {
      showToast("Could not copy to clipboard.", "error");
    }
  }

  const schema = view?.details ? resolveBankFieldSchema(view.countryCode, view.method as RentalPaymentMethodCategory) : null;
  const detailRows = view?.details
    ? Object.entries(view.details).map(([key, value]) => ({ key, value, label: schema?.fields.find((f) => f.key === key)?.label ?? key }))
    : [];

  return (
    <Modal open={Boolean(obligation)} onClose={onClose} title="How to pay">
      {loading ? (
        <Loader label="Loading" />
      ) : error ? (
        <EmptyState message={`${error.replace(/\.$/, "")}. Please don't send any payment until details are available here.`} />
      ) : view ? (
        <div className="space-y-3 text-sm">
          <div className="rounded-xl bg-slate-50 px-4 py-3 dark:bg-slate-800">
            <span className="block text-xs text-slate-400">Pay to</span>
            <span className="font-semibold text-primary-900 dark:text-white">{view.payee.name}</span>
            <span className="ml-2 text-xs text-slate-500">{payeeTypeLabel[view.payee.type] ?? ""}</span>
            {view.payee.why && <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">Why this payee? {view.payee.why}</p>}
          </div>
          <Row label="Payment" value={view.label} />
          <Row label="Amount" value={formatMoney(view.outstanding, view.currency)} />
          <Row label="Due date" value={formatDate(view.dueDate)} />
          {view.periodStart && view.periodEnd && (
            <Row label="Period" value={`${formatDate(view.periodStart)} – ${formatDate(view.periodEnd)}`} />
          )}
          <Row label="Zoiko Rooms platform fee" value={formatMoney(view.platformFee, view.currency)} />
          <Row label="Total to pay" value={formatMoney(view.total, view.currency)} />
          <Row label="Accepted methods" value={view.acceptedMethods.map((m) => METHOD_LABELS[m] ?? m).join(", ")} />

          {view.blockedReason ? (
            <p role="alert" className="flex items-start gap-1.5 rounded-xl bg-amber-50 px-4 py-2.5 text-sm text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" /> {view.blockedReason}
            </p>
          ) : (
            <>
              <div className="flex items-center justify-between gap-3 rounded-xl bg-slate-50 px-4 py-2.5 dark:bg-slate-800">
                <div>
                  <span className="block text-xs text-slate-400">Reference to quote on your transfer</span>
                  <span className="font-mono font-semibold text-slate-700 dark:text-slate-200">{view.paymentReference}</span>
                </div>
                <button onClick={() => copy(view.paymentReference, "Reference")}
                        className="flex items-center gap-1 text-xs font-semibold text-primary-700 hover:underline dark:text-primary-300">
                  <Copy className="h-3.5 w-3.5" aria-hidden="true" /> Copy
                </button>
              </div>
              {view.custodian ? (
                <div className="rounded-xl bg-primary-50 px-4 py-3 dark:bg-primary-500/10">
                  <span className="text-xs font-bold uppercase tracking-wide text-primary-700 dark:text-primary-300">Deposit protection scheme</span>
                  <p className="mt-1 font-semibold">{view.custodian.name}</p>
                  {view.custodian.reference && <p className="text-xs">Reference: {view.custodian.reference}</p>}
                  <p className="mt-1 whitespace-pre-wrap">{view.custodian.instructions}</p>
                </div>
              ) : view.method === "CASH" || (!view.details && view.acceptedMethods.includes("CASH")) ? (
                <p className="rounded-xl bg-slate-50 px-4 py-2.5 text-sm text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                  Pay {view.payee.name} in cash and ask them to confirm it here.
                </p>
              ) : null}
              {detailRows.length > 0 && (
                <div className="rounded-xl bg-primary-50 px-4 py-3 dark:bg-primary-500/10">
                  <span className="text-xs font-bold uppercase tracking-wide text-primary-700 dark:text-primary-300">
                    {view.method === "UPI" ? "UPI ID" : "Bank account"}
                  </span>
                  <div className="mt-1.5 space-y-1.5">
                    {detailRows.map((row) => (
                      <div key={row.key} className="flex items-center justify-between gap-3">
                        <p className="text-sm font-medium text-slate-800 dark:text-slate-100">
                          <span className="text-slate-500 dark:text-slate-400">{row.label}:</span> {row.value}
                        </p>
                        <button onClick={() => copy(row.value, row.label)} aria-label={`Copy ${row.label}`}
                                className="flex items-center gap-1 text-xs font-semibold text-primary-700 hover:underline dark:text-primary-300">
                          <Copy className="h-3.5 w-3.5" aria-hidden="true" /> Copy
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {view.additionalInstructions && (
                <div className="rounded-xl bg-slate-50 px-4 py-3 dark:bg-slate-800">
                  <span className="text-xs font-bold uppercase tracking-wide text-slate-500 dark:text-slate-400">Notes</span>
                  <p className="mt-1.5 whitespace-pre-wrap text-sm text-slate-700 dark:text-slate-200">{view.additionalInstructions}</p>
                </div>
              )}
              {view.setupIncomplete && (
                <p className="rounded-xl bg-amber-50 px-4 py-2.5 text-sm text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
                  Payment setup incomplete -- the payee hasn&apos;t added confirmed bank or UPI details yet.
                </p>
              )}
            </>
          )}
          <p className="flex items-center gap-1.5 pt-2 text-xs font-semibold text-primary-700 dark:text-primary-300">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" /> {view.zoikoNotice} Check the payee before sending money.
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
    if (!obligation) return;
    let cancelled = false;
    // What's still owed (a part payment may already be recorded), paid by the
    // method the host listed -- both just defaults the renter can change.
    setAmount(String(obligation.outstandingAmount > 0 ? obligation.outstandingAmount : obligation.amount));
    setDate(new Date().toISOString().slice(0, 10));
    setMethod("BANK_TRANSFER");
    setReference("");
    setFile(null);
    setError("");
    getMyRentalPaymentInstructions(obligation.id)
      .then((instruction) => {
        if (!cancelled && METHOD_OPTIONS.some((m) => m.value === instruction.method)) {
          setMethod(instruction.method as RentalPaymentMethodCategory);
        }
      })
      .catch(() => {
        // No instructions available yet -- keep the default.
      });
    return () => {
      cancelled = true;
    };
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
          <Field label="Transaction reference" hint="Optional -- e.g. the bank UTR or UPI reference, so your host can match it">
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

function RecordDetailModal({
  record,
  onClose,
  onCorrected,
}: {
  record: RentalPaymentRecord | null;
  onClose: () => void;
  onCorrected: (message: string) => void;
}) {
  const [evidenceKey, setEvidenceKey] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [reference, setReference] = useState("");
  const [method, setMethod] = useState<RentalPaymentMethodCategory>("BANK_TRANSFER");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    setEditing(false);
    setError("");
    setNotice("");
    if (record) {
      setReference(record.externalReference);
      setMethod(record.paymentMethodCategory);
    }
  }, [record]);

  if (!record) return null;
  // Only while the host hasn't acted on it yet -- after that only support can correct it.
  const canCorrect = record.status === "PAYER_RECORDED";

  async function handleUpload(file: File | undefined) {
    if (!file || !record) return;
    setUploading(true);
    setError("");
    setNotice("");
    try {
      await uploadRentalPaymentEvidence(record.id, file);
      setEvidenceKey((k) => k + 1);
      setNotice("Proof added -- your host can see it now.");
    } catch (err) {
      setError(errorMessage(err, "Could not upload this file."));
    } finally {
      setUploading(false);
    }
  }

  async function handleSave(e: FormEvent) {
    e.preventDefault();
    if (!record) return;
    const changes: { fieldName: "external_reference" | "payment_method_category"; newValue: string }[] = [];
    if (reference.trim() !== record.externalReference) changes.push({ fieldName: "external_reference", newValue: reference.trim() });
    if (method !== record.paymentMethodCategory) changes.push({ fieldName: "payment_method_category", newValue: method });
    if (changes.length === 0) {
      setEditing(false);
      return;
    }
    setSaving(true);
    setError("");
    try {
      for (const change of changes) {
        await correctOwnRentalPaymentRecord(record.id, { ...change, reason: "Corrected by the renter" });
      }
      onCorrected("Payment details updated -- your host will see the corrected details.");
    } catch (err) {
      setError(errorMessage(err, "Could not update this payment."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal open={Boolean(record)} onClose={onClose} title="Payment record">
      <div className="space-y-3 text-sm">
        {error && <p role="alert" className="rounded-xl bg-rose-50 px-4 py-2.5 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">{error}</p>}
        {notice && <p className="rounded-xl bg-emerald-50 px-4 py-2.5 text-sm text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">{notice}</p>}
        <Row label="Status" value={rentalPaymentStatusLabel[record.status] ?? record.status} />
        <Row label="Confirmation source" value={record.provenance.replace(/_/g, " ").toLowerCase()} />
        <Row label="Amount declared" value={formatMoney(record.declaredAmount, record.declaredCurrency)} />
        <Row label="Date paid" value={formatDate(record.declaredDate)} />
        {!editing ? (
          <>
            <Row label="Payment method" value={METHOD_LABELS[record.paymentMethodCategory] ?? record.paymentMethodCategory} />
            <Row label="Transaction reference" value={record.externalReference || "--"} />
            {canCorrect && (
              <button
                type="button"
                onClick={() => setEditing(true)}
                className="text-xs font-semibold text-primary-700 hover:underline dark:text-primary-300"
              >
                Fix the method or reference
              </button>
            )}
          </>
        ) : (
          <form onSubmit={handleSave} className="space-y-3 rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
            <Field label="Payment method">
              <select value={method} onChange={(e) => setMethod(e.target.value as RentalPaymentMethodCategory)} className={inputClass}>
                {METHOD_OPTIONS.map((m) => (
                  <option key={m.value} value={m.value}>{m.label}</option>
                ))}
              </select>
            </Field>
            <Field label="Transaction reference" hint="e.g. the bank UTR or UPI reference">
              <input value={reference} onChange={(e) => setReference(e.target.value)} className={inputClass} />
            </Field>
            <div className="flex justify-end gap-2">
              <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
              <Button type="submit" size="sm" loading={saving}>Save</Button>
            </div>
          </form>
        )}
        <Row label="Marked paid at" value={formatDateTime(record.createdAt)} />
        {record.confirmedAt && <Row label="Confirmed at" value={formatDateTime(record.confirmedAt)} />}
        {(record.status === "CONFIRMED" || record.status === "PARTIALLY_PAID") && (
          <button
            type="button"
            onClick={() =>
              downloadRentalPaymentReceipt(record.id)
                .then((blob) => {
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = `receipt-${record.id}.pdf`;
                  a.click();
                  URL.revokeObjectURL(url);
                })
                .catch((err) => setError(errorMessage(err, "Could not download the receipt.")))
            }
            className="flex items-center gap-1.5 text-xs font-semibold text-primary-700 hover:underline dark:text-primary-300"
          >
            <Download className="h-3.5 w-3.5" aria-hidden="true" /> Download receipt
          </button>
        )}
        <RentalPaymentTimeline recordId={record.id} />
        <div>
          <span className="mb-1 block text-xs text-slate-400">Proof of payment</span>
          <RentalPaymentEvidenceList key={evidenceKey} recordId={record.id} />
          <label className="mt-2 inline-flex cursor-pointer items-center gap-1.5 text-xs font-semibold text-primary-700 hover:underline dark:text-primary-300">
            <Upload className="h-3.5 w-3.5" aria-hidden="true" />
            <input
              type="file" accept=".pdf,.jpg,.jpeg,.png" className="sr-only" disabled={uploading}
              onChange={(e) => {
                handleUpload(e.target.files?.[0]);
                e.target.value = "";
              }}
            />
            {uploading ? "Uploading..." : "Add proof (PDF, JPG or PNG)"}
          </label>
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
