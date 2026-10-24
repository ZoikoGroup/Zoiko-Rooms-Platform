"use client";

import { useEffect, useMemo, useState } from "react";
import { ChevronDown, FileEdit, Search } from "lucide-react";
import { AgreementAmendment, AmendmentStatus } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { apiClientFetch } from "@/lib/api-client";
import { amendmentStatusLabel, amendmentStatusTone, amendmentTypeLabel } from "@/lib/status";
import { formatDate } from "@/lib/utils";

/** One amendment with enough context to recognise it -- see
 *  backend GET /api/leasing/amendments. */
interface AmendmentOverview extends AgreementAmendment {
  listingName: string;
  renterName: string;
  hostName: string;
  agreementStatus: string;
}

/** The agreement reference printed on agreement documents, e.g. ZR-AGR-00000006
 *  (backend services/agreement_document/context.py). */
function agreementRef(id: number): string {
  return `ZR-AGR-${String(id).padStart(8, "0")}`;
}

/** An agreement number typed any way it appears: "6", "#6", "ZR-AGR-6" or
 *  "ZR-AGR-00000006" (ZR-66). Null when the query isn't an agreement number. */
function parseAgreementNumber(query: string): number | null {
  const m = query.trim().match(/^(?:zr-agr-|#)?\s*0*(\d+)$/i);
  return m ? Number(m[1]) : null;
}

const TIMELINE: Array<[keyof AgreementAmendment, string]> = [
  ["createdAt", "Requested"],
  ["classifiedAt", "Classified"],
  ["termsProposedAt", "Terms proposed"],
  ["approvalsPendingAt", "Awaiting approvals"],
  ["generatedAt", "New version generated"],
  ["executionPendingAt", "Awaiting signatures"],
  ["executedAt", "Signed"],
  ["effectiveAt", "Effective"],
];

/** Read-only shape of the agreement-level records fetched when a row opens. */
interface AgreementRecordData {
  parties: Array<{ id: number; role: string; legalName: string; contactEmail: string; consentMethod: string; consentedAt: string | null }>;
  requests: Array<{ id: number; partyRole: string; method: string; status: string; deadline: string | null; completedAt: string | null }>;
  events: Array<{ id: number; signerRole: string; signerIdentifier: string; method: string; consentedAt: string }>;
  holds: Array<{ id: number; status: string; reason: string; placedAt: string; releasedAt: string | null }>;
}

/** The agreement behind an amendment -- who is on it, how signing is going and
 *  any legal hold. Pure oversight: every call here is a GET. */
function AgreementRecord({ agreementId }: { agreementId: number }) {
  const [data, setData] = useState<AgreementRecordData | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    let live = true;
    setData(null);
    setMissing(false);
    const base = `/api/leasing/agreements/${agreementId}`;
    const get = <T,>(path: string) => apiClientFetch<T>(`${base}/${path}`).catch(() => [] as unknown as T);
    // Parties is fetched first on its own: it is a 404 only when the agreement itself does not exist.
    apiClientFetch(`${base}/parties`).catch(() => {
      if (live) setMissing(true);
    });
    Promise.all([
      get<AgreementRecordData["parties"]>("parties"),
      get<AgreementRecordData["requests"]>("signature-requests"),
      get<AgreementRecordData["events"]>("signature-events"),
      get<AgreementRecordData["holds"]>("legal-holds"),
    ]).then(([parties, requests, events, holds]) => {
      if (live) setData({ parties, requests, events, holds });
    });
    return () => {
      live = false;
    };
  }, [agreementId]);

  const heading = "font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400";
  const line = "text-slate-600 dark:text-slate-300";
  const none = <p className="text-slate-400">None recorded.</p>;

  return (
    <div className="border-t border-slate-200 p-3 text-xs dark:border-slate-700">
      <p className={heading}>Agreement record · {agreementRef(agreementId)}</p>
      {missing ? (
        <p className="mt-2 text-slate-400">No agreement with this number exists.</p>
      ) : data === null ? (
        <p className="mt-2 text-slate-400">Loading…</p>
      ) : (
        <div className="mt-2 grid gap-4 sm:grid-cols-2">
          <div className="space-y-1">
            <p className={heading}>Parties</p>
            {data.parties.length === 0 ? none : data.parties.map((p) => (
              <p key={p.id} className={line}>
                <span className="text-slate-400">{p.role.replace(/_/g, " ")}:</span> {p.legalName}
                {p.consentedAt ? ` · consent ${formatDate(p.consentedAt)}` : ""}
              </p>
            ))}
          </div>
          <div className="space-y-1">
            <p className={heading}>Signature requests</p>
            {data.requests.length === 0 ? none : data.requests.map((r) => (
              <p key={r.id} className={line}>
                <span className="text-slate-400">{r.partyRole.replace(/_/g, " ")}:</span> {r.status.replace(/_/g, " ").toLowerCase()}
                {r.completedAt ? ` · ${formatDate(r.completedAt)}` : r.deadline ? ` · due ${formatDate(r.deadline)}` : ""}
              </p>
            ))}
          </div>
          <div className="space-y-1">
            <p className={heading}>Signatures recorded</p>
            {data.events.length === 0 ? none : data.events.map((e) => (
              <p key={e.id} className={line}>
                <span className="text-slate-400">{e.signerRole.replace(/_/g, " ")}:</span> {e.signerIdentifier} · {e.method.replace(/_/g, " ").toLowerCase()} · {formatDate(e.consentedAt)}
              </p>
            ))}
          </div>
          <div className="space-y-1">
            <p className={heading}>Legal holds</p>
            {data.holds.length === 0 ? none : data.holds.map((h) => (
              <p key={h.id} className={line}>
                <span className="text-slate-400">{h.status.toLowerCase()}:</span> {h.reason} · placed {formatDate(h.placedAt)}
                {h.releasedAt ? ` · released ${formatDate(h.releasedAt)}` : ""}
              </p>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** ZR-44: the super admin's read-only view of every agreement amendment.
 *  Amendments are agreed between the host and the renter; the admin can see
 *  them all but takes no review or approval action here. */
export function AgreementAmendmentsManager() {
  const [amendments, setAmendments] = useState<AmendmentOverview[] | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<AmendmentStatus | "all">("all");
  const [openId, setOpenId] = useState<number | null>(null);

  useEffect(() => {
    apiClientFetch<AmendmentOverview[]>("/api/leasing/amendments")
      .then(setAmendments)
      .catch(() => {
        setAmendments([]);
        setError("Could not load amendments.");
      });
  }, []);

  const statuses = useMemo(
    () => Array.from(new Set((amendments ?? []).map((a) => a.status))),
    [amendments],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const agreementNumber = parseAgreementNumber(q);
    return (amendments ?? []).filter((a) => {
      if (statusFilter !== "all" && a.status !== statusFilter) return false;
      if (!q) return true;
      // An agreement number finds exactly that agreement (not 1 -> 10, 11, ...).
      if (agreementNumber !== null) return a.agreementId === agreementNumber;
      return [a.listingName, a.renterName, a.hostName, a.reason, agreementRef(a.agreementId)]
        .some((v) => v.toLowerCase().includes(q));
    });
  }, [amendments, query, statusFilter]);

  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex items-center gap-2">
        <FileEdit className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
        <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Agreement Amendments</h2>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Changes to signed agreements — rent, dates, term or an added guarantor. Hosts and renters agree these between
        themselves; this view is read-only.
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <div className="relative min-w-50 flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by room, renter, host, reason or agreement (e.g. ZR-AGR-00000006)"
            className="w-full rounded-full bg-slate-50 py-2 pl-9 pr-4 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
          />
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as AmendmentStatus | "all")}
          className="rounded-full bg-slate-50 px-4 py-2 text-sm outline-none ring-1 ring-slate-200 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
        >
          <option value="all">All statuses</option>
          {statuses.map((s) => (
            <option key={s} value={s}>
              {amendmentStatusLabel[s] ?? s}
            </option>
          ))}
        </select>
      </div>

      <div className="mt-4 space-y-2">
        {amendments === null ? (
          <p className="text-sm text-slate-400">Loading…</p>
        ) : error ? (
          <p className="text-sm text-accent-600">{error}</p>
        ) : filtered.length === 0 && parseAgreementNumber(query) !== null ? (
          // Typing an agreement number opens that agreement even when it has no amendments.
          <div className="rounded-xl bg-slate-50 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10">
            <p className="p-3 text-sm text-slate-500 dark:text-slate-400">
              No amendments have been requested on {agreementRef(parseAgreementNumber(query)!)}.
            </p>
            <AgreementRecord agreementId={parseAgreementNumber(query)!} />
          </div>
        ) : filtered.length === 0 ? (
          <p className="text-sm text-slate-400">
            {amendments.length === 0 ? "No agreement amendments yet." : "No amendments match these filters."}
          </p>
        ) : (
          filtered.map((a) => {
            // Searching by agreement number opens the match straight away.
            const open = openId === a.id || parseAgreementNumber(query) !== null;
            const terms = Object.entries(a.proposedTerms ?? {});
            return (
              <div key={a.id} className="rounded-xl bg-slate-50 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10">
                <button
                  type="button"
                  onClick={() => setOpenId(open ? null : a.id)}
                  aria-expanded={open}
                  className="flex w-full flex-wrap items-center justify-between gap-3 p-3 text-left"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-primary-900 dark:text-white">
                      {a.listingName || agreementRef(a.agreementId)}
                      <span className="ml-2 text-xs font-normal text-slate-400">{agreementRef(a.agreementId)}</span>
                    </p>
                    <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                      {a.renterName || "Renter"} · {a.hostName || "Host"} · requested {formatDate(a.createdAt)}
                    </p>
                    {a.reason && <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-300">{a.reason}</p>}
                  </div>
                  <div className="flex items-center gap-2">
                    {a.amendmentType && <Badge tone="neutral">{amendmentTypeLabel[a.amendmentType] ?? a.amendmentType}</Badge>}
                    <Badge tone={amendmentStatusTone[a.status] ?? "neutral"}>{amendmentStatusLabel[a.status] ?? a.status}</Badge>
                    <ChevronDown className={`h-4 w-4 text-slate-400 transition-transform ${open ? "rotate-180" : ""}`} />
                  </div>
                </button>
                {open && (
                  <div className="grid gap-4 border-t border-slate-200 p-3 text-xs sm:grid-cols-2 dark:border-slate-700">
                    <div className="space-y-1">
                      <p className="font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Proposed change</p>
                      {terms.length === 0 && !a.proposedGuarantor?.legalName ? (
                        <p className="text-slate-400">No terms proposed yet.</p>
                      ) : (
                        <>
                          {terms.map(([k, v]) => (
                            <p key={k} className="text-slate-600 dark:text-slate-300">
                              <span className="text-slate-400">{k.replace(/_/g, " ")}:</span> {String(v)}
                            </p>
                          ))}
                          {a.proposedGuarantor?.legalName && (
                            <p className="text-slate-600 dark:text-slate-300">
                              <span className="text-slate-400">guarantor:</span> {a.proposedGuarantor.legalName}
                              {a.proposedGuarantor.contactEmail ? ` (${a.proposedGuarantor.contactEmail})` : ""}
                            </p>
                          )}
                        </>
                      )}
                    </div>
                    <div className="space-y-1">
                      <p className="font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Timeline</p>
                      {TIMELINE.filter(([key]) => a[key]).map(([key, label]) => (
                        <p key={key} className="text-slate-600 dark:text-slate-300">
                          <span className="text-slate-400">{label}:</span> {formatDate(a[key] as string)}
                        </p>
                      ))}
                    </div>
                  </div>
                )}
                {open && <AgreementRecord agreementId={a.agreementId} />}
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}
