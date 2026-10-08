"use client";

import { use, useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { BadgeCheck, MessageSquare, ShieldAlert, XCircle } from "lucide-react";
import { Logo } from "@/components/ui/Logo";
import { Button } from "@/components/ui/Button";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { errorMessage } from "@/lib/user-api";
import {
  getProviderRequest,
  providerConsentToRelease,
  providerOptOut,
  providerSendMessage,
  respondAsProvider,
  type ProviderRequestView,
} from "@/lib/external-search";

const DEMAND_LABELS: Record<string, string> = {
  desired_area: "Area",
  move_in_window: "Move-in",
  budget_band: "Budget",
  room_type: "Room type",
  occupants: "Occupants",
  requirements: "Requirements",
};

const OPTION_TEXT: Record<string, { title: string; detail: string }> = {
  CLAIM_AND_LIST: {
    title: "List with Zoiko Rooms",
    detail:
      "Claim this room and list it yourself after Zoiko Rooms' standard identity, property and authority checks. You pay only the applicable Listing Fee; no rental commission.",
  },
  ONE_OFF_INTRODUCTION: {
    title: "One-off introduction",
    detail: "Talk to this renter through Zoiko Rooms' messaging without listing the room.",
  },
};

/** ZR-AI-SEARCH-001 Section 9: the external provider answers a renter's
 *  request from the signed link in Zoiko Rooms' email. No account is needed;
 *  the renter's identity stays private unless both sides agree to share. */
export default function ProviderRespondPage({ searchParams }: { searchParams: Promise<{ token?: string; action?: string }> }) {
  const { token, action } = use(searchParams);
  const [view, setView] = useState<ProviderRequestView | null>(null);
  const [loadError, setLoadError] = useState("");
  const [model, setModel] = useState("CLAIM_AND_LIST");
  const [name, setName] = useState("");
  const [terms, setTerms] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [optedOut, setOptedOut] = useState(false);

  useEffect(() => {
    if (!token) {
      setLoadError("This link is missing its token. Use the link from Zoiko Rooms' email.");
      return;
    }
    getProviderRequest(token).then(setView).catch((err) => setLoadError(errorMessage(err, "This link isn't valid.")));
  }, [token]);

  async function run(fn: () => Promise<ProviderRequestView | void>) {
    setBusy(true);
    setError("");
    try {
      const next = await fn();
      if (next) setView(next);
    } catch (err) {
      setError(errorMessage(err, "Something went wrong. Please try again."));
    } finally {
      setBusy(false);
    }
  }

  function accept(e: FormEvent) {
    e.preventDefault();
    if (!token) return;
    run(() => respondAsProvider(token, { decision: "ACCEPT", model, providerName: name, acceptedTerms: terms }));
  }

  function send(e: FormEvent) {
    e.preventDefault();
    if (!token || !message.trim()) return;
    run(async () => {
      const next = await providerSendMessage(token, message.trim());
      setMessage("");
      return next;
    });
  }

  const awaiting = view?.status === "AWAITING_RESPONSE";
  const accepted = view?.status === "ACCEPTED";
  const claimHref = token ? `/account/claim?token=${encodeURIComponent(token)}` : "#";

  return (
    <main className="min-h-screen bg-slate-50 px-4 py-10 dark:bg-slate-950">
      <div className="mx-auto max-w-lg space-y-6">
        <div className="flex items-center justify-between">
          <Logo />
          <ThemeToggle />
        </div>

        <section className="space-y-4 rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-800">
          <h1 className="font-heading text-xl font-bold text-primary-900 dark:text-white">A renter asked us to contact you</h1>

          {loadError && <p className="text-sm text-accent-700" role="alert">{loadError}</p>}
          {!view && !loadError && <p className="text-sm text-slate-400" role="status">Loading...</p>}

          {optedOut ? (
            <p className="flex items-center gap-2 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-200" role="status">
              <BadgeCheck className="h-4 w-4" aria-hidden="true" /> You won&apos;t be contacted by Zoiko Rooms again.
            </p>
          ) : view && (
            <>
              <p className="text-sm text-slate-700 dark:text-slate-200">
                We are Zoiko Rooms, a trading name of {view.senderEntity}. A prospective renter asked us to contact you about a
                room that appears to be advertised in <strong>{view.approxLocation ?? "your area"}</strong>.
              </p>

              {Object.keys(view.renterDemand).length > 0 && (
                <dl className="grid gap-2 rounded-xl bg-slate-50 p-3 text-sm sm:grid-cols-2 dark:bg-slate-800/60">
                  {Object.entries(view.renterDemand).map(([key, value]) => (
                    <div key={key}>
                      <dt className="text-xs text-slate-400">{DEMAND_LABELS[key] ?? key}</dt>
                      <dd className="text-slate-700 dark:text-slate-200">{value}</dd>
                    </div>
                  ))}
                </dl>
              )}
              {view.renterMessage && (
                <p className="rounded-xl border border-slate-200 p-3 text-sm italic text-slate-600 dark:border-slate-700 dark:text-slate-300">
                  &ldquo;{view.renterMessage}&rdquo;
                </p>
              )}

              {action === "opt-out" && awaiting ? (
                <div className="space-y-3">
                  <p className="text-sm text-slate-600 dark:text-slate-300">
                    Stop all contact from Zoiko Rooms about rooms you advertise? We&apos;ll keep a one-way record of your
                    contact so we never message you again.
                  </p>
                  <Button disabled={busy} onClick={() => token && run(async () => { await providerOptOut(token); setOptedOut(true); })}>
                    Opt out of contact
                  </Button>
                </div>
              ) : awaiting ? (
                <form className="space-y-4" onSubmit={accept}>
                  <fieldset className="space-y-2">
                    <legend className="text-sm font-semibold text-slate-700 dark:text-slate-200">How would you like to proceed?</legend>
                    {view.options.map((opt) => (
                      <label key={opt} className="flex cursor-pointer gap-3 rounded-xl border border-slate-200 p-3 text-sm dark:border-slate-700">
                        <input type="radio" name="model" value={opt} checked={model === opt} onChange={() => setModel(opt)} className="mt-1" />
                        <span>
                          <span className="block font-semibold text-slate-800 dark:text-slate-100">{OPTION_TEXT[opt]?.title ?? opt}</span>
                          <span className="block text-xs text-slate-500">{OPTION_TEXT[opt]?.detail}</span>
                        </span>
                      </label>
                    ))}
                  </fieldset>
                  <label className="block text-sm">
                    <span className="font-semibold text-slate-700 dark:text-slate-200">Your name or business name</span>
                    <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="organization" maxLength={200}
                           className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-800" />
                  </label>
                  <label className="flex gap-2 text-xs text-slate-600 dark:text-slate-300">
                    <input type="checkbox" checked={terms} onChange={(e) => setTerms(e.target.checked)} className="mt-0.5" />
                    <span>
                      I accept the Zoiko Rooms provider introduction terms (version {view.termsVersion ?? "1"})
                      {view.leadProtectionDays ? `, including not dealing with this renter outside Zoiko Rooms for ${view.leadProtectionDays} days` : ""}.
                      Accepting is not verification: you stay &ldquo;not verified by Zoiko Rooms&rdquo; until you complete the checks.
                    </span>
                  </label>
                  {error && <p className="text-sm text-accent-700" role="alert">{error}</p>}
                  <div className="flex flex-wrap gap-2">
                    <Button type="submit" loading={busy} disabled={busy || !terms}>Accept</Button>
                    <Button type="button" variant="ghost" disabled={busy}
                            onClick={() => token && run(() => respondAsProvider(token, { decision: "DECLINE" }))}>
                      Decline this introduction
                    </Button>
                  </div>
                  <p className="text-xs text-slate-500">
                    Don&apos;t want to hear from us?{" "}
                    <Link className="underline" href={`/provider/respond?token=${encodeURIComponent(token ?? "")}&action=opt-out`}>Opt out of contact</Link>.
                  </p>
                </form>
              ) : view.status === "DECLINED" ? (
                <p className="flex items-center gap-2 rounded-lg bg-slate-50 p-3 text-sm text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                  <XCircle className="h-4 w-4" aria-hidden="true" /> You declined this introduction. Nothing has been shared.
                </p>
              ) : accepted && (
                <div className="space-y-4">
                  <p className="flex items-start gap-2 rounded-lg bg-amber-50 p-3 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
                    <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                    You accepted. You are not verified by Zoiko Rooms yet, so payments and agreements through Zoiko Rooms stay
                    unavailable until you complete the checks.
                  </p>
                  {view.acceptanceModel === "CLAIM_AND_LIST" && (
                    <Link href={claimHref} className="inline-flex rounded-xl bg-primary-700 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-800">
                      Sign in or create an account to claim this room
                    </Link>
                  )}

                  <div className="space-y-2">
                    <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-700 dark:text-slate-200">
                      <MessageSquare className="h-4 w-4" aria-hidden="true" /> Messages with the renter
                    </h2>
                    <ul className="space-y-2">
                      {view.messages.map((m) => (
                        <li key={m.id} className={`rounded-xl px-3 py-2 text-sm ${m.from === "you" ? "ml-8 bg-primary-50 dark:bg-primary-500/10" : "mr-8 bg-slate-100 dark:bg-slate-800"}`}>
                          <span className="block text-[11px] text-slate-400">{m.from === "you" ? "You" : "Renter"}</span>
                          {m.body}
                        </li>
                      ))}
                      {view.messages.length === 0 && <li className="text-xs text-slate-400">No messages yet.</li>}
                    </ul>
                    <form className="flex gap-2" onSubmit={send}>
                      <input value={message} onChange={(e) => setMessage(e.target.value)} maxLength={5000} placeholder="Write a message"
                             className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800" />
                      <Button type="submit" disabled={busy || !message.trim()}>Send</Button>
                    </form>
                    <p className="text-[11px] text-slate-400">Phone numbers, emails and links are removed from messages.</p>
                  </div>

                  {view.release.available && (
                    <div className="rounded-xl border border-slate-200 p-3 text-sm dark:border-slate-700">
                      {view.release.released ? (
                        <p>Renter&apos;s contact: <strong>{view.release.contact}</strong></p>
                      ) : view.release.youConsented ? (
                        <p className="text-slate-600 dark:text-slate-300">You agreed to share contact details. Waiting for the renter.</p>
                      ) : (
                        <Button variant="ghost" disabled={busy} onClick={() => token && run(() => providerConsentToRelease(token))}>
                          Agree to share contact details
                        </Button>
                      )}
                    </div>
                  )}
                  {error && <p className="text-sm text-accent-700" role="alert">{error}</p>}
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </main>
  );
}
