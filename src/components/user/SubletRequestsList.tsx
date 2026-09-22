"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CalendarClock, Download, Repeat } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { SubletRequest } from "@/lib/types";
import { subletRequestStatusLabel, subletRequestStatusTone } from "@/lib/status";
import { formatDate } from "@/lib/utils";
import {
  errorMessage,
  listSubletRequests,
  respondToSubletInfoRequest,
  tenantSubletDecisionRecordUrl,
  withdrawSubletRequest,
} from "@/lib/user-api";
import { Card, EmptyState, Toast, useToast } from "@/components/user/ui";

type StatusFilter = "all" | keyof typeof subletRequestStatusLabel;

const COMPLETED_STATUSES = new Set(["approved", "rejected", "withdrawn", "expired", "superseded", "cancelled_by_authority"]);

const STATUS_FILTERS: StatusFilter[] = [
  "all",
  "draft",
  "pending_verification",
  "pending_admin_review",
  "more_information_requested",
  "tenant_response_submitted",
  "approved",
  "rejected",
  "withdrawn",
  "expired",
  "superseded",
  "cancelled_by_authority",
];

// Mirrors backend crud/sublet.py's _WITHDRAWABLE_STATUSES.
const WITHDRAWABLE_STATUSES = new Set([
  "pending_verification", "pending_admin_review", "more_information_requested", "tenant_response_submitted",
]);

export function SubletRequestsList() {
  const { toast, showToast } = useToast();
  const [requests, setRequests] = useState<SubletRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [respondTarget, setRespondTarget] = useState<SubletRequest | null>(null);
  const [responseNote, setResponseNote] = useState("");
  const [responding, setResponding] = useState(false);
  const [withdrawingId, setWithdrawingId] = useState<number | null>(null);

  function load() {
    setLoading(true);
    setLoadError("");
    listSubletRequests()
      .then(setRequests)
      .catch((err) => {
        const message = errorMessage(err, "Could not load your sublet requests.");
        setLoadError(message);
        showToast(message, "error");
      })
      .finally(() => setLoading(false));
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, []);

  function openRespond(request: SubletRequest) {
    setRespondTarget(request);
    setResponseNote("");
  }

  async function submitResponse() {
    if (!respondTarget || !responseNote.trim()) return;
    setResponding(true);
    try {
      const updated = await respondToSubletInfoRequest(respondTarget.id, responseNote.trim());
      setRequests((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      showToast("Your response was sent.");
      setRespondTarget(null);
    } catch (err) {
      showToast(errorMessage(err, "Could not send your response."), "error");
    } finally {
      setResponding(false);
    }
  }

  async function handleWithdraw(request: SubletRequest) {
    setWithdrawingId(request.id);
    try {
      const updated = await withdrawSubletRequest(request.id);
      setRequests((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      showToast("Sublet request withdrawn.");
    } catch (err) {
      showToast(errorMessage(err, "Could not withdraw this sublet request."), "error");
    } finally {
      setWithdrawingId(null);
    }
  }

  const filtered = useMemo(
    () => (statusFilter === "all" ? requests : requests.filter((r) => r.status === statusFilter)),
    [requests, statusFilter]
  );

  if (loading) return <Loader label="Loading your sublet requests" />;

  if (loadError) {
    return (
      <Card>
        <div className="flex flex-col items-center gap-3 py-8 text-center">
          <AlertTriangle className="h-6 w-6 text-accent-600" />
          <p className="text-sm text-slate-500 dark:text-slate-400">{loadError}</p>
          <Button size="sm" variant="outline" onClick={load}>
            Retry
          </Button>
        </div>
      </Card>
    );
  }

  if (requests.length === 0) {
    return (
      <Card>
        <div className="flex flex-col items-center gap-4 py-10 text-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
            <Repeat className="h-6 w-6" />
          </span>
          <EmptyState message="You have not requested to sublet any of your rentals." />
          <Link href="/account/rentals">
            <Button size="sm" variant="outline">
              Go to My Rentals
            </Button>
          </Link>
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Request permission to sublet, and track decisions for your rentals.
        </p>
        {/* ZR-SUB-003 Wireframe A's own landing-page CTA -- creation itself
            still happens from the specific rental's own card (My Rentals),
            since the form needs that occupancy's context (arrangement
            options, identity checks); this routes there rather than
            duplicating that flow here. */}
        <Link href="/account/rentals">
          <Button size="sm" variant="primary">
            + New sublet request
          </Button>
        </Link>
      </div>
      <div className="flex flex-wrap gap-2">
        {STATUS_FILTERS.map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`rounded-full px-3 py-1.5 text-xs font-semibold transition-colors ${
              statusFilter === s
                ? "bg-primary-700 text-white"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
            }`}
          >
            {s === "all" ? "All" : subletRequestStatusLabel[s]}
          </button>
        ))}
      </div>

      {filtered.length === 0 && (
        <Card>
          <EmptyState message="No sublet requests match this filter." />
        </Card>
      )}

      {filtered.map((request) => (
        <Card key={request.id} className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="font-heading text-sm font-bold text-primary-900 dark:text-white">
              Occupancy #{request.currentOccupancyId}
            </p>
            <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
              <span className="flex items-center gap-1">
                <CalendarClock className="h-3 w-3" /> Requested {formatDate(request.createdAt)}
              </span>
              <span>Proposed renter party #{request.proposedRenterPartyId}</span>
              {request.decidedAt && <span>Decided {formatDate(request.decidedAt)}</span>}
            </p>
            {(request.proposedStartDate || request.proposedEndDate) && (
              <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
                Proposed period: {request.proposedStartDate ? formatDate(request.proposedStartDate) : "—"} to{" "}
                {request.proposedEndDate ? formatDate(request.proposedEndDate) : "—"}
              </p>
            )}
            {request.reason && (
              <p className="mt-2 max-w-xl text-xs text-slate-500 dark:text-slate-400">
                Your reason: &ldquo;{request.reason}&rdquo;
              </p>
            )}
            {request.adminNotes && (
              <p className="mt-2 max-w-xl text-xs text-slate-500 dark:text-slate-400">
                Reviewer notes: {request.adminNotes}
              </p>
            )}
            {request.infoRequestNote && (
              <p className="mt-2 max-w-xl rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
                Your host asked: &ldquo;{request.infoRequestNote}&rdquo;
                {request.infoRequestedDocumentTypes.length > 0 && (
                  <> — please send: {request.infoRequestedDocumentTypes.join(", ")}</>
                )}
                {request.infoRequestDueAt && <> — by {formatDate(request.infoRequestDueAt)}</>}
              </p>
            )}
            {request.infoResponseNote && (
              <p className="mt-2 max-w-xl text-xs text-slate-500 dark:text-slate-400">
                Your response: &ldquo;{request.infoResponseNote}&rdquo;
              </p>
            )}
            {request.status === "approved" &&
              (request.approvalConditions || request.approvalConditionList.length > 0 || request.approvalExpiresAt) && (
                <p className="mt-2 max-w-xl rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300">
                  {request.approvalConditionList.length > 0 && (
                    <>Conditions: {request.approvalConditionList.join("; ")}</>
                  )}
                  {request.approvalConditionList.length > 0 && request.approvalConditions && " · "}
                  {request.approvalConditions && <>Notes: {request.approvalConditions}</>}
                  {(request.approvalConditionList.length > 0 || request.approvalConditions) && request.approvalExpiresAt && " · "}
                  {request.approvalExpiresAt && <>Expires {formatDate(request.approvalExpiresAt)}</>}
                </p>
              )}
            {request.status === "approved" && (
              <p className="mt-2 max-w-xl text-[11px] text-slate-400">
                Approved — permission may still be subject to your host&apos;s conditions above and any applicable
                legal or contractual requirements.
              </p>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Badge tone={subletRequestStatusTone[request.status] ?? "neutral"}>
              {subletRequestStatusLabel[request.status] ?? request.status}
            </Badge>
            {request.status === "more_information_requested" && !request.infoResponseNote && (
              <Button size="sm" variant="primary" onClick={() => openRespond(request)}>
                Respond
              </Button>
            )}
            {WITHDRAWABLE_STATUSES.has(request.status) && (
              <Button
                size="sm"
                variant="outline"
                loading={withdrawingId === request.id}
                onClick={() => handleWithdraw(request)}
              >
                Withdraw
              </Button>
            )}
            {COMPLETED_STATUSES.has(request.status) && (
              <a href={tenantSubletDecisionRecordUrl(request.id)} download>
                <Button size="sm" variant="outline">
                  <Download className="h-3.5 w-3.5" /> Download record
                </Button>
              </a>
            )}
          </div>
        </Card>
      ))}

      <Modal open={Boolean(respondTarget)} onClose={() => setRespondTarget(null)} title="Respond to your host">
        <div className="space-y-3.5">
          {respondTarget?.infoRequestNote && (
            <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
              Your host asked: &ldquo;{respondTarget.infoRequestNote}&rdquo;
            </p>
          )}
          <textarea
            value={responseNote}
            onChange={(e) => setResponseNote(e.target.value)}
            rows={4}
            placeholder="Provide the requested information here."
            className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
          />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setRespondTarget(null)}>
              Cancel
            </Button>
            <Button variant="primary" loading={responding} disabled={!responseNote.trim()} onClick={submitResponse}>
              Send response
            </Button>
          </div>
        </div>
      </Modal>

      <Toast toast={toast} />
    </div>
  );
}
