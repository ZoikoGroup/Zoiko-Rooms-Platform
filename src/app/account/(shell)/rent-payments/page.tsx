import { RentalPaymentsManager } from "@/components/user/RentalPaymentsManager";

export default function RentPaymentsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Rent &amp; deposit payments</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Your rent is paid directly to your host/property owner using the payment method agreed with them. Zoiko does not collect or process your rent payment.
        </p>
      </div>
      <RentalPaymentsManager />
    </div>
  );
}
