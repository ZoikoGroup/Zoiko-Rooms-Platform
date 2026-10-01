import { Suspense } from "react";
import { IdentityVerificationWizard } from "@/components/user/IdentityVerificationWizard";
import { VerificationStatusSummary } from "@/components/user/VerificationStatusSummary";

export default function IdentityPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">
          Identity verification
        </h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Verify who you are once -- it&apos;s reused for every property you list or rent. This is separate from
          verifying a property or your authority to list it.
        </p>
      </div>
      {/* useSearchParams (the phone-handoff link) needs a Suspense boundary. */}
      <Suspense fallback={<p className="text-sm text-slate-400">Loading your identity status...</p>}>
        <IdentityVerificationWizard />
      </Suspense>
      <VerificationStatusSummary />
    </div>
  );
}
