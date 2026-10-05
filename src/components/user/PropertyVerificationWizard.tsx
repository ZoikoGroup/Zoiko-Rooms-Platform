"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import {
  AlertTriangle, ArrowDown, ArrowLeft, ArrowRight, ArrowUp, BadgeCheck, CheckCircle2, Circle, Clock, FileText,
  MapPin, Search, ShieldCheck, Trash2, Upload, XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Field, inputClass } from "@/components/user/ui";
import { errorMessage } from "@/lib/user-api";
import { formatDate } from "@/lib/utils";
import { browserMapProvider } from "@/lib/map-provider";
import {
  AddressSuggestion, EvidenceType, GeoPoint, PropertyKind, PropertyPolicy, PropertyVerificationSession, StructuredAddress,
  confidenceLabel, confirmPropertyAddress, confirmPropertyLocation, evidenceTypeLabel, getPropertyPolicy,
  getPropertyVerificationForProperty, propertyEvidenceUrl, propertyStateLabel, propertyStateTone, removePropertyEvidence,
  answerPropertyDuplicate, restartPropertyVerification, retrieveAddress, setPropertyAddress, setPropertyUnit,
  startPropertyVerification,
  submitPropertyVerification, suggestAddresses, uploadPropertyEvidence,
} from "@/lib/property-verification";

const PropertyPinMap = dynamic(() => import("@/components/user/PropertyPinMap").then((m) => m.PropertyPinMap), {
  ssr: false, loading: () => <div className="h-[300px] animate-pulse rounded-xl bg-slate-100 dark:bg-slate-800" />,
});

/**
 * ZR-PROPERTY-VERIFY-001 Section 6 host wizard:
 * 0 Introduction -> 1 Find the address -> 2 Confirm the address ->
 * 3 Confirm the location -> 4 Property / unit -> 5 Existence evidence ->
 * 6 Review & submit -> 7 Outcome (verified / in review / action required).
 * Every step is saved and re-checked on the server; the map is never the
 * only way to confirm the location (Section 17).
 */

type Step = 0 | 1 | 2 | 3 | 4 | 5 | 6;
const TITLES = ["Verify this property", "Find the property", "Confirm the address", "Confirm the location",
  "Identify the property / unit", "Confirm the property exists", "Review property verification"] as const;
const OPEN = ["IN_PROGRESS", "ACTION_REQUIRED"];
const EMPTY: StructuredAddress = {
  addressLine1: "", addressLine2: "", subpremise: "", locality: "", administrativeArea: "", postalCode: "", countryCode: "",
};
const COUNTRY_CENTER: Record<string, GeoPoint> = {
  IN: { latitude: 20.5937, longitude: 78.9629 }, GB: { latitude: 52.3555, longitude: -1.1743 },
  US: { latitude: 39.8283, longitude: -98.5795 },
};
type AddressFieldKey = "addressLine1" | "addressLine2" | "locality" | "administrativeArea" | "postalCode";
const DEFAULT_LABELS: Record<AddressFieldKey, string> = {
  addressLine1: "Address line 1", addressLine2: "Address line 2 (unit / building / locality)",
  locality: "City / locality", administrativeArea: "Region / state", postalCode: "Postal code",
};
/** Section 17: field names follow the country's own addressing. */
const COUNTRY_LABELS: Record<string, Partial<Record<AddressFieldKey, string>>> = {
  GB: { addressLine1: "House number / name and street", addressLine2: "Flat, building or area",
        locality: "Town / city", administrativeArea: "County", postalCode: "Postcode" },
  US: { addressLine1: "Street address", addressLine2: "Apt, suite or unit", locality: "City",
        administrativeArea: "State", postalCode: "ZIP code" },
  IN: { addressLine1: "House / door number and street or village", addressLine2: "Area / locality",
        locality: "Town / city", administrativeArea: "State", postalCode: "PIN code" },
};
function fieldLabel(country: string, key: AddressFieldKey): string {
  return COUNTRY_LABELS[(country || "").toUpperCase()]?.[key] ?? DEFAULT_LABELS[key];
}

function newKey() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

function fullAddress(address: StructuredAddress) {
  return { ...EMPTY, ...Object.fromEntries(Object.entries(address).map(([k, v]) => [k, v ?? ""])) } as StructuredAddress;
}

function oneLine(a: Partial<StructuredAddress> | null | undefined) {
  if (!a) return "";
  return a.formatted || [a.subpremise, a.addressLine1, a.addressLine2, a.locality, a.administrativeArea, a.postalCode, a.countryCode]
    .filter(Boolean).join(", ");
}

/** Nudge a point by metres north/east -- the non-drag pin control. */
function nudge(p: GeoPoint, northM: number, eastM: number): GeoPoint {
  const dLat = northM / 111_320;
  const dLng = eastM / (111_320 * Math.cos((p.latitude * Math.PI) / 180));
  return { latitude: p.latitude + dLat, longitude: p.longitude + dLng };
}

function meters(a: GeoPoint, b: GeoPoint) {
  const r = 6371008.8, rad = Math.PI / 180;
  const dLat = (b.latitude - a.latitude) * rad, dLng = (b.longitude - a.longitude) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.latitude * rad) * Math.cos(b.latitude * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * r * Math.asin(Math.sqrt(h));
}

export function PropertyVerificationWizard({ propertyId, propertyLabel, onClose, onChanged }: {
  propertyId: number;
  propertyLabel: string;
  onClose: () => void;
  onChanged?: () => void;
}) {
  const [session, setSession] = useState<PropertyVerificationSession | null>(null);
  const [policy, setPolicy] = useState<PropertyPolicy | null>(null);
  const [step, setStep] = useState<Step>(0);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [loaded, setLoaded] = useState(false);
  const startKey = useRef(newKey());
  const submitKey = useRef(newKey());
  const headingRef = useRef<HTMLHeadingElement>(null);

  const load = useCallback(async () => {
    try {
      const status = await getPropertyVerificationForProperty(propertyId);
      setSession(status.verification);
      if (status.verification) setStep(resumeStep(status.verification));
      const pol = await getPropertyPolicy(status.verification?.countryCode ?? "");
      setPolicy(pol);
    } catch (err) {
      setErrors([errorMessage(err, "We couldn't load this property's verification.")]);
    } finally {
      setLoaded(true);
    }
  }, [propertyId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { headingRef.current?.focus(); }, [step]);

  /** Runs a server step; a 409 means another window changed it -- reload. */
  async function run(action: () => Promise<PropertyVerificationSession>, next?: Step) {
    setBusy(true);
    setErrors([]);
    try {
      const updated = await action();
      setSession(updated);
      onChanged?.();
      if (updated.countryCode && updated.countryCode !== policy?.countryCode) {
        getPropertyPolicy(updated.countryCode).then(setPolicy).catch(() => undefined);
      }
      if (next !== undefined) setStep(next);
      return updated;
    } catch (err) {
      const message = errorMessage(err, "Something went wrong. Please try again.");
      setErrors([message]);
      if (message.includes("changed in another window")) void load();
      return null;
    } finally {
      setBusy(false);
    }
  }

  if (!loaded) return <p className="text-sm text-slate-400" role="status">Loading property verification...</p>;

  const state = session?.state ?? "NOT_STARTED";
  const showOutcome = session && !OPEN.includes(session.state) && session.state !== "NOT_STARTED";

  return (
    <div className="space-y-5">
      {!showOutcome && step > 0 && (
        <>
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Step {step} of 6</p>
            <Button size="sm" variant="ghost" onClick={onClose}>Save and exit</Button>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800" aria-hidden="true">
            <div className="h-full rounded-full bg-primary-600 transition-all" style={{ width: `${(step / 6) * 100}%` }} />
          </div>
        </>
      )}
      <h2 ref={headingRef} tabIndex={-1} className="font-heading text-xl font-bold text-primary-900 outline-none dark:text-white">
        {showOutcome ? "Property verification" : TITLES[step]}
      </h2>
      {errors.length > 0 && (
        <div role="alert" className="rounded-xl bg-accent-50 px-4 py-3 text-sm text-accent-700 ring-1 ring-accent-200 dark:bg-accent-500/10 dark:text-accent-300">
          <p className="flex items-center gap-2 font-semibold"><XCircle className="h-4 w-4" aria-hidden="true" /> There&apos;s a problem</p>
          <ul className="mt-1 list-disc pl-6">{errors.map((e) => <li key={e}>{e}</li>)}</ul>
        </div>
      )}
      {session?.state === "ACTION_REQUIRED" && !showOutcome && session.message && (
        <Info title="What needs fixing" tone="warn">{session.message}</Info>
      )}

      {showOutcome && session ? (
        <Outcome session={session} busy={busy} onClose={onClose}
                 onFix={(target) => setStep(target)}
                 onRestart={() => run(() => restartPropertyVerification(session), 1)} />
      ) : step === 0 ? (
        <Intro label={propertyLabel} state={state} onExit={onClose}
               onStart={() => session ? setStep(resumeStep(session)) :
                 run(() => startPropertyVerification(propertyId, startKey.current), 1)} busy={busy} />
      ) : session && step === 1 ? (
        <FindAddress session={session} policy={policy} busy={busy} onBack={() => setStep(0)}
                     onSubmit={(address, mode, placeId) => run(() => setPropertyAddress(session, address, mode, placeId), 2)} />
      ) : session && step === 2 ? (
        <ConfirmAddress session={session} busy={busy} onBack={() => setStep(1)}
                        onConfirm={(useSuggestion) => run(() => confirmPropertyAddress(session, useSuggestion), 3)} />
      ) : session && step === 3 ? (
        <ConfirmLocation session={session} policy={policy} busy={busy} onBack={() => setStep(2)}
                         onWrongAddress={() => setStep(1)}
                         onConfirm={() => run(() => confirmPropertyLocation(session, { action: "confirm" }), 4)}
                         onAdjust={(p, reason) => run(() => confirmPropertyLocation(session, { action: "adjust", ...p, reason }), 4)} />
      ) : session && step === 4 ? (
        <UnitDetails session={session} policy={policy} busy={busy} onBack={() => setStep(3)}
                     onSubmit={(body) => run(() => setPropertyUnit(session, body), 5)}
                     onAnswerDuplicate={(answer, note) => run(() => answerPropertyDuplicate(session, answer, note))} />
      ) : session && step === 5 ? (
        <Evidence session={session} policy={policy} busy={busy} onBack={() => setStep(4)} onNext={() => setStep(6)}
                  onUpload={(type, file) => run(() => uploadPropertyEvidence(session, type, file))}
                  onRemove={(id) => run(() => removePropertyEvidence(session, id))} />
      ) : session && step === 6 ? (
        <Review session={session} label={propertyLabel} busy={busy} onBack={() => setStep(5)}
                onSubmit={async () => {
                  const updated = await run(() => submitPropertyVerification(session, submitKey.current));
                  if (updated?.state === "ACTION_REQUIRED") {
                    submitKey.current = newKey();
                    const codes = updated.reasonCodes;
                    setStep(codes.some((c) => c.startsWith("UNIT")) ? 4 : codes.some((c) => c.startsWith("EVIDENCE")) ? 5 : 1);
                  }
                }} />
      ) : null}
    </div>
  );
}

function resumeStep(s: PropertyVerificationSession): Step {
  if (!s.canonicalAddress?.addressLine1) return 1;
  if (!s.addressConfirmed) return 2;
  if (!s.pinStatus) return 3;
  if (!s.propertyKind) return 4;
  if (s.evidence.length === 0) return 5;
  return 6;
}

// -- shared --------------------------------------------------------------------

function Info({ title, children, tone = "plain" }: { title: string; children: React.ReactNode; tone?: "plain" | "info" | "warn" | "ok" }) {
  const cls = { plain: "bg-slate-50 dark:bg-slate-800/60", info: "bg-primary-50 dark:bg-primary-500/10",
    warn: "bg-amber-50 dark:bg-amber-500/10", ok: "bg-emerald-50 dark:bg-emerald-500/10" }[tone];
  return (
    <div className={`rounded-xl px-4 py-3 ${cls}`}>
      <p className="text-sm font-semibold text-primary-900 dark:text-white">{title}</p>
      <div className="mt-0.5 text-sm text-slate-600 dark:text-slate-300">{children}</div>
    </div>
  );
}

function Actions({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-between">{children}</div>;
}

function BackButton({ onClick }: { onClick: () => void }) {
  return <Button variant="ghost" onClick={onClick}><ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back</Button>;
}

// -- Screen 0 --------------------------------------------------------------------

function Intro({ label, state, busy, onStart, onExit }: {
  label: string; state: string; busy: boolean; onStart: () => void; onExit: () => void;
}) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-500 dark:text-slate-400">Confirm the property address and location before it can be published.</p>
      <div className="rounded-xl p-4 ring-1 ring-slate-200 dark:ring-slate-700">
        <p className="font-semibold text-primary-900 dark:text-white">{label}</p>
        <p className="text-xs text-slate-500">Current status: {propertyStateLabel[state as keyof typeof propertyStateLabel] ?? state}</p>
      </div>
      <Info title="What this checks">
        <ul className="mt-1 space-y-1">
          <li className="flex gap-2"><CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden="true" /> Address and location</li>
          <li className="flex gap-2"><CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden="true" /> Whether the property/unit can be reasonably confirmed to exist</li>
          <li className="flex gap-2"><XCircle className="h-4 w-4 text-slate-400" aria-hidden="true" /> This does not prove ownership or permission to list</li>
        </ul>
      </Info>
      <Actions>
        <Button variant="ghost" onClick={onExit}>Save and exit</Button>
        <Button loading={busy} onClick={onStart}>Start verification</Button>
      </Actions>
    </div>
  );
}

// -- Screen 1 --------------------------------------------------------------------

function FindAddress({ session, policy, busy, onBack, onSubmit }: {
  session: PropertyVerificationSession; policy: PropertyPolicy | null; busy: boolean; onBack: () => void;
  onSubmit: (address: StructuredAddress, mode: "SELECTED" | "MANUAL", placeId: string) => void;
}) {
  const country = session.countryCode || policy?.countryCode || "";
  const [manual, setManual] = useState(!policy?.autocomplete);
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<AddressSuggestion[]>([]);
  const [active, setActive] = useState(-1);
  const [searchNote, setSearchNote] = useState("");
  const [address, setAddress] = useState<StructuredAddress>(
    fullAddress({ ...EMPTY, ...(session.canonicalAddress?.addressLine1 ? session.canonicalAddress : session.submittedAddress), countryCode: country } as StructuredAddress));
  const [mode, setMode] = useState<"SELECTED" | "MANUAL">("MANUAL");
  const [placeId, setPlaceId] = useState("");
  const sessionToken = useRef(newKey());
  const listId = useId();
  const required = new Set((policy?.requiredAddressFields ?? []).map((f) => f.replace(/_(\w)/g, (_, c) => c.toUpperCase())));

  useEffect(() => { setManual(!policy?.autocomplete); }, [policy?.autocomplete]);

  useEffect(() => {
    if (manual || query.trim().length < 3) { setSuggestions([]); return; }
    const timer = setTimeout(async () => {
      try {
        const r = await suggestAddresses(query, country, sessionToken.current);
        setSuggestions(r.suggestions);
        setActive(-1);
        setSearchNote(r.suggestions.length ? `${r.suggestions.length} results available` : "No results -- try a different search or enter the address manually.");
      } catch (err) {
        setSuggestions([]);
        setSearchNote(errorMessage(err, "Location search is temporarily unavailable. Enter the address manually."));
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [query, manual, country]);

  async function choose(s: AddressSuggestion) {
    try {
      const r = await retrieveAddress(s.id, sessionToken.current);
      setAddress(fullAddress({ ...r.address, countryCode: r.address.countryCode || country }));
      setMode("SELECTED");
      setPlaceId(r.providerPlaceId);
      setSuggestions([]);
      setQuery(s.text);
      setManual(true);
      sessionToken.current = newKey();
    } catch (err) {
      setSearchNote(errorMessage(err, "We couldn't load that address. Enter it manually."));
      setManual(true);
    }
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (!suggestions.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(i + 1, suggestions.length - 1)); }
    if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); }
    if (e.key === "Enter" && active >= 0) { e.preventDefault(); void choose(suggestions[active]); }
    if (e.key === "Escape") setSuggestions([]);
  }

  const set = (k: keyof StructuredAddress) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setAddress({ ...address, [k]: e.target.value });
    if (mode === "SELECTED") setMode("MANUAL");
  };

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-500 dark:text-slate-400">Start typing the address. Choose the correct result, or enter it manually.</p>
      <Field label="Country / territory" hint="Set by the property's region.">
        <input className={inputClass} value={policy?.countryName ? `${policy.countryName} (${country})` : country} readOnly />
      </Field>

      {!manual && (
        <div className="relative">
          <label htmlFor={`${listId}-input`} className="mb-1 block text-sm font-medium text-primary-900 dark:text-white">Address search</label>
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" aria-hidden="true" />
            <input id={`${listId}-input`} className={`${inputClass} pl-9`} value={query} onChange={(e) => setQuery(e.target.value)}
                   onKeyDown={onKey} role="combobox" aria-expanded={suggestions.length > 0} aria-controls={listId}
                   aria-autocomplete="list" aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
                   autoComplete="off" placeholder="e.g. 2-599 Madupally" />
          </div>
          <p className="sr-only" aria-live="polite">{searchNote}</p>
          {suggestions.length > 0 && (
            <ul id={listId} role="listbox" className="absolute z-20 mt-1 w-full overflow-hidden rounded-xl bg-white shadow-lg ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700">
              {suggestions.map((s, i) => (
                <li key={s.id} id={`${listId}-${i}`} role="option" aria-selected={i === active}
                    onMouseDown={(e) => { e.preventDefault(); void choose(s); }}
                    className={`cursor-pointer px-4 py-2 text-sm ${i === active ? "bg-primary-50 dark:bg-primary-500/10" : ""}`}>
                  <span className="font-medium text-primary-900 dark:text-white">{s.text}</span>
                  {s.secondary && <span className="block text-xs text-slate-500">{s.secondary}</span>}
                </li>
              ))}
            </ul>
          )}
          {searchNote && suggestions.length === 0 && query.length >= 3 && <p className="mt-1 text-xs text-slate-500">{searchNote}</p>}
        </div>
      )}

      {!manual ? (
        <p className="text-sm text-slate-500">Can&apos;t find the address?{" "}
          <button type="button" className="font-semibold text-primary-700 underline dark:text-primary-300" onClick={() => setManual(true)}>Enter address manually</button>
        </p>
      ) : (
        <div className="space-y-3">
          {!policy?.autocomplete && (
            <p className="text-xs text-slate-500">Enter the address as it appears on official documents.</p>
          )}
          {(["addressLine1", "addressLine2", "locality", "administrativeArea", "postalCode"] as const).map((k) => (
            <Field key={k} label={`${fieldLabel(country, k)}${required.has(k) ? "" : " (optional)"}`}>
              <input className={inputClass} dir="auto" value={address[k]} onChange={set(k)} required={required.has(k)}
                     autoComplete={{ addressLine1: "address-line1", addressLine2: "address-line2", locality: "address-level2",
                       administrativeArea: "address-level1", postalCode: "postal-code" }[k]} />
            </Field>
          ))}
          {policy?.autocomplete && (
            <button type="button" className="text-xs font-semibold text-primary-700 underline dark:text-primary-300" onClick={() => setManual(false)}>
              Search for the address instead
            </button>
          )}
        </div>
      )}

      <Actions>
        <BackButton onClick={onBack} />
        <Button loading={busy} disabled={!manual || !address.addressLine1.trim()}
                onClick={() => onSubmit({ ...address, countryCode: country }, mode, placeId)}>Continue</Button>
      </Actions>
    </div>
  );
}

// -- Screen 2 --------------------------------------------------------------------

function ConfirmAddress({ session, busy, onBack, onConfirm }: {
  session: PropertyVerificationSession; busy: boolean; onBack: () => void; onConfirm: (useSuggestion: boolean) => void;
}) {
  const [reviewing, setReviewing] = useState(false);
  const c = session.canonicalAddress;
  const s = session.suggestedAddress;
  const rows: [string, string | undefined][] = [
    [fieldLabel(c.countryCode ?? "", "addressLine1"), c.addressLine1],
    [fieldLabel(c.countryCode ?? "", "addressLine2"), c.addressLine2],
    [fieldLabel(c.countryCode ?? "", "locality"), c.locality],
    [fieldLabel(c.countryCode ?? "", "administrativeArea"), c.administrativeArea],
    [fieldLabel(c.countryCode ?? "", "postalCode"), c.postalCode], ["Country", c.countryCode],
  ];
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-500 dark:text-slate-400">
        {session.addressStatus === "UNRESOLVED"
          ? "We couldn't find this address automatically. We'll verify it another way -- check it's correct."
          : session.addressStatus === "PARTIAL"
            ? "The map only knows the area (town or postal code), not your street or door number. We kept your address exactly as you entered it -- check it's correct."
            : "We standardized the address. Check that it is correct."}
      </p>
      <dl className="grid gap-2 rounded-xl bg-slate-50 p-4 text-sm sm:grid-cols-2 dark:bg-slate-800/60">
        {rows.map(([label, value]) => (
          <div key={label}><dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</dt>
            <dd className="text-primary-900 dark:text-white">{value || "--"}</dd></div>
        ))}
      </dl>
      {s && (
        <div className="rounded-xl bg-amber-50 px-4 py-3 text-sm dark:bg-amber-500/10">
          <p className="flex items-center gap-2 font-semibold text-amber-800 dark:text-amber-200">
            <AlertTriangle className="h-4 w-4" aria-hidden="true" /> Suggested correction available
          </p>
          {reviewing ? (
            <div className="mt-2 space-y-2">
              <p className="text-slate-700 dark:text-slate-200">Suggested: <strong>{oneLine(s)}</strong></p>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" loading={busy} onClick={() => onConfirm(true)}>Use suggested address</Button>
                <Button size="sm" variant="outline" onClick={() => setReviewing(false)}>Keep my address</Button>
              </div>
            </div>
          ) : (
            <button type="button" className="mt-1 font-semibold text-amber-800 underline dark:text-amber-200" onClick={() => setReviewing(true)}>Review correction</button>
          )}
        </div>
      )}
      <Actions>
        <BackButton onClick={onBack} />
        <Button loading={busy} onClick={() => onConfirm(false)}>Confirm address</Button>
      </Actions>
    </div>
  );
}

// -- Screen 3 --------------------------------------------------------------------

function ConfirmLocation({ session, policy, busy, onBack, onWrongAddress, onConfirm, onAdjust }: {
  session: PropertyVerificationSession; policy: PropertyPolicy | null; busy: boolean; onBack: () => void;
  onWrongAddress: () => void; onConfirm: () => void; onAdjust: (p: GeoPoint, reason: string) => void;
}) {
  const original = session.originalLocation;
  const start = session.confirmedLocation ?? original ?? COUNTRY_CENTER[session.countryCode] ?? { latitude: 20, longitude: 0 };
  const [adjusting, setAdjusting] = useState(!original);
  const [marker, setMarker] = useState<GeoPoint>(start);
  const [reason, setReason] = useState(original ? "" : "The address couldn't be found on the map, so I placed the marker on the property.");
  const moved = original ? Math.round(meters(original, marker)) : null;
  const policyM = policy?.pinMoveReviewMeters ?? 50;
  const step = 5;

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-500 dark:text-slate-400">The marker should identify the property, not a nearby landmark or neighborhood.</p>
      {browserMapProvider() === "none" ? (
        <p className="rounded-xl bg-slate-50 p-3 text-sm text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
          The map isn&apos;t available here. Confirm the location below, or enter the property&apos;s coordinates
          (for example from your phone&apos;s map app) with Adjust marker.
        </p>
      ) : (
        <PropertyPinMap original={original} marker={marker} adjustable={adjusting} policyMeters={policyM}
                        onMove={adjusting ? setMarker : undefined} />
      )}
      <div className="text-sm text-slate-600 dark:text-slate-300" aria-live="polite">
        {original ? (
          <>
            <p>Location confidence: <strong>{confidenceLabel[session.locationConfidence] ?? "Low"}</strong></p>
            {session.locationConfidence === "LOW" && (
              <p className="text-amber-700 dark:text-amber-300">
                The marker is at the centre of the area, not your property. Choose Adjust marker and move it onto the property.
              </p>
            )}
          </>
        ) : (
          <p className="text-amber-700 dark:text-amber-300">We found the area, but not the exact property. Place the marker on the property.</p>
        )}
        <p>Address: {oneLine(session.canonicalAddress)}</p>
        {adjusting && moved !== null && (
          <p>Marker moved {moved} m from the map result{moved > policyM ? " -- beyond this the property needs another verification step." : "."}</p>
        )}
      </div>

      {adjusting ? (
        <div className="space-y-3 rounded-xl bg-slate-50 p-4 dark:bg-slate-800/60">
          <p className="text-sm font-semibold text-primary-900 dark:text-white">Adjust the marker</p>
          <p className="text-xs text-slate-500">Drag the marker, click the map, or move it {step} m at a time:</p>
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Move marker">
            <Button size="sm" variant="outline" onClick={() => setMarker(nudge(marker, step, 0))}><ArrowUp className="h-4 w-4" aria-hidden="true" /> North</Button>
            <Button size="sm" variant="outline" onClick={() => setMarker(nudge(marker, -step, 0))}><ArrowDown className="h-4 w-4" aria-hidden="true" /> South</Button>
            <Button size="sm" variant="outline" onClick={() => setMarker(nudge(marker, 0, -step))}><ArrowLeft className="h-4 w-4" aria-hidden="true" /> West</Button>
            <Button size="sm" variant="outline" onClick={() => setMarker(nudge(marker, 0, step))}><ArrowRight className="h-4 w-4" aria-hidden="true" /> East</Button>
            {original && <Button size="sm" variant="ghost" onClick={() => setMarker(original)}>Reset</Button>}
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <CoordinateInput label="Latitude" value={marker.latitude} min={-90} max={90}
                             onChange={(v) => setMarker({ ...marker, latitude: v })} />
            <CoordinateInput label="Longitude" value={marker.longitude} min={-180} max={180}
                             onChange={(v) => setMarker({ ...marker, longitude: v })} />
          </div>
          <Field label="Why does the marker need to move?">
            <input className={inputClass} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300}
                   placeholder="e.g. The marker is on the road, not the building entrance" />
          </Field>
          <div className="flex flex-wrap gap-2">
            <Button loading={busy} disabled={!reason.trim()} onClick={() => onAdjust(marker, reason)}>Save marker position</Button>
            {original && <Button variant="ghost" onClick={() => { setAdjusting(false); setMarker(original); }}>Cancel</Button>}
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-2 sm:flex-row">
          <Button loading={busy} onClick={onConfirm}><CheckCircle2 className="h-4 w-4" aria-hidden="true" /> This is correct</Button>
          <Button variant="outline" onClick={() => setAdjusting(true)}><MapPin className="h-4 w-4" aria-hidden="true" /> Adjust marker</Button>
          <Button variant="ghost" onClick={onWrongAddress}>Address is wrong</Button>
        </div>
      )}
      <Actions><BackButton onClick={onBack} /><span /></Actions>
    </div>
  );
}

/** Non-drag, keyboard alternative for placing the marker (Section 17). */
function CoordinateInput({ label, value, min, max, onChange }: {
  label: string; value: number; min: number; max: number; onChange: (v: number) => void;
}) {
  const [text, setText] = useState(value.toFixed(6));
  const [focused, setFocused] = useState(false);
  const shown = focused ? text : value.toFixed(6);
  return (
    <Field label={label}>
      <input className={inputClass} inputMode="decimal" value={shown}
             onFocus={() => { setText(value.toFixed(6)); setFocused(true); }}
             onBlur={() => setFocused(false)}
             onChange={(e) => {
               setText(e.target.value);
               const v = Number(e.target.value);
               if (e.target.value.trim() && Number.isFinite(v) && v >= min && v <= max) onChange(v);
             }} />
    </Field>
  );
}

/** What we could read from an uploaded document (OCR or the PDF's text) --
 *  plain yes/no checks with icon + text, never confidence scores. */
function DocumentReading({ reading }: { reading: PropertyVerificationSession["evidence"][number]["reading"] }) {
  if (!reading) return null;
  if (reading.quality === "POOR" || reading.quality === "UNREADABLE") {
    return (
      <p className="w-full text-xs text-amber-700 dark:text-amber-300" role="status">
        <AlertTriangle className="mr-1 inline h-3.5 w-3.5" aria-hidden="true" />
        We couldn&apos;t read this clearly. Upload a sharper photo (flat, good light) or the PDF from the official website.
      </p>
    );
  }
  if (reading.quality === "OCR_UNAVAILABLE") {
    return <p className="w-full text-xs text-slate-500">Our team will read this document during review.</p>;
  }
  const checks: [string, boolean | null][] = [
    ["Address found", reading.addressFound],
    ["Postal code found", reading.postalCodeFound],
    ["Unit found", reading.unitFound],
    ["Looks like the document type you chose", reading.looksLikeChosenType],
  ];
  return (
    <ul className="flex w-full flex-wrap gap-x-4 gap-y-1 text-xs">
      {checks.filter(([, value]) => value !== null).map(([label, value]) => (
        <li key={label} className={value ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300"}>
          {value ? <CheckCircle2 className="mr-1 inline h-3.5 w-3.5" aria-hidden="true" />
            : <XCircle className="mr-1 inline h-3.5 w-3.5" aria-hidden="true" />}
          {label}{value ? "" : " -- not found"}
        </li>
      ))}
      {reading.outdated && (
        <li className="text-amber-700 dark:text-amber-300">
          <AlertTriangle className="mr-1 inline h-3.5 w-3.5" aria-hidden="true" />Older than 3 years -- upload a recent one
        </li>
      )}
    </ul>
  );
}

// -- Screen 4 --------------------------------------------------------------------

function UnitDetails({ session, policy, busy, onBack, onSubmit, onAnswerDuplicate }: {
  session: PropertyVerificationSession; policy: PropertyPolicy | null; busy: boolean; onBack: () => void;
  onSubmit: (body: { propertyKind: PropertyKind; buildingName: string; unit: string; floor: string }) => void;
  onAnswerDuplicate: (answer: "NOT_SAME_PROPERTY" | "SAME_PROPERTY", note: string) => void;
}) {
  const [dupAnswer, setDupAnswer] = useState<"" | "NOT_SAME_PROPERTY" | "SAME_PROPERTY">(session.duplicateHostAnswer);
  const [dupNote, setDupNote] = useState(session.duplicateHostNote);
  const [kind, setKind] = useState<PropertyKind>((session.propertyKind || "HOUSE") as PropertyKind);
  const [building, setBuilding] = useState(session.buildingName);
  const [unit, setUnit] = useState(session.unit);
  const [floor, setFloor] = useState(session.floor);
  const unitRequired = (policy?.unitRequiredFor ?? ["APARTMENT"]).includes(kind);
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-500 dark:text-slate-400">Add the minimum details needed to distinguish this property from others at the same address.</p>
      <Field label="Property type">
        <select className={inputClass} value={kind} onChange={(e) => setKind(e.target.value as PropertyKind)}>
          <option value="HOUSE">House</option><option value="APARTMENT">Apartment / flat</option><option value="OTHER">Other</option>
        </select>
      </Field>
      <Field label="Building name (optional)"><input className={inputClass} dir="auto" value={building} onChange={(e) => setBuilding(e.target.value)} /></Field>
      <Field label={`Unit / flat / apartment${unitRequired ? "" : " (optional)"}`}
             hint={unitRequired ? "Required for apartments -- different units at one address are different properties." : undefined}>
        <input className={inputClass} value={unit} onChange={(e) => setUnit(e.target.value)} required={unitRequired} />
      </Field>
      <Field label="Floor (optional)"><input className={inputClass} value={floor} onChange={(e) => setFloor(e.target.value)} /></Field>
      {session.possibleDuplicate && (
        <Info title="Existing Zoiko property match found" tone="warn">
          This address and unit look like a property already on Zoiko Rooms. Tell us whether it&apos;s the same property --
          our team reviews it either way.
          <fieldset className="mt-2 space-y-1.5">
            <legend className="sr-only">Is this the same property?</legend>
            <label className="flex items-center gap-2"><input type="radio" checked={dupAnswer === "NOT_SAME_PROPERTY"}
              onChange={() => setDupAnswer("NOT_SAME_PROPERTY")} /> No -- it&apos;s a different property or unit</label>
            <label className="flex items-center gap-2"><input type="radio" checked={dupAnswer === "SAME_PROPERTY"}
              onChange={() => setDupAnswer("SAME_PROPERTY")} /> Yes -- review the potential duplicate</label>
            {dupAnswer === "NOT_SAME_PROPERTY" && (
              <input className={inputClass} dir="auto" placeholder="How is it different? e.g. a different flat number"
                     value={dupNote} onChange={(e) => setDupNote(e.target.value)} maxLength={500} />
            )}
            <Button size="sm" variant="outline" disabled={busy || !dupAnswer || (dupAnswer === "NOT_SAME_PROPERTY" && !dupNote.trim())}
                    onClick={() => dupAnswer && onAnswerDuplicate(dupAnswer, dupNote)}>
              {session.duplicateHostAnswer ? "Update answer" : "Save answer"}
            </Button>
          </fieldset>
        </Info>
      )}
      <Actions>
        <BackButton onClick={onBack} />
        <Button loading={busy} disabled={unitRequired && !unit.trim()}
                onClick={() => onSubmit({ propertyKind: kind, buildingName: building, unit, floor })}>Continue</Button>
      </Actions>
    </div>
  );
}

// -- Screen 5 --------------------------------------------------------------------

function Evidence({ session, policy, busy, onBack, onNext, onUpload, onRemove }: {
  session: PropertyVerificationSession; policy: PropertyPolicy | null; busy: boolean; onBack: () => void; onNext: () => void;
  onUpload: (type: EvidenceType, file: File) => void; onRemove: (id: number) => void;
}) {
  const types = useMemo(() => policy?.acceptedEvidenceTypes ?? [], [policy?.acceptedEvidenceTypes]);
  const [type, setType] = useState<EvidenceType>(types[0] ?? "PROPERTY_TAX_RECORD");
  const fileRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (types.length && !types.includes(type)) setType(types[0]); }, [types, type]);
  const resolved = session.geocodeStatus === "RESOLVED";
  const confident = ["EXACT", "HIGH"].includes(session.locationConfidence) && session.pinStatus !== "REVIEW_REQUIRED";
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-500 dark:text-slate-400">
        We&apos;ll use trusted property/address sources where available. If we cannot confirm enough automatically, upload evidence.
      </p>
      <Info title="Automated checks">
        <ul className="mt-1 space-y-1">
          <Check ok={resolved}>Address can be resolved</Check>
          <Check ok={confident}>Location confidence acceptable</Check>
          <Check ok={false} pending>Property registry / source check unavailable or incomplete</Check>
        </ul>
      </Info>
      <div className="space-y-3 rounded-xl p-4 ring-1 ring-slate-200 dark:ring-slate-700">
        <p className="text-sm font-semibold text-primary-900 dark:text-white">Evidence needed</p>
        <Field label="Document type" hint="Accepted evidence depends on country and property type.">
          <select className={inputClass} value={type} onChange={(e) => setType(e.target.value as EvidenceType)}>
            {types.map((t) => <option key={t} value={t}>{evidenceTypeLabel[t]}</option>)}
          </select>
        </Field>
        {type === "UTILITY_BILL" && (
          <p className="text-xs text-amber-700 dark:text-amber-300">
            A utility bill supports the address, but on its own it doesn&apos;t prove the property exists -- add an official property document if you can.
          </p>
        )}
        <label className="inline-flex cursor-pointer items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold text-primary-700 ring-1 ring-primary-200 focus-within:ring-2 focus-within:ring-primary-400 dark:text-primary-300">
          <Upload className="h-4 w-4" aria-hidden="true" /> Upload property evidence
          <input ref={fileRef} type="file" accept="application/pdf,image/jpeg,image/png" className="sr-only" disabled={busy}
                 onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) onUpload(type, f); }} />
        </label>
        <p className="text-xs text-slate-500">PDF, JPG or PNG. A PDF with selectable text is checked fastest.</p>
        {session.evidence.length > 0 && (
          <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
            {session.evidence.map((e) => (
              <li key={e.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <a className="flex items-center gap-2 text-primary-700 hover:underline dark:text-primary-300" target="_blank" rel="noreferrer"
                   href={propertyEvidenceUrl(session.id, e.id)}>
                  <FileText className="h-4 w-4" aria-hidden="true" /> {e.originalFilename}
                </a>
                <span className="flex items-center gap-2 text-xs text-slate-500">
                  {evidenceTypeLabel[e.evidenceType]}
                  <button type="button" aria-label={`Remove ${e.originalFilename}`} onClick={() => onRemove(e.id)} disabled={busy}
                          className="rounded p-1 text-slate-400 hover:text-accent-600"><Trash2 className="h-4 w-4" aria-hidden="true" /></button>
                </span>
                <DocumentReading reading={e.reading} />
              </li>
            ))}
          </ul>
        )}
      </div>
      <p className="text-xs text-slate-500">Examples: land/property registry record, tax/assessment record, official property document, building/unit document.</p>
      <Actions>
        <BackButton onClick={onBack} />
        <Button disabled={session.evidence.length === 0} onClick={onNext}>Continue</Button>
      </Actions>
    </div>
  );
}

function Check({ ok, pending, children }: { ok: boolean; pending?: boolean; children: React.ReactNode }) {
  return (
    <li className="flex items-center gap-2">
      {ok ? <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden="true" />
        : pending ? <Circle className="h-4 w-4 text-slate-400" aria-hidden="true" />
          : <AlertTriangle className="h-4 w-4 text-amber-600" aria-hidden="true" />}
      <span>{children}{ok ? "" : pending ? "" : " -- we'll confirm with your evidence"}</span>
    </li>
  );
}

// -- Screen 6 --------------------------------------------------------------------

function Review({ session, label, busy, onBack, onSubmit }: {
  session: PropertyVerificationSession; label: string; busy: boolean; onBack: () => void; onSubmit: () => void;
}) {
  const [attested, setAttested] = useState(false);
  const locationText = session.pinStatus === "REVIEW_REQUIRED" ? "Placed by you -- will be reviewed"
    : `Confirmed · ${(confidenceLabel[session.locationConfidence] ?? "low").toLowerCase()} confidence`;
  return (
    <div className="space-y-4">
      <dl className="grid gap-3 rounded-xl bg-slate-50 p-4 text-sm sm:grid-cols-2 dark:bg-slate-800/60">
        <Item label="Property" value={label} />
        <Item label="Canonical address" value={oneLine(session.canonicalAddress)} />
        <Item label="Location" value={locationText} />
        <Item label="Unit" value={[session.propertyKind.toLowerCase(), session.unit].filter(Boolean).join(" ") || "--"} />
        <Item label="Evidence" value={`${session.evidence.length} file${session.evidence.length === 1 ? "" : "s"}`} />
      </dl>
      <label className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-200">
        <input type="checkbox" className="mt-1" checked={attested} onChange={(e) => setAttested(e.target.checked)} />
        I confirm this information accurately identifies the property I intend to list.
      </label>
      <Info title="Privacy" tone="info">Precise property coordinates and verification documents are not shown publicly.</Info>
      <Actions>
        <BackButton onClick={onBack} />
        <Button loading={busy} disabled={!attested} onClick={onSubmit}>Submit for verification</Button>
      </Actions>
    </div>
  );
}

function Item({ label, value }: { label: string; value: string }) {
  return (
    <div><dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-primary-900 dark:text-white">{value}</dd></div>
  );
}

// -- Screen 7 --------------------------------------------------------------------

function Outcome({ session, busy, onClose, onFix, onRestart }: {
  session: PropertyVerificationSession; busy: boolean; onClose: () => void; onFix: (step: Step) => void; onRestart: () => void;
}) {
  const s = session.state;
  const icon = useMemo(() => s === "VERIFIED" ? <BadgeCheck className="h-6 w-6 text-emerald-600" aria-hidden="true" />
    : s === "MANUAL_REVIEW" ? <Clock className="h-6 w-6 text-primary-600" aria-hidden="true" />
      : s === "EXPIRING_SOON" ? <AlertTriangle className="h-6 w-6 text-amber-600" aria-hidden="true" />
        : <ShieldCheck className="h-6 w-6 text-slate-500" aria-hidden="true" />, [s]);
  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          {icon}
          <div>
            <p className="font-semibold text-primary-900 dark:text-white">{propertyStateLabel[s]}</p>
            <p className="text-sm text-slate-500" aria-live="polite">
              {s === "VERIFIED" && "Address and property checks are complete."}
              {s === "EXPIRING_SOON" && `Verified until ${session.expiresAt ? formatDate(session.expiresAt) : "soon"} -- renew to keep listing.`}
              {s === "MANUAL_REVIEW" && "We need to review the property information. You can leave this page."}
              {!["VERIFIED", "EXPIRING_SOON", "MANUAL_REVIEW"].includes(s) && (session.message || "Verify this property again.")}
            </p>
          </div>
        </div>
        <Badge tone={propertyStateTone[s]} dot>{propertyStateLabel[s]}</Badge>
      </div>
      {s === "VERIFIED" && session.expiresAt && <p className="text-xs text-slate-500">Valid until {formatDate(session.expiresAt)}.</p>}
      <Actions>
        <Button variant="ghost" onClick={onClose}>{s === "MANUAL_REVIEW" ? "Go to dashboard" : "Close"}</Button>
        <div className="flex flex-col gap-2 sm:flex-row">
          {s === "VERIFIED" && <Link href="/account/host/listings"><Button fullWidth>Continue to authority verification</Button></Link>}
          {s === "ACTION_REQUIRED" && (
            <>
              <Button variant="outline" onClick={() => onFix(1)}>Review address</Button>
              <Button onClick={() => onFix(5)}>Add evidence</Button>
            </>
          )}
          {session.canRestart && <Button loading={busy} onClick={onRestart}>
            {s === "EXPIRING_SOON" || s === "EXPIRED" ? "Renew verification" : s === "REJECTED" ? "Resubmit" : "Reverify property"}
          </Button>}
        </div>
      </Actions>
    </div>
  );
}
