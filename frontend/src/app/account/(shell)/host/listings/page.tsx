import { HostingListingsManager } from "@/components/user/HostingListingsManager";

export default function HostListingsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">My listings</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Draft a listing from one of your rooms and publish it once every compliance check passes.
        </p>
      </div>
      <HostingListingsManager />
    </div>
  );
}
