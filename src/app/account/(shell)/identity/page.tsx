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
          Verify your identity using an accepted government-issued identity document. Available document types
          depend on your country or region.
        </p>
      </div>
      <VerificationStatusSummary />
      <IdentityVerificationManager />
    </div>
  );
}
