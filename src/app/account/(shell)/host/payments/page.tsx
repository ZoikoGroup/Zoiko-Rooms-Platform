import { RecipientRentalPaymentsManager } from "@/components/user/RecipientRentalPaymentsManager";

export default function HostPaymentsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Payments</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Amounts due, payment records, payment instructions and Listing Fee receipts across your properties.
        </p>
      </div>
      <RecipientRentalPaymentsManager />
    </div>
  );
}
