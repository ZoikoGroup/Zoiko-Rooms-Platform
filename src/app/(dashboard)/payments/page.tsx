import { PaymentsOverview } from "@/components/admin/PaymentsOverview";
import { requireSuperAdmin } from "@/lib/api";

export default async function AdminPaymentsPage() {
  await requireSuperAdmin();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Payments</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Zoiko&apos;s own Listing Fee revenue, and the rent records between hosts and renters -- which is paid to hosts
          directly and never held by Zoiko.
        </p>
      </div>
      <PaymentsOverview />
    </div>
  );
}
