import { BookingsTable } from "@/components/admin/BookingsTable";
import { apiFetch, requireSuperAdmin } from "@/lib/api";
import { Booking, Guest, Listing } from "@/lib/types";

export default async function AdminBookingsPage() {
  await requireSuperAdmin();

  const [bookings, listings, guests] = await Promise.all([
    apiFetch<Booking[]>("/api/bookings"),
    apiFetch<Listing[]>("/api/listings"),
    apiFetch<Guest[]>("/api/guests"),
  ]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Bookings</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Track and manage every long-term room-share reservation.
        </p>
      </div>
      <BookingsTable bookings={bookings} listings={listings} guests={guests} />
    </div>
  );
}
