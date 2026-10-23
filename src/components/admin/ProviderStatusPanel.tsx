"use client";

import { useEffect, useState } from "react";
import { CircleCheck, CircleAlert, PlugZap } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { apiClientFetch } from "@/lib/api-client";
import { formatDate } from "@/lib/utils";

interface ProviderStatus {
  healthy: boolean;
  updatedAt: string;
}

const PROVIDERS = {
  payments: {
    title: "Payment provider",
    path: "/api/finance/payment-provider/status",
    ok: "Payments are being sent to the provider as normal.",
    down: "Paused: new payments are not dispatched and stalled ones are marked failed instead of being retried against a weaker route.",
  },
  signatures: {
    title: "E-signature provider",
    path: "/api/leasing/signature-provider/status",
    ok: "Signature requests are being sent as normal.",
    down: "Paused: new signature requests are not sent and any left waiting past their deadline are marked failed.",
  },
} as const;

/** Read-only provider health for the super admin. The platform switches itself
 *  into a safe mode when a provider is down and back out when it recovers --
 *  there is nothing to approve or toggle from this screen. */
export function ProviderStatusPanel({ provider }: { provider: keyof typeof PROVIDERS }) {
  const cfg = PROVIDERS[provider];
  const [status, setStatus] = useState<ProviderStatus | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let live = true;
    apiClientFetch<ProviderStatus>(cfg.path)
      .then((s) => live && setStatus(s))
      .catch(() => live && setError(true));
    return () => {
      live = false;
    };
  }, [cfg.path]);

  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <PlugZap className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
          <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">{cfg.title}</h2>
        </div>
        {status && (
          <Badge tone={status.healthy ? "success" : "danger"}>{status.healthy ? "Operational" : "Paused"}</Badge>
        )}
      </div>
      {error ? (
        <p className="mt-3 text-sm text-accent-600">Could not load provider status.</p>
      ) : status === null ? (
        <p className="mt-3 text-sm text-slate-400">Loading…</p>
      ) : (
        <div className="mt-3 flex items-start gap-2 text-sm text-slate-600 dark:text-slate-300">
          {status.healthy ? (
            <CircleCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
          ) : (
            <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-accent-600" />
          )}
          <div>
            <p>{status.healthy ? cfg.ok : cfg.down}</p>
            <p className="mt-1 text-xs text-slate-400">Status last changed {formatDate(status.updatedAt)}</p>
          </div>
        </div>
      )}
    </section>
  );
}
