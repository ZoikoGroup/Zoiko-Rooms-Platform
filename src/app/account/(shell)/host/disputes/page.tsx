import { HostDisputesManager } from "@/components/user/HostDisputesManager";

export default function HostDisputesPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Disputes</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Claims raised against or by you on a tenancy. You can respond, share evidence, negotiate a settlement or
          escalate for review.
        </p>
      </div>
      <HostDisputesManager />
    </div>
  );
}
