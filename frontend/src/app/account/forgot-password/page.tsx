"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { CheckCircle2, Mail } from "lucide-react";
import { Logo } from "@/components/ui/Logo";
import { Button } from "@/components/ui/Button";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { requestPasswordReset } from "@/lib/user-auth";
import { errorMessage } from "@/lib/user-api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!email.trim()) {
      setError("Enter the email address on your account.");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      const result = await requestPasswordReset(email.trim());
      setMessage(result.message);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-primary-900 px-4 py-12 sm:px-8">
      <div className="bg-noise pointer-events-none absolute inset-0 opacity-30" />
      <div className="absolute right-4 top-4 sm:right-6 sm:top-6">
        <ThemeToggle className="text-primary-200 hover:bg-white/10 hover:text-white" />
      </div>

      <div className="relative w-full max-w-md">
        <div className="mb-8 flex flex-col items-center gap-4 animate-fade-up">
          <Logo variant="light" href="/account/login" />
        </div>

        <div className="animate-scale-in stagger-1 rounded-3xl bg-white p-8 shadow-2xl shadow-black/30 ring-1 ring-white/10 sm:p-10 dark:bg-slate-900">
          {message ? (
            <div className="text-center">
              <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-emerald-50 dark:bg-emerald-500/10">
                <CheckCircle2 className="h-7 w-7 text-emerald-600" />
              </div>
              <h2 className="mt-4 font-heading text-xl font-extrabold text-primary-900 dark:text-white">
                Check your email
              </h2>
              <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">{message}</p>
              <Link href="/account/login" className="mt-6 inline-block">
                <Button variant="primary" size="lg">
                  Back to sign in
                </Button>
              </Link>
            </div>
          ) : (
            <>
              <h2 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">
                Forgot your password?
              </h2>
              <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
                Enter your email and we&apos;ll send you a link to reset it.
              </p>

              <form onSubmit={handleSubmit} className="mt-7 space-y-4">
                <label className="block">
                  <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                    Email address
                  </span>
                  <div className="group flex items-center gap-2 rounded-xl bg-slate-50 px-4 py-3 ring-1 ring-slate-200 transition-all duration-200 hover:ring-primary-300 focus-within:bg-white focus-within:ring-2 focus-within:ring-primary-400 dark:bg-slate-800 dark:ring-slate-700 dark:focus-within:bg-slate-800">
                    <Mail className="h-4 w-4 shrink-0 text-slate-400 transition-colors group-focus-within:text-primary-600" />
                    <input
                      type="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="you@example.com"
                      autoComplete="username"
                      className="w-full bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400 dark:text-slate-100"
                    />
                  </div>
                </label>

                {error && (
                  <p className="animate-fade-in rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
                    {error}
                  </p>
                )}

                <Button type="submit" variant="primary" size="lg" fullWidth loading={submitting}>
                  {submitting ? "Sending" : "Send reset link"}
                </Button>
              </form>

              <p className="mt-5 text-center text-sm text-slate-500 dark:text-slate-400">
                Remembered it?{" "}
                <Link href="/account/login" className="font-semibold text-primary-700 hover:text-accent-600 dark:text-primary-300">
                  Sign in
                </Link>
              </p>
            </>
          )}
        </div>
      </div>
    </main>
  );
}
