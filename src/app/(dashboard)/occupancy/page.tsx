import { Suspense } from "react";
import { BookingChangeRequestsManager } from "@/components/admin/BookingChangeRequestsManager";
import { OccupancyManager } from "@/components/admin/OccupancyManager";
import { SubletRequestsManager } from "@/components/admin/SubletRequestsManager";

export default function AdminOccupancyPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Occupancy</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Tenancies, change requests and sublets across the platform. Read-only — hosts and renters manage these
          between themselves.
        </p>
      </div>
      {/* OccupancyManager reads ?q= from the URL, so it needs a Suspense boundary on a prerendered page. */}
      <Suspense fallback={null}>
        <OccupancyManager />
      </Suspense>
      <BookingChangeRequestsManager />
      <SubletRequestsManager />
    </div>
  );
}
