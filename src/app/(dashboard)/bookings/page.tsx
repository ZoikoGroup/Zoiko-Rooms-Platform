import { BookingsTable } from "@/components/admin/BookingsTable";
import { apiFetch, requireSuperAdmin } from "@/lib/api";
import { Booking } from "@/lib/types";

export default async function AdminBookingsPage() {
  await requireSuperAdmin();

  const bookings = await apiFetch<Booking[]>("/api/bookings");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Bookings</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Historical record only -- the real booking pipeline (applications, offers, agreements) lives under Leasing.
        </p>
      </div>
      <BookingsTable bookings={bookings} />
    </div>
  );
}
