"use client";

import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { AlertTriangle, BarChart3, CheckCircle2, FileText, History, MapPinned, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { formatDate } from "@/lib/utils";
import {
  PropertyReviewCase, PropertyReviewQueueItem, ReviewDecision, evidenceTypeLabel, getPropertyReviewCase,
  getPropertyReviewReasons, getPropertyVerificationMetrics, listPropertyReviewQueue, propertyEvidenceUrl,
  propertyStateLabel, propertyStateTone, reviewPropertyVerification,
} from "@/lib/property-verification";

const PropertyPinMap = dynamic(() => import("@/components/user/PropertyPinMap").then((m) => m.PropertyPinMap), {
  ssr: false, loading: () => <div className="h-[220px] animate-pulse rounded-xl bg-slate-100 dark:bg-slate-800" />,
});

const FILTERS = [
  { value: "MANUAL_REVIEW", label: "In review" },
  { value: "ACTION_REQUIRED", label: "Action required" },
  { value: "VERIFIED", label: "Verified" },
  { value: "REJECTED", label: "Rejected" },
  { value: "all", label: "All" },
];
const DECISION_LABEL: Record<ReviewDecision, string> = {
  APPROVE: "Approve property", REQUEST_INFO: "Request more information", REJECT: "Reject",
};

function label(code: string) {
  return code.toLowerCase().replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

/**
 * ZR-PROPERTY-VERIFY-001 Section 16 -- Trust & Safety property review. Shows
 * only what the case needs; decisions need a standard reason code; duplicate
 * cases need a second, different reviewer (four-eyes); reviewers can't edit
 * the host's property data.
 */
export function PropertyLocationReviewManager() {
  const [filter, setFilter] = useState("MANUAL_REVIEW");
  const [queue, setQueue] = useState<PropertyReviewQueueItem[]>([]);
  const [metrics, setMetrics] = useState<Record<string, unknown> | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setQueue(await listPropertyReviewQueue(filter));
      setMetrics(await getPropertyVerificationMetrics(30).catch(() => null));
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => { void load(); }, [load]);

  function notify(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3200);
  }

  return (
    <section className="space-y-4 rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex items-center gap-2">
        <MapPinned className="h-5 w-5 text-primary-700 dark:text-primary-300" aria-hidden="true" />
        <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Property &amp; location verification</h2>
        <span className="text-xs text-slate-400">ZR-PROPERTY-VERIFY-001 review queue</span>
      </div>

      {metrics && <Metrics data={metrics} />}

      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Filter property verifications">
        {FILTERS.map((f) => (
          <button key={f.value} role="tab" aria-selected={filter === f.value} onClick={() => setFilter(f.value)}
                  className={`rounded-full px-3 py-1.5 text-xs font-semibold ${filter === f.value ? "bg-primary-700 text-white"
                    : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300"}`}>
            {f.label}
          </button>
        ))}
      </div>

      {loading ? <p className="text-sm text-slate-400" role="status">Loading...</p> : queue.length === 0 ? (
        <p className="py-8 text-center text-sm text-slate-400">Nothing here.</p>
      ) : (
        <ul className="divide-y divide-slate-100 dark:divide-slate-800">
          {queue.map((item) => (
            <li key={item.id}>
              <button onClick={() => setOpenId(item.id)}
                      className="flex w-full flex-wrap items-center justify-between gap-3 py-3 text-left hover:bg-slate-50 dark:hover:bg-slate-800/50">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs text-slate-400">PV-{item.id}</span>
                  <Badge tone={propertyStateTone[item.state]} dot>{propertyStateLabel[item.state]}</Badge>
                  <span className="text-sm text-primary-900 dark:text-white">Property #{item.propertyId}</span>
                  <span className="text-xs text-slate-500">{item.countryCode}</span>
                  {item.possibleDuplicate && <Badge tone="danger">Possible duplicate</Badge>}
                  {item.awaitingSecondApproval && <Badge tone="warning">Awaiting second approval</Badge>}
                </span>
                <span className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                  {item.reasonCodes.slice(0, 3).map((c) => <span key={c} className="rounded bg-slate-100 px-1.5 py-0.5 dark:bg-slate-800">{label(c)}</span>)}
                  {item.submittedAt ? `Submitted ${formatDate(item.submittedAt)}` : formatDate(item.createdAt)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {openId !== null && (
        <CaseModal id={openId} onClose={() => setOpenId(null)} onDecided={(m) => { notify(m); setOpenId(null); void load(); }} />
      )}
      {toast && (
        <div role="status" aria-live="polite" className="fixed bottom-6 right-6 z-300 rounded-xl bg-primary-900 px-4 py-3 text-sm text-white shadow-2xl">{toast}</div>
      )}
    </section>
  );
}

function Metrics({ data }: { data: Record<string, unknown> }) {
  const pct = (v: unknown) => (typeof v === "number" ? `${v}%` : "--");
  const items: [string, string][] = [
    ["Waiting for review", String(data.pending_review ?? 0)],
    ["Auto-pass rate", pct(data.auto_pass_rate)],
    ["Manual review rate", pct(data.manual_review_rate)],
    ["Manual entry rate", pct(data.manual_entry_rate)],
    ["Pin adjustment rate", pct(data.pin_adjustment_rate)],
    ["Median pin move", data.median_pin_move_meters != null ? `${data.median_pin_move_meters} m` : "--"],
    ["Duplicate flags", String(data.duplicate_flags ?? 0)],
    ["Location provider", String(data.provider ?? "--")],
  ];
  return (
    <div className="rounded-xl bg-slate-50 p-4 dark:bg-slate-800/60">
      <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
        <BarChart3 className="h-3.5 w-3.5" aria-hidden="true" /> Last {String(data.period_days ?? 30)} days
      </p>
      <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        {items.map(([k, v]) => (
          <div key={k}><dt className="text-xs text-slate-500">{k}</dt><dd className="font-semibold text-primary-900 dark:text-white">{v}</dd></div>
        ))}
      </dl>
    </div>
  );
}

function CaseModal({ id, onClose, onDecided }: { id: number; onClose: () => void; onDecided: (m: string) => void }) {
  const [record, setRecord] = useState<PropertyReviewCase | null>(null);
  const [reasons, setReasons] = useState<Record<ReviewDecision, { code: string; message: string }[]> | null>(null);
  const [decision, setDecision] = useState<ReviewDecision>("APPROVE");
  const [reasonCode, setReasonCode] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getPropertyReviewCase(id).then(setRecord).catch(() => setError("Could not load this case."));
    getPropertyReviewReasons().then(setReasons).catch(() => undefined);
  }, [id]);
  useEffect(() => { setReasonCode(reasons?.[decision]?.[0]?.code ?? ""); }, [decision, reasons]);

  async function submit() {
    setBusy(true);
    setError("");
    try {
      const updated = await reviewPropertyVerification(id, decision, reasonCode, note);
      onDecided(updated.state === "MANUAL_REVIEW" ? "First approval recorded -- a second reviewer must approve."
        : `Decision recorded: ${DECISION_LABEL[decision]}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not record the decision");
    } finally {
      setBusy(false);
    }
  }

  const r = record;
  const withinPolicy = r && r.pinMovedMeters != null && r.pinMovedMeters <= r.pinMovePolicyMeters;
  return (
    <Modal open onClose={onClose} title={`Property verification review · PV-${id}`} size="xl">
      {error && <p className="mb-3 text-sm text-accent-700" role="alert">{error}</p>}
      {!r ? <p className="text-sm text-slate-400" role="status">Loading case...</p> : (
        <div className="max-h-[75vh] space-y-5 overflow-y-auto pr-1">
          <dl className="grid gap-3 rounded-xl bg-slate-50 p-4 text-sm sm:grid-cols-4 dark:bg-slate-800/60">
            <Row k="Property" v={r.propertyLabel} />
            <Row k="Host" v={r.hostIdentityVerified ? "Identity Verified" : "Identity not verified"} />
            <Row k="Jurisdiction" v={`${r.countryCode} / ${r.jurisdiction}`} />
            <Row k="State" v={propertyStateLabel[r.state]} />
          </dl>

          <div className="grid gap-5 lg:grid-cols-2">
            <div className="space-y-2">
              <h3 className="text-sm font-semibold text-primary-900 dark:text-white">Address &amp; location</h3>
              <dl className="space-y-1 text-sm">
                <Row k="Submitted" v={line(r.submittedAddress)} inline />
                <Row k="Canonical" v={line(r.canonicalAddress)} inline />
                <Row k="Entry" v={r.entryMode === "SELECTED" ? "Selected from search" : "Entered manually"} inline />
                <Row k="Provider" v={r.provider || "--"} inline />
                <Row k="Precision" v={r.locationPrecision ? label(r.locationPrecision) : "Not found"} inline />
                <Row k="Pin" v={r.pinStatus ? label(r.pinStatus) : "--"} inline />
                <Row k="Pin movement" inline v={r.pinMovedMeters == null ? (r.pinStatus === "REVIEW_REQUIRED" ? "Placed by hand" : "--")
                  : `${r.pinMovedMeters} m · ${withinPolicy ? "within" : "beyond"} policy (${r.pinMovePolicyMeters} m)`} />
                {r.pinAdjustReason && <Row k="Host's reason" v={r.pinAdjustReason} inline />}
                {r.pinReverseGeocode && <Row k="Pin resolves to" v={r.pinReverseGeocode} inline />}
                <Row k="Unit" v={[label(r.propertyKind || "--"), r.unit, r.buildingName, r.floor && `floor ${r.floor}`].filter(Boolean).join(" · ")} inline />
                {r.duplicateOfPropertyId && <Row k="Possible duplicate of" v={`Property #${r.duplicateOfPropertyId}`} inline />}
              </dl>
              {r.confirmedLocation && (
                <PropertyPinMap original={r.originalLocation} marker={r.confirmedLocation} adjustable={false} height={220} />
              )}
            </div>

            <div className="space-y-3">
              <h3 className="text-sm font-semibold text-primary-900 dark:text-white">Property evidence</h3>
              <ul className="space-y-2 text-sm">
                {r.evidenceDetail.map((e) => (
                  <li key={e.id} className="rounded-lg p-2 ring-1 ring-slate-200 dark:ring-slate-700">
                    {e.purged ? <span className="text-slate-500">{e.originalFilename} (deleted under retention)</span> : (
                      <a href={propertyEvidenceUrl(r.id, e.id, true)} target="_blank" rel="noreferrer"
                         className="flex items-center gap-1.5 font-medium text-primary-700 hover:underline dark:text-primary-300">
                        <FileText className="h-4 w-4" aria-hidden="true" /> {e.originalFilename}
                      </a>
                    )}
                    <p className="mt-1 text-xs text-slate-500">{evidenceTypeLabel[e.evidenceType]}</p>
                    <div className="mt-1 flex flex-wrap gap-1.5 text-xs">
                      <Signal ok={e.readable} text={e.readable ? "Readable" : "Not machine-readable"} />
                      {e.addressMatched != null && <Signal ok={e.addressMatched} text={e.addressMatched ? "Address matches" : "Address differs"} />}
                      {e.unitMatched != null && <Signal ok={e.unitMatched} text={e.unitMatched ? "Unit confirmed" : "Unit not found"} />}
                      {e.postalMatched != null && <Signal ok={e.postalMatched} text={e.postalMatched ? "Postal code matches" : "Postal code differs"} />}
                      {e.ownerNameMatched != null && <Signal ok={e.ownerNameMatched} text={e.ownerNameMatched ? "Owner name matches identity" : "Owner name not on document"} />}
                      {e.documentTypeMatched != null && <Signal ok={e.documentTypeMatched} text={e.documentTypeMatched ? "Looks like the chosen type" : "Type unclear"} />}
                      {e.signals.includes("EVIDENCE_OUTDATED") && <Signal ok={false} text={`Outdated (${e.documentYear})`} />}
                      {e.reusedElsewhere && <Signal ok={false} text="Used for another property" />}
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500 dark:bg-slate-800">Scan: {label(e.scanStatus)}</span>
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500 dark:bg-slate-800">
                        Read from: {e.textSource === "PDF_TEXT" ? "PDF text" : e.textSource === "OCR" ? `OCR (${e.ocrConfidence ?? "?"}% confidence)` : "not read"}
                        {e.documentYear ? ` · year ${e.documentYear}` : ""}{e.referenceNumber ? ` · ref ${e.referenceNumber}` : ""}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
              <h3 className="text-sm font-semibold text-primary-900 dark:text-white">Reason signals</h3>
              <ul className="space-y-1 text-sm">
                <li><Signal ok={r.addressStatus === "VALIDATED"} text={`Address ${label(r.addressStatus || "unresolved").toLowerCase()}`} /></li>
                <li><Signal ok={false} text={`Source check ${label(r.sourceCheck).toLowerCase()} -- document corroboration used`} /></li>
                {r.reasonCodes.map((c) => <li key={c}><Signal ok={false} text={label(c)} /></li>)}
              </ul>
            </div>
          </div>

          {r.state === "MANUAL_REVIEW" ? (
            <div className="space-y-3 rounded-xl bg-slate-50 p-4 dark:bg-slate-800/60">
              <h3 className="text-sm font-semibold text-primary-900 dark:text-white">Decision</h3>
              {r.awaitingSecondApproval && (
                <p className="flex items-center gap-2 text-sm text-amber-700"><AlertTriangle className="h-4 w-4" aria-hidden="true" />
                  First approval recorded. A second, different reviewer must approve this duplicate case.</p>
              )}
              <fieldset className="flex flex-wrap gap-3 text-sm">
                <legend className="sr-only">Decision</legend>
                {(Object.keys(DECISION_LABEL) as ReviewDecision[]).map((d) => (
                  <label key={d} className="flex items-center gap-1.5">
                    <input type="radio" name="pv-decision" checked={decision === d} onChange={() => setDecision(d)} /> {DECISION_LABEL[d]}
                  </label>
                ))}
              </fieldset>
              <label className="block text-sm">
                <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">Reason code</span>
                <select className="w-full rounded-xl bg-white px-3 py-2 text-sm ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
                        value={reasonCode} onChange={(e) => setReasonCode(e.target.value)}>
                  {(reasons?.[decision] ?? []).map((c) => <option key={c.code} value={c.code}>{label(c.code.replace(/^REVIEW_/, ""))}</option>)}
                </select>
                {decision !== "APPROVE" && reasons?.[decision]?.find((c) => c.code === reasonCode) && (
                  <span className="mt-1 block text-xs text-slate-500">The host will see: &ldquo;{reasons[decision].find((c) => c.code === reasonCode)?.message}&rdquo;</span>
                )}
              </label>
              <label className="block text-sm">
                <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">Note (optional, internal)</span>
                <textarea className="w-full rounded-xl bg-white px-3 py-2 text-sm ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
                          rows={2} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />
              </label>
              <div className="flex justify-end"><Button loading={busy} disabled={!reasonCode} onClick={() => void submit()}>{DECISION_LABEL[decision]}</Button></div>
            </div>
          ) : (
            <p className="text-sm text-slate-500">This verification is {propertyStateLabel[r.state].toLowerCase()} -- no decision needed.</p>
          )}

          <div className="space-y-2">
            <h3 className="flex items-center gap-1.5 text-sm font-semibold text-primary-900 dark:text-white"><History className="h-4 w-4" aria-hidden="true" /> History</h3>
            <ol className="space-y-1 text-xs text-slate-600 dark:text-slate-300">
              {r.history.map((h, i) => (
                <li key={i} className="flex flex-wrap gap-x-2">
                  <span className="font-mono text-slate-400">{formatDate(h.occurredAt)}</span>
                  <span className="font-semibold">{label(h.eventType)}</span>
                  {h.previousState && h.newState && <span>{h.previousState} → {h.newState}</span>}
                  {h.reasonCodes.length > 0 && <span>({h.reasonCodes.map(label).join(", ")})</span>}
                  <span className="text-slate-400">{h.actorKind}</span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}
    </Modal>
  );
}

function line(a: Record<string, string | undefined> | null | undefined) {
  if (!a) return "--";
  return a.formatted || [a.subpremise, a.addressLine1, a.addressLine2, a.locality, a.administrativeArea, a.postalCode, a.countryCode]
    .filter(Boolean).join(", ") || "--";
}

function Row({ k, v, inline = false }: { k: string; v: string; inline?: boolean }) {
  return inline ? (
    <div className="flex gap-2"><dt className="w-32 shrink-0 text-xs text-slate-500">{k}</dt><dd className="text-primary-900 dark:text-white">{v}</dd></div>
  ) : (
    <div><dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">{k}</dt><dd className="text-primary-900 dark:text-white">{v}</dd></div>
  );
}

function Signal({ ok, text }: { ok: boolean; text: string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 ${ok ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300"
      : "bg-amber-50 text-amber-800 dark:bg-amber-500/10 dark:text-amber-200"}`}>
      {ok ? <CheckCircle2 className="h-3 w-3" aria-hidden="true" /> : <XCircle className="h-3 w-3" aria-hidden="true" />} {text}
    </span>
  );
}
