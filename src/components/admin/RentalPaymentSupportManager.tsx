"use client";

import { useCallback, useEffect, useState } from "react";
import { LifeBuoy } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { ApiError, apiClientFetch } from "@/lib/api-client";
import { EvidenceArtifact, RentalPaymentEvidenceHold, RentalPaymentObligation, RentalPaymentRecord } from "@/lib/types";
import { rentalPaymentStatusLabel, rentalPaymentStatusTone } from "@/lib/status";
import { formatCurrency, formatDate } from "@/lib/utils";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const BASE = "/api/finance/rental-payments";

const METHOD_LABELS: Record<string, string> = {
  BANK_TRANSFER: "Bank transfer", UPI: "UPI", CASH: "Cash", CARD: "Card", OTHER: "Other",
};

// Record fields support may correct (crud/rental_payment.py:CORRECTABLE_RECORD_FIELDS).
const CORRECTABLE_FIELDS = [
  { value: "declared_amount", label: "Amount" },
  { value: "declared_date", label: "Date paid (YYYY-MM-DD)" },
  { value: "external_reference", label: "Transaction reference" },
  { value: "payment_method_category", label: "Method (BANK_TRANSFER, UPI, CASH, OTHER)" },
];

type Action =
  | { kind: "waive" | "cancel"; obligation: RentalPaymentObligation }
  | { kind: "confirm" | "reverse" | "correct"; record: RentalPaymentRecord }
  | { kind: "hold"; artifact: EvidenceArtifact };

const ACTION_TITLES: Record<Action["kind"], string> = {
  waive: "Waive this obligation",
  cancel: "Cancel this obligation",
  confirm: "Confirm this payment (support override)",
  reverse: "Reverse this payment",
  correct: "Correct this payment",
  hold: "Place a legal hold on this file",
};

const ACTION_HINTS: Record<Action["kind"], string> = {
  waive: "Nothing more is owed on it. Use when the host agreed to forgive it.",
  cancel: "It should never have existed (e.g. created in error).",
  confirm: "Only when the host genuinely can't act (e.g. lost access) -- recorded as a support correction, not the host's own confirmation.",
  reverse: "The money went back or never arrived after being confirmed -- the amount is owed again.",
  correct: "Fix a wrong detail on the payment. The old value is kept in its history.",
  hold: "Stops this file being deleted by retention rules (e.g. a legal case).",
};

function actionUrl(action: Action): string {
  switch (action.kind) {
    case "waive":
    case "cancel":
      return `${BASE}/obligations/${action.obligation.id}/${action.kind}`;
    case "hold":
      return `${BASE}/evidence/${action.artifact.id}/hold`;
    case "correct":
      return `${BASE}/records/${action.record.id}/corrections`;
    default:
      return `${BASE}/records/${action.record.id}/${action.kind}`;
  }
}

/** Support tools for rent payments that are otherwise backend-only: look an
 *  obligation up, act on it and its payments (waive, cancel, confirm,
 *  reverse, correct), see the proof on each payment and put it on legal
 *  hold, and run the hourly jobs on demand. Every action needs a reason and
 *  is audited. */
export function RentalPaymentSupportManager({ showToast }: { showToast: (message: string) => void }) {
  const [allowed, setAllowed] = useState(true);
  const [lookupId, setLookupId] = useState("");
  const [obligation, setObligation] = useState<RentalPaymentObligation | null>(null);
  const [evidence, setEvidence] = useState<Record<number, EvidenceArtifact[]>>({});
  const [holds, setHolds] = useState<Record<number, RentalPaymentEvidenceHold[]>>({});
  const [action, setAction] = useState<Action | null>(null);
  const [reason, setReason] = useState("");
  const [fieldName, setFieldName] = useState(CORRECTABLE_FIELDS[0].value);
  const [newValue, setNewValue] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [jobResults, setJobResults] = useState<Record<string, number | string> | null>(null);
  const [runningJobs, setRunningJobs] = useState(false);

  const loadEvidence = useCallback(async (records: RentalPaymentRecord[]) => {
    const entries = await Promise.all(
      records.map(async (r) => {
        const items = await apiClientFetch<EvidenceArtifact[]>(`${BASE}/records/${r.id}/evidence`).catch(() => []);
        return [r.id, items] as const;
      }),
    );
    setEvidence(Object.fromEntries(entries));
    const allArtifacts = entries.flatMap(([, items]) => items);
    const holdEntries = await Promise.all(
      allArtifacts.map(async (a) => {
        const items = await apiClientFetch<RentalPaymentEvidenceHold[]>(`${BASE}/evidence/${a.id}/holds`).catch(() => []);
        return [a.id, items] as const;
      }),
    );
    setHolds(Object.fromEntries(holdEntries));
  }, []);

  const loadObligation = useCallback(async (id: number) => {
    try {
      const data = await apiClientFetch<RentalPaymentObligation>(`${BASE}/obligations/${id}`);
      setObligation(data);
      loadEvidence(data.records);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) setAllowed(false);
      else showToast(err instanceof ApiError && err.status === 404 ? "No obligation with that ID" : "Failed to load that obligation");
      setObligation(null);
    }
  }, [loadEvidence, showToast]);

  useEffect(() => {
    // Only payment staff and super admins can use these tools.
    apiClientFetch(`${BASE}/disputes?status=`).catch((err) => {
      if (err instanceof ApiError && err.status === 403) setAllowed(false);
    });
  }, []);

  function open(next: Action) {
    setAction(next);
    setReason("");
    setFieldName(CORRECTABLE_FIELDS[0].value);
    setNewValue("");
  }

  async function submitAction(e: React.FormEvent) {
    e.preventDefault();
    if (!action || !reason.trim()) {
      showToast("A reason is required -- it's kept in the audit trail");
      return;
    }
    setSubmitting(true);
    try {
      const body = JSON.stringify(action.kind === "correct" ? { fieldName, newValue, reason: reason.trim() } : { reason: reason.trim() });
      await apiClientFetch(actionUrl(action), { method: "POST", body });
      showToast("Done -- recorded in the audit trail");
      setAction(null);
      if (obligation) loadObligation(obligation.id);
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : "That action failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function releaseHold(hold: RentalPaymentEvidenceHold) {
    try {
      await apiClientFetch(`${BASE}/evidence/holds/${hold.id}/release`, { method: "POST" });
      showToast("Hold released");
      if (obligation) loadEvidence(obligation.records);
    } catch {
      showToast("Failed to release the hold");
    }
  }

  async function openFile(recordId: number, artifact: EvidenceArtifact) {
    try {
      const res = await fetch(`${API_URL}${BASE}/records/${recordId}/evidence/${artifact.id}`, { credentials: "include" });
      if (!res.ok) throw new Error();
      const url = URL.createObjectURL(await res.blob());
      window.open(url, "_blank", "noopener");
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch {
      showToast("Could not open this file");
    }
  }

  async function runJobs() {
    setRunningJobs(true);
    try {
      setJobResults(await apiClientFetch<Record<string, number | string>>(`${BASE}/scheduled-jobs/run`, { method: "POST" }));
      showToast("Scheduled jobs ran");
    } catch (err) {
      showToast(err instanceof ApiError && err.status === 403 ? "Only a super admin can run the jobs" : "Failed to run the jobs");
    } finally {
      setRunningJobs(false);
    }
  }

  if (!allowed) return null;

  const canWaiveOrCancel = obligation && !["WAIVED", "CANCELLED"].includes(obligation.status);

  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <LifeBuoy className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
          <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Rent payment support</h2>
        </div>
        <Button size="sm" variant="outline" loading={runningJobs} onClick={runJobs}>
          Run scheduled jobs now
        </Button>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Look up a rent or deposit obligation by ID (shown in the disputes queue and on tenant/host records) to act on it.
      </p>
      {jobResults && (
        <p className="mt-2 rounded-xl bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
          {Object.entries(jobResults).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(" · ")}
        </p>
      )}

      <form
        className="mt-4 flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          const id = Number(lookupId);
          if (Number.isInteger(id) && id > 0) loadObligation(id);
        }}
      >
        <input
          value={lookupId} onChange={(e) => setLookupId(e.target.value)} inputMode="numeric" placeholder="Obligation ID"
          className="w-40 rounded-xl bg-slate-50 px-3 py-2 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
        />
        <Button type="submit" size="sm" variant="primary">Look up</Button>
      </form>

      {obligation && (
        <div className="mt-4 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-semibold capitalize text-primary-900 dark:text-white">
                  #{obligation.id} {obligation.displayLabel} · {formatCurrency(obligation.amount, obligation.currency)}
                </p>
                <Badge tone={rentalPaymentStatusTone[obligation.status] ?? "neutral"}>
                  {rentalPaymentStatusLabel[obligation.status] ?? obligation.status}
                </Badge>
              </div>
              <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                Due {formatDate(obligation.dueDate)} · still owed {formatCurrency(obligation.outstandingAmount, obligation.currency)}
                {" "}· tenant {obligation.tenantGuestId} · host party #{obligation.recipientPartyId}
              </p>
            </div>
            {canWaiveOrCancel && (
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => open({ kind: "waive", obligation })}>Waive</Button>
                <Button size="sm" variant="outline" onClick={() => open({ kind: "cancel", obligation })}>Cancel</Button>
              </div>
            )}
          </div>

          {obligation.records.length === 0 && <p className="text-sm text-slate-400">No payments recorded on it yet.</p>}
          {obligation.records.map((r) => (
            <div key={r.id} className="rounded-xl border border-slate-100 p-3 dark:border-white/10">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-semibold text-primary-900 dark:text-white">
                      Payment #{r.id} · {formatCurrency(r.declaredAmount, r.declaredCurrency)}
                    </p>
                    <Badge tone={rentalPaymentStatusTone[r.status] ?? "neutral"}>{rentalPaymentStatusLabel[r.status] ?? r.status}</Badge>
                  </div>
                  <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                    {METHOD_LABELS[r.paymentMethodCategory] ?? r.paymentMethodCategory} on {formatDate(r.declaredDate)}
                    {r.externalReference ? ` · ref ${r.externalReference}` : ""} · {r.provenance.replace(/_/g, " ").toLowerCase()}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {(r.status === "PAYER_RECORDED" || r.status === "DISPUTED") && (
                    <Button size="sm" variant="outline" onClick={() => open({ kind: "confirm", record: r })}>Confirm</Button>
                  )}
                  {r.status === "CONFIRMED" && (
                    <Button size="sm" variant="outline" onClick={() => open({ kind: "reverse", record: r })}>Reverse</Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => open({ kind: "correct", record: r })}>Correct</Button>
                </div>
              </div>
              <div className="mt-2 space-y-1">
                {(evidence[r.id] ?? []).map((a) => {
                  const active = (holds[a.id] ?? []).find((h) => h.status === "ACTIVE");
                  return (
                    <div key={a.id} className="flex flex-wrap items-center justify-between gap-2 text-xs">
                      <button type="button" onClick={() => openFile(r.id, a)} className="font-semibold text-primary-700 hover:underline dark:text-primary-300">
                        {a.originalFilename}
                      </button>
                      {active ? (
                        <span className="flex items-center gap-2 text-amber-700 dark:text-amber-300">
                          On legal hold ({active.reason})
                          <button type="button" onClick={() => releaseHold(active)} className="font-semibold hover:underline">Release</button>
                        </span>
                      ) : (
                        <button type="button" onClick={() => open({ kind: "hold", artifact: a })} className="text-slate-500 hover:underline">
                          Place legal hold
                        </button>
                      )}
                    </div>
                  );
                })}
                {(evidence[r.id] ?? []).length === 0 && <p className="text-xs text-slate-400">No proof attached.</p>}
              </div>
            </div>
          ))}
        </div>
      )}

      <Modal open={Boolean(action)} onClose={() => setAction(null)} title={action ? ACTION_TITLES[action.kind] : ""}>
        {action && (
          <form onSubmit={submitAction} className="space-y-3.5">
            <p className="text-xs text-slate-500 dark:text-slate-400">{ACTION_HINTS[action.kind]}</p>
            {action.kind === "correct" && (
              <>
                <select
                  value={fieldName} onChange={(e) => setFieldName(e.target.value)}
                  className="w-full rounded-xl bg-slate-50 px-3 py-2 text-sm ring-1 ring-slate-200 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                >
                  {CORRECTABLE_FIELDS.map((f) => (
                    <option key={f.value} value={f.value}>{f.label}</option>
                  ))}
                </select>
                <input
                  value={newValue} onChange={(e) => setNewValue(e.target.value)} placeholder="New value" required
                  className="w-full rounded-xl bg-slate-50 px-3 py-2 text-sm ring-1 ring-slate-200 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                />
              </>
            )}
            <textarea
              value={reason} onChange={(e) => setReason(e.target.value)} rows={3} required placeholder="Reason (kept in the audit trail)"
              className="w-full resize-none rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setAction(null)}>Cancel</Button>
              <Button type="submit" variant="primary" loading={submitting}>Confirm</Button>
            </div>
          </form>
        )}
      </Modal>
    </section>
  );
}
