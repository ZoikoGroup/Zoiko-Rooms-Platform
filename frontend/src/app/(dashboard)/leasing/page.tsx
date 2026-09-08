import { LeasingManager } from "@/components/admin/LeasingManager";

export default function AdminLeasingPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Leasing</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Review applications, negotiate offer terms, and get agreements signed before move-in.
        </p>
      </div>
      <LeasingManager />
    </div>
  );
}
