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

export function formatDate(date: string) {
  return new Date(date).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
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
