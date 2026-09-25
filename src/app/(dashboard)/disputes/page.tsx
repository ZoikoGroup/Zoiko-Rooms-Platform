import { DisputeResolutionManager } from "@/components/admin/DisputeResolutionManager";

export default function AdminDisputesPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Disputes</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Review dispute cases, decide claims, manage evidence and financial holds, and track deadlines through to resolution.
        </p>
      </div>
      <DisputeResolutionManager />
    </div>
  );
}
