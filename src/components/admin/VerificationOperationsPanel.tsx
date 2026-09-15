"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { apiClientFetch } from "@/lib/api-client";

interface Metrics {
  occupancyEligibilityPendingCount: number;
  occupancyEligibilityAvgTurnaroundSeconds: number | null;
  screeningPendingCount: number;
  screeningAvgTurnaroundSeconds: number | null;
  propertyCredentialsExpiringWithin30Days: number;
  evidenceArtifactsNotYetSweptForRetention: number;
  breakGlassGrantsLast30Days: number;
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  const hours = seconds / 3600;
  if (hours < 1) return `${Math.round(seconds / 60)} min`;
  if (hours < 24) return `${hours.toFixed(1)} hrs`;
  return `${(hours / 24).toFixed(1)} days`;
}

/** ZR-ENG-CLR-012 Section 31: on-demand operational visibility -- no
 * scheduler/metrics infra exists in this stack, so these are computed live
 * from the same tables the managers above already read, and the two sweeps
 * (retention deletion, follow-up notifications) are manual substitutes for
 * a cron tick an admin can trigger here. */
export function VerificationOperationsPanel() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [sweepingRetention, setSweepingRetention] = useState(false);
  const [sweepingFollowUps, setSweepingFollowUps] = useState(false);
  const [toast, setToast] = useState("");

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3200);
  }

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiClientFetch<Metrics>("/api/verification/operational-metrics");
      setMetrics(data);
    } catch {
      showToast("Failed to load operational metrics");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function sweepRetention() {
    setSweepingRetention(true);
    try {
      const result = await apiClientFetch<{ deletedCount: number }>("/api/verification/evidence-artifacts/sweep-retention", { method: "POST" });
      showToast(`Retention sweep: ${result.deletedCount} artifact(s) deleted.`);
      await load();
    } catch {
      showToast("Retention sweep failed");
    } finally {
      setSweepingRetention(false);
    }
  }

  async function sweepFollowUps() {
    setSweepingFollowUps(true);
    try {
      const result = await apiClientFetch<{ notifiedCount: number }>("/api/verification/occupancy-eligibility-checks/sweep-follow-ups", { method: "POST" });
      showToast(`Follow-up sweep: ${result.notifiedCount} notification(s) sent.`);
      await load();
    } catch {
      showToast("Follow-up sweep failed");
    } finally {
      setSweepingFollowUps(false);
    }
  }

  const stat = (label: string, value: string | number) => (
    <div className="rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
      <p className="text-xs text-slate-500 dark:text-slate-400">{label}</p>
      <p className="mt-0.5 text-lg font-bold text-primary-900 dark:text-white">{value}</p>
    </div>
  );

  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex items-center gap-2">
        <Activity className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
        <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Verification Operations</h2>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        No scheduler exists in this stack — these are on-demand sweeps, the manual substitute for a cron tick.
      </p>

      {loading ? (
        <p className="mt-4 text-sm text-slate-400">Loading...</p>
      ) : metrics ? (
        <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3">
          {stat("Occupancy eligibility pending", metrics.occupancyEligibilityPendingCount)}
          {stat("Occupancy avg turnaround", formatDuration(metrics.occupancyEligibilityAvgTurnaroundSeconds))}
          {stat("Screening pending", metrics.screeningPendingCount)}
          {stat("Screening avg turnaround", formatDuration(metrics.screeningAvgTurnaroundSeconds))}
          {stat("Property credentials expiring <30d", metrics.propertyCredentialsExpiringWithin30Days)}
          {stat("Evidence artifacts not yet swept", metrics.evidenceArtifactsNotYetSweptForRetention)}
          {stat("Break-glass grants (30d)", metrics.breakGlassGrantsLast30Days)}
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-2">
        <Button size="sm" variant="outline" loading={sweepingRetention} onClick={sweepRetention}>
          Sweep expired evidence
        </Button>
        <Button size="sm" variant="outline" loading={sweepingFollowUps} onClick={sweepFollowUps}>
          Sweep follow-up reminders
        </Button>
      </div>

      {toast && (
        <div className="animate-fade-up fixed bottom-6 right-6 z-[300] flex max-w-sm items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" /> {toast}
        </div>
      )}
    </section>
  );
}
