import { AgreementAmendmentsManager } from "@/components/admin/AgreementAmendmentsManager";
import { AutomationMetricsPanel } from "@/components/admin/AutomationMetricsPanel";
import { ProviderStatusPanel } from "@/components/admin/ProviderStatusPanel";
import { LeasingManager } from "@/components/admin/LeasingManager";

export default function AdminLeasingPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Leasing</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Applications, offers, agreements and amendments across the platform. Read-only — hosts and renters agree these
          between themselves and the platform automates the rest.
        </p>
      </div>
      <LeasingManager />
      <AgreementAmendmentsManager />
      <AutomationMetricsPanel source="leasing" />
      <ProviderStatusPanel provider="signatures" />
    </div>
  );
}
