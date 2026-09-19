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
import {
  ActivationGateStatus,
  AdminRole,
  AdminUserSummary,
  Application,
  DisclosureRequirement,
  Listing,
  Occupancy,
  OptionalClauseChoice,
  PublishEligibility,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { ApiError, apiClientFetch } from "@/lib/api-client";
import { getCurrentAdmin } from "@/lib/auth";
import { agreementStatusLabel, agreementStatusTone, offerStatusLabel, offerStatusTone } from "@/lib/status";
import { formatCurrency, formatDate } from "@/lib/utils";

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

export function LeasingManager() {
  const [applications, setApplications] = useState<Application[]>([]);
  const [publishedListings, setPublishedListings] = useState<Listing[]>([]);
  const [listingsById, setListingsById] = useState<Record<string, Listing>>({});
  const [ownerNamesById, setOwnerNamesById] = useState<Record<number, string>>({});
  const [role, setRole] = useState<AdminRole | null>(null);
  const [toast, setToast] = useState("");
  const [termsOfferId, setTermsOfferId] = useState<number | null>(null);
  const [termsListingId, setTermsListingId] = useState<string | null>(null);
  const [termsForm, setTermsForm] = useState(emptyTermsForm);
  const [agreementOfferId, setAgreementOfferId] = useState<number | null>(null);
  const [agreementClauseChoices, setAgreementClauseChoices] = useState<OptionalClauseChoice[]>([]);
  const [selectedClauseIds, setSelectedClauseIds] = useState<string[]>([]);
  const [signingAsAgent, setSigningAsAgent] = useState(false);
  const [agentAuthorityEvidenceRef, setAgentAuthorityEvidenceRef] = useState("");
  const [applicationModalOpen, setApplicationModalOpen] = useState(false);
  const [editingApplicationId, setEditingApplicationId] = useState<number | null>(null);
  const [applicationForm, setApplicationForm] = useState(emptyApplicationForm);
  const [rejectingId, setRejectingId] = useState<number | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [overlapOfferId, setOverlapOfferId] = useState<number | null>(null);
  const [overlapMessage, setOverlapMessage] = useState("");
  const [overlapReasonInput, setOverlapReasonInput] = useState("");
  const [disclosuresByAgreement, setDisclosuresByAgreement] = useState<Record<number, DisclosureRequirement[]>>({});
  const [expandedDisclosuresAgreementId, setExpandedDisclosuresAgreementId] = useState<number | null>(null);
  const [disclosuresLoading, setDisclosuresLoading] = useState(false);
  const [deliveringDisclosureId, setDeliveringDisclosureId] = useState<number | null>(null);
  const [legalOrderAgreementId, setLegalOrderAgreementId] = useState<number | null>(null);
  const [legalOrderStartDate, setLegalOrderStartDate] = useState("");
  const [legalOrderTermMonths, setLegalOrderTermMonths] = useState("");
  const [legalOrderRent, setLegalOrderRent] = useState("");
  const [legalOrderEvidenceRef, setLegalOrderEvidenceRef] = useState("");
  const [legalOrderReason, setLegalOrderReason] = useState("");
  const [legalOrderSubmitting, setLegalOrderSubmitting] = useState(false);

  const [occupancyByOfferId, setOccupancyByOfferId] = useState<Record<number, Occupancy>>({});
  const [gateStatusByOccupancy, setGateStatusByOccupancy] = useState<Record<number, ActivationGateStatus>>({});
  const [handoverBusy, setHandoverBusy] = useState<string | null>(null);

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3600);
  }

  const loadApplications = useCallback(async () => {
    try {
      const admin = await getCurrentAdmin();
      setRole(admin?.role ?? null);
      const [applicationsData, listingsData, occupanciesData] = await Promise.all([
        apiClientFetch<Application[]>("/api/leasing/applications"),
        apiClientFetch<Listing[]>("/api/listings"),
        apiClientFetch<Occupancy[]>("/api/occupancy"),
      ]);
      setApplications(applicationsData);
      setPublishedListings(listingsData.filter((l) => l.state === "PUBLISHED"));
      setListingsById(Object.fromEntries(listingsData.map((l) => [l.id, l])));
      setOccupancyByOfferId(Object.fromEntries(occupanciesData.map((o) => [o.offerId, o])));

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
      showToast("Application approved");
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

  function openTermsModal(offerId: number, listingId: string) {
    // Suggest a monthly rent from the listing's advertised nightly price
    // instead of leaving the admin to type a number from memory with nothing
    // to check it against -- still fully editable if the actual negotiated
    // rent differs.
    const listing = listingsById[listingId];
    const suggestedMonthlyRent = listing ? String(Math.round(listing.pricePerNight * 30)) : "";
    setTermsForm({ ...emptyTermsForm, monthlyRent: suggestedMonthlyRent });
    setTermsOfferId(offerId);
    setTermsListingId(listingId);
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
      setTermsListingId(null);
      showToast("Offer terms added");
      loadApplications();
    } catch {
      showToast("Failed to add offer terms");
    }
  }

  async function transitionOffer(offerId: number, action: "send" | "accept" | "decline", overrideReason?: string) {
    try {
      await apiClientFetch(`/api/leasing/offers/${offerId}/${action}`, {
        method: "POST",
        ...(action === "accept" ? { body: JSON.stringify({ overrideReason: overrideReason ?? "" }) } : {}),
      });
      showToast(`Offer ${action === "send" ? "sent" : action === "accept" ? "accepted" : "declined"}`);
      setOverlapOfferId(null);
      setOverlapMessage("");
      setOverlapReasonInput("");
      loadApplications();
    } catch (err) {
      // ZR-ENG-CLR-001 Rule 6/Section 9: a BLOCK-tier occupant-overlap
      // conflict isn't a dead end for a walk-in guest either -- let the
      // admin self-declare a reason and proceed anyway, same escape hatch
      // the renter's own self-service accept has.
      if (
        action === "accept" && !overrideReason &&
        err instanceof ApiError && err.status === 409 && err.message.includes("overlapping active booking")
      ) {
        setOverlapOfferId(offerId);
        setOverlapMessage(err.message);
        return;
      }
      showToast(`Failed to ${action} offer`);
    }
  }

  async function openAgreementModal(offerId: number) {
    try {
      const eligibility = await apiClientFetch<PublishEligibility>(`/api/leasing/offers/${offerId}/agreement-eligibility`);
      if (!eligibility.eligible) {
        showToast(`Not eligible to create an agreement: ${eligibility.reasons.join("; ")}`);
        return;
      }
      const choices = await apiClientFetch<OptionalClauseChoice[]>(`/api/leasing/offers/${offerId}/clause-options`);
      setAgreementClauseChoices(choices);
      setSelectedClauseIds([]);
      setSigningAsAgent(false);
      setAgentAuthorityEvidenceRef("");
      setAgreementOfferId(offerId);
    } catch {
      showToast("Failed to load agreement options");
    }
  }

  function toggleClauseId(clauseId: string) {
    setSelectedClauseIds((ids) => (ids.includes(clauseId) ? ids.filter((id) => id !== clauseId) : [...ids, clauseId]));
  }

  async function submitAgreement() {
    if (agreementOfferId === null) return;
    if (signingAsAgent && !agentAuthorityEvidenceRef.trim()) {
      showToast("Authority evidence is required when signing as an agent");
      return;
    }
    try {
      await apiClientFetch(`/api/leasing/offers/${agreementOfferId}/agreement`, {
        method: "POST",
        body: JSON.stringify({
          selectedOptionalClauseIds: selectedClauseIds,
          signingAsAgent,
          agentAuthorityEvidenceRef: agentAuthorityEvidenceRef.trim(),
        }),
      });
      showToast("Agreement created as draft");
      setAgreementOfferId(null);
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

  const loadGateStatus = useCallback(async (occupancyId: number) => {
    try {
      const data = await apiClientFetch<ActivationGateStatus>(`/api/occupancy/${occupancyId}/activation-gate`);
      setGateStatusByOccupancy((prev) => ({ ...prev, [occupancyId]: data }));
    } catch {
      // Non-fatal -- handover progress just won't show for this one.
    }
  }, []);

  useEffect(() => {
    Object.values(occupancyByOfferId).forEach((occ) => {
      if (occ.status === "PENDING_MOVE_IN" && !(occ.id in gateStatusByOccupancy)) {
        loadGateStatus(occ.id);
      }
    });
  }, [occupancyByOfferId, gateStatusByOccupancy, loadGateStatus]);

  async function recordHandoverStep(occupancyId: number, step: "prepare" | "events") {
    const key = `${occupancyId}:${step}`;
    setHandoverBusy(key);
    try {
      await apiClientFetch(`/api/occupancy/${occupancyId}/handover/${step}`, {
        method: "POST",
        body: JSON.stringify({ evidenceRef: "", notes: "" }),
      });
      await loadGateStatus(occupancyId);
      showToast(step === "prepare" ? "Handover marked ready" : "Possession marked delivered");
    } catch {
      showToast("Failed to record this handover step");
    } finally {
      setHandoverBusy(null);
    }
  }

  async function toggleDisclosures(agreementId: number) {
    if (expandedDisclosuresAgreementId === agreementId) {
      setExpandedDisclosuresAgreementId(null);
      return;
    }
    setExpandedDisclosuresAgreementId(agreementId);
    if (disclosuresByAgreement[agreementId]) return;
    setDisclosuresLoading(true);
    try {
      const data = await apiClientFetch<DisclosureRequirement[]>(`/api/leasing/agreements/${agreementId}/disclosures`);
      setDisclosuresByAgreement((prev) => ({ ...prev, [agreementId]: data }));
    } catch {
      showToast("Failed to load disclosures");
    } finally {
      setDisclosuresLoading(false);
    }
  }

  async function deliverDisclosure(agreementId: number, disclosureId: number) {
    setDeliveringDisclosureId(disclosureId);
    try {
      const updated = await apiClientFetch<DisclosureRequirement>(
        `/api/leasing/agreements/${agreementId}/disclosures/${disclosureId}/deliver`, { method: "POST" },
      );
      setDisclosuresByAgreement((prev) => ({
        ...prev,
        [agreementId]: (prev[agreementId] ?? []).map((d) => (d.id === updated.id ? updated : d)),
      }));
      showToast("Disclosure delivered");
    } catch {
      showToast("Failed to deliver this disclosure");
    } finally {
      setDeliveringDisclosureId(null);
    }
  }

  function openLegalOrder(agreementId: number) {
    setLegalOrderAgreementId(agreementId);
    setLegalOrderStartDate("");
    setLegalOrderTermMonths("");
    setLegalOrderRent("");
    setLegalOrderEvidenceRef("");
    setLegalOrderReason("");
  }

  async function submitLegalOrderChange() {
    if (!legalOrderAgreementId) return;
    if (!legalOrderEvidenceRef.trim()) return showToast("An authority evidence reference is required");
    const payload: Record<string, unknown> = { authorityEvidenceRef: legalOrderEvidenceRef.trim(), reason: legalOrderReason.trim() };
    if (legalOrderStartDate) payload.proposedStartDate = legalOrderStartDate;
    if (legalOrderTermMonths) {
      const months = Number(legalOrderTermMonths);
      if (!Number.isInteger(months) || months < 1) return showToast("Enter a whole number of months (at least 1)");
      payload.newTermMonths = months;
    }
    if (legalOrderRent) {
      const rent = Number(legalOrderRent);
      if (!Number.isFinite(rent) || rent <= 0) return showToast("Enter a valid monthly rent");
      payload.proposedMonthlyRent = rent;
    }
    if (!payload.proposedStartDate && !payload.newTermMonths && !payload.proposedMonthlyRent) {
      return showToast("Provide at least one of: new move-in date, new term, or new rent");
    }

    setLegalOrderSubmitting(true);
    try {
      await apiClientFetch(`/api/leasing/agreements/${legalOrderAgreementId}/legal-order-changes`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      showToast("Legal order change applied — the tenant needs to re-sign the updated agreement.");
      setLegalOrderAgreementId(null);
    } catch {
      showToast("Failed to apply this legal order change");
    } finally {
      setLegalOrderSubmitting(false);
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
                    {formatCurrency(latestTerms.monthlyRent, latestTerms.currency)}/month ·{" "}
                    {formatCurrency(latestTerms.depositAmount, latestTerms.currency)} deposit ·{" "}
                    {latestTerms.termMonths} months from {formatDate(latestTerms.startDate)}
                  </p>
                ) : (
                  <p className="text-sm text-slate-400">No terms yet</p>
                )}
                <div className="flex flex-wrap gap-2">
                  {(offer.status === "DRAFT" || offer.status === "SENT") && (
                    <Button size="sm" variant="outline" onClick={() => openTermsModal(offer.id, application.listingId)}>
                      {latestTerms ? "Update Terms" : "Add Terms"}
                    </Button>
                  )}
                  {offer.status === "DRAFT" && latestTerms && (
                    <Button size="sm" variant="primary" onClick={() => transitionOffer(offer.id, "send")}>
                      <Send className="h-3.5 w-3.5" /> Send Offer
                    </Button>
                  )}
                  {offer.status === "SENT" && (
                    offer.guestHasAccount ? (
                      <p className="text-xs italic text-slate-400 dark:text-slate-500">
                        Awaiting the renter&apos;s own response — they have a Zoiko account and must accept or decline this themselves.
                      </p>
                    ) : (
                      <>
                        <Button size="sm" variant="primary" onClick={() => transitionOffer(offer.id, "accept")}>
                          <CheckCircle2 className="h-3.5 w-3.5" /> Accept
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => transitionOffer(offer.id, "decline")}>
                          <XCircle className="h-3.5 w-3.5" /> Decline
                        </Button>
                      </>
                    )
                  )}
                  {offer.status === "ACCEPTED" && !agreement && (
                    <Button size="sm" variant="accent" onClick={() => openAgreementModal(offer.id)}>
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

                {agreement.status === "SIGNED" && (() => {
                  const occupancy = occupancyByOfferId[offer.id];
                  if (!occupancy || occupancy.status !== "PENDING_MOVE_IN") return null;
                  const gate = gateStatusByOccupancy[occupancy.id];
                  const eventTypes = new Set((gate?.handoverEvents ?? []).map((e) => e.eventType));
                  const hasReady = eventTypes.has("HANDOVER_READY");
                  const hasDelivered = eventTypes.has("POSSESSION_DELIVERED");
                  const hasReceipt = eventTypes.has("RENTER_RECEIPT");
                  return (
                    <div className="space-y-1.5 rounded-xl bg-white p-2.5 ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
                      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                        Move-in handover (all 3 required before Confirm Move-In)
                      </p>
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone={hasReady ? "success" : "warning"}>1. Handover ready{hasReady ? " ✓" : ""}</Badge>
                        {!hasReady && (
                          <Button
                            size="sm"
                            variant="outline"
                            loading={handoverBusy === `${occupancy.id}:prepare`}
                            onClick={() => recordHandoverStep(occupancy.id, "prepare")}
                          >
                            Mark Handover Ready
                          </Button>
                        )}
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone={hasDelivered ? "success" : "warning"}>2. Possession delivered{hasDelivered ? " ✓" : ""}</Badge>
                        {hasReady && !hasDelivered && (
                          <Button
                            size="sm"
                            variant="outline"
                            loading={handoverBusy === `${occupancy.id}:events`}
                            onClick={() => recordHandoverStep(occupancy.id, "events")}
                          >
                            Mark Possession Delivered
                          </Button>
                        )}
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone={hasReceipt ? "success" : "warning"}>
                          3. Renter confirms receipt{hasReceipt ? " ✓" : " (the renter does this from their own account)"}
                        </Badge>
                      </div>
                    </div>
                  );
                })()}

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
                        offer.guestHasAccount ? (
                          <p className="text-xs italic text-slate-400 dark:text-slate-500">
                            Awaiting the renter&apos;s own signature — they have a Zoiko account and must sign this themselves.
                          </p>
                        ) : (
                          <Button size="sm" variant="outline" onClick={() => signAgreement(agreement.id, "renter")}>
                            Sign as Renter
                          </Button>
                        )
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
                  {agreement.status !== "DRAFT" && (
                    <Button size="sm" variant="outline" onClick={() => toggleDisclosures(agreement.id)}>
                      {expandedDisclosuresAgreementId === agreement.id ? "Hide Disclosures" : "Disclosures"}
                    </Button>
                  )}
                  {role === "super_admin" && agreement.status === "SIGNED" && (
                    <Button size="sm" variant="outline" onClick={() => openLegalOrder(agreement.id)}>
                      Legal Order Change
                    </Button>
                  )}
                </div>

                {expandedDisclosuresAgreementId === agreement.id && (
                  <div className="mt-2 space-y-2 rounded-xl bg-white p-2.5 ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
                    {disclosuresLoading && !disclosuresByAgreement[agreement.id] ? (
                      <p className="text-xs text-slate-400">Loading...</p>
                    ) : (disclosuresByAgreement[agreement.id] ?? []).length === 0 ? (
                      <p className="text-xs text-slate-400">No disclosures on this agreement.</p>
                    ) : (
                      disclosuresByAgreement[agreement.id].map((d) => (
                        <div key={d.id} className="flex items-center justify-between gap-2">
                          <span className="text-xs text-slate-700 dark:text-slate-200">{d.title || d.disclosureType}</span>
                          {d.status === "REQUIRED_MISSING" ? (
                            <Button
                              size="sm"
                              variant="outline"
                              loading={deliveringDisclosureId === d.id}
                              onClick={() => deliverDisclosure(agreement.id, d.id)}
                            >
                              Deliver
                            </Button>
                          ) : (
                            <Badge tone={d.status === "ACKNOWLEDGED" ? "success" : "primary"}>
                              {d.status === "ACKNOWLEDGED" ? "Acknowledged" : "Delivered"}
                            </Badge>
                          )}
                        </div>
                      ))
                    )}
                  </div>
                )}
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

      <Modal
        open={overlapOfferId !== null}
        onClose={() => {
          setOverlapOfferId(null);
          setOverlapMessage("");
          setOverlapReasonInput("");
        }}
        title="Overlapping booking detected"
      >
        <div className="space-y-3.5">
          <p className="text-sm text-slate-600 dark:text-slate-300">{overlapMessage}</p>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            You can still accept this offer if you have a genuine reason (e.g. the other booking is ending) — this
            will be recorded on the offer for later review.
          </p>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Reason for accepting anyway
            </label>
            <textarea
              value={overlapReasonInput}
              onChange={(e) => setOverlapReasonInput(e.target.value)}
              rows={3}
              className="w-full resize-none rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <Button
            variant="primary"
            fullWidth
            disabled={!overlapReasonInput.trim()}
            onClick={() => {
              if (overlapOfferId !== null) transitionOffer(overlapOfferId, "accept", overlapReasonInput.trim());
            }}
          >
            Accept anyway
          </Button>
        </div>
      </Modal>

      <Modal open={termsOfferId !== null} onClose={() => { setTermsOfferId(null); setTermsListingId(null); }} title="Offer Terms">
        <form onSubmit={submitTerms} className="space-y-3.5">
          {termsListingId && listingsById[termsListingId] && (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Listing is advertised at{" "}
              {formatCurrency(listingsById[termsListingId].pricePerNight, listingsById[termsListingId].currency)}/night —
              monthly rent below is
              pre-filled from that; change it if the actual agreed rent differs.
            </p>
          )}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Monthly Rent{termsListingId && listingsById[termsListingId] ? ` (${listingsById[termsListingId].currency})` : ""}
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
                Deposit{termsListingId && listingsById[termsListingId] ? ` (${listingsById[termsListingId].currency})` : ""}
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

      <Modal open={agreementOfferId !== null} onClose={() => setAgreementOfferId(null)} title="Create Agreement">
        <div className="space-y-3.5">
          {agreementClauseChoices.length > 0 ? (
            <>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Pick any optional clauses that apply to this tenancy. Mandatory clauses are always included automatically.
              </p>
              <div className="space-y-2">
                {agreementClauseChoices.map((choice) => (
                  <label
                    key={choice.clauseId}
                    className="flex items-center gap-2 rounded-xl bg-slate-50 px-4 py-2.5 text-sm dark:bg-slate-800 dark:text-slate-100"
                  >
                    <input
                      type="checkbox"
                      checked={selectedClauseIds.includes(choice.clauseId)}
                      onChange={() => toggleClauseId(choice.clauseId)}
                    />
                    {choice.title}
                  </label>
                ))}
              </div>
            </>
          ) : (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              No optional clauses apply to this listing — the agreement will use its mandatory clauses only.
            </p>
          )}
          <div className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-700">
            <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
              <input type="checkbox" checked={signingAsAgent} onChange={(e) => setSigningAsAgent(e.target.checked)} />
              I am signing as an authorized agent on behalf of the landlord/company
            </label>
            {signingAsAgent && (
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                  Authority Evidence Reference
                </label>
                <input
                  type="text"
                  value={agentAuthorityEvidenceRef}
                  onChange={(e) => setAgentAuthorityEvidenceRef(e.target.value)}
                  placeholder="e.g. letting-agent-mandate.pdf"
                  className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                  required
                />
              </div>
            )}
          </div>
          <Button variant="primary" fullWidth onClick={submitAgreement}>
            Create Agreement
          </Button>
        </div>
      </Modal>

      <Modal
        open={Boolean(legalOrderAgreementId)}
        onClose={() => setLegalOrderAgreementId(null)}
        title="Apply a legal/regulatory order change"
      >
        <div className="space-y-3.5">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            No tenant approval is required — a court/regulator order is its own authority. This amends the agreement
            immediately; the tenant will need to re-sign. Provide at least one of the fields below.
          </p>
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">New move-in date (optional)</label>
            <input
              type="date"
              value={legalOrderStartDate}
              onChange={(e) => setLegalOrderStartDate(e.target.value)}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">New term in months (optional)</label>
            <input
              type="number"
              min={1}
              step={1}
              value={legalOrderTermMonths}
              onChange={(e) => setLegalOrderTermMonths(e.target.value)}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">
              New monthly rent (optional{(() => {
                const app = applications.find((a) => a.offer?.agreement?.id === legalOrderAgreementId);
                const currency = app ? listingsById[app.listingId]?.currency : undefined;
                return currency ? `, ${currency}` : "";
              })()})
            </label>
            <input
              type="number"
              min={0}
              step="0.01"
              value={legalOrderRent}
              onChange={(e) => setLegalOrderRent(e.target.value)}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Authority evidence reference (required)</label>
            <input
              value={legalOrderEvidenceRef}
              onChange={(e) => setLegalOrderEvidenceRef(e.target.value)}
              placeholder="e.g. Rent Tribunal Order RT-2026-0091"
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Reason / note (optional)</label>
            <textarea
              value={legalOrderReason}
              onChange={(e) => setLegalOrderReason(e.target.value)}
              rows={3}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setLegalOrderAgreementId(null)}>
              Cancel
            </Button>
            <Button variant="primary" loading={legalOrderSubmitting} onClick={submitLegalOrderChange}>
              Apply change
            </Button>
          </div>
        </div>
      </Modal>

      {toast && (
        <div className="animate-fade-up fixed bottom-6 right-6 z-[300] flex max-w-sm items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" /> {toast}
        </div>
      )}
    </div>
  );
}
