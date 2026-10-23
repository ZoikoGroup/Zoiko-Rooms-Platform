"use client";

import { use, useState } from "react";
import Link from "next/link";
import { CheckCircle2, XCircle } from "lucide-react";
import { Logo } from "@/components/ui/Logo";
import { apiClientFetch } from "@/lib/api-client";

type State = "ask" | "working" | "done" | "invalid" | "error";

/** Where the "Unsubscribe from this alert" link in a room-alert email lands.
 *  It asks first, so a mail scanner that merely opens the link can't unsubscribe anyone. */
export default function UnsubscribePage({ searchParams }: { searchParams: Promise<{ alert?: string; token?: string }> }) {
  const { alert, token } = use(searchParams);
  const [state, setState] = useState<State>(alert && token ? "ask" : "invalid");

  async function unsubscribe() {
    if (!alert || !token) return;
    setState("working");
    try {
      const res = await apiClientFetch<{ unsubscribed: boolean }>(
        `/api/public/alerts/${encodeURIComponent(alert)}/unsubscribe?token=${encodeURIComponent(token)}`,
        { method: "POST" },
      );
      setState(res.unsubscribed ? "done" : "invalid");
    } catch {
      setState("error");
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#EDF1F8] p-4 dark:bg-slate-950">
      <div className="w-full max-w-md space-y-5 rounded-2xl border border-slate-200 bg-white p-8 text-center shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="flex justify-center">
          <Logo />
        </div>
        {(state === "ask" || state === "working") && (
          <>
            <p className="text-sm font-semibold text-slate-900 dark:text-white">Stop emails for this room alert?</p>
            <p className="text-sm text-slate-600 dark:text-slate-300">
              You won&apos;t get any more emails when new rooms match this search.
            </p>
            <button
              type="button"
              onClick={unsubscribe}
              disabled={state === "working"}
              className="inline-block rounded-xl bg-primary-900 px-4 py-2 text-sm font-bold text-white disabled:opacity-60"
            >
              {state === "working" ? "Unsubscribing…" : "Unsubscribe"}
            </button>
          </>
        )}
        {state === "done" && (
          <>
            <CheckCircle2 className="mx-auto h-10 w-10 text-emerald-600" />
            <p className="text-sm font-semibold text-slate-900 dark:text-white">You&apos;ve been unsubscribed</p>
            <p className="text-sm text-slate-600 dark:text-slate-300">
              You won&apos;t receive any more room alert emails for this search.
            </p>
            <Link href="/find-a-room" className="inline-block rounded-xl bg-primary-900 px-4 py-2 text-sm font-bold text-white">
              Find a room
            </Link>
          </>
        )}
        {(state === "invalid" || state === "error") && (
          <>
            <XCircle className="mx-auto h-10 w-10 text-accent-600" />
            <p className="text-sm font-semibold text-slate-900 dark:text-white">
              {state === "invalid" ? "Link no longer valid" : "Something went wrong"}
            </p>
            <p className="text-sm text-slate-600 dark:text-slate-300">
              {state === "invalid"
                ? "This unsubscribe link has already been used or is invalid."
                : "We couldn't reach the server. Please try again in a moment."}
            </p>
          </>
        )}
      </div>
    </div>
  );
}
