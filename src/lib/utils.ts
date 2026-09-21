import { clsx, type ClassValue } from "clsx";

export function cn(...inputs: ClassValue[]) {
  return clsx(inputs);
}

// A locale isn't derivable from a currency code, so each supported currency gets
// a sensible display locale here. Anything not listed falls back to "en-IN" --
// matching the app's original single-currency behavior exactly, so every existing
// single-argument formatCurrency(amount) call keeps rendering identically.
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

/** `currency` is optional and defaults to "INR" -- the app's original, and still
 *  overwhelmingly common, currency -- so every pre-existing call site that only
 *  ever passed an amount continues to render exactly as before. */
export function formatCurrency(amount: number, currency: string = "INR") {
  const locale = CURRENCY_LOCALES[currency] ?? "en-IN";
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(amount);
}

/** ZR-PAY-002's own examples show two-decimal amounts (£850.00, £24.99) --
 *  formatCurrency's maximumFractionDigits: 0 would round those away, so this
 *  is a separate helper for the Listing Fee / rental payment screens rather
 *  than changing formatCurrency's existing, widely-relied-on behavior.
 *  Lets Intl.NumberFormat use each currency's own natural decimal places. */
export function formatMoney(amount: number, currency: string) {
  const locale = CURRENCY_LOCALES[currency] ?? "en-GB";
  return new Intl.NumberFormat(locale, { style: "currency", currency }).format(amount);
}

export function formatDate(date: string) {
  return new Date(date).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** ZR-PAY-002 Section 5.1's own timestamp style ("18 Sep 2026, 14:31") --
 *  used wherever a payment declaration/confirmation timestamp, not just a
 *  due date, needs to be shown. */
export function formatDateTime(date: string) {
  return new Date(date).toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
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
