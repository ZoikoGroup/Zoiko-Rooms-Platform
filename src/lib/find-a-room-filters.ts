/** The Find a Room filters, kept in the page address (?price=30000&type=Studio…) so a refresh,
 *  the back button or a shared link brings the same search back. Anything missing or not a
 *  value the page offers falls back to its default. */

export const FURNISHED_VALUES = ["all", "yes", "part", "no"] as const;
export const AVAILABILITY_VALUES = ["all", "date", "future"] as const;
export const SORT_VALUES = ["best", "plow", "phigh", "soon"] as const;
export const VIEW_VALUES = ["list", "map"] as const;
export const STAY_VALUES = [0, 3, 6, 12] as const;
export const BATHROOM_VALUES = ["Private", "Shared"] as const;
export const AMENITY_VALUES = ["Wi-Fi", "Workspace", "Laundry", "Kitchen"] as const;
export const ACCESSIBILITY_VALUES = ["Step-free entrance", "Lift access"] as const;

export interface RoomFilters {
  city: string;
  price: string;
  roomType: string;
  furnished: string;
  availability: string;
  moveIn: string;
  stayMonths: number;
  sortBy: (typeof SORT_VALUES)[number];
  view: (typeof VIEW_VALUES)[number];
  bathrooms: string[];
  amenities: string[];
  accessibility: string[];
}

export function defaultFilters(today: string): RoomFilters {
  return {
    city: "",
    price: "all",
    roomType: "all",
    furnished: "all",
    availability: "all",
    moveIn: today,
    stayMonths: 0,
    sortBy: "best",
    view: "list",
    bathrooms: [],
    amenities: [],
    accessibility: [],
  };
}

type ParamReader = { get(name: string): string | null };

function pick<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
  return value !== null && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

function pickList(value: string | null, allowed: readonly string[]): string[] {
  if (!value) return [];
  return Array.from(new Set(value.split(",").map((v) => v.trim()).filter((v) => allowed.includes(v))));
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** A rent limit from the address: a whole number above zero, else "all". */
function validPrice(value: string | null): string {
  return value && /^\d{1,8}$/.test(value) && Number(value) > 0 ? String(Number(value)) : "all";
}

/** Read the filters out of the address. */
export function filtersFromParams(params: ParamReader, today: string): RoomFilters {
  const stay = Number(params.get("stay"));
  const move = params.get("move");
  const type = (params.get("type") ?? "").trim();
  return {
    city: (params.get("city") ?? "").trim().slice(0, 120),
    price: validPrice(params.get("price")),
    roomType: type && type !== "all" ? type.slice(0, 60) : "all",
    furnished: pick(params.get("furnished"), FURNISHED_VALUES, "all"),
    availability: pick(params.get("avail"), AVAILABILITY_VALUES, "all"),
    moveIn: move && ISO_DATE.test(move) && !Number.isNaN(Date.parse(move)) ? move : today,
    stayMonths: (STAY_VALUES as readonly number[]).includes(stay) ? stay : 0,
    sortBy: pick(params.get("sort"), SORT_VALUES, "best"),
    view: pick(params.get("view"), VIEW_VALUES, "list"),
    bathrooms: pickList(params.get("bath"), BATHROOM_VALUES),
    amenities: pickList(params.get("amen"), AMENITY_VALUES),
    accessibility: pickList(params.get("acc"), ACCESSIBILITY_VALUES),
  };
}

const KEYS = ["city", "price", "type", "furnished", "avail", "move", "stay", "sort", "view", "bath", "amen", "acc"] as const;

/** Put the filters into a copy of `base` -- only the ones that differ from their default, so the
 *  address stays clean -- and leave any unrelated parameters untouched. */
export function applyFiltersToParams(base: URLSearchParams, filters: RoomFilters, today: string): URLSearchParams {
  const next = new URLSearchParams(base.toString());
  for (const key of KEYS) next.delete(key);
  const set = (key: (typeof KEYS)[number], value: string, isDefault: boolean) => {
    if (!isDefault && value) next.set(key, value);
  };
  set("city", filters.city.trim(), !filters.city.trim());
  set("price", filters.price, filters.price === "all");
  set("type", filters.roomType, filters.roomType === "all");
  set("furnished", filters.furnished, filters.furnished === "all");
  set("avail", filters.availability, filters.availability === "all");
  set("move", filters.moveIn, filters.moveIn === today);
  set("stay", String(filters.stayMonths), filters.stayMonths === 0);
  set("sort", filters.sortBy, filters.sortBy === "best");
  set("view", filters.view, filters.view === "list");
  set("bath", filters.bathrooms.join(","), filters.bathrooms.length === 0);
  set("amen", filters.amenities.join(","), filters.amenities.length === 0);
  set("acc", filters.accessibility.join(","), filters.accessibility.length === 0);
  return next;
}
