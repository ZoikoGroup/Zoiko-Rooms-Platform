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
  signHostedAgreement,
} from "@/lib/user-api";
import {
  REPLACING_ARRANGEMENT_TYPES,
  subletArrangementTypeAdminLabel,
  subletDeclineReasonCodeLabel,
  subletRequestStatusLabel,
  subletRequestStatusTone,
} from "@/lib/status";
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
  const [declineReasonCode, setDeclineReasonCode] = useState("");
  const [infoTarget, setInfoTarget] = useState<SubletRequest | null>(null);
  const [infoNote, setInfoNote] = useState("");
  const [infoDocumentTypes, setInfoDocumentTypes] = useState("");
  const [infoDueAt, setInfoDueAt] = useState("");
  const [approveTarget, setApproveTarget] = useState<SubletRequest | null>(null);
  const [approveConditions, setApproveConditions] = useState("");
  const [approveConditionList, setApproveConditionList] = useState<string[]>([]);
  const [approveConditionDraft, setApproveConditionDraft] = useState("");
  const [approveExpiresAt, setApproveExpiresAt] = useState("");
  const [approveAuthorityConfirmed, setApproveAuthorityConfirmed] = useState(false);
  const [approveStepUpPassword, setApproveStepUpPassword] = useState("");
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
    setApproveConditionList([]);
    setApproveConditionDraft("");
    setApproveExpiresAt("");
    setApproveAuthorityConfirmed(false);
    setApproveStepUpPassword("");
  }

  function addApproveCondition() {
    if (!approveConditionDraft.trim()) return;
    setApproveConditionList((prev) => [...prev, approveConditionDraft.trim()]);
    setApproveConditionDraft("");
  }

  const approveRequiresStepUp = Boolean(
    approveTarget && (REPLACING_ARRANGEMENT_TYPES as readonly string[]).includes(approveTarget.arrangementType)
  );

  async function submitApprove() {
    if (!approveTarget || !approveAuthorityConfirmed) return;
    if (approveRequiresStepUp && !approveStepUpPassword) return;
    setBusyId(approveTarget.id);
    try {
      const updated = await approveHostedSubletRequest(approveTarget.id, {
        conditions: approveConditions.trim(),
        conditionList: approveConditionList,
        expiresAt: approveExpiresAt ? new Date(approveExpiresAt).toISOString() : null,
        authorityConfirmed: approveAuthorityConfirmed,
        stepUpPassword: approveStepUpPassword,
      });
      setRequests((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      showToast("Sublet request approved — occupancy transferred.");
      setApproveTarget(null);
    } catch (err) {
      setApproveStepUpPassword("");
      showToast(errorMessage(err, "Could not approve this sublet request."), "error");
    } finally {
      setBusyId(null);
    }
  }

  function openRequestInfo(request: SubletRequest) {
    setInfoTarget(request);
    setInfoNote("");
    setInfoDocumentTypes("");
    setInfoDueAt("");
  }

  async function submitRequestInfo() {
    if (!infoTarget || !infoNote.trim()) return;
    setBusyId(infoTarget.id);
    try {
      const updated = await requestHostedSubletMoreInfo(infoTarget.id, infoNote.trim(), {
        requestedDocumentTypes: infoDocumentTypes
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
        dueAt: infoDueAt ? new Date(infoDueAt).toISOString() : null,
      });
      setRequests((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      showToast("Asked the tenant for more information.");
      setInfoTarget(null);
    } catch (err) {
      showToast(errorMessage(err, "Could not send this information request."), "error");
    } finally {
      setBusyId(null);
    }
  }

  async function handleSignCoTenantAgreement(request: SubletRequest) {
    if (!request.newAgreementId) return;
    setBusyId(request.id);
    try {
      await signHostedAgreement(request.newAgreementId);
      showToast("Agreement signed. Waiting on the co-tenant's own signature and payment.");
    } catch (err) {
      showToast(errorMessage(err, "Could not sign this agreement — it may already be signed."), "error");
    } finally {
      setBusyId(null);
    }
  }

  function openDecline(request: SubletRequest) {
    setDeclineTarget(request);
    setDeclineNotes("");
    setDeclineReasonCode("");
  }

  async function submitDecline() {
    if (!declineTarget) return;
    if (!declineReasonCode) {
      showToast("Choose a reason for declining this request.", "error");
      return;
    }
    if (declineReasonCode === "OTHER" && !declineNotes.trim()) {
      showToast("Add a short explanation when the reason is \"Other\".", "error");
      return;
    }
    setBusyId(declineTarget.id);
    try {
      const updated = await declineHostedSubletRequest(declineTarget.id, declineNotes.trim(), declineReasonCode);
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

  const pending = requests.filter(
    (r) => r.status === "pending_admin_review" || r.status === "pending_verification" || r.status === "tenant_response_submitted",
  );
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
                    {(request.proposedStartDate || request.proposedEndDate) && (
                      <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
                        Proposed period: {request.proposedStartDate ? formatDate(request.proposedStartDate) : "—"} to{" "}
                        {request.proposedEndDate ? formatDate(request.proposedEndDate) : "—"}
                      </p>
                    )}
                    {request.infoRequestNote && (
                      <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
                        You asked: &ldquo;{request.infoRequestNote}&rdquo;
                        {request.infoRequestedDocumentTypes.length > 0 && (
                          <> — documents: {request.infoRequestedDocumentTypes.join(", ")}</>
                        )}
                        {request.infoRequestDueAt && <> — due {formatDate(request.infoRequestDueAt)}</>}
                      </p>
                    )}
                    {request.infoResponseNote && (
                      <p className="mt-2 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
                        Tenant replied: &ldquo;{request.infoResponseNote}&rdquo;
                      </p>
                    )}
                    {request.status === "approved" &&
                      (request.approvalConditions || request.approvalConditionList.length > 0 || request.approvalExpiresAt) && (
                        <p className="mt-2 rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300">
                          {request.approvalConditionList.length > 0 && (
                            <>Conditions: {request.approvalConditionList.join("; ")}</>
                          )}
                          {request.approvalConditionList.length > 0 && request.approvalConditions && " · "}
                          {request.approvalConditions && <>Notes: {request.approvalConditions}</>}
                          {(request.approvalConditionList.length > 0 || request.approvalConditions) && request.approvalExpiresAt && " · "}
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
                    {decided.includes(request) && request.status === "approved" && request.newAgreementId && (
                      <Button
                        size="sm"
                        variant="outline"
                        loading={busyId === request.id}
                        onClick={() => handleSignCoTenantAgreement(request)}
                      >
                        Sign co-tenant agreement
                      </Button>
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
            {approveConditionList.length > 0 && (
              <ul className="mb-2 space-y-1">
                {approveConditionList.map((c, i) => (
                  <li
                    key={i}
                    className="flex items-center justify-between gap-2 rounded-lg bg-slate-50 px-3 py-1.5 text-xs text-slate-600 dark:bg-slate-800/60 dark:text-slate-300"
                  >
                    {c}
                    <button
                      type="button"
                      className="text-slate-400 hover:text-accent-600"
                      onClick={() => setApproveConditionList((prev) => prev.filter((_, idx) => idx !== i))}
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="flex gap-2">
              <input
                value={approveConditionDraft}
                onChange={(e) => setApproveConditionDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addApproveCondition();
                  }
                }}
                placeholder="e.g. No additional occupants"
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
              <Button type="button" variant="outline" onClick={addApproveCondition}>
                + Add condition
              </Button>
            </div>
            <textarea
              value={approveConditions}
              onChange={(e) => setApproveConditions(e.target.value)}
              rows={2}
              placeholder="Any other notes on the conditions (optional)"
              className="mt-2 w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
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
          <label className="flex items-start gap-2 text-xs text-slate-600 dark:text-slate-300">
            <input
              type="checkbox"
              checked={approveAuthorityConfirmed}
              onChange={(e) => setApproveAuthorityConfirmed(e.target.checked)}
              className="mt-0.5"
            />
            I confirm I am authorized to make this decision for this rental.
          </label>
          {approveRequiresStepUp && (
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Confirm your password
              </label>
              <p className="mb-1.5 text-xs text-slate-500 dark:text-slate-400">
                This hands the tenancy over to a new occupant and can&apos;t be undone — re-enter your password to
                confirm.
              </p>
              <input
                type="password"
                value={approveStepUpPassword}
                onChange={(e) => setApproveStepUpPassword(e.target.value)}
                placeholder="Your account password"
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setApproveTarget(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={busyId === approveTarget?.id}
              disabled={!approveAuthorityConfirmed || (approveRequiresStepUp && !approveStepUpPassword)}
              onClick={submitApprove}
            >
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
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Requested document types (optional)
            </label>
            <input
              value={infoDocumentTypes}
              onChange={(e) => setInfoDocumentTypes(e.target.value)}
              placeholder="e.g. Employer reference, Proof of income (comma-separated)"
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Response due date (optional)
            </label>
            <input
              type="date"
              value={infoDueAt}
              onChange={(e) => setInfoDueAt(e.target.value)}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
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
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Reason
            </label>
            <select
              value={declineReasonCode}
              onChange={(e) => setDeclineReasonCode(e.target.value)}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            >
              <option value="">Select a reason…</option>
              {Object.entries(subletDeclineReasonCodeLabel).map(([code, label]) => (
                <option key={code} value={code}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {declineReasonCode === "OTHER"
              ? "Explain your reason — the renter will see this note."
              : "Optionally add more detail — the renter will see this note."}
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
            <Button
              variant="accent"
              loading={busyId === declineTarget?.id}
              disabled={!declineReasonCode || (declineReasonCode === "OTHER" && !declineNotes.trim())}
              onClick={submitDecline}
            >
              Decline request
            </Button>
          </div>
        </div>
      </Modal>

      <Toast toast={toast} />
    </div>
  );
}
