"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import {
  AlertTriangle, ArrowLeft, ArrowRight, BadgeCheck, CheckCircle2, Circle, Clock, FileText, Mail, ShieldCheck,
  Trash2, Upload,
} from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Field, inputClass } from "@/components/user/ui";
import { errorMessage } from "@/lib/user-api";
import { formatDate } from "@/lib/utils";
import {
  AuthorityRequirement, AuthorityVerification, Organization, RelationshipType, ScopeCode, authorityEvidenceUrl,
  authorityStateLabel, authorityStateTone, createOrganization, listOrganizations, listPropertyAuthority,
  relationshipHint, relationshipLabel, removeAuthorityEvidence, renewAuthorityVerification, requestOwnerConfirmation,
  revokeAuthorityVerification, scopeLabel, setAuthorityDetails, startAuthorityVerification,
  submitAuthorityVerification, uploadAuthorityEvidence,
} from "@/lib/authority-verification";

/**
 * ZR-AUTHORITY-002 host flow (Sections 3-8):
 * 0 Role -> 1 Authority details (agent / sublet / representative only) ->
 * 2 Evidence & confirmations -> 3 Review & submit -> outcome.
 * Requirements come from the server's Authority Regulatory Pack; every
 * status and CTA is server-derived. Authority is separate from identity and
 * property verification and is never inferred from them.
 */

type Step = 0 | 1 | 2 | 3;
const RELATIONSHIPS: RelationshipType[] = ["OWNER", "CO_OWNER", "REPRESENTATIVE", "AGENT", "PROPERTY_MANAGER", "TENANT_SUBLETTER"];
const OPEN = ["COLLECTING", "ACTION_REQUIRED"];
const SCOPES: ScopeCode[] = ["ADVERTISE", "RENT", "MANAGE", "SUBLET", "COLLECT_RENT"];

function newKey() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

export function AuthorityVerificationWizard({ propertyId, propertyLabel, onClose }: {
  propertyId: number;
  propertyLabel: string;
  onClose?: () => void;
}) {
  const [v, setV] = useState<AuthorityVerification | null>(null);
  const [history, setHistory] = useState<AuthorityVerification[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [step, setStep] = useState<Step>(0);
  const [startKey] = useState(newKey);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await listPropertyAuthority(propertyId);
      const current = rows.find((r) => r.state !== "SUPERSEDED") ?? null;
      setHistory(rows);
      setV(current);
      if (current && OPEN.includes(current.state)) setStep(current.evidence.length ? 2 : needsDetails(current) ? 1 : 2);
    } catch (err) {
      setError(errorMessage(err, "Could not load authority verification."));
    } finally {
      setLoading(false);
    }
  }, [propertyId]);

  useEffect(() => { void load(); }, [load]);

  async function run(action: () => Promise<AuthorityVerification>, next?: Step) {
    setBusy(true);
    setError("");
    try {
      const updated = await action();
      setV(updated);
      if (next !== undefined) setStep(next);
      return updated;
    } catch (err) {
      setError(errorMessage(err, "Something went wrong. Your progress is saved -- try again."));
      return null;
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p className="text-sm text-slate-400" role="status">Loading authority verification...</p>;

  const editing = v && OPEN.includes(v.state);

  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">{propertyLabel}</p>
        <h2 className="font-heading text-lg font-bold text-primary-900 dark:text-white">
          {editing || !v ? "Verify your authority to list this property" : authorityStateLabel[v.state]}
        </h2>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          This is separate from your identity and the property check: it confirms you have the right to advertise and
          rent this property.
        </p>
      </header>

      {error && (
        <p className="flex items-start gap-2 rounded-lg bg-accent-50 px-3 py-2 text-sm text-accent-800 dark:bg-accent-500/10 dark:text-accent-200" role="alert">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" /> {error}
        </p>
      )}

      {!v ? (
        <RoleStep busy={busy} onPick={(rel) => run(() => startAuthorityVerification(propertyId, rel, `${startKey}-${rel}`),
          undefined).then((started) => started && setStep(needsDetails(started) ? 1 : 2))} />
      ) : editing ? (
        <>
          <Stepper step={step} showDetails={needsDetails(v)} />
          {v.state === "ACTION_REQUIRED" && v.reason.message && (
            <p className="flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" /> {v.reason.message}
            </p>
          )}
          {step <= 1 && needsDetails(v) && (
            <DetailsStep v={v} busy={busy} onSave={(details) => run(() => setAuthorityDetails(v, details), 2)} />
          )}
          {(step === 2 || (step <= 1 && !needsDetails(v))) && (
            <EvidenceStep v={v} busy={busy} run={run} onBack={needsDetails(v) ? () => setStep(1) : undefined}
                          onNext={() => setStep(3)} />
          )}
          {step === 3 && (
            <ReviewStep v={v} busy={busy} onBack={() => setStep(2)}
                        onSubmit={() => run(() => submitAuthorityVerification(v, newKey()))} />
          )}
        </>
      ) : (
        <Outcome v={v} busy={busy} history={history}
                 onRenew={(note) => run(() => renewAuthorityVerification(v, note)).then((n) => {
                   if (n) setStep(needsDetails(n) ? 1 : 2);
                   void load();
                 })}
                 onRevoke={() => run(() => revokeAuthorityVerification(v))}
                 onStartNew={() => { setV(null); setStep(0); }}
                 onClose={onClose} />
      )}
    </div>
  );
}

function needsDetails(v: AuthorityVerification) {
  return v.route !== "OWNER" || v.relationshipType === "REPRESENTATIVE";
}

function Stepper({ step, showDetails }: { step: Step; showDetails: boolean }) {
  const steps = [showDetails ? "Authority details" : null, "Evidence", "Review"].filter(Boolean) as string[];
  const index = showDetails ? step - 1 : step - 2;
  return (
    <ol className="flex flex-wrap gap-3 text-xs" aria-label="Progress">
      {steps.map((label, i) => (
        <li key={label} className={`flex items-center gap-1.5 ${i <= index ? "font-semibold text-primary-700 dark:text-primary-300" : "text-slate-400"}`}
            aria-current={i === index ? "step" : undefined}>
          {i < index ? <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" /> : <Circle className="h-3.5 w-3.5" aria-hidden="true" />}
          {label}
        </li>
      ))}
    </ol>
  );
}

function RoleStep({ busy, onPick }: { busy: boolean; onPick: (rel: RelationshipType) => void }) {
  const [choice, setChoice] = useState<RelationshipType | null>(null);
  return (
    <fieldset className="space-y-3">
      <legend className="text-sm font-semibold text-slate-700 dark:text-slate-200">What is your relationship to this property?</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {RELATIONSHIPS.map((rel) => (
          <label key={rel} className={`flex cursor-pointer flex-col gap-1 rounded-xl border p-3 text-sm transition ${
            choice === rel ? "border-primary-500 bg-primary-50 dark:bg-primary-500/10" : "border-slate-200 hover:border-primary-300 dark:border-slate-700"}`}>
            <span className="flex items-center gap-2 font-semibold text-slate-800 dark:text-slate-100">
              <input type="radio" name="relationship" value={rel} checked={choice === rel} onChange={() => setChoice(rel)} />
              {relationshipLabel[rel]}
            </span>
            <span className="text-xs text-slate-500 dark:text-slate-400">{relationshipHint[rel]}</span>
          </label>
        ))}
      </div>
      <div className="flex justify-end">
        <Button disabled={!choice || busy} loading={busy} onClick={() => choice && onPick(choice)}>
          Continue <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
    </fieldset>
  );
}

function DetailsStep({ v, busy, onSave }: {
  v: AuthorityVerification;
  busy: boolean;
  onSave: (details: Parameters<typeof setAuthorityDetails>[1]) => void;
}) {
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [capacity, setCapacity] = useState<"PERSONAL" | "ORGANIZATION">(v.actingCapacity === "ORGANIZATION" ? "ORGANIZATION" : "PERSONAL");
  const [orgId, setOrgId] = useState<number | null>(v.organization?.id ?? null);
  const [newOrg, setNewOrg] = useState({ name: "", registrationNumber: "", representativeRole: "" });
  const [principal, setPrincipal] = useState(v.principalName);
  const [scopes, setScopes] = useState<ScopeCode[]>(v.scopeCodes);
  const [effectiveAt, setEffectiveAt] = useState(v.effectiveAt?.slice(0, 10) ?? "");
  const [expiresAt, setExpiresAt] = useState(v.expiresAt?.slice(0, 10) ?? "");
  const [restrictions, setRestrictions] = useState(v.restrictions);
  const [orgError, setOrgError] = useState("");
  const canUseOrg = v.route === "AGENT" || v.relationshipType === "REPRESENTATIVE";

  useEffect(() => { if (canUseOrg) listOrganizations().then(setOrgs).catch(() => setOrgs([])); }, [canUseOrg]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    let organizationId = capacity === "ORGANIZATION" ? orgId ?? undefined : undefined;
    if (capacity === "ORGANIZATION" && !organizationId) {
      if (!newOrg.name.trim()) {
        setOrgError("Choose or add the organization you act for.");
        return;
      }
      try {
        const created = await createOrganization({ ...newOrg, countryCode: v.countryCode });
        organizationId = created.id;
      } catch (err) {
        setOrgError(errorMessage(err, "Could not save the organization."));
        return;
      }
    }
    onSave({
      actingCapacity: canUseOrg ? capacity : undefined, organizationId, principalName: principal,
      scopeCodes: scopes, effectiveAt: effectiveAt || undefined, expiresAt: expiresAt || undefined, restrictions,
    });
  }

  const principalLabel = v.route === "SUBLET" ? "Landlord's full name" : v.relationshipType === "REPRESENTATIVE"
    ? "Owning company, trust or estate" : "Owner / principal's full name";

  return (
    <form onSubmit={submit} className="space-y-4">
      {canUseOrg && (
        <fieldset className="space-y-2">
          <legend className="text-sm font-semibold text-slate-700 dark:text-slate-200">Are you acting personally or for an organization?</legend>
          <div className="flex flex-wrap gap-4 text-sm">
            {(["PERSONAL", "ORGANIZATION"] as const).map((c) => (
              <label key={c} className="flex items-center gap-2">
                <input type="radio" name="capacity" checked={capacity === c} onChange={() => setCapacity(c)} />
                {c === "PERSONAL" ? "Personally" : "For an organization"}
              </label>
            ))}
          </div>
          {capacity === "ORGANIZATION" && (
            <div className="space-y-2 rounded-xl border border-slate-200 p-3 dark:border-slate-700">
              {orgs.length > 0 && (
                <Field label="Organization">
                  <select className={inputClass} value={orgId ?? ""} onChange={(e) => setOrgId(e.target.value ? Number(e.target.value) : null)}>
                    <option value="">Add a new organization</option>
                    {orgs.map((o) => <option key={o.id} value={o.id}>{o.name}{o.status === "VERIFIED" ? " (verified)" : ""}</option>)}
                  </select>
                </Field>
              )}
              {!orgId && (
                <div className="grid gap-2 sm:grid-cols-3">
                  <Field label="Organization name">
                    <input className={inputClass} value={newOrg.name} onChange={(e) => setNewOrg({ ...newOrg, name: e.target.value })} />
                  </Field>
                  <Field label="Registration number" hint="Optional">
                    <input className={inputClass} value={newOrg.registrationNumber}
                           onChange={(e) => setNewOrg({ ...newOrg, registrationNumber: e.target.value })} />
                  </Field>
                  <Field label="Your role">
                    <input className={inputClass} value={newOrg.representativeRole}
                           onChange={(e) => setNewOrg({ ...newOrg, representativeRole: e.target.value })} />
                  </Field>
                </div>
              )}
              <p className="text-xs text-slate-500">A company registration shows the company exists -- it isn&apos;t a mandate for this property.</p>
              {orgError && <p className="text-xs text-accent-700" role="alert">{orgError}</p>}
            </div>
          )}
        </fieldset>
      )}

      <Field label={principalLabel} hint="As it appears on the mandate, deed or permission">
        <input className={inputClass} value={principal} onChange={(e) => setPrincipal(e.target.value)} autoComplete="off" />
      </Field>

      {v.route !== "OWNER" && (
        <>
          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold text-slate-700 dark:text-slate-200">What does the authority allow?</legend>
            <div className="flex flex-wrap gap-3 text-sm">
              {SCOPES.map((s) => (
                <label key={s} className="flex items-center gap-2">
                  <input type="checkbox" checked={scopes.includes(s)}
                         onChange={(e) => setScopes(e.target.checked ? [...scopes, s] : scopes.filter((x) => x !== s))} />
                  {scopeLabel[s]}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Authority starts">
              <input type="date" className={inputClass} value={effectiveAt} onChange={(e) => setEffectiveAt(e.target.value)} />
            </Field>
            <Field label="Authority ends" hint="Leave blank if it has no end date">
              <input type="date" className={inputClass} value={expiresAt} onChange={(e) => setExpiresAt(e.target.value)} />
            </Field>
          </div>
          <Field label="Restrictions" hint="Optional -- e.g. minimum stay, rooms excluded">
            <textarea className={inputClass} rows={2} value={restrictions} onChange={(e) => setRestrictions(e.target.value)} />
          </Field>
        </>
      )}

      <div className="flex justify-end">
        <Button type="submit" loading={busy} disabled={busy}>
          Save and continue <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
    </form>
  );
}

function EvidenceStep({ v, busy, run, onBack, onNext }: {
  v: AuthorityVerification;
  busy: boolean;
  run: (action: () => Promise<AuthorityVerification>) => Promise<AuthorityVerification | null>;
  onBack?: () => void;
  onNext: () => void;
}) {
  const shown = v.requirements.filter((r) => r.required || r.status !== "MISSING" || r.requirement_id !== "CO_OWNER_CONSENT");
  const missing = v.requirements.filter((r) => r.required && r.status !== "READY" && r.status !== "AWAITING_CONFIRMATION");
  return (
    <div className="space-y-4">
      {shown.map((req) => <RequirementCard key={req.requirement_id} v={v} req={req} busy={busy} run={run} />)}
      <div className="flex items-center justify-between gap-2">
        {onBack ? (
          <Button variant="ghost" onClick={onBack}><ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back</Button>
        ) : <span />}
        <Button onClick={onNext} disabled={busy || !v.evidence.length}>
          Review <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
      {missing.length > 0 && v.evidence.length > 0 && (
        <p className="text-xs text-slate-500">You can still submit -- we&apos;ll tell you exactly what&apos;s missing.</p>
      )}
    </div>
  );
}

function RequirementCard({ v, req, busy, run }: {
  v: AuthorityVerification;
  req: AuthorityRequirement;
  busy: boolean;
  run: (action: () => Promise<AuthorityVerification>) => Promise<AuthorityVerification | null>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [issuer, setIssuer] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [confirmKey] = useState(newKey);
  const items = v.evidence.filter((e) => e.requirementId === req.requirement_id);
  const confirmations = v.confirmations.filter((c) => c.requirementId === req.requirement_id);
  const tone = req.status === "READY" ? "success" : req.status === "AWAITING_CONFIRMATION" ? "primary"
    : req.status === "NEEDS_REPLACEMENT" ? "warning" : req.required ? "warning" : "neutral";
  const statusText = { READY: "Added", AWAITING_CONFIRMATION: "Waiting for confirmation", NEEDS_REPLACEMENT: "Needs replacing",
    MISSING: req.required ? "Required" : "Optional" }[req.status];

  return (
    <section className="space-y-3 rounded-xl border border-slate-200 p-4 dark:border-slate-700" aria-labelledby={`req-${req.requirement_id}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 id={`req-${req.requirement_id}`} className="text-sm font-bold text-slate-800 dark:text-slate-100">{req.title}</h3>
          <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{req.purpose}</p>
        </div>
        <Badge tone={tone}>{statusText}</Badge>
      </div>
      {req.accepted_examples.length > 0 && (
        <p className="text-xs text-slate-500">Accepted: {req.accepted_examples.join(" · ")}</p>
      )}

      {items.length > 0 && (
        <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
          {items.map((e) => (
            <li key={e.id} className="flex items-center justify-between gap-2 py-2">
              {e.sourceType === "OWNER_CONFIRMATION" ? (
                <span className="flex items-center gap-2 text-emerald-700 dark:text-emerald-300">
                  <BadgeCheck className="h-4 w-4" aria-hidden="true" /> Confirmed by {e.issuer}
                </span>
              ) : (
                <a className="flex items-center gap-2 text-primary-700 hover:underline dark:text-primary-300" target="_blank"
                   rel="noreferrer" href={authorityEvidenceUrl(v.id, e.id)}>
                  <FileText className="h-4 w-4" aria-hidden="true" /> {e.originalFilename}
                </a>
              )}
              {e.sourceType === "UPLOAD" && (
                <button type="button" className="text-slate-400 hover:text-accent-600" disabled={busy}
                        aria-label={`Remove ${e.originalFilename}`}
                        onClick={() => run(() => removeAuthorityEvidence(v, e.id))}>
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      <form className="grid gap-2 sm:grid-cols-[1fr_auto]" onSubmit={(e) => {
        e.preventDefault();
        if (!file) return;
        void run(() => uploadAuthorityEvidence(v, req.requirement_id, file, { issuer, expiresAt })).then((ok) => {
          if (ok) { setFile(null); setIssuer(""); setExpiresAt(""); }
        });
      }}>
        <div className="grid gap-2 sm:grid-cols-3">
          <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-dashed border-slate-300 px-3 py-2 text-xs text-slate-600 dark:border-slate-600 dark:text-slate-300">
            <Upload className="h-4 w-4" aria-hidden="true" />
            <span className="truncate">{file ? file.name : "Choose a PDF, JPG or PNG"}</span>
            <input type="file" className="sr-only" accept="application/pdf,image/jpeg,image/png"
                   onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
          <input className={inputClass} placeholder="Issuer (optional)" value={issuer} onChange={(e) => setIssuer(e.target.value)}
                 aria-label="Issuer" />
          <input type="date" className={inputClass} value={expiresAt} onChange={(e) => setExpiresAt(e.target.value)}
                 aria-label="Document expiry date (optional)" title="Document expiry date (optional)" />
        </div>
        <Button type="submit" size="sm" variant="outline" disabled={!file || busy}>Upload</Button>
      </form>

      {req.owner_confirmation && (
        <div className="space-y-2 rounded-lg bg-slate-50 p-3 dark:bg-slate-800/60">
          <p className="flex items-center gap-2 text-xs font-semibold text-slate-700 dark:text-slate-200">
            <Mail className="h-4 w-4" aria-hidden="true" />
            {req.requirement_id === "TENANT_SUBLET_PERMISSION" ? "Or ask your landlord to confirm" :
              req.requirement_id === "CO_OWNER_CONSENT" ? "Ask your co-owner to confirm" : "Ask the owner to confirm"}
          </p>
          <p className="text-xs text-slate-500">
            We email them a secure link and a separate one-time code. You can&apos;t confirm on their behalf.
          </p>
          {confirmations.map((c) => (
            <p key={c.id} className="flex items-center gap-2 text-xs">
              {c.status === "PENDING" ? <Clock className="h-3.5 w-3.5 text-slate-400" aria-hidden="true" /> :
                c.status === "CONFIRMED" ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" aria-hidden="true" /> :
                  <AlertTriangle className="h-3.5 w-3.5 text-amber-600" aria-hidden="true" />}
              {c.recipientName || "Request"} -- {c.status.toLowerCase()}
              {c.status === "PENDING" && ` (link valid until ${formatDate(c.expiresAt)})`}
            </p>
          ))}
          {!req.confirmed && (
            <form className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]" onSubmit={(e) => {
              e.preventDefault();
              void run(() => requestOwnerConfirmation(v, req.requirement_id, email, name, `${confirmKey}-${email}`)).then((ok) => {
                if (ok) { setEmail(""); setName(""); }
              });
            }}>
              <input className={inputClass} placeholder="Their name" value={name} onChange={(e) => setName(e.target.value)} aria-label="Their name" />
              <input type="email" required className={inputClass} placeholder="Their email" value={email}
                     onChange={(e) => setEmail(e.target.value)} aria-label="Their email" />
              <Button type="submit" size="sm" variant="outline" disabled={busy || !email}>Send request</Button>
            </form>
          )}
        </div>
      )}
    </section>
  );
}

function ReviewStep({ v, busy, onBack, onSubmit }: {
  v: AuthorityVerification; busy: boolean; onBack: () => void; onSubmit: () => void;
}) {
  const [attested, setAttested] = useState(false);
  return (
    <div className="space-y-4">
      <dl className="grid gap-2 rounded-xl border border-slate-200 p-4 text-sm dark:border-slate-700 sm:grid-cols-2">
        <div><dt className="text-xs text-slate-400">Your role</dt><dd className="font-semibold">{relationshipLabel[v.relationshipType]}</dd></div>
        {v.organization && <div><dt className="text-xs text-slate-400">Organization</dt><dd className="font-semibold">{v.organization.name}</dd></div>}
        {v.principalName && <div><dt className="text-xs text-slate-400">Owner / principal</dt><dd className="font-semibold">{v.principalName}</dd></div>}
        {v.route !== "OWNER" && <div><dt className="text-xs text-slate-400">Scope</dt><dd className="font-semibold">{v.scopeCodes.map((s) => scopeLabel[s]).join(", ")}</dd></div>}
        {(v.effectiveAt || v.expiresAt) && (
          <div><dt className="text-xs text-slate-400">Dates</dt><dd className="font-semibold">
            {v.effectiveAt ? formatDate(v.effectiveAt) : "Now"} -- {v.expiresAt ? formatDate(v.expiresAt) : "no end date"}
          </dd></div>
        )}
        <div><dt className="text-xs text-slate-400">Evidence</dt><dd className="font-semibold">{v.evidence.length} item(s)</dd></div>
      </dl>
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" className="mt-1" checked={attested} onChange={(e) => setAttested(e.target.checked)} />
        <span>I confirm this evidence is current, has not been revoked, and I am authorized to submit it.</span>
      </label>
      <div className="flex items-center justify-between">
        <Button variant="ghost" onClick={onBack}><ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back</Button>
        <Button onClick={onSubmit} disabled={!attested || busy} loading={busy}>
          <ShieldCheck className="h-4 w-4" aria-hidden="true" /> Submit for verification
        </Button>
      </div>
    </div>
  );
}

function Outcome({ v, busy, history, onRenew, onRevoke, onStartNew, onClose }: {
  v: AuthorityVerification;
  busy: boolean;
  history: AuthorityVerification[];
  onRenew: (reconsiderationNote?: string) => void;
  onRevoke: () => void;
  onStartNew: () => void;
  onClose?: () => void;
}) {
  const [note, setNote] = useState("");
  const [confirmRevoke, setConfirmRevoke] = useState(false);
  const tone = authorityStateTone(v.state);
  const earlier = useMemo(() => history.filter((h) => h.id !== v.id).slice(0, 5), [history, v.id]);
  return (
    <div className="space-y-4">
      <div className={`rounded-xl p-4 text-sm ${
        tone === "success" ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-200"
          : tone === "neutral" ? "bg-slate-50 text-slate-700 dark:bg-slate-800/60 dark:text-slate-200"
            : "bg-amber-50 text-amber-800 dark:bg-amber-500/10 dark:text-amber-200"}`} role="status">
        <p className="flex items-center gap-2 font-semibold">
          {tone === "success" ? <BadgeCheck className="h-4 w-4" aria-hidden="true" /> : tone === "neutral" ?
            <Clock className="h-4 w-4" aria-hidden="true" /> : <AlertTriangle className="h-4 w-4" aria-hidden="true" />}
          {authorityStateLabel[v.state]}
        </p>
        <p className="mt-1">
          {v.state === "VERIFIED" || v.state === "EXPIRING_SOON"
            ? `${relationshipLabel[v.relationshipType]}. Valid until ${v.expiresAt ? formatDate(v.expiresAt) : "further notice"}.`
            : v.reason.message || (v.state === "MANUAL_REVIEW"
              ? "We're reviewing your authority. You can leave this page; we'll update the status here."
              : "")}
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {v.allowedActions.includes("RENEW") && (
          <Button size="sm" onClick={() => onRenew()} disabled={busy}>Renew verification</Button>
        )}
        {v.allowedActions.includes("START_NEW") && (
          <Button size="sm" onClick={onStartNew} disabled={busy}>Start a new verification</Button>
        )}
        {v.allowedActions.includes("REVOKE") && !confirmRevoke && (
          <Button size="sm" variant="ghost" onClick={() => setConfirmRevoke(true)}>Withdraw my authority</Button>
        )}
        {onClose && <Button size="sm" variant="outline" onClick={onClose}>Done</Button>}
      </div>

      {confirmRevoke && (
        <div className="space-y-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
          <p>Withdrawing pauses listings at this property that rely on this authority.</p>
          <div className="flex gap-2">
            <Button size="sm" variant="accent" onClick={onRevoke} disabled={busy}>Withdraw</Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirmRevoke(false)}>Cancel</Button>
          </div>
        </div>
      )}

      {v.allowedActions.includes("REQUEST_RECONSIDERATION") && (
        <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); onRenew(note); }}>
          <Field label="Ask for reconsideration" hint="Explain what we may have missed; you can add new evidence next.">
            <textarea className={inputClass} rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
          <Button type="submit" size="sm" disabled={busy || !note.trim()}>Request reconsideration</Button>
        </form>
      )}

      {earlier.length > 0 && (
        <details className="text-xs text-slate-500">
          <summary className="cursor-pointer">Earlier verifications</summary>
          <ul className="mt-2 space-y-1">
            {earlier.map((h) => (
              <li key={h.id}>#{h.id} · {relationshipLabel[h.relationshipType]} · {authorityStateLabel[h.state]} · {formatDate(h.createdAt)}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
