import { SubletRequestsList } from "@/components/user/SubletRequestsList";

export default function SubletsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Sublet requests</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Requests to hand one of your rentals over to another verified renter. Zoiko reviews each one.
        </p>
      </div>
      <SubletRequestsList />
    </div>
  );
}
