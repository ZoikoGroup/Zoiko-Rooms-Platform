"use client";

import { use, useEffect, useState, type FormEvent } from "react";
import { BadgeCheck, ShieldCheck, XCircle } from "lucide-react";
import { Logo } from "@/components/ui/Logo";
import { Button } from "@/components/ui/Button";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { errorMessage } from "@/lib/user-api";
import { formatDate } from "@/lib/utils";
import { ConfirmationSummary, getConfirmation, respondToConfirmation, scopeLabel } from "@/lib/authority-verification";

const KIND_TEXT: Record<ConfirmationSummary["kind"], string> = {
  MANDATE: "to advertise and rent your property as your agent or property manager",
  SUBLET_PERMISSION: "to sublet the property they rent from you",
  CO_OWNER_CONSENT: "to list the property you co-own",
};

/** ZR-AUTHORITY-002 Section 13: the owner / landlord confirms or declines
 *  with the secure link plus the one-time code sent separately. No account
 *  is needed and no documents or personal data are shown. */
export default function AuthorityConfirmationPage({ searchParams }: { searchParams: Promise<{ token?: string }> }) {
  const { token } = use(searchParams);
  const [summary, setSummary] = useState<ConfirmationSummary | null>(null);
  const [loadError, setLoadError] = useState("");
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState("");

  useEffect(() => {
    if (!token) {
      setLoadError("This link is missing its token. Ask the person to send a new request.");
      return;
    }
    getConfirmation(token).then(setSummary).catch((err) => setLoadError(errorMessage(err, "This link isn't valid.")));
  }, [token]);

  async function respond(e: FormEvent, decision: "CONFIRM" | "DECLINE") {
    e.preventDefault();
    if (!token) return;
    setSubmitting(true);
    setError("");
    try {
      const r = await respondToConfirmation(token, { code: code.trim(), decision, responderName: name });
      setResult(r.status);
    } catch (err) {
      setError(errorMessage(err, "Could not record your answer."));
    } finally {
      setSubmitting(false);
    }
  }

  const closed = summary && summary.status !== "PENDING";

  return (
    <main className="min-h-screen bg-slate-50 px-4 py-10 dark:bg-slate-950">
      <div className="mx-auto max-w-lg space-y-6">
        <div className="flex items-center justify-between">
          <Logo />
          <ThemeToggle />
        </div>
        <section className="space-y-4 rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-800">
          <h1 className="flex items-center gap-2 font-heading text-xl font-bold text-primary-900 dark:text-white">
            <ShieldCheck className="h-5 w-5" aria-hidden="true" /> Confirm a request
          </h1>

          {loadError && <p className="text-sm text-accent-700" role="alert">{loadError}</p>}
          {!summary && !loadError && <p className="text-sm text-slate-400" role="status">Loading...</p>}

          {result ? (
            <p className="flex items-center gap-2 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-200" role="status">
              {result === "CONFIRMED" ? <BadgeCheck className="h-4 w-4" aria-hidden="true" /> : <XCircle className="h-4 w-4" aria-hidden="true" />}
              {result === "CONFIRMED" ? "Thank you -- your confirmation has been recorded." : "You declined this request. Nothing has been shared."}
            </p>
          ) : summary && (
            <>
              <p className="text-sm text-slate-700 dark:text-slate-200">
                <strong>{summary.requesterName}</strong> asks for your permission {KIND_TEXT[summary.kind]} in{" "}
                <strong>{summary.propertyCity}</strong>.
              </p>
              <dl className="grid gap-2 text-sm sm:grid-cols-2">
                {summary.scopeCodes.length > 0 && (
                  <div><dt className="text-xs text-slate-400">Permission requested</dt>
                    <dd>{summary.scopeCodes.map((s) => scopeLabel[s]).join(", ")}</dd></div>
                )}
                {(summary.effectiveAt || summary.endsAt) && (
                  <div><dt className="text-xs text-slate-400">Period</dt>
                    <dd>{summary.effectiveAt ? formatDate(summary.effectiveAt) : "Now"} -- {summary.endsAt ? formatDate(summary.endsAt) : "no end date"}</dd></div>
                )}
                {summary.restrictions && <div className="sm:col-span-2"><dt className="text-xs text-slate-400">Restrictions</dt><dd>{summary.restrictions}</dd></div>}
              </dl>

              {closed ? (
                <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                  This request is {summary.status.toLowerCase()}. If you need to change your answer, contact Zoiko Rooms support.
                </p>
              ) : (
                <form className="space-y-3" onSubmit={(e) => respond(e, "CONFIRM")}>
                  <label className="block text-sm">
                    <span className="font-semibold text-slate-700 dark:text-slate-200">6-digit code</span>
                    <span className="block text-xs text-slate-500">Sent to you in a separate email.</span>
                    <input inputMode="numeric" autoComplete="one-time-code" maxLength={6} required value={code}
                           onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                           className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 tracking-[0.4em] dark:border-slate-700 dark:bg-slate-800" />
                  </label>
                  <label className="block text-sm">
                    <span className="font-semibold text-slate-700 dark:text-slate-200">Your full name</span>
                    <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name"
                           className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-800" />
                  </label>
                  <p className="text-xs text-slate-500">
                    By confirming you state that you are the owner, landlord or authorized principal for this property and
                    that you grant this permission. Link valid until {formatDate(summary.expiresAt)}.
                  </p>
                  {error && <p className="text-sm text-accent-700" role="alert">{error}</p>}
                  <div className="flex flex-wrap gap-2">
                    <Button type="submit" loading={submitting} disabled={submitting || code.length !== 6 || !name.trim()}>
                      Confirm
                    </Button>
                    <Button type="button" variant="ghost" disabled={submitting || code.length !== 6}
                            onClick={(e) => respond(e as unknown as FormEvent, "DECLINE")}>
                      Decline -- I don&apos;t recognize this
                    </Button>
                  </div>
                </form>
              )}
            </>
          )}
        </section>
      </div>
    </main>
  );
}
