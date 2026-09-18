"use client";

import { useCallback, useEffect, useState } from "react";
import { ClipboardCheck, FileSignature, Mail, ThumbsDown, ThumbsUp, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { Application } from "@/lib/types";
import { formatDate } from "@/lib/utils";
import { decideHostedApplication, errorMessage, listHostedApplications } from "@/lib/user-api";
import { Card, EmptyState, SectionHeading, Toast, useToast } from "@/components/user/ui";
import { HostOfferAgreementPanel } from "@/components/user/HostOfferAgreementPanel";

/** Mirrors LeasingManager.tsx's own applicationDisplay -- same shape, host surface. */
function applicationDisplay(application: Application): { label: string; tone: "warning" | "success" | "danger" | "neutral" } {
  const latestDecision = application.decisions[application.decisions.length - 1];
  if (application.status === "WITHDRAWN") return { label: "Withdrawn", tone: "neutral" };
  if (!latestDecision) return { label: "Pending your review", tone: "warning" };
  if (latestDecision.decision === "APPROVED") return { label: "Approved", tone: "success" };
  return { label: "Rejected", tone: "danger" };
}

export function HostApplicationsManager() {
  const [applications, setApplications] = useState<Application[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [rejectTarget, setRejectTarget] = useState<Application | null>(null);
  const [rejectNote, setRejectNote] = useState("");
  const [offerTarget, setOfferTarget] = useState<Application | null>(null);
  const { toast, showToast } = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listHostedApplications();
      setApplications(data);
    } catch (err) {
      showToast(errorMessage(err, "Could not load applications."), "error");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    load();
  }, [load]);

  async function approve(application: Application) {
    setBusyId(application.id);
    try {
      const updated = await decideHostedApplication(application.id, { decision: "APPROVED" });
      setApplications((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
      showToast("Application approved.");
    } catch (err) {
      showToast(errorMessage(err, "Could not approve this application."), "error");
    } finally {
      setBusyId(null);
    }
  }

  function openReject(application: Application) {
    setRejectTarget(application);
    setRejectNote("");
  }

  async function submitReject() {
    if (!rejectTarget) return;
    setBusyId(rejectTarget.id);
    try {
      const updated = await decideHostedApplication(rejectTarget.id, { decision: "REJECTED", note: rejectNote.trim() });
      setApplications((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
      showToast("Application rejected.");
      setRejectTarget(null);
    } catch (err) {
      showToast(errorMessage(err, "Could not reject this application."), "error");
    } finally {
      setBusyId(null);
    }
  }

  if (loading) return <Loader label="Loading applications" />;

  const pending = applications.filter((a) => a.status === "SUBMITTED");
  const decided = applications.filter((a) => a.status !== "SUBMITTED");

  return (
    <div className="space-y-5">
      <SectionHeading
        title="Applications to review"
        subtitle="Renters who applied to your listings. Approve to move them toward an offer, or reject to close the application."
      />

      {applications.length === 0 ? (
        <Card>
          <div className="flex flex-col items-center gap-3 py-10 text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
              <ClipboardCheck className="h-6 w-6" />
            </span>
            <EmptyState message="No applications yet. They'll show up here as soon as a renter applies." />
          </div>
        </Card>
      ) : (
        <div className="space-y-3">
          {[...pending, ...decided].map((application) => {
            const display = applicationDisplay(application);
            const busy = busyId === application.id;
            return (
              <Card key={application.id}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-heading text-sm font-bold text-primary-900 dark:text-white">
                      {application.guestName}
                    </p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
                      <span className="flex items-center gap-1">
                        <Mail className="h-3 w-3" /> {application.guestEmail}
                      </span>
                      <span>{application.listingName || application.listingId}</span>
                      <span>Applied {formatDate(application.submittedAt)}</span>
                      {application.desiredMoveIn && <span>Move-in: {formatDate(application.desiredMoveIn)}</span>}
                    </p>
                    {application.message && (
                      <p className="mt-2 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
                        &ldquo;{application.message}&rdquo;
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Badge tone={display.tone}>{display.label}</Badge>
                    {application.status === "SUBMITTED" && (
                      <>
                        <Button size="sm" variant="primary" loading={busy} onClick={() => approve(application)}>
                          <ThumbsUp className="h-3.5 w-3.5" /> Approve
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => openReject(application)}>
                          <ThumbsDown className="h-3.5 w-3.5" /> Reject
                        </Button>
                      </>
                    )}
                    {display.label === "Approved" && (
                      <Button size="sm" variant="outline" onClick={() => setOfferTarget(application)}>
                        <FileSignature className="h-3.5 w-3.5" /> {application.offer ? "Manage offer" : "Create offer"}
                      </Button>
                    )}
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      <Modal open={Boolean(rejectTarget)} onClose={() => setRejectTarget(null)} title="Reject application">
        <div className="space-y-3.5">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Optionally tell the renter why — they will see this note.
          </p>
          <textarea
            value={rejectNote}
            onChange={(e) => setRejectNote(e.target.value)}
            rows={4}
            placeholder="e.g. We've decided to go with another applicant for this room."
            className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
          />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setRejectTarget(null)}>
              Cancel
            </Button>
            <Button variant="accent" loading={busyId === rejectTarget?.id} onClick={submitReject}>
              <XCircle className="h-3.5 w-3.5" /> Reject application
            </Button>
          </div>
        </div>
      </Modal>

      {offerTarget && (
        <HostOfferAgreementPanel
          open={Boolean(offerTarget)}
          onClose={() => setOfferTarget(null)}
          applicationId={offerTarget.id}
          offerId={offerTarget.offer?.id ?? null}
          renterName={offerTarget.guestName}
          onChanged={load}
        />
      )}

      <Toast toast={toast} />
    </div>
  );
}
