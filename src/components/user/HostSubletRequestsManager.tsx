"use client";

import { useCallback, useEffect, useState } from "react";
import { Download, HelpCircle, Repeat, ThumbsDown, ThumbsUp } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { SubletRequest } from "@/lib/types";
import { formatDate } from "@/lib/utils";
import {
  approveHostedSubletRequest,
  declineHostedSubletRequest,
  errorMessage,
  hostSubletDecisionRecordUrl,
  listHostedSubletRequests,
  requestHostedSubletMoreInfo,
} from "@/lib/user-api";
import { subletArrangementTypeAdminLabel, subletRequestStatusLabel, subletRequestStatusTone } from "@/lib/status";
import { Card, EmptyState, SectionHeading, Toast, useToast } from "@/components/user/ui";

/** ZR-SUB-003 IMPLEMENTATION LOCK: "A tenant's request for permission to sublet
 *  must be sent to the verified landlord, agent or other authorized property
 *  representative. Zoiko Rooms records and routes the request; it does not
 *  grant permission on the owner's behalf." This is that decision surface --
 *  the Host's own, not the admin dashboard's. */
export function HostSubletRequestsManager() {
  const [requests, setRequests] = useState<SubletRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [declineTarget, setDeclineTarget] = useState<SubletRequest | null>(null);
  const [declineNotes, setDeclineNotes] = useState("");
  const [infoTarget, setInfoTarget] = useState<SubletRequest | null>(null);
  const [infoNote, setInfoNote] = useState("");
  const [approveTarget, setApproveTarget] = useState<SubletRequest | null>(null);
  const [approveConditions, setApproveConditions] = useState("");
  const [approveExpiresAt, setApproveExpiresAt] = useState("");
  const { toast, showToast } = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRequests(await listHostedSubletRequests());
    } catch (err) {
      showToast(errorMessage(err, "Could not load sublet requests."), "error");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    load();
  }, [load]);

  function openApprove(request: SubletRequest) {
    setApproveTarget(request);
    setApproveConditions("");
    setApproveExpiresAt("");
  }

  async function submitApprove() {
    if (!approveTarget) return;
    setBusyId(approveTarget.id);
    try {
      const updated = await approveHostedSubletRequest(approveTarget.id, {
        conditions: approveConditions.trim(),
        expiresAt: approveExpiresAt ? new Date(approveExpiresAt).toISOString() : null,
      });
      setRequests((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      showToast("Sublet request approved — occupancy transferred.");
      setApproveTarget(null);
    } catch (err) {
      showToast(errorMessage(err, "Could not approve this sublet request."), "error");
    } finally {
      setBusyId(null);
    }
  }

  function openRequestInfo(request: SubletRequest) {
    setInfoTarget(request);
    setInfoNote("");
  }

  async function submitRequestInfo() {
    if (!infoTarget || !infoNote.trim()) return;
    setBusyId(infoTarget.id);
    try {
      const updated = await requestHostedSubletMoreInfo(infoTarget.id, infoNote.trim());
      setRequests((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      showToast("Asked the tenant for more information.");
      setInfoTarget(null);
    } catch (err) {
      showToast(errorMessage(err, "Could not send this information request."), "error");
    } finally {
      setBusyId(null);
    }
  }

  function openDecline(request: SubletRequest) {
    setDeclineTarget(request);
    setDeclineNotes("");
  }

  async function submitDecline() {
    if (!declineTarget) return;
    setBusyId(declineTarget.id);
    try {
      const updated = await declineHostedSubletRequest(declineTarget.id, declineNotes.trim());
      setRequests((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      showToast("Sublet request declined.");
      setDeclineTarget(null);
    } catch (err) {
      showToast(errorMessage(err, "Could not decline this sublet request."), "error");
    } finally {
      setBusyId(null);
    }
  }

  if (loading) return <Loader label="Loading sublet requests" />;

  const pending = requests.filter((r) => r.status === "pending_admin_review" || r.status === "pending_verification");
  const awaitingTenant = requests.filter((r) => r.status === "more_information_requested");
  const decided = requests.filter((r) => !pending.includes(r) && !awaitingTenant.includes(r));

  return (
    <div className="space-y-5">
      <SectionHeading
        title="Sublet requests"
        subtitle="A current renter is asking permission to hand their occupancy over to someone else. This decision is yours, not Zoiko's — Zoiko only routes and records it."
      />

      {requests.length === 0 ? (
        <Card>
          <div className="flex flex-col items-center gap-3 py-10 text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
              <Repeat className="h-6 w-6" />
            </span>
            <EmptyState message="No sublet requests yet. They'll show up here when a renter on one of your listings asks to sublet." />
          </div>
        </Card>
      ) : (
        <div className="space-y-3">
          {[...pending, ...awaitingTenant, ...decided].map((request) => {
            const busy = busyId === request.id;
            return (
              <Card key={request.id}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-heading text-sm font-bold text-primary-900 dark:text-white">
                      {request.listingName || `Occupancy #${request.currentOccupancyId}`}
                      {request.listingCity && `, ${request.listingCity}`}
                    </p>
                    <p className="mt-0.5 text-xs font-medium text-primary-700 dark:text-primary-300">
                      {subletArrangementTypeAdminLabel[request.arrangementType] ?? request.arrangementType}
                    </p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
                      <span>
                        {request.currentTenantName || "Current tenant"} → {request.proposedRenterName || "proposed renter"}
                      </span>
                      <span>Requested {formatDate(request.createdAt)}</span>
                      {request.authorityEvidenceRef && <span>Evidence ref: {request.authorityEvidenceRef}</span>}
                    </p>
                    {request.reason && (
                      <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
                        &ldquo;{request.reason}&rdquo;
                      </p>
                    )}
                    {request.infoRequestNote && (
                      <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
                        You asked: &ldquo;{request.infoRequestNote}&rdquo;
                      </p>
                    )}
                    {request.infoResponseNote && (
                      <p className="mt-2 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
                        Tenant replied: &ldquo;{request.infoResponseNote}&rdquo;
                      </p>
                    )}
                    {request.status === "approved" && (request.approvalConditions || request.approvalExpiresAt) && (
                      <p className="mt-2 rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300">
                        {request.approvalConditions && <>Conditions: {request.approvalConditions}</>}
                        {request.approvalConditions && request.approvalExpiresAt && " · "}
                        {request.approvalExpiresAt && <>Expires {formatDate(request.approvalExpiresAt)}</>}
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Badge tone={subletRequestStatusTone[request.status] ?? "neutral"}>
                      {subletRequestStatusLabel[request.status] ?? request.status}
                    </Badge>
                    {pending.includes(request) && (
                      <>
                        <Button size="sm" variant="primary" loading={busy} onClick={() => openApprove(request)}>
                          <ThumbsUp className="h-3.5 w-3.5" /> Approve
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => openRequestInfo(request)}>
                          <HelpCircle className="h-3.5 w-3.5" /> Ask for info
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => openDecline(request)}>
                          <ThumbsDown className="h-3.5 w-3.5" /> Decline
                        </Button>
                      </>
                    )}
                    {decided.includes(request) && (
                      <a href={hostSubletDecisionRecordUrl(request.id)} download>
                        <Button size="sm" variant="outline">
                          <Download className="h-3.5 w-3.5" /> Download record
                        </Button>
                      </a>
                    )}
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      <Modal open={Boolean(approveTarget)} onClose={() => setApproveTarget(null)} title="Approve sublet request">
        <div className="space-y-3.5">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            You can optionally attach conditions and an expiry to this approval. Approving transfers the occupancy
            immediately.
          </p>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Conditions (optional)
            </label>
            <textarea
              value={approveConditions}
              onChange={(e) => setApproveConditions(e.target.value)}
              rows={3}
              placeholder="e.g. No additional occupants. No pets."
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Approval expires (optional)
            </label>
            <input
              type="date"
              value={approveExpiresAt}
              onChange={(e) => setApproveExpiresAt(e.target.value)}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setApproveTarget(null)}>
              Cancel
            </Button>
            <Button variant="primary" loading={busyId === approveTarget?.id} onClick={submitApprove}>
              <ThumbsUp className="h-3.5 w-3.5" /> Approve request
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={Boolean(infoTarget)} onClose={() => setInfoTarget(null)} title="Ask the tenant for more information">
        <div className="space-y-3.5">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            The request will move to &ldquo;awaiting tenant&rdquo; until they respond — you won&apos;t be able to
            approve or decline it until then.
          </p>
          <textarea
            value={infoNote}
            onChange={(e) => setInfoNote(e.target.value)}
            rows={4}
            placeholder="e.g. Please share the proposed occupant's employer reference."
            className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
          />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setInfoTarget(null)}>
              Cancel
            </Button>
            <Button variant="primary" loading={busyId === infoTarget?.id} disabled={!infoNote.trim()} onClick={submitRequestInfo}>
              Send request
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={Boolean(declineTarget)} onClose={() => setDeclineTarget(null)} title="Decline sublet request">
        <div className="space-y-3.5">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Optionally tell the renter why — they will see this note.
          </p>
          <textarea
            value={declineNotes}
            onChange={(e) => setDeclineNotes(e.target.value)}
            rows={4}
            placeholder="e.g. I'm not comfortable with this arrangement for this property."
            className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
          />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setDeclineTarget(null)}>
              Cancel
            </Button>
            <Button variant="accent" loading={busyId === declineTarget?.id} onClick={submitDecline}>
              Decline request
            </Button>
          </div>
        </div>
      </Modal>

      <Toast toast={toast} />
    </div>
  );
}
