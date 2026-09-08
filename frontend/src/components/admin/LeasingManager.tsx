"use client";

import { useCallback, useEffect, useState } from "react";
import {
  CheckCircle2,
  ClipboardList,
  DoorOpen,
  Download,
  FileSignature,
  Pencil,
  Plus,
  Send,
  ThumbsDown,
  ThumbsUp,
  XCircle,
} from "lucide-react";
import { AdminRole, AdminUserSummary, Application, Listing, PublishEligibility } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { apiClientFetch } from "@/lib/api-client";
import { getCurrentAdmin } from "@/lib/auth";
import { agreementStatusLabel, agreementStatusTone, offerStatusLabel, offerStatusTone } from "@/lib/status";
import { cn, formatCurrency, formatDate } from "@/lib/utils";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const emptyTermsForm = { monthlyRent: "", depositAmount: "", startDate: "", termMonths: "11" };
const emptyApplicationForm = { listingId: "", name: "", email: "", phone: "", message: "", desiredMoveIn: "" };

function applicationDisplay(application: Application): { label: string; tone: "warning" | "success" | "danger" | "neutral" } {
  const latestDecision = application.decisions[application.decisions.length - 1];
  if (application.status === "WITHDRAWN") return { label: "Withdrawn", tone: "neutral" };
  if (!latestDecision) return { label: "Pending Review", tone: "warning" };
  if (latestDecision.decision === "APPROVED") return { label: "Approved", tone: "success" };
  return { label: "Rejected", tone: "danger" };
}

// A single "where is this booking at" glance across Application -> Offer ->
// Agreement, since those three sections otherwise have to be read separately
// to answer that question (flagged as a real UX gap: ~11 clicks with no
// single status view).
const PIPELINE_STAGES = ["Applied", "Approved", "Offer sent", "Offer accepted", "Agreement sent", "Signed"] as const;

function pipelineStep(application: Application): number {
  const latestDecision = application.decisions[application.decisions.length - 1];
  const offer = application.offer;
  const agreement = offer?.agreement;
  if (agreement?.status === "SIGNED") return 5;
  if (agreement) return 4;
  if (offer?.status === "ACCEPTED") return 3;
  if (offer && offer.status !== "DRAFT") return 2;
  if (latestDecision?.decision === "APPROVED") return 1;
  return 0;
}

function PipelineStepper({ application }: { application: Application }) {
  if (application.status === "WITHDRAWN" || application.decisions[application.decisions.length - 1]?.decision === "REJECTED") {
    return null;
  }
  const step = pipelineStep(application);
  return (
    <div className="mt-2 flex items-center gap-1">
      {PIPELINE_STAGES.map((stage, i) => (
        <div key={stage} className="flex items-center gap-1">
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
              i < step && "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400",
              i === step && "bg-primary-100 text-primary-700 dark:bg-primary-500/15 dark:text-primary-300",
              i > step && "bg-slate-100 text-slate-400 dark:bg-slate-800 dark:text-slate-500"
            )}
          >
            {stage}
          </span>
          {i < PIPELINE_STAGES.length - 1 && (
            <span className={cn("h-px w-2", i < step ? "bg-emerald-300" : "bg-slate-200 dark:bg-slate-700")} />
          )}
        </div>
      ))}
    </div>
  );
}

export function LeasingManager() {
  const [applications, setApplications] = useState<Application[]>([]);
  const [publishedListings, setPublishedListings] = useState<Listing[]>([]);
  const [listingsById, setListingsById] = useState<Record<string, Listing>>({});
  const [ownerNamesById, setOwnerNamesById] = useState<Record<number, string>>({});
  const [role, setRole] = useState<AdminRole | null>(null);
  const [toast, setToast] = useState("");
  const [termsOfferId, setTermsOfferId] = useState<number | null>(null);
  const [termsForm, setTermsForm] = useState(emptyTermsForm);
  const [applicationModalOpen, setApplicationModalOpen] = useState(false);
  const [editingApplicationId, setEditingApplicationId] = useState<number | null>(null);
  const [applicationForm, setApplicationForm] = useState(emptyApplicationForm);
  const [rejectingId, setRejectingId] = useState<number | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3600);
  }

  const loadApplications = useCallback(async () => {
    try {
      const admin = await getCurrentAdmin();
      setRole(admin?.role ?? null);
      const [applicationsData, listingsData] = await Promise.all([
        apiClientFetch<Application[]>("/api/leasing/applications"),
        apiClientFetch<Listing[]>("/api/listings"),
      ]);
      setApplications(applicationsData);
      setPublishedListings(listingsData.filter((l) => l.state === "PUBLISHED"));
      setListingsById(Object.fromEntries(listingsData.map((l) => [l.id, l])));

      // Owner/host names -- /api/admin-users is super_admin-only, so a regular
      // admin falls back to "Owner #<id>" below rather than erroring on this fetch.
      if (admin?.role === "super_admin") {
        try {
          const owners = await apiClientFetch<AdminUserSummary[]>("/api/admin-users");
          setOwnerNamesById(Object.fromEntries(owners.map((o) => [o.id, o.fullName])));
        } catch {
          // Non-fatal -- host names just won't resolve.
        }
      }
    } catch {
      showToast("Failed to load applications");
    }
  }, []);

  useEffect(() => {
    loadApplications();
  }, [loadApplications]);

  function openApplicationModal() {
    setEditingApplicationId(null);
    setApplicationForm(emptyApplicationForm);
    setApplicationModalOpen(true);
  }

  function openEditModal(application: Application) {
    setEditingApplicationId(application.id);
    setApplicationForm({
      listingId: application.listingId,
      name: application.guestName,
      email: application.guestEmail,
      phone: "",
      message: application.message,
      desiredMoveIn: application.desiredMoveIn ?? "",
    });
    setApplicationModalOpen(true);
  }

  async function submitApplication(e: React.FormEvent) {
    e.preventDefault();
    if (editingApplicationId !== null) {
      try {
        await apiClientFetch(`/api/leasing/applications/${editingApplicationId}`, {
          method: "PUT",
          body: JSON.stringify({
            message: applicationForm.message.trim(),
            desiredMoveIn: applicationForm.desiredMoveIn || undefined,
          }),
        });
        setApplicationModalOpen(false);
        showToast("Application updated");
        loadApplications();
      } catch {
        showToast("Failed to update application");
      }
      return;
    }

    if (!applicationForm.listingId || !applicationForm.name.trim() || !applicationForm.email.trim()) {
      showToast("Listing, renter name and email are required");
      return;
    }
    try {
      await apiClientFetch("/api/leasing/applications", {
        method: "POST",
        body: JSON.stringify({
          listingId: applicationForm.listingId,
          newGuest: {
            name: applicationForm.name.trim(),
            email: applicationForm.email.trim(),
            phone: applicationForm.phone.trim(),
          },
          message: applicationForm.message.trim(),
          desiredMoveIn: applicationForm.desiredMoveIn || undefined,
        }),
      });
      setApplicationModalOpen(false);
      showToast("Application recorded");
      loadApplications();
    } catch {
      showToast("Failed to record application");
    }
  }

  async function withdrawApplication(id: number) {
    try {
      await apiClientFetch(`/api/leasing/applications/${id}/withdraw`, { method: "POST" });
      showToast("Application withdrawn");
      loadApplications();
    } catch {
      showToast("Failed to withdraw application");
    }
  }

  function updateApplication(updated: Application) {
    setApplications((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
  }

  async function approve(application: Application) {
    try {
      const updated = await apiClientFetch<Application>(`/api/leasing/applications/${application.id}/decide`, {
        method: "POST",
        body: JSON.stringify({ decision: "APPROVED" }),
      });
      updateApplication(updated);
      // Approving always leads straight to creating the offer draft anyway --
      // chaining it here removes a click without skipping any real decision
      // (see finding #26: too many manual steps with no real judgment in between).
      await createOffer(updated);
    } catch {
      showToast("Failed to record decision");
    }
  }

  function openRejectModal(applicationId: number) {
    setRejectReason("");
    setRejectingId(applicationId);
  }

  async function submitReject(e: React.FormEvent) {
    e.preventDefault();
    if (rejectingId === null) return;
    try {
      const updated = await apiClientFetch<Application>(`/api/leasing/applications/${rejectingId}/decide`, {
        method: "POST",
        body: JSON.stringify({ decision: "REJECTED", note: rejectReason.trim() }),
      });
      updateApplication(updated);
      setRejectingId(null);
      showToast("Application rejected");
    } catch {
      showToast("Failed to record decision");
    }
  }

  async function createOffer(application: Application) {
    try {
      const eligibility = await apiClientFetch<PublishEligibility>(`/api/leasing/applications/${application.id}/offer-eligibility`);
      if (!eligibility.eligible) {
        showToast(`Not eligible to create an offer: ${eligibility.reasons.join("; ")}`);
        return;
      }
      await apiClientFetch(`/api/leasing/applications/${application.id}/offers`, { method: "POST" });
      showToast("Offer created as draft");
      loadApplications();
    } catch {
      showToast("Failed to create offer");
    }
  }

  function openTermsModal(offerId: number) {
    setTermsForm(emptyTermsForm);
    setTermsOfferId(offerId);
  }

  async function submitTerms(e: React.FormEvent) {
    e.preventDefault();
    if (termsOfferId === null) return;
    try {
      await apiClientFetch(`/api/leasing/offers/${termsOfferId}/terms`, {
        method: "POST",
        body: JSON.stringify({
          monthlyRent: Number(termsForm.monthlyRent),
          depositAmount: Number(termsForm.depositAmount),
          startDate: termsForm.startDate,
          termMonths: Number(termsForm.termMonths),
        }),
      });
      setTermsOfferId(null);
      showToast("Offer terms added");
      loadApplications();
    } catch {
      showToast("Failed to add offer terms");
    }
  }

  async function transitionOffer(offerId: number, action: "send" | "accept" | "decline") {
    try {
      await apiClientFetch(`/api/leasing/offers/${offerId}/${action}`, { method: "POST" });
      showToast(`Offer ${action === "send" ? "sent" : action === "accept" ? "accepted" : "declined"}`);
      loadApplications();
    } catch {
      showToast(`Failed to ${action} offer`);
    }
  }

  async function createAgreement(offerId: number) {
    try {
      const eligibility = await apiClientFetch<PublishEligibility>(`/api/leasing/offers/${offerId}/agreement-eligibility`);
      if (!eligibility.eligible) {
        showToast(`Not eligible to create an agreement: ${eligibility.reasons.join("; ")}`);
        return;
      }
      await apiClientFetch(`/api/leasing/offers/${offerId}/agreement`, { method: "POST" });
      showToast("Agreement created as draft");
      loadApplications();
    } catch {
      showToast("Failed to create agreement");
    }
  }

  async function sendAgreement(agreementId: number) {
    try {
      await apiClientFetch(`/api/leasing/agreements/${agreementId}/send`, { method: "POST" });
      showToast("Agreement sent");
      loadApplications();
    } catch {
      showToast("Failed to send agreement");
    }
  }

  async function signAgreement(agreementId: number, asParty: "provider" | "renter") {
    try {
      await apiClientFetch(`/api/leasing/agreements/${agreementId}/sign`, {
        method: "POST",
        body: JSON.stringify({ asParty }),
      });
      showToast(`Signed as ${asParty}`);
      loadApplications();
    } catch {
      showToast("Failed to sign agreement");
    }
  }

  async function confirmMoveIn(agreementId: number) {
    try {
      const eligibility = await apiClientFetch<PublishEligibility>(`/api/occupancy/agreements/${agreementId}/move-in-eligibility`);
      if (!eligibility.eligible) {
        showToast(`Not ready for move-in: ${eligibility.reasons.join("; ")}`);
        return;
      }
      await apiClientFetch(`/api/occupancy/agreements/${agreementId}/confirm-move-in`, { method: "POST" });
      showToast("Move-in confirmed — occupancy is now active");
      loadApplications();
    } catch {
      showToast("Failed to confirm move-in");
    }
  }

  async function downloadAgreementPdf(agreementId: number) {
    try {
      const res = await fetch(`${API_URL}/api/leasing/agreements/${agreementId}/pdf`, { credentials: "include" });
      if (!res.ok) throw new Error("download failed");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `agreement-${agreementId}.pdf`;
      link.click();
      URL.revokeObjectURL(url);
    } catch {
      showToast("Failed to download agreement PDF");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Real applications normally arrive from renters on your public site. Use this to record one manually for
          testing or for a renter who applied offline.
        </p>
        <Button size="sm" variant="accent" onClick={openApplicationModal}>
          <Plus className="h-3.5 w-3.5" /> New Application
        </Button>
      </div>

      {applications.map((application) => {
        const latestDecision = application.decisions[application.decisions.length - 1];
        const offer = application.offer;
        const agreement = offer?.agreement;
        const latestTerms = offer?.terms[offer.terms.length - 1];
        const display = applicationDisplay(application);
        const isPending = application.status === "SUBMITTED" && !latestDecision;
        const listing = listingsById[application.listingId];
        const hostName = listing ? ownerNamesById[listing.ownerId] : undefined;

        return (
          <div
            key={application.id}
            className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="font-heading text-sm font-bold text-primary-900 dark:text-white">
                  {application.guestName} <span className="font-normal text-slate-400">applied for</span>{" "}
                  {application.listingName || application.listingId}
                </p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Application #{application.id} · {application.listingId} · {application.guestEmail} · applied{" "}
                  {formatDate(application.submittedAt)}
                </p>
                {listing && (
                  <p className="mt-0.5 text-xs text-slate-400">
                    {listing.location}, {listing.city}
                    {hostName ? ` · Hosted by ${hostName}` : ""}
                  </p>
                )}
              </div>
              <Badge tone={display.tone}>{display.label}</Badge>
            </div>
            <PipelineStepper application={application} />
            {application.message && (
              <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">&ldquo;{application.message}&rdquo;</p>
            )}
            {latestDecision?.decision === "REJECTED" && latestDecision.note && (
              <p className="mt-1 text-xs text-accent-600">Reason: {latestDecision.note}</p>
            )}

            {isPending && (
              <div className="mt-3 flex flex-wrap gap-2">
                {role === "super_admin" ? (
                  <>
                    <Button size="sm" variant="primary" onClick={() => approve(application)}>
                      <ThumbsUp className="h-3.5 w-3.5" /> Approve
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => openRejectModal(application.id)}>
                      <ThumbsDown className="h-3.5 w-3.5" /> Reject
                    </Button>
                  </>
                ) : (
                  <p className="text-xs text-slate-400 dark:text-slate-400">Awaiting super admin review</p>
                )}
                <Button size="sm" variant="outline" onClick={() => openEditModal(application)}>
                  <Pencil className="h-3.5 w-3.5" /> Edit
                </Button>
                <Button size="sm" variant="outline" onClick={() => withdrawApplication(application.id)}>
                  <XCircle className="h-3.5 w-3.5" /> Withdraw
                </Button>
              </div>
            )}

            {latestDecision?.decision === "APPROVED" && !offer && (
              <div className="mt-3">
                <Button size="sm" variant="accent" onClick={() => createOffer(application)}>
                  <ClipboardList className="h-3.5 w-3.5" /> Create Offer
                </Button>
              </div>
            )}

            {offer && (
              <div className="mt-3 space-y-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Offer</p>
                  <Badge tone={offerStatusTone[offer.status]}>{offerStatusLabel[offer.status] ?? offer.status}</Badge>
                </div>
                {latestTerms ? (
                  <p className="text-sm text-slate-700 dark:text-slate-200">
                    {formatCurrency(latestTerms.monthlyRent)}/month · {formatCurrency(latestTerms.depositAmount)} deposit ·{" "}
                    {latestTerms.termMonths} months from {formatDate(latestTerms.startDate)}
                  </p>
                ) : (
                  <p className="text-sm text-slate-400">No terms yet</p>
                )}
                <div className="flex flex-wrap gap-2">
                  {(offer.status === "DRAFT" || offer.status === "SENT") && (
                    <Button size="sm" variant="outline" onClick={() => openTermsModal(offer.id)}>
                      {latestTerms ? "Update Terms" : "Add Terms"}
                    </Button>
                  )}
                  {offer.status === "DRAFT" && latestTerms && (
                    <Button size="sm" variant="primary" onClick={() => transitionOffer(offer.id, "send")}>
                      <Send className="h-3.5 w-3.5" /> Send Offer
                    </Button>
                  )}
                  {offer.status === "SENT" && (
                    <>
                      <Button size="sm" variant="primary" onClick={() => transitionOffer(offer.id, "accept")}>
                        <CheckCircle2 className="h-3.5 w-3.5" /> Accept
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => transitionOffer(offer.id, "decline")}>
                        <XCircle className="h-3.5 w-3.5" /> Decline
                      </Button>
                    </>
                  )}
                  {offer.status === "ACCEPTED" && !agreement && (
                    <Button size="sm" variant="accent" onClick={() => createAgreement(offer.id)}>
                      <FileSignature className="h-3.5 w-3.5" /> Create Agreement
                    </Button>
                  )}
                </div>
              </div>
            )}

            {agreement && (
              <div className="mt-2 space-y-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Agreement</p>
                  <Badge tone={agreementStatusTone[agreement.status]}>{agreementStatusLabel[agreement.status] ?? agreement.status}</Badge>
                </div>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Provider signed: {agreement.signedByProviderAt ? formatDate(agreement.signedByProviderAt) : "Not yet"} · Renter
                  signed: {agreement.signedByRenterAt ? formatDate(agreement.signedByRenterAt) : "Not yet"}
                </p>
                <div className="flex flex-wrap gap-2">
                  {agreement.status === "DRAFT" && (
                    <Button size="sm" variant="primary" onClick={() => sendAgreement(agreement.id)}>
                      <Send className="h-3.5 w-3.5" /> Send Agreement
                    </Button>
                  )}
                  {agreement.status !== "SIGNED" && agreement.status !== "VOID" && (
                    <>
                      {!agreement.signedByProviderAt && (
                        <Button size="sm" variant="outline" onClick={() => signAgreement(agreement.id, "provider")}>
                          Sign as Provider
                        </Button>
                      )}
                      {!agreement.signedByRenterAt && (
                        <Button size="sm" variant="outline" onClick={() => signAgreement(agreement.id, "renter")}>
                          Sign as Renter
                        </Button>
                      )}
                    </>
                  )}
                  {agreement.status === "SIGNED" && (
                    <Button size="sm" variant="accent" onClick={() => confirmMoveIn(agreement.id)}>
                      <DoorOpen className="h-3.5 w-3.5" /> Confirm Move-In
                    </Button>
                  )}
                  <Button size="sm" variant="outline" onClick={() => downloadAgreementPdf(agreement.id)}>
                    <Download className="h-3.5 w-3.5" /> Download PDF
                  </Button>
                </div>
              </div>
            )}
          </div>
        );
      })}

      {applications.length === 0 && (
        <p className="py-10 text-center text-sm text-slate-400 dark:text-slate-400">No applications yet.</p>
      )}

      <Modal
        open={applicationModalOpen}
        onClose={() => setApplicationModalOpen(false)}
        title={editingApplicationId !== null ? "Edit Application" : "New Application"}
      >
        <form onSubmit={submitApplication} className="space-y-3.5">
          {editingApplicationId === null && (
            <>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                  Listing
                </label>
                <select
                  value={applicationForm.listingId}
                  onChange={(e) => setApplicationForm((f) => ({ ...f, listingId: e.target.value }))}
                  className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                  required
                >
                  <option value="">Select a published listing…</option>
                  {publishedListings.map((listing) => (
                    <option key={listing.id} value={listing.id}>
                      {listing.name} — {listing.city}
                    </option>
                  ))}
                </select>
                {publishedListings.length === 0 && (
                  <p className="mt-1 text-xs text-accent-600">
                    No published listings yet — publish one from Properties &amp; Rooms first.
                  </p>
                )}
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                    Renter Name
                  </label>
                  <input
                    value={applicationForm.name}
                    onChange={(e) => setApplicationForm((f) => ({ ...f, name: e.target.value }))}
                    className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                    required
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                    Renter Email
                  </label>
                  <input
                    type="email"
                    value={applicationForm.email}
                    onChange={(e) => setApplicationForm((f) => ({ ...f, email: e.target.value }))}
                    className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                    required
                  />
                </div>
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                  Renter Phone
                </label>
                <input
                  value={applicationForm.phone}
                  onChange={(e) => setApplicationForm((f) => ({ ...f, phone: e.target.value }))}
                  className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                />
              </div>
            </>
          )}
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Desired Move-In
            </label>
            <input
              type="date"
              value={applicationForm.desiredMoveIn}
              onChange={(e) => setApplicationForm((f) => ({ ...f, desiredMoveIn: e.target.value }))}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Message
            </label>
            <textarea
              value={applicationForm.message}
              onChange={(e) => setApplicationForm((f) => ({ ...f, message: e.target.value }))}
              rows={3}
              className="w-full resize-none rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <Button type="submit" variant="primary" fullWidth>
            {editingApplicationId !== null ? "Save Changes" : "Record Application"}
          </Button>
        </form>
      </Modal>

      <Modal open={rejectingId !== null} onClose={() => setRejectingId(null)} title="Reject Application">
        <form onSubmit={submitReject} className="space-y-3.5">
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Reason (optional)
            </label>
            <textarea
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              rows={3}
              placeholder="Let the renter know why, e.g. income requirements not met"
              className="w-full resize-none rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <Button type="submit" variant="primary" fullWidth>
            Confirm Rejection
          </Button>
        </form>
      </Modal>

      <Modal open={termsOfferId !== null} onClose={() => setTermsOfferId(null)} title="Offer Terms">
        <form onSubmit={submitTerms} className="space-y-3.5">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Monthly Rent (₹)
              </label>
              <input
                type="number"
                min={0}
                value={termsForm.monthlyRent}
                onChange={(e) => setTermsForm((f) => ({ ...f, monthlyRent: e.target.value }))}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                required
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Deposit (₹)
              </label>
              <input
                type="number"
                min={0}
                value={termsForm.depositAmount}
                onChange={(e) => setTermsForm((f) => ({ ...f, depositAmount: e.target.value }))}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                required
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Start Date
              </label>
              <input
                type="date"
                value={termsForm.startDate}
                onChange={(e) => setTermsForm((f) => ({ ...f, startDate: e.target.value }))}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                required
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Term (months)
              </label>
              <input
                type="number"
                min={1}
                value={termsForm.termMonths}
                onChange={(e) => setTermsForm((f) => ({ ...f, termMonths: e.target.value }))}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                required
              />
            </div>
          </div>
          <Button type="submit" variant="primary" fullWidth>
            Save Terms
          </Button>
        </form>
      </Modal>

      {toast && (
        <div className="animate-fade-up fixed bottom-6 right-6 z-[300] flex max-w-sm items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" /> {toast}
        </div>
      )}
    </div>
  );
}
