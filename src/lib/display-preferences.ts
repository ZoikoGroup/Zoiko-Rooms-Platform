import type { UserPreferences } from "@/lib/types";

/** The signed-in user's display settings (Settings > Preferences), applied
 *  app-wide: formatDate/formatDateTime read the time zone, clock and region
 *  from here, and density/motion are set on <html> for globals.css.
 *  Cached in localStorage so the next page load starts with them. */

export interface DisplayPreferences {
  timeZone?: string;
  timeFormat: "12h" | "24h";
  locale: string;
  density: "comfortable" | "compact";
  motion: "system" | "reduce";
}

const STORAGE_KEY = "zr-display-preferences";

const REGION_LOCALE: Record<string, string> = { GB: "en-GB", US: "en-US", IN: "en-IN" };

const DEFAULTS: DisplayPreferences = { timeFormat: "24h", locale: "en-GB", density: "comfortable", motion: "system" };

function readCache(): DisplayPreferences {
  if (typeof window === "undefined") return DEFAULTS;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? { ...DEFAULTS, ...(JSON.parse(raw) as Partial<DisplayPreferences>) } : DEFAULTS;
  } catch {
    return DEFAULTS;
  }
}

let current: DisplayPreferences = readCache();

export function getDisplayPreferences(): DisplayPreferences {
  return current;
}

function applyToDocument(prefs: DisplayPreferences) {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.density = prefs.density;
  document.documentElement.dataset.motion = prefs.motion;
}

if (typeof window !== "undefined") applyToDocument(current);

/** Applies saved preferences; returns true when anything that affects
 *  rendering changed (callers re-render). */
export function applyUserPreferences(prefs: UserPreferences): boolean {
  const next: DisplayPreferences = {
    timeZone: prefs.timeZone,
    timeFormat: prefs.timeFormat,
    locale: REGION_LOCALE[prefs.region] ?? "en-GB",
    density: prefs.density,
    motion: prefs.motion,
  };
  const changed = JSON.stringify(next) !== JSON.stringify(current);
  current = next;
  applyToDocument(next);
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* storage unavailable -- settings still apply for this page */
  }
  return changed;
}

/** Back to defaults on sign-out, so the next person on this browser doesn't inherit them. */
export function clearDisplayPreferences() {
  current = DEFAULTS;
  applyToDocument(DEFAULTS);
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}
