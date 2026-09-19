import { clsx, type ClassValue } from "clsx";

export function cn(...inputs: ClassValue[]) {
  return clsx(inputs);
}

// A locale isn't derivable from a currency code, so each supported currency gets
// a sensible display locale here. Anything not listed falls back to "en-US" --
// this platform is multi-jurisdiction (England/GBP, India/INR, etc.), so there is
// no single "home" currency to assume when one isn't specified.
const CURRENCY_LOCALES: Record<string, string> = {
  INR: "en-IN",
  GBP: "en-GB",
  USD: "en-US",
  EUR: "en-IE",
  CAD: "en-CA",
  AUD: "en-AU",
  AED: "en-AE",
  SGD: "en-SG",
  NZD: "en-NZ",
};

/** `currency` is optional and defaults to "USD" as a neutral international
 *  fallback -- only used when a call site genuinely has no currency to pass
 *  (e.g. a cross-currency aggregate). Prefer always passing the record's own
 *  `currency` field when one exists; this platform is not India-only. */
export function formatCurrency(amount: number, currency: string = "USD") {
  const locale = CURRENCY_LOCALES[currency] ?? "en-US";
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(amount);
}

export function formatDate(date: string) {
  return new Date(date).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** Client-side-only preview helper for the shortening/extension request
 * modals -- mirrors the backend's crud/occupancy.py:_add_months calendar-month
 * add closely enough for a "current vs proposed" display; the server remains
 * the source of truth for the actual committed date. */
export function addMonths(isoDate: string, months: number): string {
  const d = new Date(isoDate + "T00:00:00");
  d.setMonth(d.getMonth() + months);
  return d.toISOString().slice(0, 10);
}

/** Today as a "YYYY-MM-DD" string in the browser's local timezone -- matches
 *  what `<input type="date">` both displays and compares against, so this is
 *  the right "today" for a `min` attribute or a client-side past-date check
 *  (as opposed to UTC, which could be a day off from the user's actual today). */
export function todayIsoDate(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Listing/property photo URLs are a mix of genuine external URLs (e.g. the
 *  seeded Unsplash photos) and our own /uploads storage. Only the latter needs
 *  an origin -- and it's always resolved against this app's own
 *  NEXT_PUBLIC_API_URL, never an origin baked into the stored value itself.
 *  That also self-heals any URL saved with a stale/mismatched origin (the
 *  backend's PUBLIC_API_URL is a separate env var that can drift from this
 *  one), without needing to rewrite anything in the database. */
export function resolveImageUrl(url: string | undefined | null): string | undefined {
  if (!url) return url ?? undefined;
  const uploadsPath = url.match(/\/uploads\/[^/?#]+/)?.[0];
  return uploadsPath ? `${API_URL}${uploadsPath}` : url;
}
