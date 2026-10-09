import { describe, it, expect, vi, afterEach } from "vitest";
import {
  getProviderRequest,
  respondAsProvider,
  listMyRoomRequests,
  requestProviderContact,
} from "@/lib/external-search";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const PROVIDER_VIEW = {
  approx_location: "Leeds",
  renter_demand: { desired_area: "Leeds", move_in_window: "from 1 November" },
  renter_message: "Is it free?",
  status: "ACCEPTED",
  acceptance_model: "CLAIM_AND_LIST",
  options: ["CLAIM_AND_LIST"],
  terms_version: "1",
  lead_protection_days: 90,
  sender_entity: "Zoiko Realty Group Inc.",
  messages: [{ id: 1, from: "renter", body: "Hello", at: "2026-10-08T10:00:00Z" }],
  can_message: true,
  release: { available: true, you_consented: false, other_consented: true, released: false, contact: null },
};

afterEach(() => vi.unstubAllGlobals());

describe("provider journey client", () => {
  it("posts the provider token in the body (never the URL) and maps the view", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(PROVIDER_VIEW));
    vi.stubGlobal("fetch", fetchMock);
    const view = await getProviderRequest("tok.abc123");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/public\/provider\/request$/);
    expect(url).not.toContain("tok.abc123");
    expect(JSON.parse(String(init.body))).toEqual({ token: "tok.abc123" });
    expect(view.renterDemand.move_in_window).toBe("from 1 November");
    expect(view.release.otherConsented).toBe(true);
    expect(view.messages[0].from).toBe("renter");
  });

  it("sends the acceptance with the chosen model and terms flag", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(PROVIDER_VIEW));
    vi.stubGlobal("fetch", fetchMock);
    await respondAsProvider("tok", { decision: "ACCEPT", model: "CLAIM_AND_LIST", providerName: "Leeds Lets", acceptedTerms: true });
    const body = JSON.parse(String((fetchMock.mock.calls[0] as [string, RequestInit])[1].body));
    expect(body).toMatchObject({ decision: "ACCEPT", model: "CLAIM_AND_LIST", provider_name: "Leeds Lets", accepted_terms: true });
  });

  it("maps the renter's requests", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([{
      outreach_id: 7, opportunity_id: 3, external_opportunity_id: "ext_x", approx_location: "Leeds",
      verification_status: "NOT_VERIFIED_BY_ZOIKO_ROOMS", outreach_status: "SENT", provider_response: "ACCEPTED",
      provider_name: "Leeds Lets", requested_at: null, can_message: true, messages: [], release: { released: true, contact: "owner@x.example" },
    }])));
    const [req] = await listMyRoomRequests();
    expect(req.outreachId).toBe(7);
    expect(req.canMessage).toBe(true);
    expect(req.release.contact).toBe("owner@x.example");
  });

  it("includes lead details with a contact request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ outreach_id: 1, status: "PENDING", channel: "EMAIL" }));
    vi.stubGlobal("fetch", fetchMock);
    await requestProviderContact(3, { message: "Hi", consentFields: ["move_in_window"], leadDetails: { move_in_window: "Nov" } });
    const body = JSON.parse(String((fetchMock.mock.calls[0] as [string, RequestInit])[1].body));
    expect(body.lead_details).toEqual({ move_in_window: "Nov" });
  });
});
