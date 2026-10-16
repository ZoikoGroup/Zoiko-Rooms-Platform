import { AdminSupportManager } from "@/components/admin/AdminSupportManager";

export default function AdminSupportPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Support</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Help & Support cases from hosts and renters. Time-sensitive cases are listed first.
        </p>
      </div>
      <AdminSupportManager />
    </div>
  );
}
