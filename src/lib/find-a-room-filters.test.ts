// @vitest-environment node
import { describe, it, expect } from "vitest";
import { applyFiltersToParams, defaultFilters, filtersFromParams, type RoomFilters } from "@/lib/find-a-room-filters";

const TODAY = "2026-10-29";
const read = (qs: string) => filtersFromParams(new URLSearchParams(qs), TODAY);

describe("Find a Room filters in the address", () => {
  it("an empty address gives the defaults", () => {
    expect(read("")).toEqual(defaultFilters(TODAY));
  });

  it("every filter survives a round trip through the address (a refresh)", () => {
    const picked: RoomFilters = {
      city: "Manchester",
      price: "30000",
      roomType: "Studio",
      furnished: "part",
      availability: "date",
      moveIn: "2026-12-01",
      stayMonths: 6,
      sortBy: "plow",
      view: "map",
      bathrooms: ["Private"],
      amenities: ["Wi-Fi", "Kitchen"],
      accessibility: ["Lift access"],
    };
    const qs = applyFiltersToParams(new URLSearchParams(), picked, TODAY).toString();
    expect(read(qs)).toEqual(picked);
  });

  it("keeps the address clean: defaults are left out", () => {
    expect(applyFiltersToParams(new URLSearchParams(), defaultFilters(TODAY), TODAY).toString()).toBe("");
    const only = applyFiltersToParams(new URLSearchParams(), { ...defaultFilters(TODAY), price: "50000" }, TODAY);
    expect(only.toString()).toBe("price=50000");
  });

  it("clearing a filter removes it from the address", () => {
    const withPrice = applyFiltersToParams(new URLSearchParams(), { ...defaultFilters(TODAY), price: "50000" }, TODAY);
    expect(applyFiltersToParams(withPrice, defaultFilters(TODAY), TODAY).toString()).toBe("");
  });

  it("leaves unrelated address parameters alone", () => {
    const out = applyFiltersToParams(new URLSearchParams("utm_source=mail&price=20000"), { ...defaultFilters(TODAY), furnished: "yes" }, TODAY);
    expect(out.get("utm_source")).toBe("mail");
    expect(out.get("furnished")).toBe("yes");
    expect(out.has("price")).toBe(false);
  });

  it("ignores values the page doesn't offer", () => {
    const f = read("price=abc&furnished=maybe&avail=never&sort=random&view=3d&stay=7&move=tomorrow&bath=Heated,Private&amen=Pool,Wi-Fi");
    expect(f.price).toBe("all");
    expect(f.furnished).toBe("all");
    expect(f.availability).toBe("all");
    expect(f.sortBy).toBe("best");
    expect(f.view).toBe("list");
    expect(f.stayMonths).toBe(0);
    expect(f.moveIn).toBe(TODAY);
    expect(f.bathrooms).toEqual(["Private"]);
    expect(f.amenities).toEqual(["Wi-Fi"]);
  });

  it("a rent limit in any currency's range is kept (£500, ₹30,000 ...), a nonsense one is not", () => {
    expect(read("price=500").price).toBe("500");
    expect(read("price=30000").price).toBe("30000");
    expect(read("price=0").price).toBe("all");
    expect(read("price=-5").price).toBe("all");
    expect(read("price=1e9").price).toBe("all");
  });

  it("a city from an old link (?city=London) still works", () => {
    expect(read("city=London").city).toBe("London");
  });

  it("a move-in date equal to today isn't stored, so a saved link doesn't go stale", () => {
    expect(applyFiltersToParams(new URLSearchParams(), { ...defaultFilters(TODAY), moveIn: TODAY }, TODAY).has("move")).toBe(false);
  });
});
