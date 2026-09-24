import Link from "next/link";
import { PaymentsHistory } from "@/components/user/PaymentsHistory";

export default function PaymentsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Payment History</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Payments previously recorded against your account, and what each one was allocated to. Rent and deposits are
          paid directly to your landlord, agent or other authorized recipient — Zoiko Rooms does not receive or hold
          these funds. Use{" "}
          <Link href="/account/rent-payments" className="font-semibold text-primary-700 dark:text-primary-300">
            Rent &amp; Deposit Payments
          </Link>{" "}
          to view payment instructions and record a payment.
        </p>
      </div>
      <PaymentsHistory />
    </div>
  );
}
