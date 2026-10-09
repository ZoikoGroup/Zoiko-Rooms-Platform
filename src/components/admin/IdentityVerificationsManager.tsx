"use client";

import { useCallback, useEffect, useState } from "react";
import { BarChart3, FileText, History, Lock, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { apiClientFetch } from "@/lib/api-client";
import { AdminProfile, getCurrentAdmin } from "@/lib/auth";
import { documentTypeLabel } from "@/lib/identity-documents";
import {
  IdentityCase,
  IdentityMetrics,
  IdentityPackAdmin,
  PROVIDER_OPTIONS,
  ReasonMapping,
  VeriffReadiness,
  getVeriffReadiness,
  listReasonMappings,
  runVeriffConnectionTest,
  setVeriffGate,
  upsertReasonMapping,
  eraseIdentityData,
  listIdentityPacks,
  reconcileIdentityCase,
  updateIdentityPack,
  IdentityQueueItem,
  IdentityState,
  getIdentityCase,
  getIdentityMetrics,
  identityEvidenceUrl,
  identityStateLabel,
  identityStateTone,
  listIdentityQueue,
  roleLabel,
} from "@/lib/identity";
import { IdentityDocumentType } from "@/lib/types";
import { formatDate } from "@/lib/utils";

/**
 * ZR-IDENTITY-001 / ZR-IDV-ADR-001 -- internal identity view (not
 * customer-facing). Identities are decided only by Veriff: admins see the
 * verifications, a read-only case (header, normalized checks, history), can
 * reconcile a session with Veriff, erase data, and manage the country packs,
 * go-live readiness and reason-code mappings. There is no approve / reject.
 * Section 15 metrics sit above the list.
 */

const FILTERS: { value: string; label: string }[] = [
  { value: "all", label: "All" },
  { value: "PROCESSING", label: "Checking" },
  { value: "ACTION_REQUIRED", label: "Action required" },
  { value: "VERIFIED", label: "Verified" },
  { value: "FAILED", label: "Could not verify" },
  { value: "REVERIFICATION_REQUIRED", label: "Re-verification" },
];

const CHECK_LABELS: Record<string, string> = {
  document_authenticity: "Document authenticity",
  person_document_binding: "Person-document binding",
  liveness_or_alternative_binding: "Liveness",
};

const CHECK_TONE: Record<string, "success" | "danger" | "neutral" | "warning"> = {
  PASS: "success", FAIL: "danger", NOT_CHECKED: "neutral", NOT_AVAILABLE: "neutral",
};

function codeLabel(code: string): string {
  return code.toLowerCase().replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export function IdentityVerificationsManager() {
  const [filter, setFilter] = useState("all");
  const [queue, setQueue] = useState<IdentityQueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [admin, setAdmin] = useState<AdminProfile | null>(null);
  const [metrics, setMetrics] = useState<IdentityMetrics | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [toast, setToast] = useState("");

  const showToast = useCallback((message: string) => {
    setToast(message);
    setTimeout(() => setToast(""), 3200);
  }, []);

  const isSuperAdmin = admin?.role === "super_admin";

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setQueue(await listIdentityQueue(filter));
    } catch {
      showToast("Failed to load identity verifications");
    } finally {
      setLoading(false);
    }
  }, [filter, showToast]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    getCurrentAdmin().then(setAdmin);
  }, []);

  useEffect(() => {
    if (isSuperAdmin) getIdentityMetrics(30).then(setMetrics).catch(() => setMetrics(null));
  }, [isSuperAdmin, queue]);

  return (
    <section className="space-y-4 rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex items-center gap-2">
        <ShieldCheck className="h-5 w-5 text-primary-700 dark:text-primary-300" aria-hidden="true" />
        <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Identity verifications</h2>
        <span className="text-xs text-slate-400">Decided by Veriff -- internal view, not customer-facing</span>
      </div>

      {metrics && <MetricsStrip metrics={metrics} />}
      {isSuperAdmin && <ReadinessPanel showToast={showToast} />}
      {isSuperAdmin && <PacksPanel showToast={showToast} />}
      {isSuperAdmin && <ReasonMappingsPanel showToast={showToast} />}

      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Filter identity verifications">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            role="tab"
            aria-selected={filter === f.value}
            onClick={() => setFilter(f.value)}
            className={`rounded-full px-3 py-1.5 text-xs font-semibold transition-colors ${
              filter === f.value ? "bg-primary-700 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {loading ? (
        <p className="text-sm text-slate-400" role="status">Loading...</p>
      ) : queue.length === 0 ? (
        <p className="py-8 text-center text-sm text-slate-400">Nothing here.</p>
      ) : (
        <ul className="divide-y divide-slate-100 dark:divide-slate-800">
          {queue.map((item) => (
            <li key={item.id}>
              <button
                onClick={() => setOpenId(item.id)}
                className="flex w-full flex-wrap items-center justify-between gap-3 py-3 text-left hover:bg-slate-50 focus:bg-slate-50 dark:hover:bg-slate-800/50"
              >
                <span className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs text-slate-400">#{item.id}</span>
                  <Badge tone={identityStateTone[item.sessionState] ?? "neutral"} dot>{identityStateLabel[item.sessionState] ?? item.sessionState}</Badge>
                  <span className="text-sm text-primary-900 dark:text-white">
                    {documentTypeLabel[item.documentType as IdentityDocumentType] ?? (item.documentType || "Document + selfie")}
                  </span>
                  {item.countryCode && <span className="text-xs text-slate-500">{item.countryCode}</span>}
                  {item.roleContext && <span className="text-xs text-slate-500">{roleLabel[item.roleContext as keyof typeof roleLabel] ?? item.roleContext}</span>}
                </span>
                <span className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                  {item.reasonCodes.map((c) => <span key={c} className="rounded bg-slate-100 px-1.5 py-0.5 dark:bg-slate-800">{codeLabel(c)}</span>)}
                  {item.submittedAt ? `Submitted ${formatDate(item.submittedAt)}` : formatDate(item.createdAt)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {openId !== null && (
        <CaseModal
          id={openId}
          admin={admin}
          onClose={() => { setOpenId(null); void load(); }}
          showToast={showToast}
        />
      )}

      {toast && (
        <div role="status" aria-live="polite" className="fixed bottom-6 right-6 z-300 rounded-xl bg-primary-900 px-4 py-3 text-sm text-white shadow-2xl">
          {toast}
        </div>
      )}
    </section>
  );
}

function MetricsStrip({ metrics }: { metrics: IdentityMetrics }) {
  const pct = (v: number | null) => (v === null ? "--" : `${v}%`);
  const hrs = (v: number | null) => (v === null ? "--" : v < 1 ? `${Math.round(v * 60)} min` : `${v} h`);
  const money = (v: number | null, currency: string) =>
    v === null ? "--" : new Intl.NumberFormat(undefined, { style: "currency", currency }).format(v);
  const topReasons = Object.entries(metrics.reasonCodes).slice(0, 3);
  return (
    <div className="space-y-2 rounded-xl bg-slate-50 p-4 dark:bg-slate-800/60">
      <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
        <BarChart3 className="h-3.5 w-3.5" aria-hidden="true" /> Last {metrics.periodDays} days
      </p>
      <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <Metric label="Start to verified" value={pct(metrics.startToVerifiedRate)} />
        <Metric label="Action-required recovery" value={pct(metrics.actionRequiredRecoveryRate)} />
        <Metric label="Decision time (median / p90)" value={`${hrs(metrics.timeToDecisionHours.median)} / ${hrs(metrics.timeToDecisionHours.p90)}`} />
        <Metric label="Provider error rate" value={pct(metrics.providerErrorRate)} />
        <Metric label="Top reasons" value={topReasons.length ? topReasons.map(([c, n]) => `${codeLabel(c)} (${n})`).join(", ") : "--"} />
        <Metric label="Provider outcomes" value={Object.entries(metrics.providerOutcomes ?? {}).map(([k, v]) => `${codeLabel(k)} ${v}`).join(", ") || "--"} />
        <Metric label="Webhook auth failures" value={String(metrics.webhookAuthFailures ?? 0)} />
        <Metric label="Webhook duplicates / lag" value={`${pct(metrics.webhookDuplicateRate ?? null)} · ${metrics.webhookProcessingLagSeconds ?? "--"} s`} />
        <Metric label="Unprocessed / stale" value={`${metrics.webhookUnprocessed ?? 0} events · ${metrics.staleSessions ?? 0} sessions`} />
        <Metric
          label="Provider cost (per completed / per approved)"
          value={metrics.providerCost
            ? `${money(metrics.providerCost.perCompletedVerification, metrics.providerCost.currency)} / ${money(metrics.providerCost.perApprovedAccount, metrics.providerCost.currency)}`
            : "Not configured"}
        />
      </dl>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="font-semibold text-primary-900 dark:text-white">{value}</dd>
    </div>
  );
}

function CaseModal({ id, admin, onClose, showToast }: {
  id: number; admin: AdminProfile | null; onClose: () => void; showToast: (m: string) => void;
}) {
  const [record, setRecord] = useState<IdentityCase | null>(null);
  const [error, setError] = useState("");
  const isSuperAdmin = admin?.role === "super_admin";

  useEffect(() => {
    if (!isSuperAdmin) return; // break-glass view below
    getIdentityCase(id).then(setRecord).catch(() => setError("Could not load this case."));
  }, [id, isSuperAdmin]);

  return (
    <Modal open onClose={onClose} title={`Identity verification #${id}`} size="xl">
      {!isSuperAdmin ? (
        <BreakGlassView id={id} admin={admin} showToast={showToast} />
      ) : error ? (
        <p className="text-sm text-accent-700" role="alert">{error}</p>
      ) : !record ? (
        <p className="text-sm text-slate-400" role="status">Loading case...</p>
      ) : (
        <div className="max-h-[75vh] space-y-5 overflow-y-auto pr-1">
          {/* Case header */}
          <dl className="grid gap-3 rounded-xl bg-slate-50 p-4 text-sm sm:grid-cols-4 dark:bg-slate-800/60">
            <HeaderItem label="Verification ID" value={`#${record.id}`} />
            <HeaderItem label="Account" value={`${record.accountName || "Unknown"} · party #${record.partyId}`} />
            <HeaderItem label="Country" value={record.countryCode || "--"} />
            <HeaderItem label="Role context" value={roleLabel[record.roleContext as keyof typeof roleLabel] ?? (record.roleContext || "--")} />
            <HeaderItem label="Provider" value={record.providerCode || "--"} />
            <HeaderItem label="Current state" value={identityStateLabel[record.state as IdentityState] ?? record.state} />
            <HeaderItem label="Routing reason" value={record.reasonCodes.map(codeLabel).join(", ") || "--"} />
            <HeaderItem label="Assurance" value={record.assuranceLevel} />
            {record.providerDecision && <HeaderItem label="Provider decision" value={codeLabel(record.providerDecision)} />}
            {record.providerSessionRef && <HeaderItem label="Provider session" value={record.providerSessionRef} />}
            {record.consentNoticeVersion && <HeaderItem label="Notice accepted" value={record.consentNoticeVersion} />}
          </dl>
          <CaseTools record={record} showToast={showToast} onChanged={() => getIdentityCase(id).then(setRecord)} />
          <div className="grid gap-5 lg:grid-cols-2">
            {/* Evidence workspace */}
            <div className="space-y-2">
              <h3 className="text-sm font-semibold text-primary-900 dark:text-white">Evidence</h3>
              <p className="text-xs text-slate-500">
                {record.documentType ? (documentTypeLabel[record.documentType as IdentityDocumentType] ?? record.documentType) : "No document"}
                {record.maskedDocumentNumber && ` · ${record.maskedDocumentNumber}`}
                {record.legalName && ` · confirmed legal name: ${record.legalName}`}
              </p>
              {record.evidencePurgedAt ? (
                <p className="rounded-xl bg-slate-50 p-4 text-sm text-slate-500 dark:bg-slate-800/60">
                  Evidence deleted under the retention policy on {formatDate(record.evidencePurgedAt)}. The decision is kept.
                </p>
              ) : record.hasDocument ? (
                <SecureEvidenceViewer id={record.id} contentType={record.documentContentType} watermark={`${admin?.email ?? ""} · ${new Date().toISOString().slice(0, 16).replace("T", " ")} UTC`} />
              ) : (
                <p className="rounded-xl bg-slate-50 p-4 text-sm text-slate-500 dark:bg-slate-800/60">
                  Captured and checked inside Veriff -- no copy is stored at Zoiko.
                </p>
              )}
              {record.verifierNotes && <p className="text-xs text-slate-500">Note: {record.verifierNotes}</p>}
            </div>

            {/* Normalized checks (Veriff decides) */}
            <div className="space-y-4">
              <div className="space-y-2">
                <h3 className="text-sm font-semibold text-primary-900 dark:text-white">Normalized checks</h3>
                <ul className="space-y-1.5 text-sm">
                  {Object.entries(CHECK_LABELS).map(([key, labelText]) => {
                    const value = String(record.checks[key] ?? "NOT_CHECKED");
                    return (
                      <li key={key} className="flex items-center justify-between gap-2">
                        <span className="text-slate-600 dark:text-slate-300">{labelText}</span>
                        <Badge tone={CHECK_TONE[value] ?? "neutral"}>{codeLabel(value)}</Badge>
                      </li>
                    );
                  })}
                  <li className="flex items-center justify-between gap-2">
                    <span className="text-slate-600 dark:text-slate-300">Account history</span>
                    <span className="text-xs text-slate-500">
                      Profile {identityStateLabel[record.profileState as IdentityState] ?? record.profileState}
                      {record.duplicateOfVerificationId ? ` · document matches #${record.duplicateOfVerificationId}` : ""}
                    </span>
                  </li>
                </ul>
              </div>

              <p className="text-sm text-slate-500">
                Veriff decides this verification. To fetch a delayed decision, use &ldquo;Reconcile with provider&rdquo; above.
              </p>
            </div>
          </div>

          {/* Audit */}
          <div className="space-y-2">
            <h3 className="flex items-center gap-1.5 text-sm font-semibold text-primary-900 dark:text-white">
              <History className="h-4 w-4" aria-hidden="true" /> History
            </h3>
            <ol className="space-y-1 text-xs text-slate-600 dark:text-slate-300">
              {record.history.map((h, i) => (
                <li key={i} className="flex flex-wrap gap-x-2">
                  <span className="font-mono text-slate-400">{formatDate(h.occurredAt)}</span>
                  <span className="font-semibold">{codeLabel(h.eventType.replace(/^IDENTITY_/, ""))}</span>
                  {h.decision && <span>{codeLabel(h.decision)}</span>}
                  {h.previousState && h.newState && <span>{h.previousState} → {h.newState}</span>}
                  {h.reasonCodes.length > 0 && <span>({h.reasonCodes.map(codeLabel).join(", ")})</span>}
                  <span className="text-slate-400">{h.actorKind}{h.actorId ? ` #${h.actorId}` : ""}</span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}
    </Modal>
  );
}

function HeaderItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="font-semibold text-primary-900 dark:text-white">{value}</dd>
    </div>
  );
}

/** Inline, watermarked, no download control, no right-click save. The server
 *  also serves it inline + no-store and logs every view. */
function SecureEvidenceViewer({ id, contentType, watermark }: { id: number; contentType: string; watermark: string }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let url: string | null = null;
    fetch(identityEvidenceUrl(id), { credentials: "include", cache: "no-store" })
      .then((res) => (res.ok ? res.blob() : Promise.reject(res)))
      .then((blob) => {
        url = URL.createObjectURL(blob);
        setSrc(url);
      })
      .catch(() => setFailed(true));
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [id]);

  if (failed) {
    return (
      <p className="flex items-center gap-2 rounded-xl bg-slate-50 p-4 text-sm text-slate-500 dark:bg-slate-800/60">
        <Lock className="h-4 w-4" aria-hidden="true" /> The evidence couldn&apos;t be shown.
      </p>
    );
  }
  if (!src) return <p className="text-sm text-slate-400" role="status">Loading evidence...</p>;

  const isPdf = contentType === "application/pdf";
  return (
    <div
      className="relative overflow-hidden rounded-xl ring-1 ring-slate-200 dark:ring-slate-700"
      onContextMenu={(e) => e.preventDefault()}
    >
      {isPdf ? (
        <iframe src={`${src}#toolbar=0&navpanes=0`} title="Identity document" className="h-96 w-full bg-white" />
      ) : (
        // eslint-disable-next-line @next/next/no-img-element -- a private blob URL; next/image cannot load it
        <img src={src} alt="Identity document under review" className="max-h-96 w-full select-none object-contain" draggable={false} />
      )}
      {/* Watermark overlay (images are also watermarked server-side). */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 grid place-items-center overflow-hidden">
        <div className="rotate-[-24deg] space-y-10 text-center text-xs font-semibold uppercase tracking-widest text-accent-600/25">
          {Array.from({ length: 6 }).map((_, i) => <p key={i}>{watermark} · confidential</p>)}
        </div>
      </div>
      <p className="flex items-center gap-1.5 bg-slate-50 px-3 py-1.5 text-xs text-slate-500 dark:bg-slate-800">
        <FileText className="h-3.5 w-3.5" aria-hidden="true" /> View only -- every view is logged.
      </p>
    </div>
  );
}

/** Reconcile a hosted session with the provider; lawful erasure (DSAR). */
function CaseTools({ record, showToast, onChanged }: { record: IdentityCase; showToast: (m: string) => void; onChanged: () => void }) {
  const [eraseOpen, setEraseOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const hostedOpen = record.providerSessionRef && ["IN_PROGRESS", "PROCESSING"].includes(record.state);

  async function reconcile() {
    setBusy(true);
    try {
      const { result } = await reconcileIdentityCase(record.id);
      showToast(result === "applied" ? "Provider decision applied" : `Provider: ${codeLabel(result)}`);
      onChanged();
    } catch {
      showToast("Could not reach the provider");
    } finally {
      setBusy(false);
    }
  }

  async function erase() {
    if (!reason.trim()) {
      showToast("Give the reason for the erasure request");
      return;
    }
    setBusy(true);
    try {
      const r = await eraseIdentityData(record.partyId, reason.trim());
      showToast(`Erased ${r.sessions} verification(s); provider deleted ${r.providerDeleted}${r.providerFailed ? `, ${r.providerFailed} failed` : ""}`);
      setEraseOpen(false);
      onChanged();
    } catch {
      showToast("Could not erase identity data");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-start gap-2">
      {hostedOpen && <Button size="sm" variant="outline" loading={busy} onClick={reconcile}>Reconcile with provider</Button>}
      {!eraseOpen ? (
        <Button size="sm" variant="ghost" onClick={() => setEraseOpen(true)}>Erase identity data (privacy request)</Button>
      ) : (
        <div className="w-full space-y-2 rounded-xl bg-accent-50 p-3 dark:bg-accent-500/10">
          <p className="text-xs text-accent-700 dark:text-accent-300">
            Deletes stored evidence, document numbers, names and dates of birth for this account, asks the provider to delete its
            sessions, and leaves the person unverified. Decisions and the audit trail are kept.
          </p>
          <input className="w-full rounded-xl bg-white px-3 py-2 text-sm ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
            placeholder="Request reference / reason" value={reason} onChange={(e) => setReason(e.target.value)} />
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" onClick={() => setEraseOpen(false)}>Cancel</Button>
            <Button size="sm" loading={busy} onClick={erase}>Erase</Button>
          </div>
        </div>
      )}
    </div>
  );
}

/** Country Regulatory Packs: which provider runs each country, retention and
 *  retry limits. Saving writes a new pack version. */
function PacksPanel({ showToast }: { showToast: (m: string) => void }) {
  const [packs, setPacks] = useState<IdentityPackAdmin[]>([]);
  const [open, setOpen] = useState(false);
  const [drafts, setDrafts] = useState<Record<number, Partial<IdentityPackAdmin>>>({});

  const load = useCallback(() => {
    listIdentityPacks().then(setPacks).catch(() => setPacks([]));
  }, []);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  function edit(id: number, change: Partial<IdentityPackAdmin>) {
    setDrafts((d) => ({ ...d, [id]: { ...d[id], ...change } }));
  }

  async function save(pack: IdentityPackAdmin) {
    const change = drafts[pack.id];
    if (!change) return;
    try {
      await updateIdentityPack(pack.id, change);
      showToast(`${pack.countryName || pack.countryCode} pack saved as a new version`);
      setDrafts((d) => {
        const next = { ...d };
        delete next[pack.id];
        return next;
      });
      load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Could not save the pack");
    }
  }

  return (
    <details className="rounded-xl bg-slate-50 p-4 dark:bg-slate-800/60" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary className="cursor-pointer text-sm font-semibold text-primary-900 dark:text-white">Country Regulatory Packs</summary>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-slate-500">
            <tr>
              <th className="py-1 pr-3">Country</th>
              <th className="py-1 pr-3">Provider</th>
              <th className="py-1 pr-3">Attempts / day</th>
              <th className="py-1 pr-3">Evidence kept (days)</th>
              <th className="py-1 pr-3">Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {packs.map((pack) => {
              const draft = { ...pack, ...drafts[pack.id] };
              return (
                <tr key={pack.id} className="border-t border-slate-200 dark:border-slate-700">
                  <td className="py-2 pr-3 font-semibold">{pack.countryName || pack.countryCode} <span className="text-slate-400">v{pack.version}</span></td>
                  <td className="py-2 pr-3">
                    <select className="rounded-lg bg-white px-2 py-1 ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
                      value={draft.documentProviderCode} onChange={(e) => edit(pack.id, { documentProviderCode: e.target.value })}
                      aria-label={`Provider for ${pack.countryName || pack.countryCode}`}>
                      {PROVIDER_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </td>
                  <td className="py-2 pr-3">
                    <input type="number" min={1} className="w-16 rounded-lg bg-white px-2 py-1 ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
                      value={draft.maxAttemptsPerDay} onChange={(e) => edit(pack.id, { maxAttemptsPerDay: Number(e.target.value) })}
                      aria-label="Attempts per day" />
                  </td>
                  <td className="py-2 pr-3">
                    <input type="number" min={1} className="w-20 rounded-lg bg-white px-2 py-1 ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
                      value={draft.evidenceRetentionDays ?? ""} onChange={(e) => edit(pack.id, { evidenceRetentionDays: e.target.value ? Number(e.target.value) : null })}
                      aria-label="Evidence retention days" />
                  </td>
                  <td className="py-2 pr-3">
                    {pack.providerAvailable ? <Badge tone="success">Ready</Badge> : <Badge tone="warning">Provider not configured</Badge>}
                  </td>
                  <td className="py-2">
                    <Button size="sm" variant="outline" disabled={!drafts[pack.id]} onClick={() => save(pack)}>Save</Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="mt-2 text-xs text-slate-400">
          Veriff credentials are set on the server (VERIFF_API_KEY / VERIFF_SHARED_SECRET) -- they are never entered or shown here.
        </p>
      </div>
    </details>
  );
}

/** ZR-IDV-ADR-001 Sections 15 / 17: is Veriff ready for traffic here? */
function ReadinessPanel({ showToast }: { showToast: (m: string) => void }) {
  const [ready, setReady] = useState<VeriffReadiness | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [evidence, setEvidence] = useState("");
  const [note, setNote] = useState("");

  useEffect(() => {
    getVeriffReadiness().then(setReady).catch(() => setReady(null));
  }, []);

  if (!ready) return null;
  const openGates = ready.gates.filter((g) => !g.confirmed).length;

  async function test() {
    setBusy(true);
    try {
      const next = await runVeriffConnectionTest();
      setReady(next);
      showToast(next.lastConnectionTest?.ok ? "Connection test passed" : `Connection test failed: ${next.lastConnectionTest?.detail ?? ""}`);
    } finally {
      setBusy(false);
    }
  }

  async function saveGate(code: string, confirmed: boolean) {
    try {
      setReady(await setVeriffGate(code, confirmed, evidence, note));
      setEditing(null);
      setEvidence("");
      setNote("");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Could not update the gate");
    }
  }

  return (
    <details className="rounded-xl bg-slate-50 p-4 dark:bg-slate-800/60">
      <summary className="flex cursor-pointer flex-wrap items-center gap-2 text-sm font-semibold text-primary-900 dark:text-white">
        Veriff go-live readiness
        <Badge tone={ready.enabled ? "success" : "warning"}>{ready.enabled ? "Accepting verifications" : "Not accepting verifications"}</Badge>
        <span className="text-xs font-normal text-slate-500">{ready.integration} integration · {ready.plan === "full_auto" ? "Full Auto webhook" : "Decision webhook"}</span>
      </summary>
      <div className="mt-3 space-y-4 text-sm">
        {!ready.enabled && ready.disabledReason && <p className="text-xs text-amber-700 dark:text-amber-300">{ready.disabledReason}</p>}

        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Automated checks</p>
          <ul className="space-y-1">
            {ready.checks.map((c) => (
              <li key={c.code} className="flex items-center justify-between gap-2">
                <span>{c.label}</span>
                <Badge tone={c.ok ? "success" : "neutral"}>{c.ok ? "OK" : "Not yet"}</Badge>
              </li>
            ))}
          </ul>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <Button size="sm" variant="outline" loading={busy} onClick={test}>Run connection test</Button>
            {ready.lastConnectionTest && (
              <span className="text-xs text-slate-500">
                Last: {ready.lastConnectionTest.ok ? "passed" : "failed"} · {formatDate(ready.lastConnectionTest.at)} · {ready.lastConnectionTest.detail}
              </span>
            )}
          </div>
        </div>

        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Webhook URLs to set on the Veriff integration</p>
          <ul className="space-y-0.5 font-mono text-xs text-slate-600 dark:text-slate-300">
            <li>{ready.plan === "full_auto" ? ready.webhookUrls.fullAuto : ready.webhookUrls.decision}</li>
            <li>{ready.webhookUrls.events} <span className="font-sans text-slate-400">(progress only)</span></li>
          </ul>
          <p className="mt-1 text-xs text-slate-500">
            Last signed webhook: {ready.lastAuthenticatedWebhookAt ? formatDate(ready.lastAuthenticatedWebhookAt) : "none yet"}
            {ready.lastRejectedWebhookAt && ` · last rejected: ${formatDate(ready.lastRejectedWebhookAt)}`}
          </p>
        </div>

        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Go-live gates {ready.isProduction ? `(${openGates} open -- production stays off until all are confirmed)` : "(required before switching to the production integration)"}
          </p>
          <ul className="space-y-2">
            {ready.gates.map((g) => (
              <li key={g.code} className="rounded-lg bg-white p-3 ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span>{g.label}</span>
                  <span className="flex items-center gap-2">
                    <Badge tone={g.confirmed ? "success" : "neutral"}>{g.confirmed ? "Confirmed" : "Open"}</Badge>
                    {g.confirmed ? (
                      <Button size="sm" variant="ghost" onClick={() => saveGate(g.code, false)}>Reopen</Button>
                    ) : (
                      <Button size="sm" variant="outline" onClick={() => { setEditing(g.code); setEvidence(""); setNote(""); }}>Confirm</Button>
                    )}
                  </span>
                </div>
                {g.confirmed && (
                  <p className="mt-1 text-xs text-slate-500">
                    {g.evidenceReference && `Evidence: ${g.evidenceReference} · `}{g.confirmedAt && formatDate(g.confirmedAt)}{g.note && ` · ${g.note}`}
                  </p>
                )}
                {editing === g.code && (
                  <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
                    <input className="rounded-lg bg-slate-50 px-2 py-1.5 text-xs ring-1 ring-slate-200 dark:bg-slate-800 dark:ring-slate-700"
                      placeholder="Evidence reference (contract, ticket, link)" value={evidence} onChange={(e) => setEvidence(e.target.value)}
                      aria-label="Evidence reference" />
                    <input className="rounded-lg bg-slate-50 px-2 py-1.5 text-xs ring-1 ring-slate-200 dark:bg-slate-800 dark:ring-slate-700"
                      placeholder="Note" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note" />
                    <Button size="sm" onClick={() => saveGate(g.code, true)}>Save</Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </details>
  );
}

const MAPPING_DECISIONS = ["resubmission_requested", "declined", "review", "expired", "abandoned"];
const ZOIKO_CODES = [
  "DOCUMENT_UNREADABLE", "DOCUMENT_NUMBER_INVALID", "DOCUMENT_UNSUPPORTED", "DOCUMENT_EXPIRED", "NAME_MISMATCH",
  "BINDING_FAILED", "MORE_INFORMATION_NEEDED", "RESUBMISSION_REQUESTED", "PROVIDER_DECLINED", "SESSION_EXPIRED",
  "SESSION_ABANDONED",
];

/** Provider reason codes -> Zoiko safe reason codes (contract-dependent). */
function ReasonMappingsPanel({ showToast }: { showToast: (m: string) => void }) {
  const [rows, setRows] = useState<ReasonMapping[]>([]);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState({ providerDecision: "resubmission_requested", providerReasonCode: "", zoikoReasonCode: "DOCUMENT_UNREADABLE", description: "" });

  const load = useCallback(() => {
    listReasonMappings().then(setRows).catch(() => setRows([]));
  }, []);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  async function save(mapping: Omit<ReasonMapping, "id">) {
    if (!mapping.providerReasonCode.trim()) {
      showToast("Enter the provider's reason code");
      return;
    }
    try {
      await upsertReasonMapping(mapping);
      showToast("Reason mapping saved");
      load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Could not save the mapping");
    }
  }

  const selectClass = "rounded-lg bg-white px-2 py-1 text-xs ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700";
  return (
    <details className="rounded-xl bg-slate-50 p-4 dark:bg-slate-800/60" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary className="cursor-pointer text-sm font-semibold text-primary-900 dark:text-white">Provider reason-code mapping</summary>
      <p className="mt-2 text-xs text-slate-500">
        How Veriff&apos;s own reason codes appear to people. Confirm the list against the contracted Veriff product.
        Unmapped codes fall back to generic, safe wording.
      </p>
      <table className="mt-2 w-full text-left text-xs">
        <thead className="text-slate-500">
          <tr><th className="py-1 pr-2">Decision</th><th className="py-1 pr-2">Veriff code</th><th className="py-1 pr-2">Shown as</th><th className="py-1 pr-2">Description</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-slate-200 dark:border-slate-700">
              <td className="py-1.5 pr-2">{codeLabel(r.providerDecision)}</td>
              <td className="py-1.5 pr-2 font-mono">{r.providerReasonCode}</td>
              <td className="py-1.5 pr-2">
                <select className={selectClass} value={r.zoikoReasonCode} aria-label={`Zoiko code for ${r.providerReasonCode}`}
                  onChange={(e) => save({ ...r, zoikoReasonCode: e.target.value })}>
                  {ZOIKO_CODES.map((c) => <option key={c} value={c}>{codeLabel(c)}</option>)}
                </select>
              </td>
              <td className="py-1.5 pr-2 text-slate-500">{r.description}</td>
            </tr>
          ))}
          <tr className="border-t border-slate-200 dark:border-slate-700">
            <td className="py-1.5 pr-2">
              <select className={selectClass} value={draft.providerDecision} aria-label="Provider decision"
                onChange={(e) => setDraft({ ...draft, providerDecision: e.target.value })}>
                {MAPPING_DECISIONS.map((d) => <option key={d} value={d}>{codeLabel(d)}</option>)}
              </select>
            </td>
            <td className="py-1.5 pr-2">
              <input className={`${selectClass} w-20`} value={draft.providerReasonCode} placeholder="code" aria-label="Veriff reason code"
                onChange={(e) => setDraft({ ...draft, providerReasonCode: e.target.value })} />
            </td>
            <td className="py-1.5 pr-2">
              <select className={selectClass} value={draft.zoikoReasonCode} aria-label="Shown as"
                onChange={(e) => setDraft({ ...draft, zoikoReasonCode: e.target.value })}>
                {ZOIKO_CODES.map((c) => <option key={c} value={c}>{codeLabel(c)}</option>)}
              </select>
            </td>
            <td className="py-1.5 pr-2">
              <span className="flex gap-2">
                <input className={selectClass} value={draft.description} placeholder="description" aria-label="Description"
                  onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
                <Button size="sm" variant="outline" onClick={() => save({ providerCode: "veriff", ...draft })}>Add</Button>
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </details>
  );
}

/** Admins who aren't super admins can't open review cases. To look at one
 *  document they request time-limited, reason-coded break-glass access
 *  (ZR-ENG-CLR-012 AC-27); the grant and every view are audit-logged. */
function BreakGlassView({ id, admin, showToast }: { id: number; admin: AdminProfile | null; showToast: (m: string) => void }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);

  async function request() {
    if (!reason.trim()) {
      showToast("A reason is required for break-glass access");
      return;
    }
    setBusy(true);
    try {
      const grant = await apiClientFetch<{ grantId: number; expiresAt: string }>(
        `/api/identity-verifications/${id}/break-glass-access`,
        { method: "POST", body: JSON.stringify({ reason: reason.trim() }) },
      );
      setExpiresAt(grant.expiresAt);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Could not grant break-glass access");
    } finally {
      setBusy(false);
    }
  }

  if (expiresAt) {
    return (
      <div className="space-y-3">
        <p className="flex items-center gap-2 text-sm text-amber-700 dark:text-amber-300">
          <Lock className="h-4 w-4" aria-hidden="true" /> Break-glass access until {formatDate(expiresAt)} -- this view is logged.
        </p>
        <SecureEvidenceViewer id={id} contentType="" watermark={`${admin?.email ?? ""} · break-glass · ${new Date().toISOString().slice(0, 16).replace("T", " ")} UTC`} />
        <p className="text-xs text-slate-500">Decisions on identity verifications are made by super admins.</p>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-600 dark:text-slate-300">
        Identity documents aren&apos;t visible by default. If you need to see this one, request time-limited access with a
        reason. The request and every view are recorded.
      </p>
      <label className="block text-sm">
        <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">Reason (required)</span>
        <textarea className="w-full rounded-xl bg-white px-3 py-2 text-sm ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
          rows={2} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      <Button loading={busy} onClick={request}>Request break-glass access</Button>
    </div>
  );
}
