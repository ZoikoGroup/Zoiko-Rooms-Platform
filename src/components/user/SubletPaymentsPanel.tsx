"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Wallet } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Field, inputClass } from "@/components/user/ui";
import { errorMessage } from "@/lib/user-api";
import { formatDate, formatMoney } from "@/lib/utils";
import {
  DepositRoute, DirectPaymentMethod, RentPayeeType, SubletPaymentArrangementView, SubletTransactions,
  depositRouteLabel, getSubletPaymentArrangement, getSubletTransactions, methodLabel, payeeTypeLabel,
  updateSubletPaymentArrangement,
} from "@/lib/sublet-payments";

const METHODS: DirectPaymentMethod[] = ["BANK_TRANSFER", "UPI", "CASH"];

/**
 * ZR-SUBLET-PAY-003 on an approved sublet: who receives the rent and the
 * deposit (and why), accepted methods (bank transfer / UPI / cash), the
 * terms with the Zoiko fee of 0, and one payment timeline. The original
 * tenant and the landlord can change the arrangement; payee choices come
 * from the server, so an ineligible payee can't be picked.
 */
export function SubletPaymentsPanel({ subletId }: { subletId: number }) {
  const [view, setView] = useState<SubletPaymentArrangementView | null>(null);
  const [txns, setTxns] = useState<SubletTransactions | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);

  const load = useCallback(() => {
    setError("");
    getSubletPaymentArrangement(subletId).then(setView).catch((err) => setError(errorMessage(err, "Could not load payments.")));
    getSubletTransactions(subletId).then(setTxns).catch(() => setTxns(null));
  }, [subletId]);

  useEffect(() => { load(); }, [load]);

  if (error) return <p className="mt-3 text-xs text-accent-700" role="alert">{error}</p>;
  if (!view) return <p className="mt-3 text-xs text-slate-400" role="status">Loading payments...</p>;
  const a = view.arrangement;
  if (!a) return null;
  const currency = a.terms.currency;
  const rentReady = a.readiness.rent.state === "READY";
  const depositReady = ["READY", "NOT_REQUIRED"].includes(a.readiness.deposit.state);

  return (
    <div className="mt-3 space-y-3 rounded-xl bg-slate-50 p-4 text-sm dark:bg-slate-800/60">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="flex items-center gap-2 font-semibold text-primary-900 dark:text-white">
          <Wallet className="h-4 w-4" aria-hidden="true" /> Sublet payments
        </p>
        {view.canEdit && !editing && (
          <Button size="sm" variant="outline" onClick={() => setEditing(true)}>Change payment arrangement</Button>
        )}
      </div>

      <dl className="grid gap-2 sm:grid-cols-2">
        <div>
          <dt className="text-xs text-slate-400">Monthly rent</dt>
          <dd className="font-semibold">{formatMoney(a.terms.rentAmount, currency)}
            {a.terms.rentDueDay ? <span className="font-normal text-slate-500"> · due on day {a.terms.rentDueDay}</span> : null}</dd>
        </div>
        <div>
          <dt className="text-xs text-slate-400">Rent paid to</dt>
          <dd className="font-semibold">{a.rentPayee.name} <span className="font-normal text-slate-500">({payeeTypeLabel[a.rentPayee.type]})</span></dd>
          {a.rentPayee.why && <dd className="text-xs text-slate-500">{a.rentPayee.why}</dd>}
        </div>
        <div>
          <dt className="text-xs text-slate-400">Deposit</dt>
          <dd className="font-semibold">{formatMoney(a.terms.depositAmount, currency)}</dd>
          <dd className="text-xs text-slate-500">
            {depositRouteLabel[a.deposit.route]}{a.deposit.route === "PAY_TO_CUSTODIAN" && a.deposit.custodianName ? ` -- ${a.deposit.custodianName}` : ""}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-slate-400">Accepted methods</dt>
          <dd className="font-semibold">{a.acceptedMethods.map((m) => methodLabel[m]).join(", ")}</dd>
          <dd className="text-xs text-slate-500">Zoiko Rooms platform fee: {formatMoney(a.terms.platformFee, currency)}</dd>
        </div>
      </dl>

      {(!rentReady || !depositReady) && (
        <div className="space-y-1 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-200" role="status">
          {!rentReady && <p className="flex items-start gap-1.5"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" /> Rent: {a.readiness.rent.message}</p>}
          {!depositReady && <p className="flex items-start gap-1.5"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" /> Deposit: {a.readiness.deposit.message}</p>}
        </div>
      )}
      {a.deposit.route === "PAY_TO_CUSTODIAN" && a.deposit.custodianHint && !a.deposit.custodianName && (
        <p className="text-xs text-slate-500">{a.deposit.custodianHint}</p>
      )}

      {editing && <ArrangementForm view={view} onDone={() => { setEditing(false); load(); }} onCancel={() => setEditing(false)} />}

      {txns && txns.items.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer font-semibold text-slate-600 dark:text-slate-300">
            Payment history · paid {formatMoney(txns.totals.paid, txns.currency)} · still due {formatMoney(txns.totals.due, txns.currency)}
          </summary>
          <ul className="mt-2 space-y-1.5">
            {txns.items.map((item, i) => (
              <li key={i} className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-1.5 dark:border-white/10">
                <span>
                  <span className="font-semibold capitalize">{item.purpose.toLowerCase().replace(/_/g, " ")}</span>{" "}
                  {item.kind === "OBLIGATION" ? `due ${item.dueDate ? formatDate(item.dueDate) : ""}` :
                    item.kind === "PAYMENT" ? `paid ${item.paidOn ? formatDate(item.paidOn) : ""}${item.method ? ` by ${item.method.replace(/_/g, " ").toLowerCase()}` : ""}` :
                      "return"}
                  {item.externalReference ? ` · ref ${item.externalReference}` : ""}
                  <span className="block text-slate-400">{item.evidence}</span>
                </span>
                <span className="flex items-center gap-2">
                  {formatMoney(item.amount, item.currency)}
                  <Badge tone={["CONFIRMED", "PARTIALLY_PAID"].includes(item.status) ? "success" : item.status === "DISPUTED" ? "danger" : "neutral"}>
                    {item.status.toLowerCase().replace(/_/g, " ")}
                  </Badge>
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
      <p className="flex items-center gap-1.5 text-[11px] text-slate-400">
        <CheckCircle2 className="h-3 w-3" aria-hidden="true" /> Paid directly by bank transfer, UPI or cash. Zoiko Rooms does not receive or hold this money.
      </p>
    </div>
  );
}

function ArrangementForm({ view, onDone, onCancel }: {
  view: SubletPaymentArrangementView; onDone: () => void; onCancel: () => void;
}) {
  const a = view.arrangement!;
  const [payee, setPayee] = useState<RentPayeeType>(a.rentPayee.type);
  const [methods, setMethods] = useState<DirectPaymentMethod[]>(a.acceptedMethods);
  const [route, setRoute] = useState<DepositRoute>(a.deposit.route);
  const [custodianName, setCustodianName] = useState(a.deposit.custodianName);
  const [custodianReference, setCustodianReference] = useState(a.deposit.custodianReference);
  const [custodianInstructions, setCustodianInstructions] = useState(a.deposit.custodianInstructions);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const routeOptions: DepositRoute[] = a.terms.depositAmount > 0
    ? (a.deposit.packRoute === "PAY_TO_CUSTODIAN" ? ["PAY_TO_CUSTODIAN"]
      : ["PAY_TO_LANDLORD_OR_AGENT", ...(a.deposit.sublessorAllowed && view.eligiblePayees.some((p) => p.type === "SUBLESSOR")
        ? ["PAY_TO_SUBLESSOR" as DepositRoute] : []), "PAY_TO_CUSTODIAN"])
    : ["NOT_REQUIRED"];

  async function save() {
    setBusy(true);
    setError("");
    try {
      await updateSubletPaymentArrangement(view.subletRequestId, a.version, {
        rentPayeeType: payee, acceptedMethods: methods, depositRoute: route,
        ...(route === "PAY_TO_CUSTODIAN" ? { custodianName, custodianReference, custodianInstructions } : {}),
        ...(a.locked ? { amendmentReason: reason } : {}),
      });
      onDone();
    } catch (err) {
      setError(errorMessage(err, "Could not save the payment arrangement."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900">
      <fieldset className="space-y-1">
        <legend className="text-xs font-semibold text-slate-600 dark:text-slate-300">Who receives the monthly rent?</legend>
        {view.eligiblePayees.map((p) => (
          <label key={p.type} className="flex items-start gap-2 text-sm">
            <input type="radio" className="mt-1" checked={payee === p.type} onChange={() => setPayee(p.type)} />
            <span>{p.name} <span className="text-slate-500">({payeeTypeLabel[p.type]})</span>
              {p.why && <span className="block text-xs text-slate-500">{p.why}</span>}</span>
          </label>
        ))}
        {!view.eligiblePayees.some((p) => p.type === "SUBLESSOR") && (
          <p className="text-xs text-slate-400">The original tenant can only collect rent if the landlord&apos;s permission allows it.</p>
        )}
      </fieldset>
      <fieldset className="space-y-1">
        <legend className="text-xs font-semibold text-slate-600 dark:text-slate-300">Accepted payment methods</legend>
        <div className="flex flex-wrap gap-3">
          {METHODS.map((m) => (
            <label key={m} className="flex items-center gap-1.5 text-sm">
              <input type="checkbox" checked={methods.includes(m)}
                     onChange={(e) => setMethods(e.target.checked ? [...methods, m] : methods.filter((x) => x !== m))} />
              {methodLabel[m]}
            </label>
          ))}
        </div>
      </fieldset>
      {a.terms.depositAmount > 0 && (
        <Field label="Where is the deposit paid?">
          <select className={inputClass} value={route} onChange={(e) => setRoute(e.target.value as DepositRoute)}>
            {routeOptions.map((r) => <option key={r} value={r}>{depositRouteLabel[r]}</option>)}
          </select>
        </Field>
      )}
      {route === "PAY_TO_CUSTODIAN" && (
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="Scheme name"><input className={inputClass} value={custodianName} onChange={(e) => setCustodianName(e.target.value)} /></Field>
          <Field label="Scheme reference" hint="Optional"><input className={inputClass} value={custodianReference} onChange={(e) => setCustodianReference(e.target.value)} /></Field>
          <div className="sm:col-span-2">
            <Field label="How to pay the scheme">
              <textarea className={inputClass} rows={2} value={custodianInstructions} onChange={(e) => setCustodianInstructions(e.target.value)} />
            </Field>
          </div>
        </div>
      )}
      {a.locked && (
        <Field label="Reason for the amendment" hint="The agreement is in force, so this creates a new version and both sides are notified">
          <input className={inputClass} value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      )}
      {error && <p className="text-xs text-accent-700" role="alert">{error}</p>}
      <div className="flex gap-2">
        <Button size="sm" onClick={save} loading={busy} disabled={busy || methods.length === 0 || (a.locked && !reason.trim())}>Save</Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}
