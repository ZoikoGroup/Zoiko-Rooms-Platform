"use client";

import Link from "next/link";
import { AlertTriangle, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { useUserSession } from "@/components/user/UserSessionContext";

const copy: Record<string, { title: string; body: string; cta: string }> = {
  not_submitted: {
    title: "Identity verification required",
    body: "Submit an Aadhaar, Passport or Driving License document before you can continue.",
    cta: "Verify my identity",
  },
  pending: {
    title: "Your identity verification is still pending",
    body: "A Zoiko super admin has to approve your document before this action becomes available.",
    cta: "View verification status",
  },
  rejected: {
    title: "Your identity verification was rejected",
    body: "Submit a new document so your account can be verified again.",
    cta: "Submit a new document",
  },
  expired: {
    title: "Your identity verification has expired",
    body: "Submit a current document to restore access to this action.",
    cta: "Submit a new document",
  },
  additional_evidence_required: {
    title: "More evidence is needed",
    body: "A reviewer asked for additional evidence before your identity can be approved.",
    cta: "Submit more evidence",
  },
};

/**
 * Renders `children` only when the backend reports a verified identity for this user.
 * Otherwise it explains what is blocking and links to the verification page. Purely a
 * UX affordance -- the backend independently rejects unverified submissions with 403.
 */
export function IdentityGate({
  action = "this action",
  children,
}: {
  action?: string;
  children: React.ReactNode;
}) {
  const { identityVerified, identityStatus, loading } = useUserSession();

  if (loading) {
    return (
      <div className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        <p className="text-sm text-slate-400">Checking your verification status...</p>
      </div>
    );
  }

  if (identityVerified) return <>{children}</>;

  const message = copy[identityStatus] ?? copy.not_submitted;

  return (
    <div className="flex flex-col gap-4 rounded-2xl bg-amber-50 p-5 ring-1 ring-amber-200 sm:flex-row sm:items-center sm:justify-between dark:bg-amber-500/10 dark:ring-amber-500/20">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
        <div>
          <p className="text-sm font-semibold text-amber-800 dark:text-amber-300">{message.title}</p>
          <p className="mt-0.5 text-xs text-amber-700 dark:text-amber-400">
            {message.body} You need a verified identity to {action}.
          </p>
        </div>
      </div>
      <Link href="/account/identity" className="shrink-0">
        <Button size="sm" variant="primary">
          <ShieldCheck className="h-4 w-4" /> {message.cta}
        </Button>
      </Link>
    </div>
  );
}
