"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { KeyRound } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { AuthorityVerificationWizard } from "@/components/user/AuthorityVerificationWizard";
import { Card, EmptyState, inputClass } from "@/components/user/ui";
import { errorMessage } from "@/lib/user-api";
import { formatDate } from "@/lib/utils";
import {
  AuthorityState, AuthoritySummary, authorityStateCta, authorityStateLabel, authorityStateTone, isAuthorityException,
  listMyAuthority, relationshipShort,
} from "@/lib/authority-verification";

type StatusFilter = "exceptions" | "all" | AuthorityState;
type ExpiryFilter = "any" | "30" | "90";

const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: "exceptions", label: "Needs attention" },
  { value: "all", label: "All statuses" },
  { value: "ACTION_REQUIRED", label: authorityStateLabel.ACTION_REQUIRED },
  { value: "EXPIRING_SOON", label: authorityStateLabel.EXPIRING_SOON },
  { value: "EXPIRED", label: authorityStateLabel.EXPIRED },
  { value: "REVOKED", label: authorityStateLabel.REVOKED },
  { value: "MANUAL_REVIEW", label: authorityStateLabel.MANUAL_REVIEW },
  { value: "VERIFIED", label: authorityStateLabel.VERIFIED },
];

export function filterAuthority(rows: AuthoritySummary[], status: StatusFilter, propertyId: number | "all",
  expiry: ExpiryFilter, now = Date.now()): AuthoritySummary[] {
  return rows.filter((r) => {
    if (status === "exceptions" ? !isAuthorityException(r.state) : status !== "all" && r.state !== status) return false;
    if (propertyId !== "all" && r.propertyId !== propertyId) return false;
    if (expiry !== "any") {
      if (!r.expiresAt) return false;
      if (new Date(r.expiresAt).getTime() - now > Number(expiry) * 86_400_000) return false;
    }
    return true;
  });
}

/**
 * ZR-AUTHORITY-002 Section 10.3 -- Verification Center, authority view.
 * Exception management only (filters: status, property, expiry); it is not
 * a second property dashboard, and healthy authority stays out of the
 * default view. Every action opens the server-driven authority flow.
 */
export function AuthorityExceptionCenter() {
  const [rows, setRows] = useState<AuthoritySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [status, setStatus] = useState<StatusFilter>("exceptions");
  const [propertyId, setPropertyId] = useState<number | "all">("all");
  const [expiry, setExpiry] = useState<ExpiryFilter>("any");
  const [open, setOpen] = useState<AuthoritySummary | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await listMyAuthority());
      setError("");
    } catch (err) {
      setError(errorMessage(err, "Authority status is unavailable right now."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const properties = useMemo(
    () => Array.from(new Map(rows.map((r) => [r.propertyId, r.propertyLabel])).entries()),
    [rows],
  );
  const visible = useMemo(() => filterAuthority(rows, status, propertyId, expiry), [rows, status, propertyId, expiry]);

  if (!loading && !error && rows.length === 0) return null; // not a lister -- nothing to manage

  return (
    <Card>
      <div className="flex items-center gap-2">
        <KeyRound className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" aria-hidden="true" />
        <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Authority to list</h2>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Your authority to advertise each property. Verified properties stay out of the way; change the filter to see them.
      </p>

      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        <select className={inputClass} value={status} aria-label="Filter by status"
                onChange={(e) => setStatus(e.target.value as StatusFilter)}>
          {STATUS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <select className={inputClass} value={String(propertyId)} aria-label="Filter by property"
                onChange={(e) => setPropertyId(e.target.value === "all" ? "all" : Number(e.target.value))}>
          <option value="all">All properties</option>
          {properties.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select>
        <select className={inputClass} value={expiry} aria-label="Filter by expiry"
                onChange={(e) => setExpiry(e.target.value as ExpiryFilter)}>
          <option value="any">Any expiry</option>
          <option value="30">Expires within 30 days</option>
          <option value="90">Expires within 90 days</option>
        </select>
      </div>

      <div className="mt-3" aria-live="polite">
        {loading ? <p className="py-4 text-sm text-slate-400" role="status">Loading...</p>
          : error ? <p className="py-4 text-sm text-accent-700" role="alert">{error}</p>
          : visible.length === 0 ? <EmptyState message={status === "exceptions" ? "Nothing needs your attention." : "No properties match these filters."} />
          : (
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Authority to list, by property</caption>
              <thead className="hidden text-xs text-slate-400 sm:table-header-group">
                <tr>
                  <th scope="col" className="py-2 font-medium">Property</th>
                  <th scope="col" className="py-2 font-medium">Relationship</th>
                  <th scope="col" className="py-2 font-medium">Status</th>
                  <th scope="col" className="py-2 font-medium">Valid until</th>
                  <th scope="col" className="py-2 font-medium"><span className="sr-only">Action</span></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-white/10">
                {visible.map((r) => {
                  const cta = authorityStateCta[r.state];
                  return (
                    <tr key={r.id} className="grid grid-cols-2 gap-x-3 gap-y-1 py-3 sm:table-row">
                      <td className="col-span-2 font-semibold text-primary-900 dark:text-white sm:py-3">
                        {r.propertyLabel}<span className="block text-xs font-normal text-slate-500">{r.propertyCity}</span>
                      </td>
                      <td className="text-xs text-slate-600 dark:text-slate-300 sm:py-3">{relationshipShort[r.relationshipType]}</td>
                      <td className="sm:py-3">
                        <Badge tone={authorityStateTone(r.state)} dot>{authorityStateLabel[r.state]}</Badge>
                        {r.reason.message && isAuthorityException(r.state) && (
                          <span className="mt-1 block text-xs text-slate-500">{r.reason.message}</span>
                        )}
                      </td>
                      <td className="text-xs text-slate-600 dark:text-slate-300 sm:py-3">
                        <span className="sm:hidden">Valid until: </span>{r.expiresAt ? formatDate(r.expiresAt) : "--"}
                      </td>
                      <td className="text-right sm:py-3">
                        {cta && <Button size="sm" variant="outline" onClick={() => setOpen(r)}>{cta}</Button>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
      </div>

      {open && (
        <Modal open onClose={() => { setOpen(null); void load(); }} title="Authority to list" size="xl">
          <AuthorityVerificationWizard propertyId={open.propertyId} propertyLabel={`${open.propertyLabel} · Property #${open.propertyId}`}
                                       onClose={() => { setOpen(null); void load(); }} />
        </Modal>
      )}
    </Card>
  );
}
