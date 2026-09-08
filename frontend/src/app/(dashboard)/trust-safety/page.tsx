import { IdentityVerificationsManager } from "@/components/admin/IdentityVerificationsManager";
import { TrustSafetyManager } from "@/components/admin/TrustSafetyManager";
import { requireSuperAdmin } from "@/lib/api";

export default async function AdminTrustSafetyPage() {
  await requireSuperAdmin();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Trust &amp; Safety</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Manage market releases, verify provider authority, resolve occupancy classification, and review USER identity verifications — the checks every listing and account must clear.
        </p>
      </div>
      <IdentityVerificationsManager />
      <TrustSafetyManager />
    </div>
  );
}
