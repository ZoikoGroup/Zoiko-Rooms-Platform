import { RentalsManager } from "@/components/user/RentalsManager";

export default function RentalsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">My rentals</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Rooms you currently occupy or have occupied, and the sublet requests you can raise on them.
        </p>
      </div>
      <RentalsManager />
    </div>
  );
}
