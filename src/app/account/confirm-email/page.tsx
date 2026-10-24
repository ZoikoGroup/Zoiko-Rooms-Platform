"use client";

import { use, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { CheckCircle2, XCircle } from "lucide-react";
import { Logo } from "@/components/ui/Logo";
import { confirmEmailChange, errorMessage } from "@/lib/user-api";

export default function ConfirmEmailPage({ searchParams }: { searchParams: Promise<{ token?: string; signup?: string }> }) {
  // signup=1: the confirm-your-email link sent when the account was created,
  // rather than a link confirming a new sign-in email.
  const { token, signup } = use(searchParams);
  const isSignup = signup === "1";
  const [state, setState] = useState<{ status: "working" | "done" | "error"; message: string }>({
    status: "working",
    message: "",
  });
  // The link is single-use: guard against React's dev double-invoke.
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (!token) {
      setState({ status: "error", message: "This link is missing its token." });
      return;
    }
    confirmEmailChange(token)
      .then((user) =>
        setState({
          status: "done",
          message: isSignup ? `Thanks — ${user.email} is confirmed.` : `Your sign-in email is now ${user.email}.`,
        }),
      )
      .catch((err) => setState({ status: "error", message: errorMessage(err, "This confirmation link is invalid or has expired.") }));
  }, [token, isSignup]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#EDF1F8] p-4 dark:bg-slate-950">
      <div className="w-full max-w-md space-y-5 rounded-2xl border border-slate-200 bg-white p-8 text-center shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="flex justify-center">
          <Logo />
        </div>
        {state.status === "working" && (
          <p className="text-sm text-slate-600 dark:text-slate-300">{isSignup ? "Confirming your email…" : "Confirming your new email…"}</p>
        )}
        {state.status === "done" && (
          <>
            <CheckCircle2 className="mx-auto h-10 w-10 text-emerald-600" />
            <p className="text-sm font-semibold text-slate-900 dark:text-white">{state.message}</p>
            <Link
              href={isSignup ? "/account" : "/account/settings"}
              className="inline-block rounded-xl bg-primary-900 px-4 py-2 text-sm font-bold text-white"
            >
              {isSignup ? "Go to your account" : "Go to settings"}
            </Link>
          </>
        )}
        {state.status === "error" && (
          <>
            <XCircle className="mx-auto h-10 w-10 text-rose-600" />
            <p className="text-sm font-semibold text-slate-900 dark:text-white">{state.message}</p>
            <Link href="/account/settings" className="inline-block text-sm font-bold text-primary-700 hover:underline">
              Back to settings
            </Link>
          </>
        )}
      </div>
    </div>
  );
}
