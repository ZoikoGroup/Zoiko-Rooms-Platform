import { HostSubletRequestsManager } from "@/components/user/HostSubletRequestsManager";

export default function HostSubletRequestsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Sublet Requests</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Renters on your listings asking permission to hand their occupancy to someone else. You decide — Zoiko only
          routes and records it.
        </p>
      </div>
      <HostSubletRequestsManager />
    </div>
  );
}
