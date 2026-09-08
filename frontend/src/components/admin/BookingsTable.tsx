"use client";

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { CheckCircle2, Plus, Search } from "lucide-react";
import { Booking, BookingStatus, Guest, Listing, PaymentStatus } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { formatCurrency, formatDate } from "@/lib/utils";
import { bookingStatusTone, paymentStatusTone } from "@/lib/status";
import { apiClientFetch, ApiError } from "@/lib/api-client";

const statusFilters: Array<BookingStatus | "all"> = ["all", "confirmed", "pending", "completed", "cancelled"];
const bookingStatuses: BookingStatus[] = ["confirmed", "pending", "completed", "cancelled"];
const paymentStatuses: PaymentStatus[] = ["paid", "unpaid", "refunded"];

const emptyForm = {
  guestMode: "existing" as "existing" | "new",
  guestId: "",
  newGuestName: "",
  newGuestEmail: "",
  newGuestPhone: "",
  listingId: "",
  checkIn: "",
  checkOut: "",
  guests: "2",
  status: "confirmed" as BookingStatus,
  paymentStatus: "unpaid" as PaymentStatus,
};

export function BookingsTable({ bookings, listings, guests }: { bookings: Booking[]; listings: Listing[]; guests: Guest[] }) {
  const [items, setItems] = useState(bookings);
  const searchParams = useSearchParams();
  const [query, setQuery] = useState(() => searchParams.get("q") ?? "");
  const [status, setStatus] = useState<BookingStatus | "all">("all");
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [formError, setFormError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [toast, setToast] = useState("");

  useEffect(() => {
    setLoading(true);
    const t = setTimeout(() => setLoading(false), 400);
    return () => clearTimeout(t);
  }, [query, status]);

  const filtered = useMemo(() => {
    return items.filter((b) => {
      const matchesStatus = status === "all" || b.status === status;
      const q = query.trim().toLowerCase();
      const matchesQuery =
        !q ||
        b.guestName.toLowerCase().includes(q) ||
        b.listingName.toLowerCase().includes(q) ||
        b.id.toLowerCase().includes(q);
      return matchesStatus && matchesQuery;
    });
  }, [items, query, status]);

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 2200);
  }

  function openModal() {
    setForm(emptyForm);
    setFormError("");
    setModalOpen(true);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError("");

    if (!form.listingId || !form.checkIn || !form.checkOut) {
      setFormError("Please fill in the property and both dates.");
      return;
    }
    if (form.guestMode === "existing" && !form.guestId) {
      setFormError("Please select a guest.");
      return;
    }
    if (form.guestMode === "new" && (!form.newGuestName.trim() || !form.newGuestEmail.trim())) {
      setFormError("Please enter the new guest's name and email.");
      return;
    }

    setSubmitting(true);
    try {
      const created = await apiClientFetch<Booking>("/api/bookings", {
        method: "POST",
        body: JSON.stringify({
          listingId: form.listingId,
          guestId: form.guestMode === "existing" ? form.guestId : undefined,
          newGuest:
            form.guestMode === "new"
              ? { name: form.newGuestName.trim(), email: form.newGuestEmail.trim(), phone: form.newGuestPhone.trim() }
              : undefined,
          checkIn: form.checkIn,
          checkOut: form.checkOut,
          guests: Number(form.guests),
          status: form.status,
          paymentStatus: form.paymentStatus,
        }),
      });
      setItems((prev) => [created, ...prev]);
      setModalOpen(false);
      showToast("Booking created successfully");
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to create booking. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 sm:p-6 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400 dark:text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by guest, property or booking ID"
            className="w-full rounded-full bg-slate-50 py-2.5 pl-10 pr-4 text-sm outline-none ring-1 ring-slate-100 transition-all focus:ring-primary-300 dark:bg-slate-800 dark:ring-slate-700"
          />
        </div>
        <div className="flex flex-wrap gap-2">
          {statusFilters.map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`rounded-full px-3.5 py-1.5 text-xs font-semibold capitalize transition-all duration-200 ${
                status === s
                  ? "bg-primary-700 text-white shadow-md shadow-primary-900/25"
                  : "bg-slate-50 text-slate-500 hover:bg-primary-50 hover:text-primary-700 dark:bg-slate-800 dark:text-slate-400 dark:hover:bg-primary-500/10 dark:hover:text-primary-300"
              }`}
            >
              {s}
            </button>
          ))}
        </div>
        <Button variant="accent" size="sm" onClick={openModal}>
          <Plus className="h-4 w-4" /> New Booking
        </Button>
      </div>

      <div className="mt-5 overflow-x-auto">
        <table className="w-full min-w-[880px] text-left text-sm">
          <thead>
            <tr className="border-b border-slate-100 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:border-slate-800 dark:text-slate-400">
              <th className="py-3 pr-4">Booking ID</th>
              <th className="py-3 pr-4">Guest</th>
              <th className="py-3 pr-4">Property</th>
              <th className="py-3 pr-4">Check-in</th>
              <th className="py-3 pr-4">Check-out</th>
              <th className="py-3 pr-4">Amount</th>
              <th className="py-3 pr-4">Payment</th>
              <th className="py-3 pr-4">Status</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 6 }).map((_, i) => (
                  <tr key={i} className="border-b border-slate-50 dark:border-slate-800">
                    {Array.from({ length: 8 }).map((__, j) => (
                      <td key={j} className="py-3.5 pr-4">
                        <Skeleton className="h-4 w-full max-w-[110px]" />
                      </td>
                    ))}
                  </tr>
                ))
              : filtered.map((b, i) => (
                  <tr
                    key={b.id}
                    className="animate-fade-in border-b border-slate-50 transition-colors hover:bg-primary-50/50 dark:border-slate-800 dark:hover:bg-primary-500/10"
                    style={{ animationDelay: `${Math.min(i, 8) * 0.03}s` }}
                  >
                    <td className="py-3.5 pr-4 font-mono text-xs font-semibold text-primary-700 dark:text-primary-300">{b.id}</td>
                    <td className="py-3.5 pr-4">
                      <div className="flex items-center gap-2.5">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={b.guestAvatar} alt={b.guestName} className="h-8 w-8 rounded-full bg-primary-50 dark:bg-primary-500/10" />
                        <span className="font-medium text-slate-700 dark:text-slate-300">{b.guestName}</span>
                      </div>
                    </td>
                    <td className="py-3.5 pr-4 text-slate-600 dark:text-slate-300">
                      <span className="capitalize text-xs font-semibold text-accent-600">{b.propertyType}</span>
                      <br />
                      {b.listingName}
                    </td>
                    <td className="py-3.5 pr-4 text-slate-500 dark:text-slate-400">{formatDate(b.checkIn)}</td>
                    <td className="py-3.5 pr-4 text-slate-500 dark:text-slate-400">{formatDate(b.checkOut)}</td>
                    <td className="py-3.5 pr-4 font-semibold text-primary-900 dark:text-white">{formatCurrency(b.totalAmount)}</td>
                    <td className="py-3.5 pr-4">
                      <Badge tone={paymentStatusTone[b.paymentStatus]} className="capitalize">
                        {b.paymentStatus}
                      </Badge>
                    </td>
                    <td className="py-3.5 pr-4">
                      <Badge tone={bookingStatusTone[b.status]} className="capitalize">
                        {b.status}
                      </Badge>
                    </td>
                  </tr>
                ))}
          </tbody>
        </table>

        {!loading && filtered.length === 0 && (
          <p className="py-10 text-center text-sm text-slate-400 dark:text-slate-400">No bookings match your search.</p>
        )}
      </div>

      <Modal open={modalOpen} onClose={() => setModalOpen(false)} title="New Booking">
        <form onSubmit={handleSubmit} autoComplete="off" className="max-h-[70vh] space-y-3.5 overflow-y-auto pr-1">
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Property
            </label>
            <select
              value={form.listingId}
              onChange={(e) => setForm((f) => ({ ...f, listingId: e.target.value }))}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              required
            >
              <option value="">Select a property</option>
              {listings.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name} — {l.city}
                </option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Check-in
              </label>
              <input
                type="date"
                value={form.checkIn}
                onChange={(e) => setForm((f) => ({ ...f, checkIn: e.target.value }))}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                required
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Check-out
              </label>
              <input
                type="date"
                value={form.checkOut}
                onChange={(e) => setForm((f) => ({ ...f, checkOut: e.target.value }))}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                required
              />
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Number of Guests
            </label>
            <input
              type="number"
              min={1}
              value={form.guests}
              onChange={(e) => setForm((f) => ({ ...f, guests: e.target.value }))}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>

          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setForm((f) => ({ ...f, guestMode: "existing" }))}
              className={`flex-1 rounded-xl px-3.5 py-2 text-xs font-semibold transition-all ${
                form.guestMode === "existing"
                  ? "bg-primary-700 text-white"
                  : "bg-slate-50 text-slate-500 dark:bg-slate-800 dark:text-slate-400"
              }`}
            >
              Existing Guest
            </button>
            <button
              type="button"
              onClick={() => setForm((f) => ({ ...f, guestMode: "new" }))}
              className={`flex-1 rounded-xl px-3.5 py-2 text-xs font-semibold transition-all ${
                form.guestMode === "new"
                  ? "bg-primary-700 text-white"
                  : "bg-slate-50 text-slate-500 dark:bg-slate-800 dark:text-slate-400"
              }`}
            >
              New Guest
            </button>
          </div>

          {form.guestMode === "existing" ? (
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Guest
              </label>
              <select
                value={form.guestId}
                onChange={(e) => setForm((f) => ({ ...f, guestId: e.target.value }))}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              >
                <option value="">Select a guest</option>
                {guests.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name} — {g.email}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <div className="space-y-3">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                  Guest Name
                </label>
                <input
                  value={form.newGuestName}
                  onChange={(e) => setForm((f) => ({ ...f, newGuestName: e.target.value }))}
                  className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                    Email
                  </label>
                  <input
                    type="email"
                    value={form.newGuestEmail}
                    onChange={(e) => setForm((f) => ({ ...f, newGuestEmail: e.target.value }))}
                    className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                    Phone
                  </label>
                  <input
                    value={form.newGuestPhone}
                    onChange={(e) => setForm((f) => ({ ...f, newGuestPhone: e.target.value }))}
                    className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                  />
                </div>
              </div>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Booking Status
              </label>
              <select
                value={form.status}
                onChange={(e) => setForm((f) => ({ ...f, status: e.target.value as BookingStatus }))}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm capitalize outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              >
                {bookingStatuses.map((s) => (
                  <option key={s} value={s} className="capitalize">
                    {s}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Payment Status
              </label>
              <select
                value={form.paymentStatus}
                onChange={(e) => setForm((f) => ({ ...f, paymentStatus: e.target.value as PaymentStatus }))}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm capitalize outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              >
                {paymentStatuses.map((s) => (
                  <option key={s} value={s} className="capitalize">
                    {s}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {formError && (
            <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
              {formError}
            </p>
          )}

          <Button type="submit" variant="primary" fullWidth loading={submitting} className="mt-2">
            <Plus className="h-4 w-4" /> Create Booking
          </Button>
        </form>
      </Modal>

      {toast && (
        <div className="animate-fade-up fixed bottom-6 right-6 z-[300] flex items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl">
          <CheckCircle2 className="h-4 w-4 text-emerald-400" /> {toast}
        </div>
      )}
    </div>
  );
}
