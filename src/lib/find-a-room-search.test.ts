// @vitest-environment node
import { describe, it, expect } from "vitest";
import { convertAmount, dominantCurrency, priceStepsFor, roomMatchesSearch, type SearchableRoom, type SearchCriteria } from "@/lib/find-a-room-search";

type Room = SearchableRoom & { id: string };
const TODAY = "2026-10-29";

const ROOMS: Room[] = [
  { id: "R1", title: "Bright En-suite Room", area: "Shoreditch", city: "London", roomType: "En-suite", monthlyRent: 25000, currency: "INR", minStayMonths: 3, availableDate: TODAY,
    furnishedState: "yes", bathroomType: "Private", amenities: ["Wi-Fi", "Kitchen", "Furnished"], accessibility: ["Lift access"] },
  { id: "R2", title: "Quiet Studio", area: "Camden", city: "London", roomType: "Studio", monthlyRent: 45000, currency: "INR", minStayMonths: 6, availableDate: TODAY,
    furnishedState: "no", bathroomType: "Shared", amenities: ["Wi-Fi"], accessibility: [] },
  { id: "R3", title: "Double near the station", area: "Didsbury", city: "Manchester", roomType: "Double", monthlyRent: 18000, currency: "INR", minStayMonths: 3, availableDate: TODAY,
    furnishedState: "part", bathroomType: "Shared", amenities: ["Wi-Fi", "Laundry"], accessibility: [] },
  { id: "R4", title: "Cosy Single", area: "Headingley", city: "Leeds", roomType: "Single", monthlyRent: 15000, currency: "INR", minStayMonths: 1, availableDate: TODAY,
    furnishedState: "unknown", bathroomType: "Private", amenities: [], accessibility: [] },
  { id: "R5", title: "Workspace Studio", area: "Ancoats", city: "Manchester", roomType: "Studio", monthlyRent: 30000, currency: "INR", minStayMonths: 12, availableDate: TODAY,
    furnishedState: "yes", bathroomType: "Private", amenities: ["Wi-Fi", "Workspace", "Kitchen"], accessibility: ["Step-free entrance", "Lift access"] },
];

const NONE: SearchCriteria = {
  query: "", price: "all", priceCurrency: "INR", roomType: "all", furnished: "all", availability: "all", moveIn: TODAY, stayMonths: 0,
  bathrooms: [], amenities: [], accessibility: [],
};
const search = (c: Partial<SearchCriteria>) => ROOMS.filter((r) => roomMatchesSearch(r, { ...NONE, ...c })).map((r) => r.id);

describe("Find a Room: the location search", () => {
  it("with nothing chosen every room shows", () => {
    expect(search({})).toEqual(["R1", "R2", "R3", "R4", "R5"]);
  });
  it("finds rooms by city, area or title, in any letter case", () => {
    expect(search({ query: "london" })).toEqual(["R1", "R2"]);
    expect(search({ query: "DIDSBURY" })).toEqual(["R3"]);
    expect(search({ query: "workspace" })).toEqual(["R5"]); // title
    expect(search({ query: "  manchester  " })).toEqual(["R3", "R5"]);
  });
  it("a place with no rooms gives no results", () => {
    expect(search({ query: "Bristol" })).toEqual([]);
  });
  it('the special word "all" shows everything', () => {
    expect(search({ query: "all" })).toHaveLength(5);
  });
});

describe("Find a Room: search together with filters", () => {
  it("location + price: only rooms in that place at or under the price", () => {
    expect(search({ query: "manchester", price: "20000" })).toEqual(["R3"]);
    expect(search({ query: "london", price: "30000" })).toEqual(["R1"]);
    expect(search({ query: "london", price: "20000" })).toEqual([]);
  });
  it("location + room type", () => {
    expect(search({ query: "manchester", roomType: "Studio" })).toEqual(["R5"]);
    expect(search({ query: "manchester", roomType: "studio" })).toEqual(["R5"]); // letter case ignored
    expect(search({ query: "leeds", roomType: "Studio" })).toEqual([]);
  });
  it("location + furnished", () => {
    expect(search({ query: "london", furnished: "yes" })).toEqual(["R1"]);
    expect(search({ query: "london", furnished: "no" })).toEqual(["R2"]);
    expect(search({ query: "manchester", furnished: "part" })).toEqual(["R3"]);
  });
  it("rooms that don't say whether they're furnished drop out as soon as any furnished option is picked", () => {
    expect(search({ query: "leeds" })).toEqual(["R4"]);
    expect(search({ query: "leeds", furnished: "no" })).toEqual([]); // R4 is "unknown", not "unfurnished"
  });
  it("location + bathroom", () => {
    expect(search({ query: "manchester", bathrooms: ["Private"] })).toEqual(["R5"]);
    expect(search({ bathrooms: ["Private", "Shared"] })).toHaveLength(5);
  });
  it("location + amenities: the room must have every one chosen", () => {
    expect(search({ query: "manchester", amenities: ["Wi-Fi"] })).toEqual(["R3", "R5"]);
    expect(search({ query: "manchester", amenities: ["Wi-Fi", "Workspace"] })).toEqual(["R5"]);
    expect(search({ query: "london", amenities: ["Workspace"] })).toEqual([]);
  });
  it("location + accessibility: the room must have every one chosen", () => {
    expect(search({ accessibility: ["Lift access"] })).toEqual(["R1", "R5"]);
    expect(search({ accessibility: ["Lift access", "Step-free entrance"] })).toEqual(["R5"]);
  });
  it("several filters at once narrow step by step", () => {
    const all = { query: "manchester", price: "50000", roomType: "Studio", furnished: "yes", bathrooms: ["Private"], amenities: ["Kitchen"], accessibility: ["Lift access"] };
    expect(search(all)).toEqual(["R5"]);
    expect(search({ ...all, price: "20000" })).toEqual([]); // R5 costs 30000
  });
  it("availability by move-in date: a room free later than the chosen date is hidden", () => {
    const later = ROOMS.map((r) => (r.id === "R2" ? { ...r, availableDate: "2026-12-01" } : r));
    const ids = later.filter((r) => roomMatchesSearch(r, { ...NONE, availability: "date", moveIn: "2026-11-01" })).map((r) => r.id);
    expect(ids).not.toContain("R2");
    expect(ids).toHaveLength(4);
    expect(later.filter((r) => roomMatchesSearch(r, { ...NONE, availability: "future", moveIn: "2026-11-01" }))).toHaveLength(5);
  });
});

import { COMMON_AMENITIES, FURNISHING_OPTIONS } from "@/lib/amenities";
import { deriveAccessibility, deriveFurnishedState, offersAmenity } from "@/lib/find-a-room-search";

describe("Furnishing, read from the amenities hosts pick", () => {
  it("every furnishing option a host can tick is understood", () => {
    expect(deriveFurnishedState(["Wi-Fi", "Furnished"])).toBe("yes");
    expect(deriveFurnishedState(["Part Furnished"])).toBe("part");
    expect(deriveFurnishedState(["Unfurnished", "Wi-Fi"])).toBe("no");
  });
  it('"Unfurnished" is not mistaken for "Furnished", and "Apartment" is not "part furnished"', () => {
    expect(deriveFurnishedState(["Unfurnished"])).toBe("no");
    expect(deriveFurnishedState(["Apartment view", "Wi-Fi"])).toBe("unknown");
  });
  it("a listing that says nothing about furnishing is unknown", () => {
    expect(deriveFurnishedState([])).toBe("unknown");
    expect(deriveFurnishedState(["Wi-Fi", "Parking"])).toBe("unknown");
  });
  it("furnishing words typed by hand still work", () => {
    expect(deriveFurnishedState(["fully furnished"])).toBe("yes");
    expect(deriveFurnishedState(["part-furnished"])).toBe("part");
    expect(deriveFurnishedState(["un-furnished"])).toBe("no");
  });
  it("the host's list offers all three furnishing choices", () => {
    for (const option of FURNISHING_OPTIONS) expect(COMMON_AMENITIES.map((a) => a.value)).toContain(option);
  });
});

describe("Amenity filters match the words hosts use", () => {
  it("Workspace finds a Study Table; Laundry finds a Washing Machine; Kitchen finds Kitchen Access", () => {
    expect(offersAmenity(["Study Table"], "Workspace")).toBe(true);
    expect(offersAmenity(["Washing Machine"], "Laundry")).toBe(true);
    expect(offersAmenity(["Kitchen Access"], "Kitchen")).toBe(true);
    expect(offersAmenity(["WiFi"], "Wi-Fi")).toBe(true);
  });
  it("and does not match what isn't there", () => {
    expect(offersAmenity(["Parking", "TV"], "Workspace")).toBe(false);
    expect(offersAmenity([], "Laundry")).toBe(false);
  });
  it("a search for a host-style amenity works end to end", () => {
    const room: SearchableRoom = { ...ROOMS[0], amenities: ["Study Table", "Washing Machine"] };
    expect(roomMatchesSearch(room, { ...NONE, amenities: ["Workspace", "Laundry"] })).toBe(true);
    expect(roomMatchesSearch(room, { ...NONE, amenities: ["Kitchen"] })).toBe(false);
  });
});

describe("Accessibility, read from tags and from the amenities hosts pick", () => {
  it('"Lift / Elevator" and "Step-free Entrance" amenities count', () => {
    expect(deriveAccessibility([], ["Lift / Elevator"])).toEqual(["Lift access"]);
    expect(deriveAccessibility([], ["Step-free Entrance", "Lift / Elevator"])).toEqual(["Step-free entrance", "Lift access"]);
  });
  it("tags still count, and nothing is invented", () => {
    expect(deriveAccessibility(["step-free"], [])).toEqual(["Step-free entrance"]);
    expect(deriveAccessibility(["quiet"], ["Wi-Fi"])).toEqual([]);
  });
});


describe("Price limits in the currency being looked at", () => {
  const uk: Room = { ...ROOMS[0], id: "U1", city: "London", monthlyRent: 450, currency: "GBP" };
  const uk2: Room = { ...ROOMS[1], id: "U2", city: "London", monthlyRent: 800, currency: "GBP" };
  const mixed = [...ROOMS, uk, uk2];
  const find = (c: Partial<SearchCriteria>) => mixed.filter((r) => roomMatchesSearch(r, { ...NONE, ...c })).map((r) => r.id);

  it("a UK renter's £ limit works on £ rooms (it used to be compared against a ₹ list)", () => {
    expect(find({ query: "london", price: "500", priceCurrency: "GBP" })).toEqual(["U1"]);
    expect(find({ query: "london", price: "1000", priceCurrency: "GBP" })).toEqual(["U1", "U2"]);
  });
  it("without exchange rates, a room in another currency is left out rather than wrongly compared", () => {
    // a ₹25,000 room is not 'under £500' just because 25000 > 500 -- or 'under ₹30,000' because 450 < 30000
    expect(find({ price: "500", priceCurrency: "GBP" })).toEqual(["U1"]);
    expect(find({ price: "30000", priceCurrency: "INR" })).toEqual(["R1", "R3", "R4", "R5"]);
  });
  it("with exchange rates, rooms in other currencies are compared fairly", () => {
    const rates = { USD: 1, GBP: 1.27, INR: 0.012 };
    // £500 ~ ₹52,900: the ₹25,000 / ₹30,000 / ₹18,000 / ₹15,000 rooms fit, the ₹45,000 one fits too (45,000 x 0.012 = $540 = £425)
    expect(find({ price: "500", priceCurrency: "GBP", rates })).toEqual(["R1", "R2", "R3", "R4", "R5", "U1"]);
    // £300 ~ $381: the rupee rooms at ₹15,000 ($180), ₹18,000 ($216), ₹25,000 ($300) and ₹30,000 ($360) fit; ₹45,000 ($540) and the £450 / £800 rooms don't
    expect(find({ price: "300", priceCurrency: "GBP", rates })).toEqual(["R1", "R3", "R4", "R5"]);
    // a ₹30,000 budget ~ $360 ~ £283: the £450 and £800 rooms are over
    expect(find({ price: "30000", priceCurrency: "INR", rates })).toEqual(["R1", "R3", "R4", "R5"]);
  });
  it("a currency with no rate is still left out even when other rates exist", () => {
    const odd: Room = { ...ROOMS[0], id: "X1", currency: "XYZ" };
    const ids = [...mixed, odd].filter((r) => roomMatchesSearch(r, { ...NONE, price: "30000", priceCurrency: "INR", rates: { INR: 0.012, GBP: 1.27 } })).map((r) => r.id);
    expect(ids).not.toContain("X1");
  });
  it("converts between currencies, and says so when it can't", () => {
    expect(convertAmount(100, "GBP", "GBP")).toBe(100);
    expect(convertAmount(1000, "GBP", "INR", { GBP: 1.27, INR: 0.012 })).toBeCloseTo(105833.33, 1);
    expect(convertAmount(1000, "GBP", "INR", { GBP: 1.27 })).toBeNull();
    expect(convertAmount(1000, "GBP", "INR", null)).toBeNull();
  });
  it("no limit shows every currency", () => {
    expect(find({})).toHaveLength(7);
  });
  it("picks the currency most of the rooms use, and offers sensible steps for it", () => {
    expect(dominantCurrency(mixed)).toBe("INR");
    expect(dominantCurrency([uk, uk2])).toBe("GBP");
    expect(dominantCurrency([])).toBe("INR");
    expect(priceStepsFor("GBP")).toContain(500);
    expect(priceStepsFor("gbp")).toEqual(priceStepsFor("GBP"));
    expect(priceStepsFor("XYZ").length).toBeGreaterThan(0);
  });
});


describe("Stay length hides rooms with a longer minimum stay", () => {
  const find = (stayMonths: number, more: Partial<SearchCriteria> = {}) => search({ stayMonths, ...more });
  it("flexible shows everything", () => {
    expect(find(0)).toHaveLength(5);
  });
  it("a 3-month stay only shows rooms that let you stay 3 months or less", () => {
    expect(find(3)).toEqual(["R1", "R3", "R4"]);
  });
  it("a longer stay shows more rooms", () => {
    expect(find(6)).toEqual(["R1", "R2", "R3", "R4"]);
    expect(find(12)).toHaveLength(5);
  });
  it("works together with search and other filters", () => {
    expect(find(6, { query: "london" })).toEqual(["R1", "R2"]);
    expect(find(3, { query: "london" })).toEqual(["R1"]);
    expect(find(3, { query: "manchester", price: "20000" })).toEqual(["R3"]);
    expect(find(3, { query: "manchester", roomType: "Studio" })).toEqual([]);
  });
});

import { availabilityDate } from "@/lib/find-a-room-search";

describe("Move-in availability, from the date the host sets", () => {
  it("no date, or a date that has passed, means available today", () => {
    expect(availabilityDate(null, TODAY)).toBe(TODAY);
    expect(availabilityDate(undefined, TODAY)).toBe(TODAY);
    expect(availabilityDate("", TODAY)).toBe(TODAY);
    expect(availabilityDate("2026-01-01", TODAY)).toBe(TODAY);
    expect(availabilityDate("not-a-date", TODAY)).toBe(TODAY);
  });
  it("a future date is kept", () => {
    expect(availabilityDate("2026-12-15", TODAY)).toBe("2026-12-15");
  });
  it('"By my move-in date" hides a room that isn\'t free yet by then, and shows it once the date arrives', () => {
    const room: Room = { ...ROOMS[0], availableDate: availabilityDate("2026-12-15", TODAY) };
    const fits = (moveIn: string) => roomMatchesSearch(room, { ...NONE, availability: "date", moveIn });
    expect(fits("2026-11-20")).toBe(false); // wants to move in before it's free
    expect(fits("2026-12-15")).toBe(true); // the day it becomes free
    expect(fits("2027-01-10")).toBe(true);
  });
  it('"Later is fine" still shows a room that is free later', () => {
    const room: Room = { ...ROOMS[0], availableDate: "2026-12-15" };
    expect(roomMatchesSearch(room, { ...NONE, availability: "future", moveIn: "2026-11-01" })).toBe(true);
  });
});
