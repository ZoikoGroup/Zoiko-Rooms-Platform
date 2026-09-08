import { PropertiesManager } from "@/components/admin/PropertiesManager";
import { apiFetch } from "@/lib/api";
import { Listing } from "@/lib/types";

export default async function AdminPropertiesPage() {
  const listings = await apiFetch<Listing[]>("/api/listings");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Properties &amp; Rooms</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Manage private-room listings, verify authority and occupancy evidence, and publish to the marketplace.
        </p>
      </div>
      <PropertiesManager initialListings={listings} />
    </div>
  );
}
