"use client";

import { use, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CheckCircle2, Eye, EyeOff, Lock } from "lucide-react";
import { Logo } from "@/components/ui/Logo";
import { Button } from "@/components/ui/Button";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { resetPassword } from "@/lib/user-auth";
import { errorMessage } from "@/lib/user-api";

export default function ResetPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token } = use(searchParams);
  const router = useRouter();

  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!token) {
      setError("This reset link is missing its token. Request a new one.");
      return;
    }
    if (newPassword.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("The two passwords do not match.");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      await resetPassword(token, newPassword);
      setDone(true);
    } catch (err) {
      setError(errorMessage(err, "This reset link is invalid or has expired."));
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
          {done ? (
            <div className="text-center">
              <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-emerald-50 dark:bg-emerald-500/10">
                <CheckCircle2 className="h-7 w-7 text-emerald-600" />
              </div>
              <h2 className="mt-4 font-heading text-xl font-extrabold text-primary-900 dark:text-white">
                Password reset
              </h2>
              <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
                You can now sign in with your new password. You&apos;ll need to sign in again on any other device.
              </p>
              <Button variant="primary" size="lg" className="mt-6" onClick={() => router.push("/account/login")}>
                Back to sign in
              </Button>
            </div>
          ) : !token ? (
            <div className="text-center">
              <h2 className="font-heading text-xl font-extrabold text-primary-900 dark:text-white">
                Invalid reset link
              </h2>
              <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
                This link is missing its reset token. Request a new one instead.
              </p>
              <Link href="/account/forgot-password" className="mt-6 inline-block">
                <Button variant="primary" size="lg">
                  Request a new link
                </Button>
              </Link>
            </div>
          ) : (
            <>
              <h2 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">
                Choose a new password
              </h2>
              <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">Use at least 8 characters.</p>

              <form onSubmit={handleSubmit} className="mt-7 space-y-4">
                <label className="block">
                  <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                    New password
                  </span>
                  <div className="group flex items-center gap-2 rounded-xl bg-slate-50 px-4 py-3 ring-1 ring-slate-200 transition-all duration-200 hover:ring-primary-300 focus-within:bg-white focus-within:ring-2 focus-within:ring-primary-400 dark:bg-slate-800 dark:ring-slate-700 dark:focus-within:bg-slate-800">
                    <Lock className="h-4 w-4 shrink-0 text-slate-400 transition-colors group-focus-within:text-primary-600" />
                    <input
                      type={showPassword ? "text" : "password"}
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      placeholder="At least 8 characters"
                      autoComplete="new-password"
                      className="w-full bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400 dark:text-slate-100"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword((s) => !s)}
                      className="shrink-0 text-slate-400 transition-transform duration-200 hover:scale-110 hover:text-primary-600"
                      aria-label="Toggle password visibility"
                    >
                      {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </label>

                <label className="block">
                  <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                    Confirm new password
                  </span>
                  <div className="group flex items-center gap-2 rounded-xl bg-slate-50 px-4 py-3 ring-1 ring-slate-200 transition-all duration-200 hover:ring-primary-300 focus-within:bg-white focus-within:ring-2 focus-within:ring-primary-400 dark:bg-slate-800 dark:ring-slate-700 dark:focus-within:bg-slate-800">
                    <Lock className="h-4 w-4 shrink-0 text-slate-400 transition-colors group-focus-within:text-primary-600" />
                    <input
                      type={showPassword ? "text" : "password"}
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      autoComplete="new-password"
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
                  {submitting ? "Resetting" : "Reset password"}
                </Button>
              </form>
            </>
          )}
        </div>
      </div>
    </main>
  );
}
