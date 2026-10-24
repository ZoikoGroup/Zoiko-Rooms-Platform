import Link from "next/link";
import { BedDouble, CalendarRange, Users, Wallet } from "lucide-react";
import { StatCard } from "@/components/admin/StatCard";
import { RevenueChart, RevenueTrendPoint } from "@/components/admin/charts/RevenueChart";

/** GET /api/analytics/revenue-summary */
interface RevenueSummary {
  totalRevenue: number;
  thisMonthRevenue: number;
  lastMonthRevenue: number;
  changePct: number | null;
  paidEvents: number;
  /** All amounts are converted into this currency (USD). */
  currency: string;
  sourceCurrencies: string[];
  unconvertedCurrencies: string[];
  fxRatesAsOf: string;
}
import { BookingsByTypeChart, BookingsByTypePoint } from "@/components/admin/charts/BookingsByTypeChart";
import { OccupancyChart, OccupancyByCityPoint } from "@/components/admin/charts/OccupancyChart";
import { Badge } from "@/components/ui/Badge";
import { StarRating } from "@/components/ui/StarRating";
import { apiFetch, requireSuperAdmin } from "@/lib/api";
import { Booking, Guest, Listing } from "@/lib/types";
import { formatCurrency, formatDate, resolveImageUrl } from "@/lib/utils";
import { bookingStatusTone } from "@/lib/status";

export default async function AdminDashboardPage() {
  await requireSuperAdmin();

  const [bookings, guests, listings, revenueTrend, revenueSummary, bookingsByType, occupancyByCity] = await Promise.all([
    apiFetch<Booking[]>("/api/bookings"),
    apiFetch<Guest[]>("/api/guests"),
    apiFetch<Listing[]>("/api/listings"),
    apiFetch<RevenueTrendPoint[]>("/api/analytics/revenue-trend"),
    apiFetch<RevenueSummary>("/api/analytics/revenue-summary"),
    apiFetch<BookingsByTypePoint[]>("/api/analytics/bookings-by-type"),
    apiFetch<OccupancyByCityPoint[]>("/api/analytics/occupancy-by-city"),
  ]);

  // Same source as the Revenue Trend chart: paid bookings + confirmed rent (ZR-73).
  // Tolerate an older/partial response (e.g. a backend mid-restart) rather
  // than crashing the whole dashboard on a missing list.
  const totalRevenue = revenueSummary?.totalRevenue ?? 0;
  const revenueChange = revenueSummary?.changePct ?? null;
  const reportingCurrency = revenueSummary?.currency || "USD";
  const sourceCurrencies = revenueSummary?.sourceCurrencies ?? [];
  const unconvertedCurrencies = revenueSummary?.unconvertedCurrencies ?? [];
  // Rupees, pounds etc. are converted to US dollars at the configured rates;
  // "≈" marks a total that includes a conversion.
  const converted = sourceCurrencies.some((c) => c !== reportingCurrency);
  const revenueValue = `${converted ? "≈ " : ""}${formatCurrency(totalRevenue, reportingCurrency)}`;
  const revenueNote = [
    converted &&
      `Converted from ${sourceCurrencies.join(", ")} at ${
        revenueSummary?.fxRatesAsOf ? `rates as of ${formatDate(revenueSummary.fxRatesAsOf)}` : "approximate fixed rates"
      }.`,
    unconvertedCurrencies.length > 0 && `Not included (no rate set): ${unconvertedCurrencies.join(", ")}.`,
  ]
    .filter(Boolean)
    .join(" ");
  const activeGuests = guests.filter((g) => g.status === "active").length;
  const avgRating = listings.length ? (listings.reduce((s, l) => s + l.rating, 0) / listings.length).toFixed(1) : "—";
  const recentBookings = [...bookings]
    .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime())
    .slice(0, 5);
  const topListings = [...listings].sort((a, b) => b.rating - a.rating).slice(0, 4);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Welcome back 👋</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Here&apos;s what&apos;s happening across your properties today.</p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {/* Only real comparisons -- the other cards used to show fixed, made-up trends. */}
        <StatCard
          label="Total Revenue"
          value={revenueValue}
          change={revenueChange === null ? undefined : `${revenueChange > 0 ? "+" : ""}${revenueChange}% vs last month`}
          trend={revenueChange !== null && revenueChange < 0 ? "down" : "up"}
          icon={Wallet}
          index={0}
        />
        <StatCard label="Total Bookings" value={String(bookings.length)} icon={CalendarRange} index={1} />
        <StatCard label="Active Guests" value={String(activeGuests)} icon={Users} index={2} />
        <StatCard label="Avg. Rating" value={`${avgRating} / 5`} icon={BedDouble} index={3} />
      </div>
      {revenueNote && <p className="-mt-3 text-xs text-slate-500 dark:text-slate-400">Revenue in US dollars. {revenueNote}</p>}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="animate-fade-up rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-100 lg:col-span-2 dark:bg-slate-900 dark:ring-white/10">
          <div className="flex items-center justify-between">
            <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Revenue Trend</h2>
            <Badge tone="success">Last 6 months · {reportingCurrency}</Badge>
          </div>
          <div className="mt-2">
            <RevenueChart data={revenueTrend} />
          </div>
        </div>

        <div className="animate-fade-up rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
          <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Bookings by Stay Type</h2>
          <div className="mt-4">
            <BookingsByTypeChart data={bookingsByType} />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="animate-fade-up rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-100 lg:col-span-2 dark:bg-slate-900 dark:ring-white/10">
          <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Occupancy by City</h2>
          <div className="mt-2">
            <OccupancyChart data={occupancyByCity} />
          </div>
        </div>

        <div className="animate-fade-up rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
          <div className="flex items-center justify-between">
            <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Top Listings</h2>
          </div>
          <div className="mt-3 space-y-3">
            {topListings.map((l) => (
              <div key={l.id} className="flex items-center gap-3 rounded-xl p-2 transition-colors hover:bg-primary-50 dark:hover:bg-white/10">
                {l.images[0] ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={resolveImageUrl(l.images[0])} alt={l.name} className="h-11 w-11 rounded-lg object-cover" />
                ) : (
                  // No photo yet -- a placeholder instead of a broken image.
                  <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-400 dark:bg-slate-800">
                    <BedDouble className="h-5 w-5" aria-hidden="true" />
                  </span>
                )}
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-primary-900 dark:text-white">{l.name}</p>
                  <StarRating rating={l.rating} size={11} />
                </div>
                <span className="text-xs font-bold text-primary-700 dark:text-primary-300">{formatCurrency(l.pricePerNight, l.currency)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="animate-fade-up rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        <div className="flex items-center justify-between">
          <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Recent Bookings</h2>
          <Link href="/bookings" className="text-sm font-semibold text-primary-700 hover:text-accent-600 dark:text-primary-300">
            View all
          </Link>
        </div>

        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:border-slate-800 dark:text-slate-400">
                <th className="py-2.5 pr-4">Guest</th>
                <th className="py-2.5 pr-4">Property</th>
                <th className="py-2.5 pr-4">Dates</th>
                <th className="py-2.5 pr-4">Amount</th>
                <th className="py-2.5 pr-4">Status</th>
              </tr>
            </thead>
            <tbody>
              {recentBookings.map((b) => (
                <tr key={b.id} className="border-b border-slate-50 transition-colors hover:bg-primary-50/50 dark:border-slate-800 dark:hover:bg-white/10">
                  <td className="flex items-center gap-2.5 py-3 pr-4">
                    {b.guestAvatar ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={b.guestAvatar} alt={b.guestName} className="h-8 w-8 rounded-full bg-primary-50 dark:bg-primary-500/10" />
                    ) : (
                      <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary-50 text-xs font-bold text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
                        {b.guestName?.charAt(0) || "?"}
                      </span>
                    )}
                    <span className="font-medium text-slate-700 dark:text-slate-300">{b.guestName}</span>
                  </td>
                  <td className="py-3 pr-4 text-slate-600 dark:text-slate-300">{b.listingName}</td>
                  <td className="py-3 pr-4 text-slate-500 dark:text-slate-400">
                    {formatDate(b.checkIn)} - {formatDate(b.checkOut)}
                  </td>
                  <td className="py-3 pr-4 font-semibold text-primary-900 dark:text-white">{formatCurrency(b.totalAmount)}</td>
                  <td className="py-3 pr-4">
                    <Badge tone={bookingStatusTone[b.status]} className="capitalize">
                      {b.status}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
