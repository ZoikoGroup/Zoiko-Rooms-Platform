"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { BedDouble, Building2, Eye, EyeOff, Lock, Mail, Phone, ShieldCheck, Sparkles, User } from "lucide-react";
import { Logo } from "@/components/ui/Logo";
import { Button } from "@/components/ui/Button";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { userLogin, userRegister } from "@/lib/user-auth";
import { ApiError } from "@/lib/api-client";

const floaters = [
  { icon: BedDouble, className: "left-[8%] top-[18%]", delay: "0s", size: "h-10 w-10" },
  { icon: Building2, className: "right-[10%] top-[14%]", delay: "0.8s", size: "h-8 w-8" },
  { icon: ShieldCheck, className: "left-[12%] bottom-[16%]", delay: "1.6s", size: "h-9 w-9" },
];

export default function UserRegisterPage() {
  const router = useRouter();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!fullName.trim() || !email.trim() || !password.trim()) {
      setError("Please fill in your name, email and password.");
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      await userRegister(fullName.trim(), email.trim(), phone.trim(), password);
      // A user account is usable immediately -- unlike admins, it needs no approval,
      // so sign straight in and drop the user on their dashboard.
      await userLogin(email.trim(), password);
      router.push("/account");
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
          <Logo variant="light" href="/account/login" />
          <span className="inline-flex items-center gap-1.5 rounded-full bg-white/10 px-3.5 py-1.5 text-xs font-semibold text-primary-100 ring-1 ring-white/20">
            <Sparkles className="h-3.5 w-3.5 text-accent-400" /> Renter &amp; Host Account
          </span>
        </div>

        <div className="animate-scale-in stagger-1 rounded-3xl bg-white p-8 shadow-2xl shadow-black/30 ring-1 ring-white/10 sm:p-10 dark:bg-slate-900">
          <h2 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Create your account</h2>
          <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
            One account lets you rent a room and host your own. You will verify your identity next.
          </p>

          <form onSubmit={handleSubmit} className="mt-7 space-y-4">
            <label className="block">
              <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Full name
              </span>
              <div className="group flex items-center gap-2 rounded-xl bg-slate-50 px-4 py-3 ring-1 ring-slate-200 transition-all duration-200 hover:ring-primary-300 focus-within:bg-white focus-within:ring-2 focus-within:ring-primary-400 dark:bg-slate-800 dark:ring-slate-700 dark:focus-within:bg-slate-800">
                <User className="h-4 w-4 shrink-0 text-slate-400 transition-colors group-focus-within:text-primary-600" />
                <input
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder="Jane Doe"
                  autoComplete="name"
                  className="w-full bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400 dark:text-slate-100"
                />
              </div>
            </label>

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

            <label className="block">
              <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Phone (optional)
              </span>
              <div className="group flex items-center gap-2 rounded-xl bg-slate-50 px-4 py-3 ring-1 ring-slate-200 transition-all duration-200 hover:ring-primary-300 focus-within:bg-white focus-within:ring-2 focus-within:ring-primary-400 dark:bg-slate-800 dark:ring-slate-700 dark:focus-within:bg-slate-800">
                <Phone className="h-4 w-4 shrink-0 text-slate-400 transition-colors group-focus-within:text-primary-600" />
                <input
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="+91 98200 11223"
                  autoComplete="tel"
                  className="w-full bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400 dark:text-slate-100"
                />
              </div>
            </label>

            <label className="block">
              <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Password
              </span>
              <div className="group flex items-center gap-2 rounded-xl bg-slate-50 px-4 py-3 ring-1 ring-slate-200 transition-all duration-200 hover:ring-primary-300 focus-within:bg-white focus-within:ring-2 focus-within:ring-primary-400 dark:bg-slate-800 dark:ring-slate-700 dark:focus-within:bg-slate-800">
                <Lock className="h-4 w-4 shrink-0 text-slate-400 transition-colors group-focus-within:text-primary-600" />
                <input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
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

            {error && (
              <p className="animate-fade-in rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
                {error}
              </p>
            )}

            <Button type="submit" variant="primary" size="lg" fullWidth loading={submitting}>
              {submitting ? "Creating your account" : "Create account"}
            </Button>
          </form>

          <p className="mt-5 text-center text-sm text-slate-500 dark:text-slate-400">
            Already have an account?{" "}
            <Link href="/account/login" className="font-semibold text-primary-700 hover:text-accent-600 dark:text-primary-300">
              Sign in
            </Link>
          </p>
        </div>
      </div>
    </main>
  );
}
