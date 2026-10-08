import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  searchRooms,
  requestProviderContact,
  listMyExternalOpportunities,
  listExternalOutreachQueue,
  ExternalCard,
} from "@/lib/external-search";
import { ApiError } from "@/lib/api-client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const SNAKE_CARD = {
  source_id: "demo_external",
  source_tier: "B",
  canonical_id: null,
  title: "[External listing — masked]",
  location_city: "Atlantis",
  location_region: null,
  location_country: "GB",
  rent_monthly: null,
  deposit: null,
  availability_text: "appears listed",
  room_type: "PRIVATE_ROOM",
  occupancy: null,
  amenities: ["FURNISHED"],
  distance_km: 1.2,
  quality_score: 0.5,
  images_present: false,
  last_seen_at: "2026-09-30T15:40:00Z",
  has_exact_address: false,
  has_phone: false,
  has_email: false,
  has_url: false,
  verification_status: "unverified",
  is_unlocked: false,
  opportunity_id: 3,
};

describe("searchRooms", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("maps a snake_case external-fallback response into camelCase cards", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        state: "EXTERNAL_DISCOVERED",
        internal_matches: 0,
        internal_results: [],
        external_matches: [SNAKE_CARD],
        fallback_triggered: true,
        consent_required: true,
        disclosure_text: "External results are not verified by Zoiko Rooms.",
        guardrail_notes: ["INTERNAL_ZERO"],
        audit_id: null,
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await searchRooms({ city: "Atlantis" });

    expect(fetchMock).toHaveBeenCalledOnce();
    const [, init] = fetchMock.mock.calls[0];
    expect(init.body).toContain("Atlantis");

    expect(res.state).toBe("EXTERNAL_DISCOVERED");
    expect(res.internalMatches).toBe(0);
    expect(res.consentRequired).toBe(true);
    expect(res.fallbackTriggered).toBe(true);
    expect(res.externalMatches).toHaveLength(1);
    const card: ExternalCard = res.externalMatches[0];
    // Even if a server sent it, the source identity never reaches the card.
    expect(card).not.toHaveProperty("sourceId");
    expect(card).not.toHaveProperty("sourceTier");
    expect(card.locationCity).toBe("Atlantis");
    expect(card.roomType).toBe("PRIVATE_ROOM");
    expect(card.verificationStatus).toBe("unverified");
    expect(card.isUnlocked).toBe(false);
    expect(card.opportunityId).toBe(3);
  });

  it("never surfaces contact or source-URL fields on a card (SRCH-04)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          state: "EXTERNAL_DISCOVERED",
          internal_matches: 0,
          internal_results: [],
          external_matches: [SNAKE_CARD],
          fallback_triggered: true,
          consent_required: true,
        })
      )
    );
    const res = await searchRooms({ city: "Atlantis" });
    const card = res.externalMatches[0];

    const forbidden = [
      "sourceUrl", "providerPhone", "providerEmail", "exactAddress",
      "phone", "email", "url", "address", "source_url", "provider_phone",
      "provider_email", "exact_address", "direct_booking_url",
    ];
    for (const key of forbidden) {
      expect(key in card).toBe(false);
    }
  });

  it("throws ApiError on a non-ok response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "nope" }, 429)));
    await expect(searchRooms({ city: "X" })).rejects.toBeInstanceOf(ApiError);
  });
});

describe("requestProviderContact", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("posts message + consent fields and maps the outreach result", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ outreach_id: 7, status: "PENDING", channel: "PLATFORM_MESSAGE" })
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await requestProviderContact(3, { message: "Please connect me", consentFields: ["name"] });

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/opportunities/3/contact");
    const body = JSON.parse(init.body as string);
    expect(body.message).toBe("Please connect me");
    expect(body.consent_fields).toEqual(["name"]);

    expect(res.outreachId).toBe(7);
    expect(res.status).toBe("PENDING");
    expect(res.channel).toBe("PLATFORM_MESSAGE");
  });
});

describe("listMyExternalOpportunities", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("returns masked opportunity rows only", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse([
          {
            opportunity_id: 3,
            external_opportunity_id: "ext_s1",
            status: "EXTERNAL_DISCOVERED",
            verification_status: "NOT_VERIFIED_BY_ZOIKO_ROOMS",
            approx_location: "Bristol city centre area",
            outreach_status: "PENDING",
            provider_response: "NO_RESPONSE",
            requested_at: "2026-09-30T15:40:00Z",
            masked: true,
          },
        ])
      )
    );
    const rows = await listMyExternalOpportunities();
    expect(rows).toHaveLength(1);
    expect(rows[0].opportunityId).toBe(3);
    expect(rows[0].masked).toBe(true);
    expect(rows[0].verificationStatus).toBe("NOT_VERIFIED_BY_ZOIKO_ROOMS");
  });
});

describe("listExternalOutreachQueue", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("maps the admin queue rows", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse([
        {
          outreach_id: 7,
          opportunity_id: 3,
          external_opportunity_id: "ext_s1",
          status: "EXTERNAL_DISCOVERED",
          verification_status: "NOT_VERIFIED_BY_ZOIKO_ROOMS",
          approx_location: "Bristol city centre area",
          channel: "PLATFORM_MESSAGE",
          outreach_status: "PENDING",
          requested_at: "2026-09-30T15:40:00Z",
          requested_by_user_id: 1,
          requested_by_email: "user@test.com",
        },
      ])
    );
    vi.stubGlobal("fetch", fetchMock);

    const rows = await listExternalOutreachQueue();
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/admin/external-search/outreach");
    expect(rows).toHaveLength(1);
    expect(rows[0].outreachStatus).toBe("PENDING");
    expect(rows[0].requestedByEmail).toBe("user@test.com");
    expect(rows[0].approxLocation).toBe("Bristol city centre area");
  });
});