"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  BadgeCheck,
  Camera,
  Clock,
  Copy,
  FileText,
  Hourglass,
  Smartphone,
  ShieldCheck,
  Upload,
  UserCheck,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, Field, Toast, inputClass, useToast } from "@/components/user/ui";
import { useUserSession } from "@/components/user/UserSessionContext";
import { ACCEPTED_DOCUMENT_EXTENSIONS, MAX_DOCUMENT_SIZE_MB, documentTypeLabel } from "@/lib/identity-documents";
import { IdentityDocumentType } from "@/lib/types";
import { errorMessage } from "@/lib/user-api";
import { formatDate } from "@/lib/utils";
import {
  AlternativeReason,
  IdentityCountry,
  IdentityMethod,
  IdentityPack,
  IdentityProfile,
  IdentityRole,
  IdentitySession,
  alternativeReasonLabel,
  claimIdentityHandoff,
  createIdentityHandoff,
  getIdentityPolicy,
  identityStateLabel,
  launchIdentitySession,
  refreshIdentitySession,
  restartIdentitySession,
  identityStateTone,
  listIdentityCountries,
  remediationLabel,
  requestIdentityAlternative,
  roleLabel,
  saveIdentityDetails,
  startIdentitySession,
  submitIdentitySession,
  uploadIdentityDocument,
} from "@/lib/identity";

/**
 * ZR-IDENTITY-001 Section 5 -- the person's identity verification flow:
 * 0 Intro -> 1 Confirm details -> 2 Choose method -> 3 Document ->
 * 4 Confirm it's you -> 5 Review & submit, then the durable status pages
 * (6 In review, 7 Verified + role routing, 8 Action required).
 * Progress is saved on the server at every step, so the person can leave
 * and resume -- and nothing here can mark anyone verified.
 */

type Step = 0 | 1 | 2 | 3 | 4 | 5;
const STEP_TITLES = ["Verify your identity", "Confirm your details", "Choose how to verify",
  "Verify your identity document", "Confirm it's you", "Review your identity verification"] as const;
const ROLES: IdentityRole[] = ["OWNER", "AGENT", "SUBLETTER"];
const ALTERNATIVE_REASONS = Object.keys(alternativeReasonLabel) as AlternativeReason[];
const DEFAULT_COUNTRY = "*";

const ROLE_NEXT: Record<IdentityRole, { text: string; href: string; cta: string }> = {
  OWNER: { text: "Next: verify the property and confirm your authority as owner or co-owner.", href: "/account/host", cta: "Go to your properties" },
  AGENT: { text: "Next: confirm the owner or organization mandate that authorizes you to list this property.", href: "/account/host", cta: "Go to your properties" },
  SUBLETTER: { text: "Next: confirm your current right of occupation and any permission required to sublet.", href: "/account/sublets", cta: "Go to sublets" },
};

function newIdempotencyKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

export function IdentityVerificationWizard() {
  const { identityProfile: profile, refreshIdentity, loading } = useUserSession();
  const router = useRouter();
  const searchParams = useSearchParams();
  const { toast, showToast } = useToast();

  const [countries, setCountries] = useState<IdentityCountry[]>([]);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [step, setStep] = useState<Step>(0);
  const [role, setRole] = useState<IdentityRole>("OWNER");
  const [session, setSession] = useState<IdentitySession | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const idempotencyKey = useRef(newIdempotencyKey());
  const headingRef = useRef<HTMLHeadingElement>(null);
  // Veriff (PROVIDER_HOSTED) captures the document + selfie in its own
  // flow, so the upload step is skipped and the consent comes before it.
  const hosted = profile?.pack.captureMode === "PROVIDER_HOSTED";
  const steps: Step[] = hosted ? [0, 1, 2, 4, 5] : [0, 1, 2, 3, 4, 5];
  const position = Math.max(0, steps.indexOf(step));

  useEffect(() => {
    listIdentityCountries().then(setCountries).catch(() => setCountries([]));
  }, []);

  // A phone opened from "Continue on phone": claim the single-use link.
  useEffect(() => {
    const token = searchParams.get("handoff");
    if (!token) return;
    claimIdentityHandoff(token)
      .then(async (claimed) => {
        setSession(claimed);
        await refreshIdentity();
        setWizardOpen(true);
        setStep(claimed.hasDocument ? 4 : 3);
        showToast("You're continuing your verification on this device.");
      })
      .catch((err) => setErrors([errorMessage(err, "This link isn't valid -- start again from your computer.")]))
      .finally(() => router.replace("/account/identity"));
  }, [searchParams, refreshIdentity, router, showToast]);

  // Returning from the provider's flow: only the server's status counts.
  useEffect(() => {
    if (searchParams.get("verification") !== "returned") return;
    void refreshIdentity().then(() => showToast("Thanks -- we're checking your identity. We'll update your status here."));
    router.replace("/account/identity");
  }, [searchParams, refreshIdentity, router, showToast]);

  // Resume an unfinished session where it was left.
  useEffect(() => {
    if (!profile || wizardOpen) return;
    const current = profile.currentSession;
    if (current && (current.state === "IN_PROGRESS") && profile.state === "IN_PROGRESS") {
      setSession(current);
      if (current.roleContext && ROLES.includes(current.roleContext as IdentityRole)) setRole(current.roleContext as IdentityRole);
    }
  }, [profile, wizardOpen]);

  // Move focus to the new step's heading (keyboard and screen-reader users).
  useEffect(() => {
    if (wizardOpen) headingRef.current?.focus();
  }, [step, wizardOpen]);

  const go = useCallback((next: Step) => {
    setErrors([]);
    setStep(next);
  }, []);

  const openWizard = useCallback(() => {
    setErrors([]);
    const current = profile?.currentSession;
    const hostedPack = profile?.pack.captureMode === "PROVIDER_HOSTED";
    if (current && current.state === "IN_PROGRESS" && profile?.state === "IN_PROGRESS") {
      setSession(current);
      setStep(hostedPack ? 5 : current.hasDocument ? 4 : current.method === "DOCUMENT" ? 3 : 2);
    } else if (current && current.state === "ACTION_REQUIRED" && !hostedPack) {
      setSession(current);
      setStep(3);
    } else {
      setSession(null);
      setStep(0);
    }
    setWizardOpen(true);
  }, [profile]);

  async function finish(updated: IdentitySession) {
    setSession(updated);
    await refreshIdentity();
    setWizardOpen(false);
  }

  /** Hand over to the provider's own document + selfie capture: Veriff's
   *  InContext SDK opens it over this page; if the SDK can't load (blocked
   *  script, unsupported browser) we fall back to Veriff's hosted page.
   *  Either way the SDK's "finished" message is not proof -- only the
   *  server's status, set by the provider's signed webhook, counts. */
  function goToProvider(updated: IdentitySession) {
    const url = updated.launchUrl;
    if (!url) return false;
    void openVeriffFrame(url, async (outcome) => {
      await refreshIdentitySession(updated.id).catch(() => undefined);
      await refreshIdentity();
      setWizardOpen(false);
      if (outcome === "finished") showToast("Thanks -- we're checking your identity. We'll update your status here.");
      else if (outcome === "canceled") showToast("Verification paused. You can continue whenever you're ready.");
    });
    return true;
  }

  async function continueWithProvider(current: IdentitySession) {
    setErrors([]);
    try {
      const launched = await launchIdentitySession(current.id);
      if (!goToProvider(launched)) setErrors(["This verification can't be continued -- start again."]);
    } catch (err) {
      setErrors([errorMessage(err, "We couldn't reopen your verification.")]);
    }
  }

  async function startAgain(current: IdentitySession) {
    setErrors([]);
    try {
      const fresh = await restartIdentitySession(current.id);
      await refreshIdentity();
      setSession(fresh);
      setStep(profile?.pack.captureMode === "PROVIDER_HOSTED" ? 5 : 3);
      setWizardOpen(true);
    } catch (err) {
      setErrors([errorMessage(err, "We couldn't start a new verification.")]);
    }
  }

  if (loading || !profile) {
    return (
      <Card>
        <p className="text-sm text-slate-400" role="status">Loading your identity status...</p>
      </Card>
    );
  }

  if (!wizardOpen) {
    return (
      <>
        <StatusScreen
          profile={profile} onStart={openWizard} onRefresh={refreshIdentity} errors={errors}
          onContinueProvider={continueWithProvider} onRestart={startAgain}
        />
        <Toast toast={toast} />
      </>
    );
  }

  return (
    <Card className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Step {position + 1} of {steps.length}
        </p>
        <Button size="sm" variant="ghost" onClick={() => { setWizardOpen(false); void refreshIdentity(); }}>
          Save and exit
        </Button>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800" aria-hidden="true">
        <div className="h-full rounded-full bg-primary-600 transition-all" style={{ width: `${((position + 1) / steps.length) * 100}%` }} />
      </div>
      <h2 ref={headingRef} tabIndex={-1} className="font-heading text-xl font-bold text-primary-900 outline-none dark:text-white">
        {STEP_TITLES[step]}
      </h2>

      {errors.length > 0 && <ErrorSummary errors={errors} />}

      {step === 0 && <IntroStep role={role} setRole={setRole} onNext={() => go(1)} />}
      {step === 1 && (
        <DetailsStep
          profile={profile} countries={countries} busy={busy}
          onSave={async (input, then) => {
            setBusy(true);
            setErrors([]);
            try {
              await saveIdentityDetails(input);
              await refreshIdentity();
              if (then === "exit") setWizardOpen(false);
              else go(2);
            } catch (err) {
              setErrors([errorMessage(err, "Your details couldn't be saved.")]);
            } finally {
              setBusy(false);
            }
          }}
          onBack={() => go(0)}
        />
      )}
      {step === 2 && (
        <MethodStep
          pack={profile.pack} busy={busy} session={session}
          onBack={() => go(1)}
          onDocument={async () => {
            setBusy(true);
            try {
              setSession(await startIdentitySession("DOCUMENT", role, idempotencyKey.current));
              go(hosted ? 4 : 3);
            } catch (err) {
              setErrors([errorMessage(err, "We couldn't start your verification.")]);
            } finally {
              setBusy(false);
            }
          }}
          onAlternative={async (reason, note) => {
            setBusy(true);
            try {
              const started = session ?? (await startIdentitySession("DOCUMENT", role, idempotencyKey.current));
              await finish(await requestIdentityAlternative(started.id, reason, note));
              showToast("Your verification is with a reviewer.");
            } catch (err) {
              setErrors([errorMessage(err, "We couldn't request a manual review.")]);
            } finally {
              setBusy(false);
            }
          }}
          onPhone={async () => {
            const started = session ?? (await startIdentitySession("DOCUMENT", role, idempotencyKey.current));
            setSession(started);
            return createIdentityHandoff(started.id);
          }}
          onError={(message) => setErrors([message])}
        />
      )}
      {step === 3 && session && (
        <DocumentStep
          pack={profile.pack} session={session} busy={busy}
          onBack={() => go(2)}
          onUpload={async (documentType, number, file) => {
            setBusy(true);
            setErrors([]);
            try {
              setSession(await uploadIdentityDocument(session.id, documentType, number, file));
              go(4);
            } catch (err) {
              setErrors([errorMessage(err, "We couldn't use this document.")]);
            } finally {
              setBusy(false);
            }
          }}
          onError={(message) => setErrors([message])}
        />
      )}
      {step === 4 && <BindingStep pack={profile.pack} hosted={hosted} onBack={() => go(2)} onNext={() => go(5)} />}
      {step === 5 && session && (
        <ReviewStep
          profile={profile} session={session} countries={countries} busy={busy} hosted={hosted}
          onBack={() => go(4)}
          onSubmit={async () => {
            setBusy(true);
            setErrors([]);
            try {
              const submitted = await submitIdentitySession(session.id, true);
              if (hosted) {
                if (goToProvider(submitted)) return;
                // Provider temporarily unavailable: progress is saved.
                setSession(submitted);
                setErrors([submitted.message || "Identity verification is temporarily unavailable. Your progress is saved."]);
                return;
              }
              await finish(submitted);
            } catch (err) {
              setErrors([errorMessage(err, "We couldn't submit your verification.")]);
            } finally {
              setBusy(false);
            }
          }}
        />
      )}
      <Toast toast={toast} />
    </Card>
  );
}

// -- provider capture (Veriff InContext SDK) -----------------------------------------

type ProviderOutcome = "finished" | "canceled";

async function openVeriffFrame(url: string, onDone: (outcome: ProviderOutcome) => void) {
  try {
    const { createVeriffFrame, MESSAGES } = await import("@veriff/incontext-sdk");
    const frame = createVeriffFrame({
      url,
      onEvent: (message: string) => {
        if (message === MESSAGES.FINISHED) {
          frame.close();
          onDone("finished");
        } else if (message === MESSAGES.CANCELED) {
          onDone("canceled");
        }
      },
    });
  } catch {
    // Redirect flow (ADR Section 7 fallback): Veriff returns to
    // /account/identity?verification=returned when done.
    window.location.assign(url);
  }
}

// -- shared bits -------------------------------------------------------------------

function ErrorSummary({ errors }: { errors: string[] }) {
  return (
    <div role="alert" className="rounded-xl bg-accent-50 px-4 py-3 ring-1 ring-accent-200 dark:bg-accent-500/10 dark:ring-accent-500/20">
      <p className="flex items-center gap-2 text-sm font-semibold text-accent-700 dark:text-accent-300">
        <XCircle className="h-4 w-4" aria-hidden="true" /> There&apos;s a problem
      </p>
      <ul className="mt-1 list-disc pl-6 text-sm text-accent-700 dark:text-accent-300">
        {errors.map((e) => <li key={e}>{e}</li>)}
      </ul>
    </div>
  );
}

function InfoBlock({ title, children, tone = "plain" }: { title: string; children: React.ReactNode; tone?: "plain" | "info" | "warn" | "ok" }) {
  const toneClass = {
    plain: "bg-slate-50 dark:bg-slate-800/60",
    info: "bg-primary-50 dark:bg-primary-500/10",
    warn: "bg-amber-50 dark:bg-amber-500/10",
    ok: "bg-emerald-50 dark:bg-emerald-500/10",
  }[tone];
  return (
    <div className={`rounded-xl px-4 py-3 ${toneClass}`}>
      <p className="text-sm font-semibold text-primary-900 dark:text-white">{title}</p>
      <div className="mt-0.5 text-sm text-slate-600 dark:text-slate-300">{children}</div>
    </div>
  );
}

function Actions({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-between">{children}</div>;
}

// -- Screen 0 ----------------------------------------------------------------------------

function IntroStep({ role, setRole, onNext }: { role: IdentityRole; setRole: (r: IdentityRole) => void; onNext: () => void }) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-500 dark:text-slate-400">We verify Hosts before publication to help keep Zoiko Rooms trustworthy.</p>
      <fieldset className="space-y-2">
        <legend className="text-sm font-semibold text-primary-900 dark:text-white">You are listing a property as</legend>
        {ROLES.map((r) => (
          <label key={r} className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
            <input type="radio" name="identity-role" value={r} checked={role === r} onChange={() => setRole(r)} />
            {roleLabel[r]}
          </label>
        ))}
        <p className="text-xs text-slate-400">This only decides where we take you afterwards -- you can change it before authority verification.</p>
      </fieldset>
      <InfoBlock title="What you will need">
        One supported identity method. Depending on your country, this may be a passport, national identity card,
        driving license, or another accepted document.
      </InfoBlock>
      <InfoBlock title="Privacy" tone="info">
        We only request information needed for verification. Your identity documents are not shown to renters or other Hosts.
      </InfoBlock>
      <InfoBlock title="Alternative route">
        Can&apos;t use the standard camera/document flow? You can choose another verification option or request a manual review.
      </InfoBlock>
      <Actions>
        <Link href="/account"><Button variant="ghost" fullWidth>Not now</Button></Link>
        <Button onClick={onNext}>Start verification</Button>
      </Actions>
    </div>
  );
}

// -- Screen 1 ----------------------------------------------------------------------------

function DetailsStep({ profile, countries, busy, onSave, onBack }: {
  profile: IdentityProfile;
  countries: IdentityCountry[];
  busy: boolean;
  onSave: (input: Parameters<typeof saveIdentityDetails>[0], then: "exit" | "next") => void;
  onBack: () => void;
}) {
  const [given, setGiven] = useState(profile.givenName);
  const [middle, setMiddle] = useState(profile.middleNames);
  const [family, setFamily] = useState(profile.familyName);
  const [dob, setDob] = useState(profile.dateOfBirth ?? "");
  const [country, setCountry] = useState(profile.countryCode || DEFAULT_COUNTRY);
  const [pack, setPack] = useState<IdentityPack>(profile.pack);
  const [password, setPassword] = useState("");
  const [localErrors, setLocalErrors] = useState<string[]>([]);
  const verified = profile.state === "VERIFIED";

  useEffect(() => {
    getIdentityPolicy(country).then(setPack).catch(() => undefined);
  }, [country]);

  function save(then: "exit" | "next") {
    const problems = [];
    if (!given.trim()) problems.push("Enter your given name.");
    if (!family.trim()) problems.push("Enter your family name.");
    if (pack.dateOfBirthRequired && !dob) problems.push("Enter your date of birth.");
    setLocalErrors(problems);
    if (problems.length) return;
    onSave({
      givenName: given, middleNames: middle, familyName: family, dateOfBirth: dob || null,
      countryCode: country === DEFAULT_COUNTRY ? "ZZ" : country, currentPassword: password,
    }, then);
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-500 dark:text-slate-400">Use your legal identity details. These should match the identity evidence you use.</p>
      {localErrors.length > 0 && <ErrorSummary errors={localErrors} />}
      <fieldset>
        <legend className="mb-2 text-sm font-semibold text-primary-900 dark:text-white">Full legal name</legend>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Given name"><input className={inputClass} value={given} onChange={(e) => setGiven(e.target.value)} autoComplete="given-name" required /></Field>
          <Field label="Middle names (optional)"><input className={inputClass} value={middle} onChange={(e) => setMiddle(e.target.value)} autoComplete="additional-name" /></Field>
          <Field label="Family name"><input className={inputClass} value={family} onChange={(e) => setFamily(e.target.value)} autoComplete="family-name" required /></Field>
        </div>
        <p className="mt-1 text-xs text-slate-400">Type your name exactly as it appears on your document -- accents and non-Latin scripts are kept as you type them.</p>
      </fieldset>
      <Field label="Country / territory" hint="This decides which documents are accepted and the legal wording you see.">
        <select className={inputClass} value={country} onChange={(e) => setCountry(e.target.value)}>
          {countries.map((c) => <option key={c.countryCode} value={c.countryCode}>{c.countryName}</option>)}
          <option value={DEFAULT_COUNTRY}>Another country</option>
        </select>
      </Field>
      {pack.dateOfBirthRequired && (
        <Field label="Date of birth" hint="Required in your country to match your identity and confirm age requirements.">
          <input type="date" className={inputClass} value={dob} onChange={(e) => setDob(e.target.value)} autoComplete="bday" required />
        </Field>
      )}
      {verified && (
        <Field label="Account password" hint="Changing the name or date of birth on a verified identity needs your password, and you'll verify again.">
          <input type="password" className={inputClass} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </Field>
      )}
      <Actions>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={onBack}><ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back</Button>
          <Button variant="outline" loading={busy} onClick={() => save("exit")}>Save and exit</Button>
        </div>
        <Button loading={busy} onClick={() => save("next")}>Continue</Button>
      </Actions>
    </div>
  );
}

// -- Screen 2 ----------------------------------------------------------------------------

function MethodStep({ pack, busy, session, onBack, onDocument, onAlternative, onPhone, onError }: {
  pack: IdentityPack;
  busy: boolean;
  session: IdentitySession | null;
  onBack: () => void;
  onDocument: () => void;
  onAlternative: (reason: AlternativeReason, note: string) => void;
  onPhone: () => Promise<{ token: string; expiresInSeconds: number }>;
  onError: (message: string) => void;
}) {
  const [method, setMethod] = useState<IdentityMethod | "PHONE">("DOCUMENT");
  const [reason, setReason] = useState<AlternativeReason>("NO_CAMERA");
  const [note, setNote] = useState("");
  const [handoff, setHandoff] = useState<{ link: string; minutes: number } | null>(null);
  const digitalAvailable = pack.availableMethods.includes("DIGITAL_IDENTITY");
  const manualAvailable = pack.availableMethods.includes("MANUAL");

  async function startPhone() {
    try {
      const { token, expiresInSeconds } = await onPhone();
      setHandoff({ link: `${window.location.origin}/account/identity?handoff=${encodeURIComponent(token)}`, minutes: Math.round(expiresInSeconds / 60) });
    } catch (err) {
      onError(errorMessage(err, "We couldn't create a link for your phone."));
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-500 dark:text-slate-400">Available methods depend on your country and device.</p>
      <fieldset className="space-y-2">
        <legend className="sr-only">Verification method</legend>
        <MethodOption id="DIGITAL_IDENTITY" checked={method === "DIGITAL_IDENTITY"} disabled={!digitalAvailable} onSelect={setMethod}
          title="Digital identity" icon={<ShieldCheck className="h-5 w-5" aria-hidden="true" />}
          body={digitalAvailable ? "Use a supported digital identity or identity wallet -- only the details Zoiko Rooms needs are shared."
            : "Not available in your country yet."} />
        <MethodOption id="DOCUMENT" checked={method === "DOCUMENT"} onSelect={setMethod}
          title={pack.selfieCheck ? "Identity document and selfie" : "Identity document"}
          icon={<FileText className="h-5 w-5" aria-hidden="true" />}
          body={pack.captureMode === "PROVIDER_HOSTED"
            ? "Photograph a supported government-issued identity document, then take a quick selfie, in our verification partner's secure flow."
            : "Scan or upload a supported government-issued identity document."} />
        {manualAvailable && (
          <MethodOption id="MANUAL" checked={method === "MANUAL"} onSelect={setMethod}
            title="Alternative verification" icon={<UserCheck className="h-5 w-5" aria-hidden="true" />}
            body="A non-camera route checked by a trained reviewer, if the standard flow doesn't work for you." />
        )}
        <MethodOption id="PHONE" checked={method === "PHONE"} onSelect={setMethod}
          title="Continue on phone" icon={<Smartphone className="h-5 w-5" aria-hidden="true" />}
          body="No suitable camera here? Get a short-lived link to continue on your phone. No identity data is in the link." />
      </fieldset>

      {method === "MANUAL" && (
        <div className="space-y-3 rounded-xl bg-slate-50 p-4 dark:bg-slate-800/60">
          <Field label="Why do you need another route?">
            <select className={inputClass} value={reason} onChange={(e) => setReason(e.target.value as AlternativeReason)}>
              {ALTERNATIVE_REASONS.map((r) => <option key={r} value={r}>{alternativeReasonLabel[r]}</option>)}
            </select>
          </Field>
          <Field label="Anything a reviewer should know? (optional)">
            <textarea className={inputClass} rows={2} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
        </div>
      )}
      {method === "PHONE" && (
        <div className="space-y-2 rounded-xl bg-slate-50 p-4 dark:bg-slate-800/60">
          {handoff ? (
            <>
              <p className="text-sm text-slate-600 dark:text-slate-300">
                Open this link on your phone while signed in to the same account. It works once and expires in {handoff.minutes} minutes.
              </p>
              <div className="flex gap-2">
                <input readOnly className={inputClass} value={handoff.link} aria-label="Link for your phone" onFocus={(e) => e.currentTarget.select()} />
                <Button variant="outline" onClick={() => navigator.clipboard?.writeText(handoff.link)}>
                  <Copy className="h-4 w-4" aria-hidden="true" /> Copy
                </Button>
              </div>
            </>
          ) : (
            <Button variant="outline" onClick={startPhone}><Smartphone className="h-4 w-4" aria-hidden="true" /> Create phone link</Button>
          )}
        </div>
      )}

      <Actions>
        <Button variant="ghost" onClick={onBack}><ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back</Button>
        {method === "MANUAL" ? (
          <Button loading={busy} onClick={() => onAlternative(reason, note)}>Request manual review</Button>
        ) : method === "PHONE" ? (
          <Button variant="outline" onClick={onDocument} loading={busy}>Continue here instead</Button>
        ) : (
          <Button loading={busy} disabled={method === "DIGITAL_IDENTITY"} onClick={onDocument}>Continue</Button>
        )}
      </Actions>
      {session && <p className="text-xs text-slate-400">Your progress is saved.</p>}
    </div>
  );
}

function MethodOption({ id, checked, disabled, onSelect, title, body, icon }: {
  id: IdentityMethod | "PHONE"; checked: boolean; disabled?: boolean; onSelect: (m: IdentityMethod | "PHONE") => void;
  title: string; body: string; icon: React.ReactNode;
}) {
  return (
    <label className={`flex cursor-pointer items-start gap-3 rounded-xl p-4 ring-1 transition-colors ${
      checked ? "bg-primary-50 ring-primary-300 dark:bg-primary-500/10 dark:ring-primary-500/40" : "ring-slate-200 dark:ring-slate-700"
    } ${disabled ? "cursor-not-allowed opacity-60" : ""}`}>
      <input type="radio" name="identity-method" className="mt-1" checked={checked} disabled={disabled} onChange={() => onSelect(id)} />
      <span className="text-primary-700 dark:text-primary-300">{icon}</span>
      <span>
        <span className="block text-sm font-semibold text-primary-900 dark:text-white">{title}</span>
        <span className="block text-sm text-slate-500 dark:text-slate-400">{body}</span>
      </span>
    </label>
  );
}

// -- Screen 3 ----------------------------------------------------------------------------

function DocumentStep({ pack, session, busy, onBack, onUpload, onError }: {
  pack: IdentityPack; session: IdentitySession; busy: boolean; onBack: () => void;
  onUpload: (documentType: string, number: string, file: File) => void; onError: (message: string) => void;
}) {
  const [documentType, setDocumentType] = useState(session.documentType || pack.acceptedDocumentTypes[0] || "passport");
  const [number, setNumber] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);

  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

  function pick(e: ChangeEvent<HTMLInputElement>) {
    const selected = e.target.files?.[0] ?? null;
    e.target.value = "";
    if (!selected) return;
    if (selected.size > MAX_DOCUMENT_SIZE_MB * 1024 * 1024) {
      onError(`This file is larger than ${MAX_DOCUMENT_SIZE_MB}MB. Upload a smaller copy of the original.`);
      return;
    }
    if (!/\.(pdf|jpe?g|png)$/i.test(selected.name) && !["application/pdf", "image/jpeg", "image/png"].includes(selected.type)) {
      onError("This file type can't be used. Upload a PDF, JPG or PNG of the original document.");
      return;
    }
    if (selected.size < 10 * 1024) {
      onError("We couldn't read this document clearly. Try again with the full document in view or upload the original file.");
      return;
    }
    setFile(selected);
    setPreview(selected.type.startsWith("image/") ? URL.createObjectURL(selected) : null);
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-500 dark:text-slate-400">
        Capture the original document clearly. We&apos;ll tell you if another side or page is needed.
      </p>
      {session.state === "ACTION_REQUIRED" || session.message ? (
        session.message ? <InfoBlock title="What needs fixing" tone="warn">{session.message}</InfoBlock> : null
      ) : null}
      <Field label="Document type" hint="Only documents accepted in your country are listed.">
        <select className={inputClass} value={documentType} onChange={(e) => setDocumentType(e.target.value)}>
          {pack.acceptedDocumentTypes.map((t) => (
            <option key={t} value={t}>{documentTypeLabel[t as IdentityDocumentType] ?? t}</option>
          ))}
        </select>
      </Field>
      <Field label="Document number" hint="As printed on the document. We only ever show the last four characters back to you.">
        <input className={inputClass} value={number} onChange={(e) => setNumber(e.target.value)} autoComplete="off" spellCheck={false} />
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex cursor-pointer items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-semibold text-primary-700 ring-1 ring-primary-200 focus-within:ring-2 focus-within:ring-primary-400 dark:text-primary-300 dark:ring-primary-500/30">
          <Camera className="h-4 w-4" aria-hidden="true" /> Use camera
          <input type="file" accept="image/*" capture="environment" className="sr-only" onChange={pick} />
        </label>
        <label className="flex cursor-pointer items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-semibold text-primary-700 ring-1 ring-primary-200 focus-within:ring-2 focus-within:ring-primary-400 dark:text-primary-300 dark:ring-primary-500/30">
          <Upload className="h-4 w-4" aria-hidden="true" /> Upload original file
          <input type="file" accept={ACCEPTED_DOCUMENT_EXTENSIONS} className="sr-only" onChange={pick} />
        </label>
      </div>
      {file && (
        <div className="flex items-center gap-3 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60" aria-live="polite">
          {/* eslint-disable-next-line @next/next/no-img-element -- local blob preview; next/image cannot load it */}
          {preview ? <img src={preview} alt="Preview of your document" className="h-16 w-24 rounded object-cover" /> : <FileText className="h-8 w-8 text-slate-400" aria-hidden="true" />}
          <div className="text-sm">
            <p className="font-semibold text-primary-900 dark:text-white">{file.name}</p>
            <p className="text-xs text-slate-500">Check the whole document is in view, with no glare and all four corners showing.</p>
          </div>
        </div>
      )}
      <InfoBlock title="Data minimization" tone="info">
        We only ask for what the check needs. Your full document number is encrypted and never shown again.
      </InfoBlock>
      <Actions>
        <Button variant="ghost" onClick={onBack}><ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back</Button>
        <Button loading={busy} disabled={!file} onClick={() => file && onUpload(documentType, number.trim(), file)}>Use this document</Button>
      </Actions>
    </div>
  );
}

// -- Screen 4 ----------------------------------------------------------------------------

function BindingStep({ pack, hosted, onBack, onNext }: { pack: IdentityPack; hosted: boolean; onBack: () => void; onNext: () => void }) {
  if (hosted) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-slate-500 dark:text-slate-400">We need to confirm that the person using this account matches the identity on the document.</p>
        <InfoBlock title="Standard path">
          Next you&apos;ll photograph your document and take a short selfie in our verification partner&apos;s secure flow.
          It compares the two to confirm it&apos;s really you. You&apos;ll be asked to allow camera access.
        </InfoBlock>
        {pack.biometricConsentText && <InfoBlock title="Before the camera check" tone="info">{pack.biometricConsentText}</InfoBlock>}
        <InfoBlock title="Alternative path">
          If a camera check isn&apos;t available, appropriate or accessible for you, choose another method or ask for a manual review.
        </InfoBlock>
        <Actions>
          <Button variant="ghost" onClick={onBack}>Choose another method</Button>
          <Button onClick={onNext}>Continue</Button>
        </Actions>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-500 dark:text-slate-400">We need to confirm that the person using this account matches the identity on the document.</p>
      <InfoBlock title="How we confirm it's you">
        In your country this check is done by matching the legal name you confirmed against your document. Where that
        isn&apos;t possible -- for example from a photo -- a trained reviewer compares them instead. No selfie or face scan is taken.
      </InfoBlock>
      {pack.biometricConsentText && (
        <InfoBlock title="If a camera check is used" tone="info">{pack.biometricConsentText}</InfoBlock>
      )}
      <InfoBlock title="Alternative path">
        If this doesn&apos;t work for you, choose another method or ask for a manual review.
      </InfoBlock>
      <Actions>
        <Button variant="ghost" onClick={onBack}>Choose another method</Button>
        <Button onClick={onNext}>Continue</Button>
      </Actions>
    </div>
  );
}

// -- Screen 5 ----------------------------------------------------------------------------

function ReviewStep({ profile, session, countries, busy, hosted, onBack, onSubmit }: {
  profile: IdentityProfile; session: IdentitySession; countries: IdentityCountry[]; busy: boolean; hosted: boolean;
  onBack: () => void; onSubmit: () => void;
}) {
  const [attested, setAttested] = useState(false);
  const country = countries.find((c) => c.countryCode === profile.countryCode)?.countryName ?? profile.pack.countryName;
  const legalName = [profile.givenName, profile.middleNames, profile.familyName].filter((p) => p.trim()).join(" ");
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-500 dark:text-slate-400">Check the details below before submitting.</p>
      <dl className="grid gap-3 rounded-xl bg-slate-50 p-4 text-sm sm:grid-cols-2 dark:bg-slate-800/60">
        <SummaryItem label="Name" value={legalName} />
        <SummaryItem label="Country" value={country} />
        {hosted ? (
          <SummaryItem label="Method" value="Identity document and selfie, with our verification partner" />
        ) : (
          <>
            <SummaryItem label="Method" value={documentTypeLabel[session.documentType as IdentityDocumentType] ?? session.documentType} />
            <SummaryItem label="Document" value={session.maskedDocumentNumber || "Not entered"} />
          </>
        )}
      </dl>
      <label className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-200">
        <input type="checkbox" className="mt-1" checked={attested} onChange={(e) => setAttested(e.target.checked)} />
        I confirm the information is accurate and I am submitting my own identity information.
      </label>
      <InfoBlock title="Privacy notice" tone="info">{profile.pack.privacyNoticeText}</InfoBlock>
      <InfoBlock title="Next step" tone="ok">After identity verification, we will separately verify your authority to list the selected property.</InfoBlock>
      <Actions>
        <Button variant="ghost" onClick={onBack}><ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back</Button>
        <Button loading={busy} disabled={!attested} onClick={onSubmit}>
          {hosted ? "Continue to secure verification" : "Submit verification"}
        </Button>
      </Actions>
    </div>
  );
}

function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">{label}</dt>
      <dd className="mt-0.5 text-primary-900 dark:text-white">{value}</dd>
    </div>
  );
}

// -- Screens 6, 7, 8 and the not-started state ------------------------------------------------

function StatusScreen({ profile, onStart, onRefresh, errors, onContinueProvider, onRestart }: {
  profile: IdentityProfile; onStart: () => void; onRefresh: () => Promise<void>; errors: string[];
  onContinueProvider: (session: IdentitySession) => void; onRestart: (session: IdentitySession) => void;
}) {
  const state = profile.state;
  const current = profile.currentSession;
  const role = (profile.currentSession?.roleContext || "OWNER") as IdentityRole;
  const reviewing = state === "PROCESSING" || state === "PENDING_REVIEW";

  // While checks run, refresh now and then -- no countdown, no spinner trap.
  // For a provider-hosted session this also asks the server to check with
  // the provider (useful where its webhooks can't reach the server).
  const sessionId = current?.id;
  useEffect(() => {
    if (!reviewing && !(state === "IN_PROGRESS" && current?.launchAvailable)) return;
    const tick = async () => {
      if (sessionId) await refreshIdentitySession(sessionId).catch(() => undefined);
      await onRefresh();
    };
    const timer = setInterval(() => void tick(), 15000);
    return () => clearInterval(timer);
  }, [reviewing, state, current?.launchAvailable, sessionId, onRefresh]);

  const icon = useMemo(() => {
    if (state === "VERIFIED") return <BadgeCheck className="h-6 w-6 text-emerald-600" aria-hidden="true" />;
    if (reviewing) return <Hourglass className="h-6 w-6 text-primary-600" aria-hidden="true" />;
    if (state === "ACTION_REQUIRED" || state === "REVERIFICATION_REQUIRED") return <AlertTriangle className="h-6 w-6 text-amber-600" aria-hidden="true" />;
    if (state === "FAILED") return <XCircle className="h-6 w-6 text-accent-600" aria-hidden="true" />;
    return <ShieldCheck className="h-6 w-6 text-slate-500" aria-hidden="true" />;
  }, [state, reviewing]);

  return (
    <Card className="space-y-4">
      {errors.length > 0 && <ErrorSummary errors={errors} />}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          {icon}
          <div>
            <h2 className="font-heading text-xl font-bold text-primary-900 dark:text-white">
              {state === "VERIFIED" ? "Identity verified" : reviewing ? "Verification in review" : state === "ACTION_REQUIRED" ? "We need another step" : profile.dashboard.header}
            </h2>
            <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400" aria-live="polite">
              {state === "VERIFIED" && "Your identity check is complete."}
              {reviewing && "You can leave this page. We'll update your status here and notify you when action is required."}
              {state === "ACTION_REQUIRED" && "We could not complete identity verification with the information provided."}
              {state === "NOT_STARTED" && "Verify your identity once -- it's reused for every property you list."}
              {state === "IN_PROGRESS" && "You've started verifying your identity. Continue where you left off."}
              {(state === "REVERIFICATION_REQUIRED" || state === "FAILED") && profile.dashboard.message}
            </p>
          </div>
        </div>
        <Badge tone={identityStateTone[state]} dot>{identityStateLabel[state]}</Badge>
      </div>

      {state === "VERIFIED" && (
        <>
          <InfoBlock title="Status" tone="ok">
            <span className="flex items-center gap-1.5"><BadgeCheck className="h-4 w-4" aria-hidden="true" /> Identity Verified -- account-level</span>
            {profile.verifiedAt && <span className="mt-1 block text-xs text-slate-500">Verified {formatDate(profile.verifiedAt)}</span>}
          </InfoBlock>
          <div className="grid gap-3 sm:grid-cols-3">
            {ROLES.map((r) => (
              <div key={r} className={`rounded-xl p-4 ring-1 ${r === role ? "ring-primary-300 bg-primary-50/50 dark:bg-primary-500/10" : "ring-slate-200 dark:ring-slate-700"}`}>
                <p className="text-sm font-semibold text-primary-900 dark:text-white">{roleLabel[r]}</p>
                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{ROLE_NEXT[r].text}</p>
              </div>
            ))}
          </div>
          <Actions>
            <Link href="/account"><Button variant="ghost" fullWidth>Return to dashboard</Button></Link>
            <Link href={ROLE_NEXT[role].href}><Button fullWidth>Continue to authority verification</Button></Link>
          </Actions>
          <details className="text-sm">
            <summary className="cursor-pointer text-slate-500">Your legal name or date of birth changed?</summary>
            <p className="mt-2 text-slate-500 dark:text-slate-400">
              Update them and verify again. You&apos;ll need your account password.
            </p>
            <Button className="mt-2" size="sm" variant="outline" onClick={onStart}>Update my details</Button>
          </details>
        </>
      )}

      {reviewing && (
        <>
          <InfoBlock title="Status" tone="info">
            <span className="flex items-center gap-1.5"><Clock className="h-4 w-4" aria-hidden="true" /> {state === "PROCESSING" ? "Checking identity information" : "With a trained reviewer"}</span>
          </InfoBlock>
          <InfoBlock title="What happens next">Automated checks run first. Some submissions need a trained reviewer or more evidence.</InfoBlock>
          <Actions>
            <Button variant="ghost" onClick={() => void onRefresh()}>Refresh status</Button>
            <Link href="/account"><Button fullWidth>Go to dashboard</Button></Link>
          </Actions>
        </>
      )}

      {(state === "ACTION_REQUIRED" || state === "FAILED") && (
        <>
          {profile.dashboard.message && (
            <InfoBlock title={state === "FAILED" ? "What happened" : "What needs fixing"} tone="warn">{profile.dashboard.message}</InfoBlock>
          )}
          {profile.dashboard.actions.length > 0 && (
            <InfoBlock title="What you can do">
              <ul className="list-disc pl-5">
                {profile.dashboard.actions.map((a) => <li key={a}>{remediationLabel[a] ?? a}</li>)}
              </ul>
            </InfoBlock>
          )}
          <InfoBlock title="Your drafts are safe" tone="info">Only the actions that need a verified identity are paused.</InfoBlock>
          <Actions>
            <Link href="/account"><Button variant="ghost" fullWidth>Return to dashboard</Button></Link>
            <div className="flex flex-col gap-2 sm:flex-row">
              {current?.canRestart && (
                <Button variant={current.launchAvailable ? "outline" : "primary"} onClick={() => onRestart(current)}>Start again</Button>
              )}
              {current?.launchAvailable ? (
                <Button onClick={() => onContinueProvider(current)}>Fix verification</Button>
              ) : !current?.canRestart ? (
                <Button onClick={onStart}>{state === "FAILED" ? "Try another method" : "Fix verification"}</Button>
              ) : null}
            </div>
          </Actions>
        </>
      )}

      {(state === "NOT_STARTED" || state === "IN_PROGRESS" || state === "REVERIFICATION_REQUIRED") && (
        <Actions>
          <Link href="/account"><Button variant="ghost" fullWidth>Not now</Button></Link>
          {state === "IN_PROGRESS" && current?.launchAvailable ? (
            <Button onClick={() => onContinueProvider(current)}>Continue verification</Button>
          ) : (
            <Button onClick={onStart}>{profile.dashboard.primaryAction ?? "Verify identity"}</Button>
          )}
        </Actions>
      )}
    </Card>
  );
}
