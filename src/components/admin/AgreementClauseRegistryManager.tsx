"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, FileSignature } from "lucide-react";
import { ClauseDefinition, MarketRelease } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { apiClientFetch } from "@/lib/api-client";

const STATUS_TONE = {
  APPROVED: "success",
  DRAFT: "warning",
  RETIRED: "neutral",
} as const;

// Must match backend/app/services/agreement_profile.py:SUPPORTED_JURISDICTION,
// the one region whose default clauses are seeded automatically.
const AUTO_SEEDED_REGION = "England";

/**
 * Agreements in a region can only be generated once that region has its own
 * approved clause registry (services/agreement_profile.py). England's is
 * seeded automatically; any other region starts from a copy of the default
 * clauses as drafts, which an admin reviews and approves here.
 */
export function AgreementClauseRegistryManager() {
  const [regions, setRegions] = useState<string[]>([]);
  const [region, setRegion] = useState("");
  const [clauses, setClauses] = useState<ClauseDefinition[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState("");

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3400);
  }

  useEffect(() => {
    apiClientFetch<MarketRelease[]>("/api/market-releases")
      .then((releases) => {
        const codes = releases.map((r) => r.jurisdiction).sort();
        setRegions(codes);
        setRegion((current) => current || codes[0] || "");
      })
      .catch(() => showToast("Failed to load market releases"));
  }, []);

  const load = useCallback(async () => {
    if (!region) return;
    setLoading(true);
    try {
      setClauses(
        await apiClientFetch<ClauseDefinition[]>(
          `/api/leasing/agreement-clauses?jurisdiction_scope=${encodeURIComponent(region)}`
        )
      );
    } catch {
      showToast("Failed to load agreement clauses");
    } finally {
      setLoading(false);
    }
  }, [region]);

  useEffect(() => {
    load();
  }, [load]);

  async function copyDefaults() {
    setBusy("copy");
    try {
      const created = await apiClientFetch<ClauseDefinition[]>("/api/leasing/agreement-clauses/copy-defaults", {
        method: "POST",
        body: JSON.stringify({ jurisdictionScope: region }),
      });
      showToast(
        created.length ? `${created.length} draft clause(s) added — review and approve each one.` : "Nothing to copy."
      );
      await load();
    } catch (err) {
      showToast(err instanceof Error && err.message ? err.message : "Failed to copy default clauses");
    } finally {
      setBusy(null);
    }
  }

  async function approve(clause: ClauseDefinition) {
    setBusy(`approve:${clause.id}`);
    try {
      await apiClientFetch<ClauseDefinition>(`/api/leasing/agreement-clauses/${clause.id}/approve`, { method: "POST" });
      showToast(`Approved ${clause.clauseId} v${clause.version}.`);
      await load();
    } catch (err) {
      showToast(err instanceof Error && err.message ? err.message : "Failed to approve clause");
    } finally {
      setBusy(null);
    }
  }

  const drafts = clauses.filter((c) => c.status === "DRAFT");
  const hasApproved = clauses.some((c) => c.status === "APPROVED");

  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <FileSignature className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
          <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Agreement Clauses by Region</h2>
        </div>
        <select
          value={region}
          onChange={(e) => setRegion(e.target.value)}
          className="rounded-xl bg-slate-50 px-3 py-2 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
        >
          {regions.length === 0 && <option value="">No market releases yet</option>}
          {regions.map((code) => (
            <option key={code} value={code}>
              {code}
            </option>
          ))}
        </select>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Rental agreements in a region are generated only once every mandatory clause is approved for that region —
        until then they&apos;re routed to manual review. {AUTO_SEEDED_REGION}&apos;s clauses are created automatically;
        other regions start from a copy of the defaults. The wording is placeholder, not legal sign-off.
      </p>

      <div className="mt-4 space-y-2">
        {!region ? (
          <p className="text-sm text-slate-400">Create a market release to manage its clauses.</p>
        ) : loading ? (
          <p className="text-sm text-slate-400">Loading...</p>
        ) : clauses.length === 0 ? (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10">
            <p className="text-sm text-slate-500 dark:text-slate-400">
              {region === AUTO_SEEDED_REGION
                ? "Default clauses are created the first time an agreement is prepared."
                : `No clauses for ${region} yet — agreements here are routed to manual review.`}
            </p>
            {region !== AUTO_SEEDED_REGION && (
              <Button size="sm" variant="primary" loading={busy === "copy"} onClick={copyDefaults}>
                Copy default clauses
              </Button>
            )}
          </div>
        ) : (
          <>
            {!hasApproved && drafts.length > 0 && (
              <p className="text-xs font-medium text-amber-700 dark:text-amber-300">
                {drafts.length} draft clause(s) waiting for approval.
              </p>
            )}
            {clauses.map((clause) => (
              <div
                key={clause.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10"
              >
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-primary-900 dark:text-white">
                    {clause.title || clause.clauseId}{" "}
                    <span className="text-xs font-normal text-slate-400">
                      {clause.clauseId} · v{clause.version} · {clause.mandatoryLevel}
                    </span>
                  </p>
                  {clause.approvalNote && <p className="mt-0.5 text-xs text-slate-400">{clause.approvalNote}</p>}
                </div>
                <div className="flex items-center gap-2">
                  <Badge tone={STATUS_TONE[clause.status as keyof typeof STATUS_TONE] ?? "neutral"}>{clause.status}</Badge>
                  {clause.status === "DRAFT" && (
                    <Button size="sm" variant="outline" loading={busy === `approve:${clause.id}`} onClick={() => approve(clause)}>
                      Approve
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </>
        )}
      </div>

      {toast && (
        <div className="animate-fade-up fixed bottom-6 right-6 z-[300] flex max-w-sm items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" /> {toast}
        </div>
      )}
    </section>
  );
}
