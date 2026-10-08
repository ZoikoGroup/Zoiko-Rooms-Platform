// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ExternalDiscoveryCard } from "@/components/user/chat/ExternalDiscoveryCard";
import { ExternalCard } from "@/lib/external-search";

const CARD: ExternalCard = {
  canonicalId: null,
  title: "[External listing — masked]",
  locationCity: "Bristol",
  locationRegion: null,
  locationCountry: "GB",
  rentMonthly: null,
  currency: null,
  deposit: null,
  availabilityText: "appears listed",
  roomType: "PRIVATE_ROOM",
  occupancy: null,
  amenities: ["FURNISHED"],
  distanceKm: 1.2,
  qualityScore: 0.5,
  imagesPresent: false,
  lastSeenAt: "2026-09-30T15:40:00Z",
  verificationStatus: "unverified",
  isUnlocked: false,
  opportunityId: 3,
};

describe("ExternalDiscoveryCard", () => {
  it("shows the persistent NOT VERIFIED badge (SRCH-04 disclosure)", () => {
    render(<ExternalDiscoveryCard card={CARD} onRequestContact={vi.fn()} />);
    expect(screen.getByText(/Not verified by Zoiko Rooms/i)).toBeInTheDocument();
  });

  it("exposes the persistent disclosure via the accessibility label, with no leaked identifiers", () => {
    render(<ExternalDiscoveryCard card={CARD} onRequestContact={vi.fn()} />);
    const article = screen.getByRole("article");
    expect(article).toHaveAccessibleName(/not verified by Zoiko Rooms/i);
    expect(article).toHaveAccessibleName(/appears listed/i);
    // App B: no source URL / phone / email / handle may leak through the label.
    expect(article).not.toHaveAccessibleName(/http|example\.org|@|\+44|\b07\d{2}\b/i);
  });

  it("shows only the approximate area, never an exact address", () => {
    render(<ExternalDiscoveryCard card={CARD} onRequestContact={vi.fn()} />);
    expect(screen.getByText(/Approximate area: Bristol/)).toBeInTheDocument();
  });

  it("uses 'Appears listed' wording, never 'available'", () => {
    render(<ExternalDiscoveryCard card={CARD} onRequestContact={vi.fn()} />);
    expect(screen.getByText(/Appears listed/)).toBeInTheDocument();
    expect(screen.queryByText(/available/i)).not.toBeInTheDocument();
  });

  it("shows the discovery timestamp as freshness wording (SRCH-04)", () => {
    render(<ExternalDiscoveryCard card={CARD} onRequestContact={vi.fn()} />);
    expect(screen.getByText(/Appears listed · discovered 30 Sept 2026/)).toBeInTheDocument();
    expect(screen.getByText(/availability not confirmed/)).toBeInTheDocument();
  });

  it("labels any price as the advertised price", () => {
    render(<ExternalDiscoveryCard card={{ ...CARD, rentMonthly: 850 }} onRequestContact={vi.fn()} />);
    expect(screen.getByText(/Advertised price: £850 per month/)).toBeInTheDocument();
  });

  it("must NOT offer website/booking/phone/email actions", () => {
    render(<ExternalDiscoveryCard card={CARD} onRequestContact={vi.fn()} />);
    for (const forbidden of [/visit website/i, /book externally/i, /call landlord/i, /email agent/i]) {
      expect(screen.queryByText(forbidden)).not.toBeInTheDocument();
    }
  });

  it("fires onRequestContact with the card when the CTA is clicked", () => {
    const onRequestContact = vi.fn();
    render(<ExternalDiscoveryCard card={CARD} onRequestContact={onRequestContact} />);
    fireEvent.click(screen.getByRole("button", { name: /ask zoiko rooms to contact provider/i }));
    expect(onRequestContact).toHaveBeenCalledWith(CARD);
  });

  it("disables the CTA while a contact request is pending", () => {
    render(<ExternalDiscoveryCard card={CARD} onRequestContact={vi.fn()} contactPending />);
    const button = screen.getByRole("button", { name: /requesting/i });
    expect(button).toBeDisabled();
  });

  it("disables the CTA when the card has no persisted opportunity id", () => {
    render(<ExternalDiscoveryCard card={{ ...CARD, opportunityId: null }} onRequestContact={vi.fn()} />);
    expect(screen.getByRole("button", { name: /ask zoiko rooms to contact provider/i })).toBeDisabled();
  });
});