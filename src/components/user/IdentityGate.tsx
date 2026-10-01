"use client";

import Link from "next/link";
import { AlertTriangle, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { useUserSession } from "@/components/user/UserSessionContext";

const BODY: Record<string, string> = {
  NOT_STARTED: "Verify your identity once with an accepted identity document -- it's reused for every listing and booking.",
  IN_PROGRESS: "You've started verifying your identity. Continue where you left off.",
  PROCESSING: "We're checking your identity information. We'll update your status when it's done.",
  PENDING_REVIEW: "We need to review your verification. You can leave this page; we'll update your status.",
  ACTION_REQUIRED: "We need one more step to complete your identity verification.",
  REVERIFICATION_REQUIRED: "We need to verify your identity again before this action is available.",
  FAILED: "We couldn't verify your identity. You can try another verification option.",
};

/**
 * Renders `children` only when the server reports a verified identity.
 * Otherwise explains what is blocking and links to the verification page.
 * Presentation only -- every gated backend action re-checks identity itself
 * (ZR-IDENTITY-001 Section 8.4).
 */
export function IdentityGate({
  action = "this action",
  children,
}: {
  action?: string;
  children: React.ReactNode;
}) {
  const { identityVerified, identityProfile, loading } = useUserSession();

  if (loading) {
    return (
      <div className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        <p className="text-sm text-slate-400" role="status">Checking your verification status...</p>
      </div>
    );
  }

  if (identityVerified) return <>{children}</>;

  const state = identityProfile?.state ?? "NOT_STARTED";
  const header = identityProfile?.dashboard.header ?? "Identity not verified";
  const cta = identityProfile?.dashboard.primaryAction ?? "Verify identity";
  const detail = identityProfile?.dashboard.message || BODY[state] || BODY.NOT_STARTED;

  return (
    <div className="flex flex-col gap-4 rounded-2xl bg-amber-50 p-5 ring-1 ring-amber-200 sm:flex-row sm:items-center sm:justify-between dark:bg-amber-500/10 dark:ring-amber-500/20">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" aria-hidden="true" />
        <div>
          <p className="text-sm font-semibold text-amber-800 dark:text-amber-300">{header}</p>
          <p className="mt-0.5 text-xs text-amber-700 dark:text-amber-400">
            {detail} You need a verified identity to {action}.
          </p>
        </div>
      </div>
      <Link href="/account/identity" className="shrink-0">
        <Button size="sm" variant="primary">
          <ShieldCheck className="h-4 w-4" aria-hidden="true" /> {cta}
        </Button>
      </Link>
    </div>
  );
}
