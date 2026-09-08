import { FinanceManager } from "@/components/admin/FinanceManager";

export default function AdminFinancePage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Finance</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Rent and deposit obligations, simulated payments, provider payouts, refunds, disputes, and reconciliation.
        </p>
      </div>
      <FinanceManager />
    </div>
  );
}
