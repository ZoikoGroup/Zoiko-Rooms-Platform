"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  BadgeCheck,
  Camera,
  Clock,
  Copy,
  Hourglass,
  Smartphone,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, Field, Toast, inputClass, useToast } from "@/components/user/ui";
import { useUserSession } from "@/components/user/UserSessionContext";
import { IdentityCapture } from "@/components/user/IdentityCapture";
import { documentTypeLabel } from "@/lib/identity-documents";
import { IdentityDocumentType } from "@/lib/types";
import { errorMessage } from "@/lib/user-api";
import { formatDate } from "@/lib/utils";
import {
  IdentityCountry,
  IdentityPack,
  IdentityProfile,
  IdentityRole,
  IdentitySession,
  claimIdentityHandoff,
  createIdentityHandoff,
  getIdentityPolicy,
  identityStateLabel,
  refreshIdentitySession,
  restartIdentitySession,
  identityStateTone,
  listIdentityCountries,
  remediationLabel,
  roleLabel,
  saveIdentityDetails,
  startIdentitySession,
  submitIdentitySession,
} from "@/lib/identity";

/**
 * ZR-IDENTITY-001 / ZR-IDV-ADR-001 -- the person's identity verification flow:
 * 0 Intro -> 1 Confirm details -> 2 How verification works (consent) ->
 * 3 Review & attest -> 4 Take photos (document + selfie, on our own screens),
 * then the durable status pages (checking, verified + role routing, action
 * required). Each photo is relayed straight to Veriff, which decides whether
 * the document is genuine and belongs to the person; no person reviews it.
 * Progress is saved on the server at every step, and nothing here can mark
 * anyone verified.
 */

type Step = 0 | 1 | 2 | 3 | 4;
const STEPS: Step[] = [0, 1, 2, 3, 4];
const STEP_TITLES = ["Verify your identity", "Confirm your details", "How verification works", "Review and continue",
  "Take your photos"] as const;
const ROLES: IdentityRole[] = ["OWNER", "AGENT", "SUBLETTER"];
const DEFAULT_COUNTRY = "*";
const UNAVAILABLE = "Identity verification is temporarily unavailable. Please try again later.";

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
  const position = Math.max(0, STEPS.indexOf(step));

  useEffect(() => {
    listIdentityCountries().then(setCountries).catch(() => setCountries([]));
  }, []);

  // A phone opened from "Continue on my phone": claim the single-use link.
  useEffect(() => {
    const token = searchParams.get("handoff");
    if (!token) return;
    claimIdentityHandoff(token)
      .then(async (claimed) => {
        setSession(claimed);
        await refreshIdentity();
        setWizardOpen(true);
        setStep(claimed.attested && claimed.captureAvailable ? 4 : 3);
        showToast("You're continuing your verification on this device.");
      })
      .catch((err) => setErrors([errorMessage(err, "This link isn't valid -- start again from your computer.")]))
      .finally(() => router.replace("/account/identity"));
  }, [searchParams, refreshIdentity, router, showToast]);

  // Resume an unfinished session where it was left.
  useEffect(() => {
    if (!profile || wizardOpen) return;
    const current = profile.currentSession;
    if (current && current.state === "IN_PROGRESS" && profile.state === "IN_PROGRESS") {
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
    if (current && current.state === "IN_PROGRESS" && profile?.state === "IN_PROGRESS") {
      setSession(current);
      setStep(current.attested && current.captureAvailable ? 4 : 3);
    } else {
      setSession(null);
      setStep(0);
    }
    setWizardOpen(true);
  }, [profile]);

  /** Back to the capture screens for an open attempt -- to finish it, or to
   *  take new photos when Veriff asked for a resubmission. */
  function continueCapture(current: IdentitySession) {
    setErrors([]);
    setSession(current);
    setStep(4);
    setWizardOpen(true);
  }

  async function photosSubmitted(updated: IdentitySession) {
    setSession(updated);
    await refreshIdentity();
    setWizardOpen(false);
    showToast("Thanks -- we're checking your identity. We'll update your status here.");
  }

  async function startAgain(current: IdentitySession) {
    setErrors([]);
    try {
      const fresh = await restartIdentitySession(current.id);
      await refreshIdentity();
      setSession(fresh);
      setStep(fresh.attested && fresh.captureAvailable ? 4 : 3);
      setWizardOpen(true);
    } catch (err) {
      setErrors([errorMessage(err, "We couldn't start a new verification.")]);
    }
  }

  async function ensureSession(): Promise<IdentitySession> {
    const started = session ?? (await startIdentitySession("DOCUMENT", role, idempotencyKey.current));
    setSession(started);
    return started;
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
          onContinueCapture={continueCapture} onRestart={startAgain}
        />
        <Toast toast={toast} />
      </>
    );
  }

  return (
    <Card className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Step {position + 1} of {STEPS.length}
        </p>
        <Button size="sm" variant="ghost" onClick={() => { setWizardOpen(false); void refreshIdentity(); }}>
          Save and exit
        </Button>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800" aria-hidden="true">
        <div className="h-full rounded-full bg-primary-600 transition-all" style={{ width: `${((position + 1) / STEPS.length) * 100}%` }} />
      </div>
      <h2 ref={headingRef} tabIndex={-1} className="font-heading text-xl font-bold text-primary-900 outline-none dark:text-white">
        {STEP_TITLES[step]}
      </h2>

      {errors.length > 0 && <ErrorSummary errors={errors} />}
      {!profile.pack.providerAvailable && <InfoBlock title="Not available right now" tone="warn">{UNAVAILABLE}</InfoBlock>}

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
        <HowItWorksStep
          pack={profile.pack} busy={busy} session={session}
          onBack={() => go(1)}
          onNext={async () => {
            setBusy(true);
            try {
              await ensureSession();
              go(3);
            } catch (err) {
              setErrors([errorMessage(err, "We couldn't start your verification.")]);
            } finally {
              setBusy(false);
            }
          }}
          onPhone={async () => createIdentityHandoff((await ensureSession()).id)}
          onError={(message) => setErrors([message])}
        />
      )}
      {step === 3 && session && (
        <ReviewStep
          profile={profile} countries={countries} busy={busy}
          onBack={() => go(2)}
          onSubmit={async () => {
            setBusy(true);
            setErrors([]);
            try {
              const submitted = await submitIdentitySession(session.id, true);
              setSession(submitted);
              if (submitted.captureAvailable) go(4);
              // Veriff unavailable: nobody is verified, progress is saved.
              else setErrors([submitted.message || UNAVAILABLE]);
            } catch (err) {
              setErrors([errorMessage(err, "We couldn't submit your verification.")]);
            } finally {
              setBusy(false);
            }
          }}
        />
      )}
      {step === 4 && session && (
        <IdentityCapture
          session={session} pack={profile.pack}
          onSubmitted={(updated) => void photosSubmitted(updated)}
          onExit={() => { setWizardOpen(false); void refreshIdentity(); }}
        />
      )}
      <Toast toast={toast} />
    </Card>
  );
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
        Your original government-issued identity document -- for example a passport, national identity card or driving
        license, depending on your country -- and a device with a camera.
      </InfoBlock>
      <InfoBlock title="How it works" tone="info">
        You&apos;ll photograph your document and take a quick selfie right here. Our verification partner checks
        automatically that the document is genuine and that it&apos;s you.
      </InfoBlock>
      <InfoBlock title="Privacy">
        We only request information needed for verification. Your identity documents are not shown to renters or other Hosts.
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
      <p className="text-sm text-slate-500 dark:text-slate-400">Use your legal identity details. These should match the identity document you use.</p>
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

function HowItWorksStep({ pack, busy, session, onBack, onNext, onPhone, onError }: {
  pack: IdentityPack;
  busy: boolean;
  session: IdentitySession | null;
  onBack: () => void;
  onNext: () => void;
  onPhone: () => Promise<{ token: string; expiresInSeconds: number }>;
  onError: (message: string) => void;
}) {
  const [handoff, setHandoff] = useState<{ link: string; minutes: number } | null>(null);
  const documents = pack.acceptedDocumentTypes.map((t) => documentTypeLabel[t as IdentityDocumentType] ?? t).join(", ");

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
      <InfoBlock title="1. Photograph your document">
        Use the original document -- not a photocopy, a screenshot or a photo of a screen.
        {documents && <span className="mt-1 block text-xs text-slate-500">Accepted in {pack.countryName}: {documents}.</span>}
      </InfoBlock>
      <InfoBlock title="2. Take a quick selfie">
        This confirms the document belongs to you. You&apos;ll be asked to allow camera access.
      </InfoBlock>
      <InfoBlock title="3. Automatic decision" tone="ok">
        Our verification partner checks that the document is genuine and that it&apos;s you, and you&apos;ll see the result
        here. You take the photos right here -- you won&apos;t be sent to another website.
      </InfoBlock>
      {pack.biometricConsentText && <InfoBlock title="Before the camera check" tone="info">{pack.biometricConsentText}</InfoBlock>}

      <div className="space-y-2 rounded-xl bg-slate-50 p-4 dark:bg-slate-800/60">
        <p className="flex items-center gap-2 text-sm font-semibold text-primary-900 dark:text-white">
          <Smartphone className="h-4 w-4" aria-hidden="true" /> No suitable camera on this device?
        </p>
        {handoff ? (
          <>
            <p className="text-sm text-slate-600 dark:text-slate-300">
              Open this link on your phone while signed in to the same account. It works once and expires in {handoff.minutes} minutes.
              No identity data is in the link.
            </p>
            <div className="flex gap-2">
              <input readOnly className={inputClass} value={handoff.link} aria-label="Link for your phone" onFocus={(e) => e.currentTarget.select()} />
              <Button variant="outline" onClick={() => navigator.clipboard?.writeText(handoff.link)}>
                <Copy className="h-4 w-4" aria-hidden="true" /> Copy
              </Button>
            </div>
          </>
        ) : (
          <Button variant="outline" size="sm" disabled={!pack.providerAvailable} onClick={startPhone}>Continue on my phone</Button>
        )}
      </div>

      <Actions>
        <Button variant="ghost" onClick={onBack}><ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back</Button>
        <Button loading={busy} disabled={!pack.providerAvailable} onClick={onNext}>
          <Camera className="h-4 w-4" aria-hidden="true" /> Continue
        </Button>
      </Actions>
      {session && <p className="text-xs text-slate-400">Your progress is saved.</p>}
    </div>
  );
}

// -- Screen 3 ----------------------------------------------------------------------------

function ReviewStep({ profile, countries, busy, onBack, onSubmit }: {
  profile: IdentityProfile; countries: IdentityCountry[]; busy: boolean;
  onBack: () => void; onSubmit: () => void;
}) {
  const [attested, setAttested] = useState(false);
  const country = countries.find((c) => c.countryCode === profile.countryCode)?.countryName ?? profile.pack.countryName;
  const legalName = [profile.givenName, profile.middleNames, profile.familyName].filter((p) => p.trim()).join(" ");
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-500 dark:text-slate-400">Check the details below before continuing.</p>
      <dl className="grid gap-3 rounded-xl bg-slate-50 p-4 text-sm sm:grid-cols-2 dark:bg-slate-800/60">
        <SummaryItem label="Name" value={legalName} />
        <SummaryItem label="Country" value={country} />
        <SummaryItem label="Method" value="Identity document and selfie, with our verification partner" />
      </dl>
      <label className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-200">
        <input type="checkbox" className="mt-1" checked={attested} onChange={(e) => setAttested(e.target.checked)} />
        I confirm the information is accurate and I am submitting my own identity information.
      </label>
      <InfoBlock title="Privacy notice" tone="info">{profile.pack.privacyNoticeText}</InfoBlock>
      <InfoBlock title="Next step" tone="ok">After identity verification, we will separately verify your authority to list the selected property.</InfoBlock>
      <Actions>
        <Button variant="ghost" onClick={onBack}><ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back</Button>
        <Button loading={busy} disabled={!attested || !profile.pack.providerAvailable} onClick={onSubmit}>
          <Camera className="h-4 w-4" aria-hidden="true" /> Continue to photos
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

// -- Status pages: checking, verified, action required, not started ------------------------

function StatusScreen({ profile, onStart, onRefresh, errors, onContinueCapture, onRestart }: {
  profile: IdentityProfile; onStart: () => void; onRefresh: () => Promise<void>; errors: string[];
  onContinueCapture: (session: IdentitySession) => void; onRestart: (session: IdentitySession) => void;
}) {
  const state = profile.state;
  const current = profile.currentSession;
  const role = (profile.currentSession?.roleContext || "OWNER") as IdentityRole;
  const checking = state === "PROCESSING" || state === "PENDING_REVIEW";
  const available = profile.pack.providerAvailable;

  // While Veriff checks, refresh now and then -- no countdown, no spinner
  // trap. This also asks the server to check with Veriff's decision API
  // (useful where its webhooks can't reach the server, e.g. localhost).
  const sessionId = current?.id;
  useEffect(() => {
    if (!checking) return;
    const tick = async () => {
      if (sessionId) await refreshIdentitySession(sessionId).catch(() => undefined);
      await onRefresh();
    };
    const timer = setInterval(() => void tick(), 15000);
    return () => clearInterval(timer);
  }, [checking, sessionId, onRefresh]);

  const icon = useMemo(() => {
    if (state === "VERIFIED") return <BadgeCheck className="h-6 w-6 text-emerald-600" aria-hidden="true" />;
    if (checking) return <Hourglass className="h-6 w-6 text-primary-600" aria-hidden="true" />;
    if (state === "ACTION_REQUIRED" || state === "REVERIFICATION_REQUIRED") return <AlertTriangle className="h-6 w-6 text-amber-600" aria-hidden="true" />;
    if (state === "FAILED") return <XCircle className="h-6 w-6 text-accent-600" aria-hidden="true" />;
    return <ShieldCheck className="h-6 w-6 text-slate-500" aria-hidden="true" />;
  }, [state, checking]);

  return (
    <Card className="space-y-4">
      {errors.length > 0 && <ErrorSummary errors={errors} />}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          {icon}
          <div>
            <h2 className="font-heading text-xl font-bold text-primary-900 dark:text-white">
              {state === "VERIFIED" ? "Identity verified" : checking ? "Checking your identity" : state === "ACTION_REQUIRED" ? "We need another step" : profile.dashboard.header}
            </h2>
            <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400" aria-live="polite">
              {state === "VERIFIED" && "Your identity check is complete."}
              {checking && "You can leave this page. We'll update your status here as soon as the check is complete."}
              {state === "ACTION_REQUIRED" && "We could not complete identity verification with the photos provided."}
              {state === "NOT_STARTED" && (profile.dashboard.message || "Verify your identity once -- it's reused for every property you list.")}
              {state === "IN_PROGRESS" && "You've started verifying your identity. Continue where you left off."}
              {(state === "REVERIFICATION_REQUIRED" || state === "FAILED") && profile.dashboard.message}
            </p>
          </div>
        </div>
        <Badge tone={identityStateTone[state]} dot>{identityStateLabel[state]}</Badge>
      </div>

      {!available && state !== "VERIFIED" && <InfoBlock title="Not available right now" tone="warn">{UNAVAILABLE}</InfoBlock>}

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

      {checking && (
        <>
          <InfoBlock title="Status" tone="info">
            <span className="flex items-center gap-1.5"><Clock className="h-4 w-4" aria-hidden="true" /> Our verification partner is checking your document and selfie</span>
          </InfoBlock>
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
                <Button variant={current.captureAvailable ? "outline" : "primary"} disabled={!available} onClick={() => onRestart(current)}>Start again</Button>
              )}
              {current?.captureAvailable && (
                <Button disabled={!available} onClick={() => onContinueCapture(current)}>Retake photos</Button>
              )}
              {!current?.captureAvailable && !current?.canRestart && (
                <Button disabled={!available} onClick={onStart}>Verify again</Button>
              )}
            </div>
          </Actions>
        </>
      )}

      {(state === "NOT_STARTED" || state === "IN_PROGRESS" || state === "REVERIFICATION_REQUIRED") && (
        <Actions>
          <Link href="/account"><Button variant="ghost" fullWidth>Not now</Button></Link>
          {state === "IN_PROGRESS" && current?.captureAvailable ? (
            <Button disabled={!available} onClick={() => onContinueCapture(current)}>Continue verification</Button>
          ) : (
            <Button disabled={!available} onClick={onStart}>{profile.dashboard.primaryAction ?? "Verify identity"}</Button>
          )}
        </Actions>
      )}
    </Card>
  );
}
