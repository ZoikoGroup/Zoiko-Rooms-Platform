import { HostApplicationsManager } from "@/components/user/HostApplicationsManager";

export default function HostApplicationsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Applications</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Review renters who applied to your listings and approve or reject them.
        </p>
      </div>
      <HostApplicationsManager />
    </div>
  );
}
