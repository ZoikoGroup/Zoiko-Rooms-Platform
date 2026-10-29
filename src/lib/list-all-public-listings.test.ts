// @vitest-environment node
import { describe, it, expect, vi, beforeEach } from "vitest";

const pages: { total: number; calls: string[] } = { total: 250, calls: [] };

vi.mock("@/lib/api-client", () => ({
  ApiError: class extends Error {},
  apiClientFetch: vi.fn(async (path: string) => {
    pages.calls.push(path);
    const params = new URL(path, "http://x").searchParams;
    const limit = Number(params.get("limit"));
    const offset = Number(params.get("offset"));
    const count = Math.max(0, Math.min(limit, pages.total - offset));
    return {
      items: Array.from({ length: count }, (_, i) => ({ id: `L-${offset + i}` })),
      limit,
      offset,
      total: pages.total,
      hasMore: offset + count < pages.total,
    };
  }),
}));

import { listAllPublicListings } from "@/lib/user-api";

describe("listAllPublicListings (rooms past the first 100 were never shown)", () => {
  beforeEach(() => {
    pages.calls = [];
    pages.total = 250;
  });

  it("reads every page, not just the first 100", async () => {
    const result = await listAllPublicListings();
    expect(result.items).toHaveLength(250);
    expect(new Set(result.items.map((i) => i.id)).size).toBe(250);
    expect(pages.calls).toHaveLength(3);
    expect(result.total).toBe(250);
    expect(result.hasMore).toBe(false);
  });

  it("one request when everything fits", async () => {
    pages.total = 40;
    const result = await listAllPublicListings();
    expect(result.items).toHaveLength(40);
    expect(pages.calls).toHaveLength(1);
  });

  it("an empty marketplace is fine", async () => {
    pages.total = 0;
    const result = await listAllPublicListings();
    expect(result.items).toEqual([]);
    expect(pages.calls).toHaveLength(1);
  });

  it("stops at the sanity cap instead of looping", async () => {
    pages.total = 100000;
    const result = await listAllPublicListings({}, 300);
    expect(result.items.length).toBeGreaterThanOrEqual(300);
    expect(result.items.length).toBeLessThanOrEqual(400);
    expect(result.hasMore).toBe(true);
  });
});
