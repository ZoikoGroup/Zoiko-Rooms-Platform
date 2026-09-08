"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  BarChart3,
  BedDouble,
  CalendarCheck2,
  Eye,
  EyeOff,
  Lock,
  Mail,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { Logo } from "@/components/ui/Logo";
import { Button } from "@/components/ui/Button";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { login } from "@/lib/auth";
import { ApiError } from "@/lib/api-client";

const perks = [
  { icon: CalendarCheck2, text: "Bookings" },
  { icon: BarChart3, text: "Analytics" },
  { icon: BedDouble, text: "Rooms & rates" },
];

const floaters = [
  { icon: CalendarCheck2, className: "left-[8%] top-[18%]", delay: "0s", size: "h-10 w-10" },
  { icon: BedDouble, className: "right-[10%] top-[14%]", delay: "0.8s", size: "h-8 w-8" },
  { icon: BarChart3, className: "left-[12%] bottom-[16%]", delay: "1.6s", size: "h-9 w-9" },
  { icon: ShieldCheck, className: "right-[8%] bottom-[20%]", delay: "0.4s", size: "h-7 w-7" },
];

export default function AdminLoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [remember, setRemember] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!email.trim() || !password.trim()) {
      setError("Please enter both email and password.");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      await login(email.trim(), password);
      router.push("/");
      router.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
      setSubmitting(false);
    }
  }

  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-primary-900 px-4 py-12 sm:px-8">
      <div className="bg-noise pointer-events-none absolute inset-0 opacity-30" />
      <div
        className="pointer-events-none absolute -left-24 top-0 h-96 w-96 animate-float rounded-full bg-accent-600/25 blur-3xl"
        aria-hidden
      />
      <div
        className="pointer-events-none absolute -right-24 bottom-0 h-[26rem] w-[26rem] animate-float rounded-full bg-primary-400/20 blur-3xl"
        style={{ animationDelay: "1.2s" }}
        aria-hidden
      />
      <div
        className="pointer-events-none absolute left-1/2 top-1/2 h-[30rem] w-[30rem] -translate-x-1/2 -translate-y-1/2 animate-float rounded-full bg-primary-500/10 blur-3xl"
        style={{ animationDelay: "2s" }}
        aria-hidden
      />

      {floaters.map(({ icon: Icon, className, delay, size }, i) => (
        <div
          key={i}
          className={`pointer-events-none absolute hidden animate-float text-white/10 md:block ${className}`}
          style={{ animationDelay: delay, animationDuration: "6s" }}
          aria-hidden
        >
          <Icon className={size} />
        </div>
      ))}

      <div className="absolute right-4 top-4 sm:right-6 sm:top-6">
        <ThemeToggle className="text-primary-200 hover:bg-white/10 hover:text-white" />
      </div>

      <div className="relative w-full max-w-md">
        <div className="mb-8 flex flex-col items-center gap-4 animate-fade-up">
          <Logo variant="light" />
          <span className="inline-flex items-center gap-1.5 rounded-full bg-white/10 px-3.5 py-1.5 text-xs font-semibold text-primary-100 ring-1 ring-white/20">
            <Sparkles className="h-3.5 w-3.5 text-accent-400" /> Admin Control Center
          </span>
        </div>

        <div className="animate-scale-in stagger-1 rounded-3xl bg-white p-8 shadow-2xl shadow-black/30 ring-1 ring-white/10 transition-all duration-300 hover:-translate-y-1 hover:shadow-[0_30px_60px_-15px_rgba(0,0,0,0.4)] sm:p-10 dark:bg-slate-900">
          <h2 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Welcome back</h2>
          <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">Sign in to access the Zoiko Rooms admin panel.</p>

          <form onSubmit={handleSubmit} className="mt-7 space-y-4">
            <label className="block">
              <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Email address
              </span>
              <div className="group flex items-center gap-2 rounded-xl bg-slate-50 px-4 py-3 ring-1 ring-slate-200 transition-all duration-200 hover:ring-primary-300 focus-within:bg-white focus-within:ring-2 focus-within:ring-primary-400 focus-within:shadow-md focus-within:shadow-primary-900/5 dark:bg-slate-800 dark:ring-slate-700 dark:focus-within:bg-slate-800">
                <Mail className="h-4 w-4 shrink-0 text-slate-400 transition-colors group-focus-within:text-primary-600" />
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="admin@zoikorooms.com"
                  autoComplete="username"
                  className="w-full bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400 dark:text-slate-100"
                />
              </div>
            </label>

            <label className="block">
              <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Password
              </span>
              <div className="group flex items-center gap-2 rounded-xl bg-slate-50 px-4 py-3 ring-1 ring-slate-200 transition-all duration-200 hover:ring-primary-300 focus-within:bg-white focus-within:ring-2 focus-within:ring-primary-400 focus-within:shadow-md focus-within:shadow-primary-900/5 dark:bg-slate-800 dark:ring-slate-700 dark:focus-within:bg-slate-800">
                <Lock className="h-4 w-4 shrink-0 text-slate-400 transition-colors group-focus-within:text-primary-600" />
                <input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  autoComplete="current-password"
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

            <div className="flex items-center justify-between text-sm">
              <label className="flex items-center gap-2 text-slate-500 dark:text-slate-400">
                <input
                  type="checkbox"
                  checked={remember}
                  onChange={(e) => setRemember(e.target.checked)}
                  className="h-4 w-4 rounded accent-primary-700"
                />
                Remember me
              </label>
              <a href="#" className="font-medium text-primary-700 transition-colors hover:text-accent-600 dark:text-primary-300">
                Forgot password?
              </a>
            </div>

            {error && (
              <p className="animate-fade-in rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
                {error}
              </p>
            )}

            <Button
              type="submit"
              variant="primary"
              size="lg"
              fullWidth
              loading={submitting}
              className="hover:-translate-y-0.5 active:translate-y-0"
            >
              {submitting ? "Signing you in" : "Sign in to Dashboard"}
            </Button>
          </form>

          <div className="mt-6 flex items-center justify-center gap-2 border-t border-slate-100 pt-5 dark:border-slate-800">
            {perks.map((perk, i) => {
              const Icon = perk.icon;
              return (
                <span
                  key={perk.text}
                  className="animate-fade-up flex items-center gap-1.5 rounded-full bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-500 ring-1 ring-slate-100 transition-all duration-200 hover:-translate-y-0.5 hover:bg-primary-50 hover:text-primary-700 hover:ring-primary-100 dark:bg-slate-800 dark:text-slate-400 dark:ring-slate-700 dark:hover:bg-primary-500/10 dark:hover:text-primary-300"
                  style={{ animationDelay: `${0.2 + i * 0.1}s` }}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {perk.text}
                </span>
              );
            })}
          </div>
        </div>

        <p className="relative mt-6 text-center text-xs text-primary-300">
          Renting or hosting a room?{" "}
          <Link href="/account/login" className="font-semibold text-primary-100 hover:text-white">
            Sign in to your Zoiko account
          </Link>
        </p>

        <p className="relative mt-4 animate-fade-up stagger-4 text-center text-xs text-primary-300">
          © {new Date().getFullYear()} Zoiko Rooms. All rights reserved.
        </p>
      </div>
    </main>
  );
}
