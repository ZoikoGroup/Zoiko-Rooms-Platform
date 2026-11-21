"use client";

import { useCallback, useEffect, useState } from "react";
import { BarChart3, FileText, History, Lock, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { AdminProfile, getCurrentAdmin } from "@/lib/auth";
import { documentTypeLabel } from "@/lib/identity-documents";
import {
  IdentityCase,
  IdentityMetrics,
  IdentityPackAdmin,
  PROVIDER_OPTIONS,
  eraseIdentityData,
  listIdentityPacks,
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
 * ZR-IDENTITY-001 -- internal identity view (not customer-facing). Identities are decided only by the server's
 * face match: admins see the verifications and a read-only case (header, checks, history), can erase data, and manage
 * the country packs. There is no approve / reject. Metrics sit above the list.
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
    getIdentityMetrics(30).then(setMetrics).catch(() => setMetrics(null));
  }, [queue]);

  return (
    <section className="space-y-4 rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex items-center gap-2">
        <ShieldCheck className="h-5 w-5 text-primary-700 dark:text-primary-300" aria-hidden="true" />
        <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Identity verifications</h2>
        <span className="text-xs text-slate-400">Decided by the face match -- internal view, not customer-facing</span>
      </div>

      {metrics && <MetricsStrip metrics={metrics} />}
      <PacksPanel showToast={showToast} />

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
        <Metric label="Face check unavailable" value={pct(metrics.providerErrorRate)} />
        <Metric label="Top reasons" value={topReasons.length ? topReasons.map(([c, n]) => `${codeLabel(c)} (${n})`).join(", ") : "--"} />
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
  useEffect(() => {
    getIdentityCase(id).then(setRecord).catch(() => setError("Could not load this case."));
  }, [id]);

  return (
    <Modal open onClose={onClose} title={`Identity verification #${id}`} size="xl">
      {error ? (
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
                  Photos are compared in memory and never stored.
                </p>
              )}
              {record.verifierNotes && <p className="text-xs text-slate-500">Note: {record.verifierNotes}</p>}
            </div>

            {/* Checks (the face match decides) */}
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
                The face match decides this verification. Only the face on the document photo was compared with the
                selfie: the document itself was not checked.
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

/** Lawful erasure (DSAR). */
function CaseTools({ record, showToast, onChanged }: { record: IdentityCase; showToast: (m: string) => void; onChanged: () => void }) {
  const [eraseOpen, setEraseOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  async function erase() {
    if (!reason.trim()) {
      showToast("Give the reason for the erasure request");
      return;
    }
    setBusy(true);
    try {
      const r = await eraseIdentityData(record.partyId, reason.trim());
      showToast(`Erased ${r.sessions} verification(s)`);
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
      {!eraseOpen ? (
        <Button size="sm" variant="ghost" onClick={() => setEraseOpen(true)}>Erase identity data (privacy request)</Button>
      ) : (
        <div className="w-full space-y-2 rounded-xl bg-accent-50 p-3 dark:bg-accent-500/10">
          <p className="text-xs text-accent-700 dark:text-accent-300">
            Deletes stored evidence, document numbers, names and dates of birth for this account, and leaves the person
            unverified. Decisions and the audit trail are kept.
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
          The face thresholds and models are set on the server (FACE_MATCH_PASS / FACE_MATCH_FAIL) -- they are never entered or shown here.
        </p>
      </div>
    </details>
  );
}
