import { IdentityVerificationManager } from "@/components/user/IdentityVerificationManager";
import { VerificationStatusSummary } from "@/components/user/VerificationStatusSummary";

export default function IdentityPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">
          Identity verification
        </h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Verify who you are with an Aadhaar, Passport or Driving License. Renting and hosting both depend on it.
        </p>
      </div>
      <VerificationStatusSummary />
      <IdentityVerificationManager />
    </div>
  );
}
