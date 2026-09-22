import { RentalPaymentsManager } from "@/components/user/RentalPaymentsManager";

export default function RentPaymentsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Rent &amp; deposit payments</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Obligations, payment instructions and records for your rentals. Zoiko Rooms never receives or holds these payments.
        </p>
      </div>
      <RentalPaymentsManager />
    </div>
  );
}
