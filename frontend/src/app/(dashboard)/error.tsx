"use client";

import { useEffect } from "react";
import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/Button";

/** Catches errors thrown while rendering any admin page (e.g. a transient
 *  backend outage during a super-admin-only page's server-side auth check --
 *  see requireSuperAdmin in src/lib/api.ts). Without this, that kind of
 *  failure would either crash to Next's generic error screen or, before that
 *  fix, could be misread as an authorization failure. This lets the admin
 *  retry in place instead of losing the page they were on. */
export default function DashboardError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 p-8 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-accent-50 text-accent-600 dark:bg-accent-500/10">
        <AlertTriangle className="h-6 w-6" />
      </span>
      <div>
        <p className="font-heading text-base font-bold text-primary-900 dark:text-white">
          Something went wrong loading this page
        </p>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          This is usually temporary. Try again, or use the sidebar to navigate elsewhere.
        </p>
      </div>
      <Button onClick={() => unstable_retry()}>Try again</Button>
    </div>
  );
}
