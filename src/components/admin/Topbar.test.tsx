// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { Topbar } from "@/components/admin/Topbar";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("next/link", () => ({ default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a> }));
vi.mock("next/image", () => ({ default: (props: { alt: string }) => <span>{props.alt}</span> }));
vi.mock("@/lib/auth", () => ({
  getCurrentAdmin: () => Promise.resolve({ email: "admin@zoikorooms.com" }),
  logout: vi.fn(),
}));
vi.mock("@/lib/notifications", () => ({
  ADMIN_NOTIFICATIONS_BASE: "/api/notifications",
  getUnreadNotificationCount: () => Promise.resolve({ count: 0 }),
  listNotifications: () => Promise.resolve([]),
  markAllNotificationsRead: vi.fn(),
  markNotificationRead: vi.fn(),
  resolveNotificationHref: () => "/",
}));

// Every kind of result the backend's /api/search can return (backend/app/crud/search.py).
const RESULTS = [
  { id: "L1", type: "listing", title: "Room 29 listing", subtitle: "London · Private room", href: "/properties?q=Room%2029" },
  { id: "G1", type: "guest", title: "Room29 Guest", subtitle: "g@x.com", href: "/guests?q=Room29" },
  { id: "B1", type: "booking", title: "Room29 Guest — Room 29", subtitle: "Booking B1", href: "/bookings?q=B1" },
  { id: "4", type: "application", title: "Room29 Guest — Room 29 app", subtitle: "Application #4 · SUBMITTED", href: "/leasing?q=Room29" },
  { id: "7", type: "occupancy", title: "Room29 Guest — Room 29 stay", subtitle: "Occupancy #7 · ACTIVE", href: "/occupancy?q=Room29" },
];

vi.mock("@/lib/api-client", () => ({
  apiClientFetch: vi.fn((path: string) => {
    if (path.startsWith("/api/search")) return Promise.resolve(RESULTS);
    return Promise.resolve([]);
  }),
}));

describe("Topbar search (ZR-48 / ZR-72)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("shows every kind of result instead of crashing the page", async () => {
    render(<Topbar onOpenMobileSidebar={vi.fn()} onToggleChat={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText(/Search bookings, guests, properties/), { target: { value: "room29" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    for (const r of RESULTS) expect(screen.getByText(r.title)).toBeInTheDocument();
  });

  it("an unknown future result type still renders", async () => {
    const { apiClientFetch } = await import("@/lib/api-client");
    vi.mocked(apiClientFetch).mockResolvedValueOnce([{ id: "X", type: "something_new", title: "Brand new kind", subtitle: "", href: "/" }]);
    render(<Topbar onOpenMobileSidebar={vi.fn()} onToggleChat={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText(/Search bookings, guests, properties/), { target: { value: "new" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    expect(screen.getByText("Brand new kind")).toBeInTheDocument();
  });

  it("picking a result goes to its page", async () => {
    render(<Topbar onOpenMobileSidebar={vi.fn()} onToggleChat={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText(/Search bookings, guests, properties/), { target: { value: "room29" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    fireEvent.click(screen.getByText(RESULTS[3].title));
    expect(push).toHaveBeenCalledWith("/leasing?q=Room29");
  });
});
