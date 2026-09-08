import { PaymentsHistory } from "@/components/user/PaymentsHistory";

export default function PaymentsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Payments</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Every payment recorded against your account, and what each one was allocated to.
        </p>
      </div>
      <PaymentsHistory />
    </div>
  );
}
