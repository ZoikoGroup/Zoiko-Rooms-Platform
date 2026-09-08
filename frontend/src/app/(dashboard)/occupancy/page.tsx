import { OccupancyManager } from "@/components/admin/OccupancyManager";

export default function AdminOccupancyPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Occupancy</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Track active tenancies and keep recurring rent obligations up to date.
        </p>
      </div>
      <OccupancyManager />
    </div>
  );
}
