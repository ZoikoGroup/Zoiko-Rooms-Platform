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
import { PhoneInputWithCountry } from "@/components/ui/PhoneInputWithCountry";
import { splitPhoneNumber, validatePhoneWithCountry } from "@/lib/phone-validation";
import { userLogin, userRegister } from "@/lib/user-auth";
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

const AUTH_TABS = [
  { label: "Login", href: "/account/login" },
  { label: "Register", href: "/account/register", isActive: true },
  { label: "Reset Password", href: "/account/forgot-password" },
] as const satisfies readonly AuthTabItem[];

const LEGAL_LINKS = [
  { label: "Privacy", href: "https://zoikorooms.com/legal/privacy-notice" },
  { label: "Terms", href: "https://zoikorooms.com/legal/terms-of-use" },
  { label: "Accessibility", href: "https://zoikorooms.com/list-a-room" },
  { label: "Help", href: "https://zoikorooms.com/resources/help-center" },
] as const satisfies readonly LegalLinkItem[];

function AuthAside() {
  return (
    <aside className="relative hidden overflow-hidden bg-[#f2f8fc] xl:block">
      <Image
        src="/1.png"
        alt=""
        width={1600}
        height={1200}
        priority
        sizes="52vw"
        className="absolute inset-x-0 bottom-0 h-auto w-full"
      />

      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 z-[5] h-[38%] bg-gradient-to-b from-[#eaf3fb] via-[#eaf3fb]/80 to-transparent"
      />

      <div className="relative z-10 xl:pl-[86px] xl:pr-[129.67px] xl:pt-[clamp(40px,10vh,140px)]">
        <Link
          href="https://zoikorooms.com/"
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

function RegisterForm() {
  const router = useRouter();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("+91");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [phoneError, setPhoneError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!fullName.trim() || !email.trim() || !password.trim()) {
      setError("Please fill in your name, email and password.");
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
      setError("Please enter a valid email address.");
      return;
    }
    const { countryCode, digits } = splitPhoneNumber(phone);
    const phoneValidationMsg = validatePhoneWithCountry(countryCode, digits);
    if (phoneValidationMsg) {
      setPhoneError(phoneValidationMsg);
      setError(phoneValidationMsg);
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    setError("");
    setPhoneError("");
    setSubmitting(true);
    try {
      await userRegister(fullName.trim(), email.trim(), phone.trim(), password);
      // A user account is usable immediately -- unlike admins, it needs no approval,
      // so sign straight in and drop the user on their dashboard.
      await userLogin(email.trim(), password);
      router.push("/account");
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

  return (
    <>
      <h1 className="font-[family-name:var(--font-manrope)] text-[34px] font-extrabold leading-[45px] tracking-[-1.25px] text-[#0e2148] sm:text-[36px]">
        Create your account
      </h1>
      <p className="mt-[6.67px] text-[15.8px] leading-[21px] text-[#6b7a90]">
        One account lets you rent a room and host your own. You will verify your
        identity next.
      </p>

      <form
        onSubmit={handleSubmit}
        noValidate
        className="mt-[21.2px] flex flex-col gap-[11.7px]"
      >
        <TextField
          id="register-full-name"
          name="fullName"
          type="text"
          label="Full name"
          placeholder="Jane Doe"
          autoComplete="name"
          value={fullName}
          onChange={(event) => setFullName(event.target.value)}
        />

        <TextField
          id="register-email"
          name="email"
          type="email"
          label="Email address"
          placeholder="you@example.com"
          autoComplete="username"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />

        <div className="flex flex-col gap-[6.67px]">
          
          <PhoneInputWithCountry className="text-[14.2px] font-medium leading-[18px] text-[#14213d]"
            value={phone}
            onChange={(fullPhone) => {
              setPhone(fullPhone);
              if (phoneError) setPhoneError("");
            }}
            error={phoneError}
            onErrorChange={setPhoneError}
          />
        </div>

        <TextField
          id="register-password"
          name="password"
          type={showPassword ? "text" : "password"}
          label="Password"
          placeholder="At least 8 characters"
          autoComplete="new-password"
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

        {error && (
          <p
            role="alert"
            className="mt-1 rounded-[6px] bg-[#fdecec] px-3 py-2 text-[13px] font-medium leading-[18px] text-[#b00d0d] ring-1 ring-[#f6caca]"
          >
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="mt-2 flex h-[46.67px] w-full items-center justify-center rounded-[6px] bg-[#d80b0b] px-[5px] py-[0.833px] text-[16.7px] font-semibold leading-[22px] text-white shadow-[0_8px_18px_-10px_rgba(10,110,189,0.7)] transition-colors hover:bg-[#c20a0a] focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[#d80b0b]/30 disabled:cursor-not-allowed disabled:opacity-70"
        >
          {submitting ? "Creating your account…" : "Create account"}
        </button>
      </form>

      <p className="mt-4 flex h-[31px] items-end justify-center gap-[6.67px] text-[15px] leading-5 text-[#14213d]">
        <span>Already have an account?</span>
        <Link
          href="/account/login"
          className="font-semibold text-[#d80b0b] transition-opacity hover:opacity-80"
        >
          Sign in
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

export default function UserRegisterPage() {
  return (
    <main
      className={`${ibmPlexSans.variable} ${manrope.variable} ${poppins.variable} grid min-h-screen grid-cols-1 bg-[#f3f6f9] font-[family-name:var(--font-ibm-plex-sans)] xl:min-h-[900px] xl:grid-cols-[52.01%_minmax(0,1fr)]`}
    >
      <AuthAside />
      <section className="flex items-center justify-center px-4 py-10 sm:px-8 xl:items-stretch xl:py-[46.67px] xl:pl-[clamp(24px,5.81vw,83.67px)] xl:pr-[clamp(24px,9.31vw,134px)]">
        <AuthCard>
          <RegisterForm />
        </AuthCard>
      </section>
    </main>
  );
}