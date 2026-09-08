import { ApplicationsManager } from "@/components/user/ApplicationsManager";

export default function ApplicationsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">My applications</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Every rental application you have submitted, and where each one stands.
        </p>
      </div>
      <ApplicationsManager />
    </div>
  );
}
