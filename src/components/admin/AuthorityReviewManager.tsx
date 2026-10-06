"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Building, CheckCircle2, Database, FileText, History, KeyRound, UserCheck, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { AuthorityPackEditor } from "@/components/admin/AuthorityPackEditor";
import { errorMessage } from "@/lib/user-api";
import { formatDate } from "@/lib/utils";
import {
  AuthorityCase, AuthorityQueueItem, Organization, adminAuthorityEvidenceUrl, adminRevokeAuthority, assignAuthorityCase,
  recordOwnershipChange, authorityStateLabel, authorityStateTone, decideOrganization, getAuthorityCase, getAuthorityMetrics,
  getAuthorityReviewReasons, listAuthorityQueue, listPendingOrganizations, relationshipLabel, reviewAuthority, scopeLabel,
} from "@/lib/authority-verification";

const FILTERS = [
  { value: "MANUAL_REVIEW", label: "In review" },
  { value: "ACTION_REQUIRED", label: "Action required" },
  { value: "VERIFIED", label: "Verified" },
  { value: "REJECTED", label: "Rejected" },
  { value: "REVOKED", label: "Revoked" },
  { value: "all", label: "All" },
];
type Decision = "APPROVE" | "REQUEST_EVIDENCE" | "REJECT";
const DECISION_LABEL: Record<Decision, string> = {
  APPROVE: "Approve authority", REQUEST_EVIDENCE: "Request more evidence", REJECT: "Reject",
};
const REVOKE_REASONS = ["REVOKED_BY_TRUST_SAFETY", "REVOKED_BY_PRINCIPAL", "OWNERSHIP_CHANGED"];

function label(code: string) {
  return code.toLowerCase().replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

function Signal({ value, text }: { value: boolean | null | undefined; text: string }) {
  if (value === null || value === undefined) return <span className="text-slate-400">{text}: unknown</span>;
  return value ? (
    <span className="flex items-center gap-1 text-emerald-700 dark:text-emerald-300"><CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />{text}</span>
  ) : (
    <span className="flex items-center gap-1 text-accent-700 dark:text-accent-300"><XCircle className="h-3.5 w-3.5" aria-hidden="true" />{text}</span>
  );
}

/**
 * ZR-AUTHORITY-002 Section 11 -- Trust & Safety authority review: the
 * verified identity, the property, the claimed relationship, evidence with
 * automated match signals and conflicting claims side by side. Decisions
 * need a standard reason code; conflicts, entity chains and integrity
 * signals need a second, different reviewer. A case is held by one assigned
 * reviewer at a time (Section 13); opening evidence or deciding takes an
 * unassigned case. Reviewers can't edit the host's evidence.
 */
export function AuthorityReviewManager() {
  const [filter, setFilter] = useState("MANUAL_REVIEW");
  const [queue, setQueue] = useState<AuthorityQueueItem[]>([]);
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [metrics, setMetrics] = useState<Record<string, unknown> | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setQueue(await listAuthorityQueue(filter));
      setOrgs(await listPendingOrganizations().catch(() => []));
      setMetrics(await getAuthorityMetrics(30).catch(() => null));
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
        <KeyRound className="h-5 w-5 text-primary-700 dark:text-primary-300" aria-hidden="true" />
        <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Listing authority verification</h2>
        <span className="text-xs text-slate-400">ZR-AUTHORITY-002 review queue</span>
      </div>

      {metrics && (
        <dl className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-5">
          {(["started", "submitted", "verified", "pending_review", "auto_approval_rate"] as const).map((k) => (
            <div key={k} className="rounded-lg bg-slate-50 p-2 dark:bg-slate-800/60">
              <dt className="text-slate-400">{label(k)}</dt>
              <dd className="font-semibold text-primary-900 dark:text-white">
                {metrics[k] === null || metrics[k] === undefined ? "--" : `${metrics[k]}${k.endsWith("rate") ? "%" : ""}`}
              </dd>
            </div>
          ))}
        </dl>
      )}

      <AuthorityPackEditor />

      {orgs.length > 0 && (
        <div className="space-y-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
          <p className="flex items-center gap-2 text-xs font-semibold text-slate-700 dark:text-slate-200">
            <Building className="h-4 w-4" aria-hidden="true" /> Organizations awaiting verification
          </p>
          <ul className="space-y-1 text-sm">
            {orgs.map((o) => (
              <li key={o.id} className="flex flex-wrap items-center justify-between gap-2">
                <span>{o.name} {o.registrationNumber && <span className="text-xs text-slate-500">· {o.registrationNumber}</span>}
                  {o.countryCode && <span className="text-xs text-slate-500"> · {o.countryCode}</span>}</span>
                <span className="flex gap-2">
                  <Button size="sm" variant="outline" onClick={() => decideOrganization(o.id, true).then(() => { notify("Organization verified"); void load(); })}>Verify</Button>
                  <Button size="sm" variant="ghost" onClick={() => decideOrganization(o.id, false).then(() => { notify("Organization rejected"); void load(); })}>Reject</Button>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Filter authority verifications">
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
                  <span className="font-mono text-xs text-slate-400">AV-{item.id}</span>
                  <Badge tone={authorityStateTone(item.state)} dot>{authorityStateLabel[item.state]}</Badge>
                  <span className="text-sm text-primary-900 dark:text-white">Property #{item.propertyId}</span>
                  <span className="text-xs text-slate-500">{label(item.relationshipType)} · {item.countryCode || "--"}</span>
                  {item.awaitingSecondApproval && <Badge tone="warning">Awaiting second approval</Badge>}
                  {item.isReconsideration && <Badge tone="primary">Reconsideration</Badge>}
                  {item.assignedAdminId !== null && <Badge tone="neutral">Assigned to reviewer #{item.assignedAdminId}</Badge>}
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

function CaseModal({ id, onClose, onDecided }: { id: number; onClose: () => void; onDecided: (message: string) => void }) {
  const [c, setC] = useState<AuthorityCase | null>(null);
  const [reasons, setReasons] = useState<Record<Decision, { code: string; message: string }[]> | null>(null);
  const [decision, setDecision] = useState<Decision>("APPROVE");
  const [reasonCode, setReasonCode] = useState("");
  const [note, setNote] = useState("");
  const [revokeCode, setRevokeCode] = useState(REVOKE_REASONS[0]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const reload = useCallback(() => {
    getAuthorityCase(id).then(setC).catch((err) => setError(errorMessage(err, "Could not load the case.")));
  }, [id]);

  useEffect(() => {
    reload();
    getAuthorityReviewReasons().then(setReasons).catch(() => setReasons(null));
  }, [reload]);

  async function assign(release: boolean) {
    if (!c) return;
    setBusy(true);
    setError("");
    try {
      await assignAuthorityCase(c.id, release);
      reload();
    } catch (err) {
      setError(errorMessage(err, "Could not change the assignment."));
    } finally {
      setBusy(false);
    }
  }

  async function ownershipChanged() {
    if (!c || !window.confirm("Record an ownership change for this property? Owner and agent authority for it will be withdrawn and its live listings paused.")) return;
    setBusy(true);
    try {
      const r = await recordOwnershipChange(c.property.id);
      onDecided(`Ownership change recorded -- ${r.reopened} authority record(s) reopened`);
    } catch (err) {
      setError(errorMessage(err, "Could not record the ownership change."));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { setReasonCode(reasons?.[decision]?.[0]?.code ?? ""); }, [decision, reasons]);

  async function decide() {
    if (!c) return;
    setBusy(true);
    setError("");
    try {
      const r = await reviewAuthority(c, decision, reasonCode, note);
      onDecided(r.state === "MANUAL_REVIEW" ? "First approval recorded -- a second reviewer must approve" : `Decision recorded: ${authorityStateLabel[r.state]}`);
    } catch (err) {
      setError(errorMessage(err, "Could not record the decision."));
    } finally {
      setBusy(false);
    }
  }

  async function revoke() {
    if (!c) return;
    setBusy(true);
    try {
      await adminRevokeAuthority(c.id, revokeCode);
      onDecided("Authority revoked; dependent listings paused");
    } catch (err) {
      setError(errorMessage(err, "Could not revoke."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open onClose={onClose} title={`Authority case AV-${id}`} size="xl">
      {!c ? <p className="text-sm text-slate-400" role="status">{error || "Loading..."}</p> : (
        <div className="space-y-5 text-sm">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
              <p className="text-xs text-slate-400">Verified identity</p>
              <p className="font-semibold">{c.verifiedLegalName || "--"} <span className="text-xs text-slate-400">(party #{c.partyId})</span></p>
              <p className="mt-1 text-xs">{relationshipLabel[c.relationshipType]}</p>
              {c.organization && <p className="text-xs">Via {c.organization.name} ({c.organization.status.toLowerCase()})</p>}
              {c.principalName && <p className="text-xs">Principal: {c.principalName}</p>}
            </div>
            <div className="rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
              <p className="text-xs text-slate-400">Property #{c.property.id}</p>
              <p className="font-semibold">{c.property.address}, {c.property.city} {c.property.postalCode}</p>
              <p className="mt-1 text-xs">{c.property.verified ? "Property verified" : "Property not verified"}</p>
              {c.scopeCodes.length > 0 && <p className="text-xs">Scope: {c.scopeCodes.map((s) => scopeLabel[s]).join(", ")}</p>}
              {(c.effectiveAt || c.expiresAt) && <p className="text-xs">Dates: {c.effectiveAt ? formatDate(c.effectiveAt) : "--"} → {c.expiresAt ? formatDate(c.expiresAt) : "--"}</p>}
            </div>
          </div>

          {c.reasonCodes.length > 0 && (
            <p className="flex flex-wrap items-center gap-2 text-xs">
              <AlertTriangle className="h-4 w-4 text-amber-600" aria-hidden="true" />
              {c.reasonCodes.map((code) => <span key={code} className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">{label(code)}</span>)}
            </p>
          )}
          {c.state === "MANUAL_REVIEW" && (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 p-3 text-xs dark:bg-slate-800/60">
              <span className="flex items-center gap-2">
                <UserCheck className="h-4 w-4" aria-hidden="true" />
                {c.assignedAdminId === null ? "Unassigned -- opening evidence or deciding assigns it to you"
                  : `Assigned to reviewer #${c.assignedAdminId}${c.assignedAt ? ` since ${formatDate(c.assignedAt)}` : ""}`}
              </span>
              <span className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => assign(false)} disabled={busy}>Assign to me</Button>
                {c.assignedAdminId !== null && <Button size="sm" variant="ghost" onClick={() => assign(true)} disabled={busy}>Release</Button>}
              </span>
            </div>
          )}
          {c.reconsiderationNote && <p className="rounded-lg bg-primary-50 p-3 text-xs dark:bg-primary-500/10">Host&apos;s reconsideration note: {c.reconsiderationNote}</p>}

          <div className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Evidence</p>
            <ul className="space-y-2">
              {c.evidence.map((e) => (
                <li key={e.id} className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    {e.sourceType === "UPLOAD" && e.available ? (
                      <a className="flex items-center gap-2 font-semibold text-primary-700 hover:underline dark:text-primary-300" target="_blank" rel="noreferrer"
                         href={adminAuthorityEvidenceUrl(c.id, e.id)}>
                        <FileText className="h-4 w-4" aria-hidden="true" /> {e.originalFilename}
                      </a>
                    ) : e.sourceType === "REGISTRY" || e.sourceType === "CONNECTOR" ? (
                      <span className="flex items-center gap-2 font-semibold"><Database className="h-4 w-4" aria-hidden="true" />
                        {e.issuer} record {e.documentReference}</span>
                    ) : <span className="font-semibold">{e.sourceType === "OWNER_CONFIRMATION" ? `Confirmed by ${e.issuer}` : e.originalFilename}</span>}
                    <span className="text-xs text-slate-500">{label(e.requirementId)} · {e.processingStatus.toLowerCase()}</span>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-3 text-xs">
                    <Signal value={e.readable} text={e.textSource === "OCR" ? `Readable (OCR ${e.ocrConfidence ?? "--"}%)` : "Readable"} />
                    <Signal value={e.typeMatched} text="Accepted document type" />
                    <Signal value={e.propertyMatched} text="Property matches" />
                    <Signal value={e.nameMatched} text="Name matches" />
                    <Signal value={e.principalMatched} text="Principal matches" />
                    {e.reusedElsewhere && <span className="text-accent-700">Reused on another claim</span>}
                    {e.tamperSignal && <span className="text-accent-700">Possible edit signal (weak)</span>}
                    {e.sourceType === "UPLOAD" && (
                      <span className={e.scanStatus === "ERROR" ? "text-accent-700" : "text-slate-500"}>
                        Malware scan: {e.scanStatus === "CLEAN" ? "clean" : e.scanStatus === "ERROR" ? "could not complete" : "not configured"}
                      </span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </div>

          {c.confirmations.length > 0 && (
            <div className="space-y-1 text-xs">
              <p className="font-semibold uppercase tracking-wide text-slate-400">Owner / landlord confirmations</p>
              {c.confirmations.map((cf) => (
                <p key={cf.id}>{label(cf.kind)} · {cf.status.toLowerCase()}{cf.responderName && ` by ${cf.responderName}`}{cf.respondedAt && ` on ${formatDate(cf.respondedAt)}`}</p>
              ))}
            </div>
          )}

          {c.otherClaims.length > 0 && (
            <div className="space-y-1 text-xs">
              <p className="font-semibold uppercase tracking-wide text-slate-400">Other claims on this property</p>
              {c.otherClaims.map((o) => (
                <p key={o.id}>AV-{o.id} · party #{o.partyId} · {label(o.relationshipType)} · {authorityStateLabel[o.state]}</p>
              ))}
            </div>
          )}

          <details className="text-xs">
            <summary className="flex cursor-pointer items-center gap-1 text-slate-500"><History className="h-3.5 w-3.5" aria-hidden="true" /> History</summary>
            <ul className="mt-2 space-y-1">
              {c.events.map((e, i) => <li key={i}>{formatDate(e.createdAt)} · {label(e.type)}{e.newState && ` → ${e.newState}`} · {e.actorKind}</li>)}
            </ul>
          </details>

          {c.state === "MANUAL_REVIEW" && reasons && (
            <div className="space-y-3 rounded-xl border border-slate-200 p-4 dark:border-slate-700">
              {c.awaitingSecondApproval && <Badge tone="warning">First approval recorded -- needs a different reviewer</Badge>}
              <div className="flex flex-wrap gap-3">
                {(Object.keys(DECISION_LABEL) as Decision[]).map((d) => (
                  <label key={d} className="flex items-center gap-2">
                    <input type="radio" name="decision" checked={decision === d} onChange={() => setDecision(d)} /> {DECISION_LABEL[d]}
                  </label>
                ))}
              </div>
              <select className="w-full rounded-lg border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-800" value={reasonCode}
                      onChange={(e) => setReasonCode(e.target.value)} aria-label="Reason code">
                {reasons[decision].map((r) => <option key={r.code} value={r.code}>{label(r.code)} -- {r.message}</option>)}
              </select>
              <textarea className="w-full rounded-lg border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-800" rows={2}
                        placeholder="Internal note (never shown to the host)" value={note} onChange={(e) => setNote(e.target.value)} />
              <Button onClick={decide} loading={busy} disabled={busy || !reasonCode}>Record decision</Button>
            </div>
          )}

          {(c.state === "VERIFIED" || c.state === "EXPIRING_SOON") && (
            <div className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 p-4 dark:border-slate-700">
              <select className="rounded-lg border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800" value={revokeCode}
                      onChange={(e) => setRevokeCode(e.target.value)} aria-label="Revocation reason">
                {REVOKE_REASONS.map((r) => <option key={r} value={r}>{label(r)}</option>)}
              </select>
              <Button variant="accent" onClick={revoke} disabled={busy}>Revoke authority</Button>
            </div>
          )}
          <div className="flex justify-end">
            <Button size="sm" variant="ghost" onClick={ownershipChanged} disabled={busy}>Record ownership change for this property</Button>
          </div>
          {error && <p className="text-sm text-accent-700" role="alert">{error}</p>}
        </div>
      )}
    </Modal>
  );
}
