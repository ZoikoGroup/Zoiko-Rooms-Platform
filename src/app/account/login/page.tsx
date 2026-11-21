// "use client";

// import { useState, type FormEvent } from "react";
// import Link from "next/link";
// import { useRouter } from "next/navigation";
// import { applyUserPreferences } from "@/lib/display-preferences";
// import { getUserPreferences } from "@/lib/user-api";
// import { BedDouble, Building2, Eye, EyeOff, Lock, Mail, ShieldCheck, Sparkles } from "lucide-react";
// import { Logo } from "@/components/ui/Logo";
// import { Button } from "@/components/ui/Button";
// import { ThemeToggle } from "@/components/ui/ThemeToggle";
// import { userLogin } from "@/lib/user-auth";
// import { ApiError } from "@/lib/api-client";

// const perks = [
//   { icon: BedDouble, text: "Rent a room" },
//   { icon: Building2, text: "Host a room" },
//   { icon: ShieldCheck, text: "Verified stays" },
// ];

// const floaters = [
//   { icon: BedDouble, className: "left-[8%] top-[18%]", delay: "0s", size: "h-10 w-10" },
//   { icon: Building2, className: "right-[10%] top-[14%]", delay: "0.8s", size: "h-8 w-8" },
//   { icon: ShieldCheck, className: "left-[12%] bottom-[16%]", delay: "1.6s", size: "h-9 w-9" },
// ];

// /** Only same-site account paths are followed after login (no open redirect). */
// function safeNextPath(next: string | null): string {
//   return next && next.startsWith("/account/") && !next.startsWith("//") && !next.includes("://") ? next : "/account";
// }

// export default function UserLoginPage() {
//   const router = useRouter();
//   const [email, setEmail] = useState("");
//   const [password, setPassword] = useState("");
//   const [showPassword, setShowPassword] = useState(false);
//   const [submitting, setSubmitting] = useState(false);
//   const [error, setError] = useState("");

//   async function handleSubmit(e: FormEvent) {
//     e.preventDefault();
//     if (!email.trim() || !password.trim()) {
//       setError("Please enter both email and password.");
//       return;
//     }
//     setError("");
//     setSubmitting(true);
//     try {
//       await userLogin(email.trim(), password);
//       const next = new URLSearchParams(window.location.search).get("next");
//       let destination = safeNextPath(next);
//       if (!next) {
//         // Settings > Account: "Open this workspace after sign-in".
//         try {
//           const prefs = await getUserPreferences();
//           applyUserPreferences(prefs);
//           if (prefs.defaultWorkspace === "host") destination = "/account/host";
//         } catch {
//           /* fall back to the dashboard */
//         }
//       }
//       router.push(destination);
//       router.refresh();
//     } catch (err) {
//       setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
//       setSubmitting(false);
//     }
//   }

//   return (
//     <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-primary-900 px-4 py-12 sm:px-8">
//       <div className="bg-noise pointer-events-none absolute inset-0 opacity-30" />
//       <div
//         className="pointer-events-none absolute -left-24 top-0 h-96 w-96 animate-float rounded-full bg-accent-600/25 blur-3xl"
//         aria-hidden
//       />
//       <div
//         className="pointer-events-none absolute -right-24 bottom-0 h-[26rem] w-[26rem] animate-float rounded-full bg-primary-400/20 blur-3xl"
//         style={{ animationDelay: "1.2s" }}
//         aria-hidden
//       />

//       {floaters.map(({ icon: Icon, className, delay, size }, i) => (
//         <div
//           key={i}
//           className={`pointer-events-none absolute hidden animate-float text-white/10 md:block ${className}`}
//           style={{ animationDelay: delay, animationDuration: "6s" }}
//           aria-hidden
//         >
//           <Icon className={size} />
//         </div>
//       ))}

//       <div className="absolute right-4 top-4 sm:right-6 sm:top-6">
//         <ThemeToggle className="text-primary-200 hover:bg-white/10 hover:text-white" />
//       </div>

//       <div className="relative w-full max-w-md">
//         <div className="mb-8 flex flex-col items-center gap-4 animate-fade-up">
//           <Logo variant="light" href="/account/login" />
//           <span className="inline-flex items-center gap-1.5 rounded-full bg-white/10 px-3.5 py-1.5 text-xs font-semibold text-primary-100 ring-1 ring-white/20">
//             <Sparkles className="h-3.5 w-3.5 text-accent-400" /> Renter &amp; Host Account
//           </span>
//         </div>

//         <div className="animate-scale-in stagger-1 rounded-3xl bg-white p-6 shadow-2xl shadow-black/30 ring-1 ring-white/10 transition-all duration-300 hover:-translate-y-1 sm:p-10 dark:bg-slate-900">
//           <h2 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Welcome back</h2>
//           <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
//             Sign in to rent a room or manage the rooms you host.
//           </p>

//           <form onSubmit={handleSubmit} className="mt-7 space-y-4">
//             <label className="block">
//               <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
//                 Email address
//               </span>
//               <div className="group flex items-center gap-2 rounded-xl bg-slate-50 px-4 py-3 ring-1 ring-slate-200 transition-all duration-200 hover:ring-primary-300 focus-within:bg-white focus-within:ring-2 focus-within:ring-primary-400 dark:bg-slate-800 dark:ring-slate-700 dark:focus-within:bg-slate-800">
//                 <Mail className="h-4 w-4 shrink-0 text-slate-400 transition-colors group-focus-within:text-primary-600" />
//                 <input
//                   type="email"
//                   value={email}
//                   onChange={(e) => setEmail(e.target.value)}
//                   placeholder="you@example.com"
//                   autoComplete="username"
//                   className="w-full bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400 dark:text-slate-100"
//                 />
//               </div>
//             </label>

//             <label className="block">
//               <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
//                 Password
//               </span>
//               <div className="group flex items-center gap-2 rounded-xl bg-slate-50 px-4 py-3 ring-1 ring-slate-200 transition-all duration-200 hover:ring-primary-300 focus-within:bg-white focus-within:ring-2 focus-within:ring-primary-400 dark:bg-slate-800 dark:ring-slate-700 dark:focus-within:bg-slate-800">
//                 <Lock className="h-4 w-4 shrink-0 text-slate-400 transition-colors group-focus-within:text-primary-600" />
//                 <input
//                   type={showPassword ? "text" : "password"}
//                   value={password}
//                   onChange={(e) => setPassword(e.target.value)}
//                   placeholder="••••••••"
//                   autoComplete="current-password"
//                   className="w-full bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400 dark:text-slate-100"
//                 />
//                 <button
//                   type="button"
//                   onClick={() => setShowPassword((s) => !s)}
//                   className="shrink-0 text-slate-400 transition-transform duration-200 hover:scale-110 hover:text-primary-600"
//                   aria-label="Toggle password visibility"
//                 >
//                   {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
//                 </button>
//               </div>
//             </label>

//             <div className="flex justify-end">
//               <Link
//                 href="/account/forgot-password"
//                 className="text-sm font-medium text-primary-700 hover:text-accent-600 dark:text-primary-300"
//               >
//                 Forgot password?
//               </Link>
//             </div>

//             {error && (
//               <p className="animate-fade-in rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
//                 {error}
//               </p>
//             )}

//             <Button
//               type="submit"
//               variant="primary"
//               size="lg"
//               fullWidth
//               loading={submitting}
//               className="hover:-translate-y-0.5 active:translate-y-0"
//             >
//               {submitting ? "Signing you in" : "Sign in"}
//             </Button>
//           </form>

//           <p className="mt-5 text-center text-sm text-slate-500 dark:text-slate-400">
//             New to Zoiko Rooms?{" "}
//             <Link href="/account/register" className="font-semibold text-primary-700 hover:text-accent-600 dark:text-primary-300">
//               Create an account
//             </Link>
//           </p>

//           <div className="mt-6 flex flex-wrap items-center justify-center gap-2 border-t border-slate-100 pt-5 dark:border-slate-800">
//             {perks.map((perk, i) => {
//               const Icon = perk.icon;
//               return (
//                 <span
//                   key={perk.text}
//                   className="animate-fade-up flex items-center gap-1.5 rounded-full bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-500 ring-1 ring-slate-100 dark:bg-slate-800 dark:text-slate-400 dark:ring-slate-700"
//                   style={{ animationDelay: `${0.2 + i * 0.1}s` }}
//                 >
//                   <Icon className="h-3.5 w-3.5" />
//                   {perk.text}
//                 </span>
//               );
//             })}
//           </div>
//         </div>

//         <p className="relative mt-6 text-center text-xs text-primary-300">
//           Zoiko staff?{" "}
//           <Link href="/login" className="font-semibold text-primary-100 hover:text-white">
//             Go to the admin sign-in
//           </Link>
//         </p>
//       </div>
//     </main>
//   );
// }

"use client";

import {
  useState,
  type FormEvent,
  type InputHTMLAttributes,
  type ReactNode,
} from "react";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Eye, EyeOff } from "lucide-react";
import { IBM_Plex_Sans, Manrope, Poppins } from "next/font/google";
import { applyUserPreferences } from "@/lib/display-preferences";
import { getUserPreferences } from "@/lib/user-api";
import { userLogin } from "@/lib/user-auth";
import { ApiError } from "@/lib/api-client";

const ibmPlexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-ibm-plex-sans",
  display: "swap",
});

const manrope = Manrope({
  subsets: ["latin"],
  weight: ["800"],
  variable: "--font-manrope",
  display: "swap",
});

const poppins = Poppins({
  subsets: ["latin"],
  weight: ["500"],
  variable: "--font-poppins",
  display: "swap",
});

type AuthTabItem = { label: string; href: string; isActive?: boolean };
type LegalLinkItem = { label: string; href: string };
type SocialProviderId = "microsoft" | "google";
type SocialProviderItem = {
  id: SocialProviderId;
  label: string;
  name: "Microsoft" | "Google";
};

/** Only same-site account paths are followed after login (no open redirect). */
function safeNextPath(next: string | null): string {
  return next &&
    next.startsWith("/account/") &&
    !next.startsWith("//") &&
    !next.includes("://")
    ? next
    : "/account";
}

const AUTH_TABS = [
  { label: "Login", href: "/account/login", isActive: true },
  { label: "Register", href: "/account/register" },
  { label: "Reset Password", href: "/account/forgot-password" },
] as const satisfies readonly AuthTabItem[];

const LEGAL_LINKS = [
  { label: "Privacy", href: "https://zoikorooms.com/legal/privacy-notice" },
  { label: "Terms", href: "https://zoikorooms.com/legal/terms-of-use" },
  { label: "Accessibility", href: "https://zoikorooms.com/list-a-room" },
  { label: "Help", href: "https://zoikorooms.com/resources/help-center" },
] as const satisfies readonly LegalLinkItem[];

const SOCIAL_PROVIDERS = [
  { id: "microsoft", label: "Continue with Microsoft", name: "Microsoft" },
  { id: "google", label: "Continue with Google", name: "Google" },
] as const satisfies readonly SocialProviderItem[];

function MicrosoftIcon() {
  return (
    <svg viewBox="0 0 24 24" className="size-7 shrink-0" aria-hidden="true">
      <rect x="2" y="2" width="9.5" height="9.5" fill="#F25022" />
      <rect x="12.5" y="2" width="9.5" height="9.5" fill="#7FBA00" />
      <rect x="2" y="12.5" width="9.5" height="9.5" fill="#00A4EF" />
      <rect x="12.5" y="12.5" width="9.5" height="9.5" fill="#FFB900" />
    </svg>
  );
}

function GoogleIcon() {
  return (
    <svg viewBox="0 0 24 24" className="size-7 shrink-0" aria-hidden="true">
      <path
        fill="#EA4335"
        d="M12 10.2v3.9h5.5c-.2 1.3-1.6 3.9-5.5 3.9-3.3 0-6-2.7-6-6s2.7-6 6-6c1.9 0 3.1.8 3.8 1.5l2.6-2.5C16.8 3.5 14.6 2.5 12 2.5 6.8 2.5 2.5 6.8 2.5 12S6.8 21.5 12 21.5c5.5 0 9.1-3.9 9.1-9.3 0-.6-.1-1.1-.2-2H12z"
      />
      <path
        fill="#4285F4"
        d="M21.1 12.2c0-.6-.1-1.1-.2-1.6H12v3.9h5.5c-.2 1.2-.9 2.2-1.9 2.9l2.9 2.3c1.7-1.6 2.6-3.9 2.6-7.5z"
      />
      <path
        fill="#FBBC05"
        d="M6.4 14.3c-.2-.6-.3-1.2-.3-1.8s.1-1.2.3-1.8L3.4 8.4C2.8 9.6 2.5 10.8 2.5 12s.3 2.4.9 3.6l3-2.3z"
      />
      <path
        fill="#34A853"
        d="M12 21.5c2.6 0 4.8-.9 6.4-2.4l-2.9-2.3c-.8.5-1.9.9-3.5.9-2.7 0-5-1.8-5.8-4.3l-3 2.3C4.8 19.3 8.1 21.5 12 21.5z"
      />
    </svg>
  );
}

function AuthAside() {
  return (
    <aside className="relative hidden overflow-hidden bg-[#f2f8fc] xl:block">
      {/* Image pinned to bottom, full width; extra height is cropped from the TOP */}
      <Image
        src="/1.png"
        alt=""
        width={1600}
        height={1200}
        priority
        sizes="52vw"
        className="absolute inset-x-0 bottom-0 h-auto w-full"
      />

      {/* Bluish gradient: top only */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 z-[5] h-[38%] bg-gradient-to-b from-[#eaf3fb] via-[#eaf3fb]/80 to-transparent"
      />

      <div className="relative z-10 xl:pl-[86px] xl:pr-[129.67px] xl:pt-[clamp(40px,10vh,140px)]">
        <Link
          href="/"
          aria-label="Zoiko Rooms home"
          className="block h-[46.67px] pt-[3px]"
        >
          <Image
            src="/logo.webp"
            alt="Zoiko Rooms"
            width={540}
            height={83}
            priority
            className="h-auto w-[268.22px] max-w-full"
          />
        </Link>

        <h2 className="mt-[27.41px] font-[family-name:var(--font-manrope)] text-[36px] font-extrabold leading-[38px] tracking-[-1.87px] text-[#0e2f73] sm:text-[44px] sm:leading-[46px] xl:text-[53.3px] xl:leading-[55.467px]">
          <span className="block">Find. Book. Stay.</span>
          <span className="block text-[#d80b0b]">With Confidence</span>
        </h2>

        <p className="mt-[14.8px] text-[17.5px] leading-[25.375px] tracking-[0.0875px] text-[#3a4558]">
          Discover the perfect room, anywhere in the world.
        </p>
      </div>
    </aside>
  );
}

function AuthTabs({ tabs }: { tabs: readonly AuthTabItem[] }) {
  return (
    <nav aria-label="Account" className="px-[18px]">
      <ul className="grid grid-cols-3">
        {tabs.map((tab, index) => {
          const isActive = Boolean(tab.isActive);

          return (
            <li key={tab.label} className="flex">
              <Link
                href={tab.href}
                aria-current={isActive ? "page" : undefined}
                className={[
                  "relative flex min-h-[41.67px] w-full items-center justify-center pb-[14.58px] pt-[16.25px] text-center text-[15px] leading-5 transition-colors",
                  isActive
                    ? "font-semibold text-[#d80b0b] after:absolute after:inset-x-0 after:-bottom-[1.66px] after:h-[3.33px] after:rounded-[2px] after:bg-[#d80b0b]"
                    : "font-medium text-[#1f2937] hover:text-[#d80b0b]",
                  index === tabs.length - 1
                    ? "before:absolute before:left-0 before:top-1/2 before:h-[18.33px] before:w-[0.83px] before:-translate-y-1/2 before:bg-[#dde3ea]"
                    : "",
                ].join(" ")}
              >
                {tab.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

function AuthFooter() {
  return (
    <footer className="flex flex-wrap items-center gap-x-[36.28px] gap-y-1 px-[5px] pt-[15.7px] sm:flex-nowrap">
      <nav aria-label="Legal" className="shrink-0">
        <ul className="flex items-center gap-[2px]">
          {LEGAL_LINKS.map((link, index) => (
            <li key={link.label} className="flex items-center gap-[2px]">
              {index > 0 && (
                <span
                  aria-hidden
                  className="px-2 text-[12.5px] leading-4 text-[#9ca3af]"
                >
                  •
                </span>
              )}
              <Link
                href={link.href}
                className="px-[1.667px] py-[3.333px] text-[12.5px] leading-4 text-[#4b5563] transition-colors hover:text-[#d80b0b]"
              >
                {link.label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>

      <p className="shrink-0 text-[12.5px] leading-4 text-[#4b5563]">
        © 2026 Zoiko Tech Inc.
      </p>
    </footer>
  );
}

function SocialButton({
  label,
  icon,
  onClick,
}: {
  label: string;
  icon: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex h-[50px] w-full items-center gap-5 rounded-[6px] border border-[#d9dfea] bg-white px-[18px] text-left font-[family-name:var(--font-poppins)] text-[14px] font-medium leading-[21px] text-[#14213d] transition-colors hover:bg-[#f8fafc] focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[#d80b0b]/15"
    >
      {icon}
      <span className="flex-1">{label}</span>
      <span aria-hidden className="text-[17px] leading-[26px] text-[#5e6b85]">
        ›
      </span>
    </button>
  );
}

type TextFieldProps = Omit<InputHTMLAttributes<HTMLInputElement>, "id"> & {
  id: string;
  label: string;
  trailing?: ReactNode;
};

function TextField({
  id,
  label,
  trailing,
  className = "",
  ...inputProps
}: TextFieldProps) {
  return (
    <div className="flex flex-col gap-[6.67px]">
      <label
        htmlFor={id}
        className="text-[14.2px] font-medium leading-[18px] text-[#14213d]"
      >
        {label}
      </label>
      <div className="relative">
        <input
          id={id}
          {...inputProps}
          className={`block h-[43.33px] w-full rounded-[6px] border border-[#dde3ea] bg-white py-[12.33px] pl-[14.83px] text-[13.3px] leading-[17px] text-[#14213d] placeholder:text-[#8a94a6] focus-visible:border-[#d80b0b] focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[#d80b0b]/15 ${
            trailing ? "pr-[48.17px]" : "pr-[14.83px]"
          } ${className}`}
        />
        {trailing}
      </div>
    </div>
  );
}

function LoginForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!email.trim() || !password.trim()) {
      setError("Please enter both email and password.");
      return;
    }
    setError("");
    setNotice("");
    setSubmitting(true);
    try {
      // NOTE: `remember` is UI only until userLogin supports it.
      await userLogin(email.trim(), password);
      const next = new URLSearchParams(window.location.search).get("next");
      let destination = safeNextPath(next);
      if (!next) {
        // Settings > Account: "Open this workspace after sign-in".
        try {
          const prefs = await getUserPreferences();
          applyUserPreferences(prefs);
          if (prefs.defaultWorkspace === "host") destination = "/account/host";
        } catch {
          /* fall back to the dashboard */
        }
      }
      router.push(destination);
      router.refresh();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Something went wrong. Please try again.",
      );
      setSubmitting(false);
    }
  }

  // Placeholder: the backend has no OAuth endpoints yet.
  function handleSocial(name: "Microsoft" | "Google") {
    setError("");
    setNotice(
      `Sign in with ${name} is not available yet. Please use your email and password.`,
    );
  }

  return (
    <>
      <h1 className="font-[family-name:var(--font-manrope)] text-[34px] font-extrabold leading-[45px] tracking-[-1.25px] text-[#0e2148] sm:text-[41.7px]">
        Welcome back!
      </h1>
      <p className="mt-[6.67px] text-[15.8px] leading-[21px] text-[#6b7a90]">
        Sign in to your Zoiko Room account.
      </p>

      <form
        onSubmit={handleSubmit}
        noValidate
        className="mt-[21.2px] flex flex-col gap-[11.7px]"
      >
        <TextField
          id="login-email"
          name="email"
          type="email"
          label="Email address"
          placeholder="name@company.com"
          autoComplete="username"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />

        <TextField
          id="login-password"
          name="password"
          type={showPassword ? "text" : "password"}
          label="Password"
          placeholder="Enter your password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          trailing={
            <button
              type="button"
              onClick={() => setShowPassword((visible) => !visible)}
              aria-label={showPassword ? "Hide password" : "Show password"}
              aria-pressed={showPassword}
              className="absolute right-[6.67px] top-1/2 flex size-[35px] -translate-y-1/2 items-center justify-center rounded-[8px] text-[#6b7a90] transition-colors hover:bg-[#f3f6f9] focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[#d80b0b]/15"
            >
              {showPassword ? (
                <EyeOff size={20} strokeWidth={1.75} aria-hidden />
              ) : (
                <Eye size={20} strokeWidth={1.75} aria-hidden />
              )}
            </button>
          }
        />

        <div className="flex flex-wrap items-center justify-between gap-x-[76px] gap-y-3 pb-[6.63px] pt-[3.3px]">
          <label
            htmlFor="login-remember"
            className="flex cursor-pointer items-center gap-2.5"
          >
            <span className="relative block size-5 shrink-0">
              <input
                id="login-remember"
                name="remember"
                type="checkbox"
                checked={remember}
                onChange={(event) => setRemember(event.target.checked)}
                className="peer sr-only"
              />
              <span
                aria-hidden
                className="absolute inset-0 rounded-[4px] border-2 border-[#c5cdd9] bg-white transition-colors peer-checked:border-[#d80b0b] peer-checked:bg-[#d80b0b] peer-focus-visible:ring-[3px] peer-focus-visible:ring-[#d80b0b]/25"
              />
              <span
                aria-hidden
                className="absolute inset-0 flex items-center justify-center pb-[2.4px] opacity-0 peer-checked:opacity-100"
              >
                <span className="block h-[5px] w-[9.17px] -rotate-45 border-b-2 border-l-2 border-white" />
              </span>
            </span>
            <span className="text-[13.3px] leading-[17px] text-[#14213d]">
              Remember me for 30 days
            </span>
          </label>

          <Link
            href="/account/forgot-password"
            className="text-[13.3px] leading-[17px] text-[#d80b0b] transition-opacity hover:opacity-80"
          >
            Forgot password?
          </Link>
        </div>

        {error && (
          <p
            role="alert"
            className="rounded-[6px] bg-[#fdecec] px-3 py-2 text-[13px] font-medium leading-[18px] text-[#b00d0d] ring-1 ring-[#f6caca]"
          >
            {error}
          </p>
        )}
        {notice && (
          <p
            role="status"
            className="rounded-[6px] bg-[#eef3fb] px-3 py-2 text-[13px] font-medium leading-[18px] text-[#33415c] ring-1 ring-[#d9e2f2]"
          >
            {notice}
          </p>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="flex h-[46.67px] w-full items-center justify-center rounded-[6px] bg-[#d80b0b] px-[5px] py-[0.833px] text-[16.7px] font-semibold leading-[22px] text-white shadow-[0_8px_18px_-10px_rgba(10,110,189,0.7)] transition-colors hover:bg-[#c20a0a] focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[#d80b0b]/30 disabled:cursor-not-allowed disabled:opacity-70"
        >
          {submitting ? "Signing you in…" : "Log In"}
        </button>
      </form>

      <div className="mt-[16.63px] pb-1 pt-5">
        <div className="flex items-center gap-2.5">
          <span aria-hidden className="h-[0.83px] flex-1 bg-[#dde3ea]" />
          <span className="whitespace-nowrap text-[12.5px] leading-4 text-[#6b7280]">
            or continue with
          </span>
          <span aria-hidden className="h-[0.83px] flex-1 bg-[#dde3ea]" />
        </div>
      </div>

      <div className="mt-[17px] flex flex-col gap-2.5">
        {SOCIAL_PROVIDERS.map((provider) => (
          <SocialButton
            key={provider.id}
            label={provider.label}
            icon={
              provider.id === "microsoft" ? <MicrosoftIcon /> : <GoogleIcon />
            }
            onClick={() => handleSocial(provider.name)}
          />
        ))}
      </div>

      <p className="mt-2.5 flex h-[31px] items-end justify-center gap-[6.67px] text-[15px] leading-5 text-[#14213d]">
        <span>New to Zoiko Room?</span>
        <Link
          href="/account/register"
          className="font-semibold text-[#d80b0b] transition-opacity hover:opacity-80"
        >
          Register
        </Link>
      </p>
    </>
  );
}

function AuthCard({ children }: { children: ReactNode }) {
  return (
    <div className="flex w-full max-w-[473.33px] flex-col rounded-3xl bg-white px-[18.33px] pb-[18.33px] pt-[5px] shadow-[0_1px_2px_0_rgba(15,30,60,0.04),0_24px_60px_-30px_rgba(15,30,60,0.18)] xl:min-h-[806.67px]">
      <AuthTabs tabs={AUTH_TABS} />

      <div className="flex flex-1 pb-4 pt-1 xl:pb-[65.54px]">
        <div className="flex-1 rounded-[18px] bg-white px-6 pt-[25px] shadow-[0_0_0_1px_#f0f3f7,0_10px_30px_-18px_rgba(15,30,60,0.14)] sm:px-[33.25px]">
          {children}
        </div>
      </div>

      <AuthFooter />
    </div>
  );
}

export default function LoginPage() {
  return (
    <main
      className={`${ibmPlexSans.variable} ${manrope.variable} ${poppins.variable} grid min-h-screen grid-cols-1 bg-[#f3f6f9] font-[family-name:var(--font-ibm-plex-sans)] xl:min-h-[900px] xl:grid-cols-[52.01%_minmax(0,1fr)]`}
    >
      <AuthAside />
      <section className="flex items-center justify-center px-4 py-10 sm:px-8 xl:items-stretch xl:py-[46.67px] xl:pl-[clamp(24px,5.81vw,83.67px)] xl:pr-[clamp(24px,9.31vw,134px)]">
        <AuthCard>
          <LoginForm />
        </AuthCard>
      </section>
    </main>
  );
}
