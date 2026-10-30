/** The Find a Room search: does a room match the location text and every filter the visitor set?
 *  Kept apart from the page so it can be tested on its own. */

/** The parts of a room the search looks at. */
export interface SearchableRoom {
  title: string;
  area: string;
  city: string;
  roomType: string;
  monthlyRent: number;
  currency: string;
  minStayMonths: number;
  availableDate: string;
  furnishedState: "yes" | "part" | "no" | "unknown";
  bathroomType: string;
  amenities: string[];
  accessibility: string[];
}

export interface SearchCriteria {
  /** The location text from the search box. */
  query: string;
  /** "all" or the highest monthly rent, as text. */
  price: string;
  /** The currency that rent limit is in. */
  priceCurrency: string;
  /** Indicative US dollars per unit of each currency, to compare rooms priced in another currency.
   *  Without a rate for a room's currency that room can't be compared with the limit and isn't shown. */
  rates?: Record<string, number> | null;
  roomType: string;
  furnished: string;
  availability: string;
  /** The move-in date (YYYY-MM-DD), used by the "By my move-in date" availability option. */
  moveIn: string;
  /** How long the visitor wants to stay; 0 = flexible. A room whose minimum stay is longer is not shown. */
  stayMonths: number;
  bathrooms: string[];
  amenities: string[];
  accessibility: string[];
}

export function roomMatchesSearch(room: SearchableRoom, c: SearchCriteria): boolean {
  // Location text: matches the area, the city or the title.
  const q = c.query.toLowerCase().trim();
  if (q && q !== "all") {
    const found =
      room.area.toLowerCase().includes(q) || room.city.toLowerCase().includes(q) || room.title.toLowerCase().includes(q);
    if (!found) return false;
  }

  // A rent limit is in one currency; a room priced in another is compared at the indicative rate,
  // or left out when there is no rate to compare with.
  if (c.price !== "all") {
    const rent = convertAmount(room.monthlyRent, room.currency, c.priceCurrency, c.rates);
    if (rent === null || rent > Number(c.price)) return false;
  }

  if (c.roomType !== "all" && room.roomType.toLowerCase() !== c.roomType.toLowerCase()) return false;

  if (c.furnished !== "all" && room.furnishedState !== c.furnished) return false;

  if (c.availability === "date" && room.availableDate > c.moveIn) return false;

  if (c.stayMonths > 0 && room.minStayMonths > c.stayMonths) return false;

  if (c.bathrooms.length > 0 && !c.bathrooms.includes(room.bathroomType)) return false;

  // Every chosen amenity must be offered.
  if (c.amenities.length > 0 && !c.amenities.every((wanted) => offersAmenity(room.amenities, wanted))) return false;

  // Every chosen accessibility feature must be offered.
  if (c.accessibility.length > 0 && !c.accessibility.every((wanted) => room.accessibility.includes(wanted))) return false;

  return true;
}

// ---------------------------------------------------------------------------
// What a listing's amenities and tags say about the room. Hosts pick amenities from
// lib/amenities.ts (or type their own), so the filters have to understand those words.
// ---------------------------------------------------------------------------

export type FurnishedState = "yes" | "part" | "no" | "unknown";

/** Furnished / part furnished / unfurnished, from the amenities the host ticked or typed.
 *  "unknown" only when the listing says nothing about furnishing. */
export function deriveFurnishedState(amenities: string[]): FurnishedState {
  const text = amenities.map((a) => a.toLowerCase());
  if (text.some((a) => /\bpart[\s-]*furnish/.test(a))) return "part";
  if (text.some((a) => /\bun[\s-]*furnish/.test(a))) return "no";
  if (text.some((a) => /\bfurnish/.test(a))) return "yes";
  return "unknown";
}

/** Words a host might use for what a filter calls something else. */
const AMENITY_ALIASES: Record<string, string[]> = {
  workspace: ["workspace", "work space", "study table", "study desk", "desk"],
  laundry: ["laundry", "washing machine", "washer"],
  kitchen: ["kitchen"],
  "wi-fi": ["wi-fi", "wifi", "wi fi", "internet"],
};

/** Does the room offer the amenity the visitor asked for? */
export function offersAmenity(offered: string[], wanted: string): boolean {
  const key = wanted.toLowerCase();
  const terms = AMENITY_ALIASES[key] ?? [key];
  return offered.some((o) => terms.some((t) => o.toLowerCase().includes(t)));
}

/** "Step-free entrance" / "Lift access" -- from the listing's tags, and from amenities such as
 *  "Lift / Elevator" or "Step-free Entrance" that hosts tick. */
export function deriveAccessibility(tags: string[], amenities: string[]): string[] {
  const all = [...tags, ...amenities].map((t) => t.toLowerCase());
  const out: string[] = [];
  if (all.some((t) => t.includes("step-free") || t.includes("step free") || t.includes("level access"))) out.push("Step-free entrance");
  if (all.some((t) => t.includes("lift") || t.includes("elevator"))) out.push("Lift access");
  return out;
}

// ---------------------------------------------------------------------------
// Price limits: offered in the currency the visitor is looking at.
// ---------------------------------------------------------------------------

/** Sensible "up to" steps per month, by currency. */
const PRICE_STEPS: Record<string, number[]> = {
  INR: [20000, 25000, 30000, 50000, 100000],
  GBP: [300, 400, 500, 600, 800, 1000],
  USD: [500, 750, 1000, 1500, 2000],
  EUR: [500, 750, 1000, 1500, 2000],
  AED: [1500, 2500, 3500, 5000, 7500],
  AUD: [600, 900, 1200, 1800, 2500],
  CAD: [600, 900, 1200, 1800, 2500],
};
const FALLBACK_PRICE_STEPS = [500, 1000, 2000, 5000, 10000];

export function priceStepsFor(currency: string): number[] {
  return PRICE_STEPS[currency.toUpperCase()] ?? FALLBACK_PRICE_STEPS;
}

/** The currency most of these rooms are priced in (ties go to the alphabetically first). */
export function dominantCurrency(rooms: { currency: string }[], fallback = "INR"): string {
  const counts = new Map<string, number>();
  for (const room of rooms) counts.set(room.currency, (counts.get(room.currency) ?? 0) + 1);
  let best = fallback;
  let bestCount = 0;
  for (const [currency, count] of [...counts.entries()].sort(([a], [b]) => a.localeCompare(b))) {
    if (count > bestCount) {
      best = currency;
      bestCount = count;
    }
  }
  return best;
}

/** `amount` in `from`, expressed in `to` at the indicative rates -- or null when either has no rate. */
export function convertAmount(
  amount: number,
  from: string,
  to: string,
  usdPerUnit?: Record<string, number> | null,
): number | null {
  if (from === to) return amount;
  const fromRate = usdPerUnit?.[from.toUpperCase()];
  const toRate = usdPerUnit?.[to.toUpperCase()];
  if (!fromRate || !toRate) return null;
  return (amount * fromRate) / toRate;
}

/** The day a room can be moved into: the host's date, or today when there is none or it has passed. */
export function availabilityDate(availableFrom: string | null | undefined, today: string): string {
  return availableFrom && /^\d{4}-\d{2}-\d{2}$/.test(availableFrom) && availableFrom > today ? availableFrom : today;
}
