"use client";

import { useCallback, useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Card, EmptyState, Field, SectionHeading, inputClass } from "@/components/user/ui";
import type { ToastTone } from "@/components/user/ui";
import type { DepositClaim, DepositClaimItem, DepositRecord } from "@/lib/types";
import {
  DEPOSIT_CLAIM_CATEGORIES,
  attachHostDepositClaimEvidence,
  depositEvidenceUrl,
  errorMessage,
  listHostDepositClaims,
  listHostDeposits,
  listMyDepositClaims,
  listMyDeposits,
  recordDepositSchemeDecision,
  respondToDepositClaimItem,
  reviseHostDepositClaimItem,
  settleHostDeposit,
  submitHostDepositClaim,
} from "@/lib/user-api";
import { formatCurrency, formatDate } from "@/lib/utils";

/** Deposits after a tenancy, settled between host and renter with no admin:
 *  the host claims within the claim window, the renter answers each item
 *  (silence = accepted), a disputed item is lowered by the host or decided by
 *  the deposit scheme (either side records it), and the deposit settles on
 *  its own once the window closes. Shared by the host and renter screens. */

type Side = "host" | "renter";

const RECORD_TONE: Record<DepositRecord["status"], "primary" | "success" | "warning" | "neutral" | "danger"> = {
  HELD: "primary",
  PARTIALLY_RELEASED: "primary",
  FROZEN: "warning",
  RELEASED: "success",
  FORFEITED: "neutral",
};

const CLAIM_TONE: Record<DepositClaim["status"], "primary" | "success" | "warning" | "neutral" | "danger"> = {
  RENTER_RESPONSE_PENDING: "warning",
  AGREED: "success",
  DISPUTED: "danger",
  REFERRED: "warning",
  RESOLVED: "success",
};

const CLAIM_LABEL: Record<DepositClaim["status"], string> = {
  RENTER_RESPONSE_PENDING: "Waiting for renter",
  AGREED: "Agreed",
  DISPUTED: "Disputed",
  REFERRED: "With the deposit scheme",
  RESOLVED: "Resolved",
};

const RESPONSE_LABEL: Record<string, string> = {
  ACCEPT: "Accepted",
  PARTIAL_ACCEPT: "Part accepted",
  DISPUTE: "Disputed",
  DEEMED_ACCEPTED: "Accepted (no answer)",
};

function categoryLabel(code: string): string {
  return DEPOSIT_CLAIM_CATEGORIES.find((c) => c.value === code)?.label ?? code;
}

function isOpen(record: DepositRecord): boolean {
  return record.status === "HELD" || record.status === "PARTIALLY_RELEASED" || record.status === "FROZEN";
}

function windowNote(record: DepositRecord, side: Side): string {
  if (!isOpen(record)) return record.notes || "Settled.";
  if (!record.claimWindowEndsAt) {
    return side === "host"
      ? "The tenancy is still running. You can claim against the deposit until 14 days after it ends."
      : "Held for your tenancy. After it ends, your host has a short window to claim; the rest comes back to you automatically.";
  }
  return side === "host"
    ? `Claim window closes ${formatDate(record.claimWindowEndsAt)}. After that, whatever isn't claimed goes back to the renter automatically.`
    : `Your host can claim until ${formatDate(record.claimWindowEndsAt)}. After that, the unclaimed balance comes back to you automatically.`;
}

// -- One claim item ---------------------------------------------------------

function ClaimItemRow({
  side,
  claim,
  item,
  busy,
  onRespond,
  onRevise,
  onEvidence,
}: {
  side: Side;
  claim: DepositClaim;
  item: DepositClaimItem;
  busy: boolean;
  onRespond: (item: DepositClaimItem, response: "ACCEPT" | "PARTIAL_ACCEPT" | "DISPUTE", amount?: number) => void;
  onRevise: (item: DepositClaimItem, amount: number) => void;
  onEvidence: (item: DepositClaimItem, file: File) => void;
}) {
  const [partial, setPartial] = useState("");
  const [showPartial, setShowPartial] = useState(false);
  const [lower, setLower] = useState("");
  const canAnswer = side === "renter" && claim.status === "RENTER_RESPONSE_PENDING" && item.tenantResponse === "";
  const canLower = side === "host" && item.tenantResponse === "DISPUTE" && item.finalAmount == null
    && (claim.status === "DISPUTED" || claim.status === "REFERRED");

  return (
    <div className="space-y-2 rounded-xl bg-slate-50 p-3 text-xs text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-semibold">{categoryLabel(item.categoryCode)}</span>
        <span>
          {formatCurrency(item.amountRequested, claim.currency)}
          {item.finalAmount != null && item.finalAmount !== item.amountRequested && (
            <> → <span className="font-semibold">{formatCurrency(item.finalAmount, claim.currency)}</span></>
          )}
        </span>
      </div>
      {item.description && <p className="text-slate-500 dark:text-slate-400">{item.description}</p>}
      <div className="flex flex-wrap items-center gap-2">
        {item.tenantResponse && <Badge tone={item.tenantResponse === "DISPUTE" ? "danger" : "success"}>{RESPONSE_LABEL[item.tenantResponse]}</Badge>}
        {item.hasEvidence ? (
          <a className="font-semibold text-primary-600 hover:underline" href={depositEvidenceUrl(side, item.id)} target="_blank" rel="noreferrer">
            View evidence
          </a>
        ) : side === "host" && isOpenClaim(claim) ? (
          <label className="cursor-pointer font-semibold text-primary-600 hover:underline">
            Attach evidence
            <input
              type="file"
              className="hidden"
              accept="image/*,application/pdf"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) onEvidence(item, f); e.target.value = ""; }}
            />
          </label>
        ) : null}
      </div>

      {canAnswer && (
        <div className="space-y-2 border-t border-slate-200 pt-2 dark:border-slate-700">
          {showPartial ? (
            <div className="flex flex-wrap gap-2">
              <input
                className={`${inputClass} max-w-[10rem]`}
                type="number"
                min={0}
                step="0.01"
                placeholder="Amount you accept"
                value={partial}
                onChange={(e) => setPartial(e.target.value)}
              />
              <Button size="sm" loading={busy} onClick={() => onRespond(item, "PARTIAL_ACCEPT", Number(partial))} disabled={!partial}>
                Accept this amount
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setShowPartial(false)}>Cancel</Button>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" loading={busy} onClick={() => onRespond(item, "ACCEPT")}>Accept</Button>
              <Button size="sm" variant="ghost" onClick={() => setShowPartial(true)}>Accept part</Button>
              <Button size="sm" variant="ghost" loading={busy} onClick={() => onRespond(item, "DISPUTE")}>Dispute</Button>
            </div>
          )}
        </div>
      )}

      {canLower && (
        <div className="flex flex-wrap gap-2 border-t border-slate-200 pt-2 dark:border-slate-700">
          <input
            className={`${inputClass} max-w-[10rem]`}
            type="number"
            min={0}
            max={item.amountRequested}
            step="0.01"
            placeholder="New amount (0 drops it)"
            value={lower}
            onChange={(e) => setLower(e.target.value)}
          />
          <Button size="sm" variant="outline" loading={busy} disabled={lower === ""} onClick={() => onRevise(item, Number(lower))}>
            Lower item
          </Button>
        </div>
      )}
    </div>
  );
}

function isOpenClaim(claim: DepositClaim): boolean {
  return claim.status === "RENTER_RESPONSE_PENDING" || claim.status === "DISPUTED" || claim.status === "REFERRED";
}

// -- Scheme decision form ---------------------------------------------------

function SchemeDecisionForm({
  claim,
  busy,
  onSave,
}: {
  claim: DepositClaim;
  busy: boolean;
  onSave: (amounts: { itemId: number; finalAmount: number }[], reference: string, notes: string) => void;
}) {
  const openItems = claim.items.filter((i) => i.finalAmount == null);
  const [amounts, setAmounts] = useState<Record<number, string>>({});
  const [reference, setReference] = useState("");
  const [notes, setNotes] = useState("");
  const ready = reference.trim() !== "" && openItems.every((i) => amounts[i.id] !== undefined && amounts[i.id] !== "");

  return (
    <div className="space-y-2 rounded-xl border border-slate-200 p-3 dark:border-slate-700">
      <p className="text-xs text-slate-600 dark:text-slate-300">
        Enter the deposit scheme&apos;s decision for each disputed item. The other side is told; if it&apos;s wrong, they can raise a payment dispute.
      </p>
      {openItems.map((i) => (
        <Field key={i.id} label={`${categoryLabel(i.categoryCode)} (claimed ${formatCurrency(i.amountRequested, claim.currency)})`}>
          <input
            className={inputClass}
            type="number"
            min={0}
            max={i.amountRequested}
            step="0.01"
            placeholder="Amount the scheme awarded to the host"
            value={amounts[i.id] ?? ""}
            onChange={(e) => setAmounts((prev) => ({ ...prev, [i.id]: e.target.value }))}
          />
        </Field>
      ))}
      <Field label="Scheme case reference">
        <input className={inputClass} value={reference} onChange={(e) => setReference(e.target.value)} />
      </Field>
      <Field label="Notes (optional)">
        <input className={inputClass} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </Field>
      <Button
        size="sm"
        loading={busy}
        disabled={!ready}
        onClick={() => onSave(openItems.map((i) => ({ itemId: i.id, finalAmount: Number(amounts[i.id]) })), reference.trim(), notes.trim())}
      >
        Save decision
      </Button>
    </div>
  );
}

// -- New claim form (host) --------------------------------------------------

function NewClaimForm({
  record,
  busy,
  onSubmit,
  onCancel,
}: {
  record: DepositRecord;
  busy: boolean;
  onSubmit: (items: { categoryCode: string; amountRequested: number; description: string }[]) => void;
  onCancel: () => void;
}) {
  const [rows, setRows] = useState([{ categoryCode: DEPOSIT_CLAIM_CATEGORIES[0].value, amount: "", description: "" }]);
  const available = Math.max(0, record.heldAmount - record.releasedAmount - record.claimedAmount);
  const total = rows.reduce((sum, r) => sum + (Number(r.amount) || 0), 0);
  const valid = rows.every((r) => Number(r.amount) > 0) && total <= available + 0.001;

  function update(index: number, patch: Partial<(typeof rows)[number]>) {
    setRows((prev) => prev.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }

  return (
    <div className="space-y-3 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
      <p className="text-xs text-slate-600 dark:text-slate-300">
        Up to {formatCurrency(available, record.currency)} can be claimed. The renter has 14 days to answer each item; anything they
        don&apos;t answer counts as accepted. You can attach evidence to each item after submitting.
      </p>
      {rows.map((row, index) => (
        <div key={index} className="grid gap-2 sm:grid-cols-[1fr_8rem_auto]">
          <select className={inputClass} value={row.categoryCode} onChange={(e) => update(index, { categoryCode: e.target.value })}>
            {DEPOSIT_CLAIM_CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
          <input
            className={inputClass}
            type="number"
            min={0}
            step="0.01"
            placeholder="Amount"
            value={row.amount}
            onChange={(e) => update(index, { amount: e.target.value })}
          />
          <Button
            size="sm"
            variant="ghost"
            aria-label="Remove item"
            disabled={rows.length === 1}
            onClick={() => setRows((prev) => prev.filter((_, i) => i !== index))}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
          <input
            className={`${inputClass} sm:col-span-3`}
            placeholder="What it's for"
            value={row.description}
            onChange={(e) => update(index, { description: e.target.value })}
          />
        </div>
      ))}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setRows((prev) => [...prev, { categoryCode: DEPOSIT_CLAIM_CATEGORIES[0].value, amount: "", description: "" }])}
        >
          <Plus className="h-3.5 w-3.5" /> Add item
        </Button>
        <span className="text-xs text-slate-500">Total {formatCurrency(total, record.currency)}</span>
      </div>
      <div className="flex gap-2">
        <Button
          size="sm"
          loading={busy}
          disabled={!valid}
          onClick={() => onSubmit(rows.map((r) => ({ categoryCode: r.categoryCode, amountRequested: Number(r.amount), description: r.description.trim() })))}
        >
          Submit claim
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

// -- Section ----------------------------------------------------------------

export function DepositClaimsSection({
  side,
  showToast,
}: {
  side: Side;
  showToast: (message: string, tone?: ToastTone) => void;
}) {
  const [loading, setLoading] = useState(true);
  const [records, setRecords] = useState<DepositRecord[]>([]);
  const [claims, setClaims] = useState<DepositClaim[]>([]);
  const [busy, setBusy] = useState(false);
  const [claimFormFor, setClaimFormFor] = useState<number | null>(null);
  const [decisionFor, setDecisionFor] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      if (side === "host") {
        const recs = await listHostDeposits();
        const perRecord = await Promise.all(recs.map((r) => listHostDepositClaims(r.id)));
        setRecords(recs);
        setClaims(perRecord.flat());
      } else {
        const [recs, cls] = await Promise.all([listMyDeposits(), listMyDepositClaims()]);
        setRecords(recs);
        setClaims(cls);
      }
    } catch (err) {
      showToast(errorMessage(err, "Could not load deposits."), "error");
    } finally {
      setLoading(false);
    }
  }, [side, showToast]);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(action: () => Promise<unknown>, success: string, failure: string) {
    setBusy(true);
    try {
      await action();
      showToast(success);
      await load();
      return true;
    } catch (err) {
      showToast(errorMessage(err, failure), "error");
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <Loader label="Loading deposits" />;

  return (
    <Card className="space-y-4">
      <SectionHeading
        title="Deposits"
        subtitle={
          side === "host"
            ? "Claim against a deposit after a tenancy. Disputes go to the deposit scheme; nothing waits on Zoiko Rooms."
            : "Your deposits and any claims your host has made against them."
        }
      />
      {records.length === 0 ? (
        <EmptyState message="No deposits yet." />
      ) : (
        <div className="space-y-4">
          {records.map((record) => {
            const recordClaims = claims.filter((c) => c.depositRecordId === record.id);
            const windowClosed = record.claimWindowEndsAt != null && new Date(record.claimWindowEndsAt) <= new Date();
            const hasOpenClaim = recordClaims.some(isOpenClaim);
            return (
              <div key={record.id} className="space-y-3 rounded-2xl ring-1 ring-slate-100 p-4 dark:ring-white/10">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">{record.roomLabel || `Deposit #${record.id}`}</p>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      Held {formatCurrency(record.heldAmount, record.currency)}
                      {record.claimedAmount > 0 && <> · Claimed {formatCurrency(record.claimedAmount, record.currency)}</>}
                      {record.releasedAmount > 0 && <> · Returned {formatCurrency(record.releasedAmount, record.currency)}</>}
                    </p>
                  </div>
                  <Badge tone={RECORD_TONE[record.status]}>{record.status.replace(/_/g, " ").toLowerCase()}</Badge>
                </div>
                <p className="text-xs text-slate-500 dark:text-slate-400">{windowNote(record, side)}</p>

                {recordClaims.map((claim) => (
                  <div key={claim.id} className="space-y-2">
                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                      <span className="font-semibold text-slate-700 dark:text-slate-200">
                        Claim #{claim.id} · {formatDate(claim.submittedAt)}
                      </span>
                      <Badge tone={CLAIM_TONE[claim.status]}>{CLAIM_LABEL[claim.status]}</Badge>
                    </div>
                    {claim.status === "RENTER_RESPONSE_PENDING" && claim.responseDueAt && (
                      <p className="text-xs text-slate-500 dark:text-slate-400">
                        {side === "renter" ? "Answer" : "The renter must answer"} by {formatDate(claim.responseDueAt)} -- unanswered items count as accepted.
                      </p>
                    )}
                    {claim.status === "DISPUTED" && claim.negotiationEndsAt && (
                      <p className="text-xs text-slate-500 dark:text-slate-400">
                        {side === "host" ? "Lower or drop the disputed items to settle now. " : "Your host can lower the disputed items. "}
                        If there&apos;s no agreement by {formatDate(claim.negotiationEndsAt)}, it goes to the deposit protection scheme.
                      </p>
                    )}
                    {claim.status === "REFERRED" && (
                      <p className="text-xs text-slate-500 dark:text-slate-400">
                        Take the disputed items to your deposit protection scheme, then record its decision here. The disputed amount stays held until then.
                      </p>
                    )}
                    {claim.schemeReference && (
                      <p className="text-xs text-slate-500 dark:text-slate-400">Deposit scheme ref {claim.schemeReference}</p>
                    )}
                    {claim.items.map((item) => (
                      <ClaimItemRow
                        key={item.id}
                        side={side}
                        claim={claim}
                        item={item}
                        busy={busy}
                        onRespond={(it, response, amount) =>
                          void run(() => respondToDepositClaimItem(it.id, response, amount), "Answer saved.", "Could not save your answer.")}
                        onRevise={(it, amount) =>
                          void run(() => reviseHostDepositClaimItem(it.id, amount), "Item lowered.", "Could not lower this item.")}
                        onEvidence={(it, file) =>
                          void run(() => attachHostDepositClaimEvidence(it.id, file), "Evidence attached.", "Could not attach evidence.")}
                      />
                    ))}
                    {(claim.status === "DISPUTED" || claim.status === "REFERRED") && (
                      decisionFor === claim.id ? (
                        <SchemeDecisionForm
                          claim={claim}
                          busy={busy}
                          onSave={async (amounts, reference, notes) => {
                            const ok = await run(
                              () => recordDepositSchemeDecision(side, claim.id, { itemFinalAmounts: amounts, schemeReference: reference, notes }),
                              "Decision recorded.",
                              "Could not record the decision.",
                            );
                            if (ok) setDecisionFor(null);
                          }}
                        />
                      ) : (
                        <Button size="sm" variant="ghost" onClick={() => setDecisionFor(claim.id)}>
                          Record the deposit scheme&apos;s decision
                        </Button>
                      )
                    )}
                  </div>
                ))}

                {side === "host" && isOpen(record) && !windowClosed && (
                  claimFormFor === record.id ? (
                    <NewClaimForm
                      record={record}
                      busy={busy}
                      onCancel={() => setClaimFormFor(null)}
                      onSubmit={async (items) => {
                        const ok = await run(() => submitHostDepositClaim(record.id, items), "Claim sent to the renter.", "Could not submit this claim.");
                        if (ok) setClaimFormFor(null);
                      }}
                    />
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      <Button size="sm" variant="outline" onClick={() => setClaimFormFor(record.id)}>
                        <Plus className="h-3.5 w-3.5" /> Claim against deposit
                      </Button>
                      {record.claimWindowEndsAt && !hasOpenClaim && (
                        <Button
                          size="sm"
                          variant="ghost"
                          loading={busy}
                          onClick={() => {
                            if (window.confirm("Settle now? You won't be able to make any more claims on this deposit.")) {
                              void run(() => settleHostDeposit(record.id), "Deposit settled.", "Could not settle this deposit.");
                            }
                          }}
                        >
                          Settle now, no more claims
                        </Button>
                      )}
                    </div>
                  )
                )}
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}
