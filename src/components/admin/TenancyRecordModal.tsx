"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Modal } from "@/components/ui/Modal";
import { apiClientFetch } from "@/lib/api-client";
import { formatDate } from "@/lib/utils";

interface Timeline {
  handoverEvents: Array<{ id: number; eventType: string; actorKind: string; notes: string; createdAt: string }>;
  activationDecisions: Array<{ id: number; outcome: string; reasonCodes: string[]; trigger: string; evaluatedAt: string }>;
}
interface ConditionItem { id: number; reportType: string; area: string; conditionRating: string | null; notes: string; createdAt: string }
interface Incident { id: number; severity: string; description: string; status: string; openedAt: string; resolvedAt: string | null; creditedAmount: number }
interface CoTenant { id: number; guestId: string; addedAt: string }
interface EntryVisit { id: number; purpose: string; scheduledAt: string; isEmergency: boolean; status: string }
interface TerminationRecord {
  basis: string;
  noticeGivenAt: string | null;
  liabilityEndDate: string | null;
  terminationEffectiveDate: string | null;
  physicalMoveOutDate: string | null;
}

interface TenancyRecord {
  timeline: Timeline | null;
  condition: ConditionItem[];
  incidents: Incident[];
  coTenants: CoTenant[];
  visits: EntryVisit[];
  termination: TerminationRecord | null;
}

const label = (v: string) => v.replace(/_/g, " ").toLowerCase();
const heading = "text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400";
const line = "text-xs text-slate-600 dark:text-slate-300";
const none = <p className="text-xs text-slate-400">None recorded.</p>;

/** Read-only history of one tenancy -- the handover timeline, move-in/out
 *  condition report, habitability incidents, co-tenants, host entry visits and
 *  termination record. Hosts and renters create all of it; the admin only looks. */
export function TenancyRecordModal({ occupancyId, onClose }: { occupancyId: number | null; onClose: () => void }) {
  const [rec, setRec] = useState<TenancyRecord | null>(null);

  useEffect(() => {
    if (occupancyId === null) return;
    let live = true;
    setRec(null);
    const base = `/api/occupancy/${occupancyId}`;
    const get = <T,>(path: string, fallback: T) => apiClientFetch<T>(`${base}/${path}`).catch(() => fallback);
    Promise.all([
      get<Timeline | null>("timeline", null),
      get<ConditionItem[]>("condition-report", []),
      get<Incident[]>("habitability-incidents", []),
      get<CoTenant[]>("co-tenants", []),
      get<EntryVisit[]>("entry-visits", []),
      get<TerminationRecord | null>("termination-record", null),
    ]).then(([timeline, condition, incidents, coTenants, visits, termination]) => {
      if (live) setRec({ timeline, condition, incidents, coTenants, visits, termination });
    });
    return () => {
      live = false;
    };
  }, [occupancyId]);

  const timelineEmpty =
    !rec?.timeline || (rec.timeline.handoverEvents.length === 0 && rec.timeline.activationDecisions.length === 0);

  return (
    <Modal open={occupancyId !== null} onClose={onClose} title={`Tenancy record${occupancyId ? ` #${occupancyId}` : ""}`}>
      <div className="space-y-4">
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Read-only. The host and renter keep this record between themselves; nothing here needs a review.
        </p>
        {rec === null ? (
          <p className="text-sm text-slate-400">Loading…</p>
        ) : (
          <>
            <div className="space-y-1">
              <p className={heading}>Move-in timeline</p>
              {timelineEmpty ? none : (
                <>
                  {rec.timeline!.handoverEvents.map((e) => (
                    <p key={`h${e.id}`} className={line}>
                      <span className="text-slate-400">{formatDate(e.createdAt)}</span> · {label(e.eventType)} · by {e.actorKind}
                      {e.notes ? ` — ${e.notes}` : ""}
                    </p>
                  ))}
                  {rec.timeline!.activationDecisions.map((d) => (
                    <p key={`d${d.id}`} className={line}>
                      <span className="text-slate-400">{formatDate(d.evaluatedAt)}</span> · automatic check {label(d.outcome)}
                      {d.reasonCodes.length ? ` (${d.reasonCodes.map(label).join(", ")})` : ""}
                    </p>
                  ))}
                </>
              )}
            </div>
            <div className="space-y-1">
              <p className={heading}>Condition report</p>
              {rec.condition.length === 0 ? none : rec.condition.map((c) => (
                <p key={c.id} className={line}>
                  <span className="text-slate-400">{label(c.reportType)}:</span> {c.area}
                  {c.conditionRating ? ` · ${label(c.conditionRating)}` : ""}{c.notes ? ` — ${c.notes}` : ""}
                </p>
              ))}
            </div>
            <div className="space-y-1">
              <p className={heading}>Habitability incidents</p>
              {rec.incidents.length === 0 ? none : rec.incidents.map((i) => (
                <div key={i.id} className="flex flex-wrap items-center gap-2">
                  <Badge tone={i.status === "RESOLVED" ? "success" : "warning"}>{label(i.status)}</Badge>
                  <p className={line}>
                    {label(i.severity)} · {i.description} · opened {formatDate(i.openedAt)}
                    {i.resolvedAt ? ` · resolved ${formatDate(i.resolvedAt)}` : ""}
                    {i.creditedAmount ? ` · credited ${i.creditedAmount}` : ""}
                  </p>
                </div>
              ))}
            </div>
            <div className="space-y-1">
              <p className={heading}>Co-tenants</p>
              {rec.coTenants.length === 0 ? none : rec.coTenants.map((t) => (
                <p key={t.id} className={line}>{t.guestId} · added {formatDate(t.addedAt)}</p>
              ))}
            </div>
            <div className="space-y-1">
              <p className={heading}>Host entry visits</p>
              {rec.visits.length === 0 ? none : rec.visits.map((v) => (
                <p key={v.id} className={line}>
                  <span className="text-slate-400">{formatDate(v.scheduledAt)}</span> · {v.purpose} · {label(v.status)}
                  {v.isEmergency ? " · emergency" : ""}
                </p>
              ))}
            </div>
            <div className="space-y-1">
              <p className={heading}>Termination record</p>
              {!rec.termination ? none : (
                <p className={line}>
                  {label(rec.termination.basis)}
                  {rec.termination.noticeGivenAt ? ` · notice ${formatDate(rec.termination.noticeGivenAt)}` : ""}
                  {rec.termination.terminationEffectiveDate ? ` · effective ${formatDate(rec.termination.terminationEffectiveDate)}` : ""}
                  {rec.termination.physicalMoveOutDate ? ` · moved out ${formatDate(rec.termination.physicalMoveOutDate)}` : ""}
                </p>
              )}
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
